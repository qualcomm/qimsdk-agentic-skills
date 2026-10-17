#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
workspace_setup_m.py — Idempotent Mode M host preflight and aarch64 module build.

Mode M cross-compiles an imsdk-postproc-<module>/ artifact (a
ml-postprocess-<module>.cc + CMakeLists.txt for a qtimlpostprocess post-process
module) into an aarch64 shared library, then deploy_mode_m.py installs it on the
device. This module owns the host side: preflight, cross-compile, and the gates.

Contrast with Mode C / Mode D:
  - Mode C/D build on a separate Linux workstation over SSH, against a large SDK
    (gst-plugins-imsdk source tree / the Yocto standard SDK).
  - Mode M builds LOCALLY on whatever Linux/WSL host runs the skill, with the
    distro cross-compiler and a CMake toolchain file. There is no SDK, no
    sysroot, no SSH and no workstation in this step — a post-process module
    links no IMSDK library at all, only the C++ runtime.

Recipe:
  1. Preflight: aarch64-linux-gnu-g++/gcc, cmake >= 3.8.2, make, binutils
     (nm/objdump/readelf/file), artifact shape, header resolvability.
  2. Emit aarch64-toolchain.cmake into the output dir.
  3. cmake -S <artifact> -B <output>/build -DCMAKE_TOOLCHAIN_FILE=... \\
       -DIMSDK_MODULES_INCLUDE_DIR=<include dir>
  4. cmake --build, then GATE the produced library on four properties that a
     green `cmake --build` does not establish:
       1. target architecture is aarch64          (else: dlopen "wrong ELF class")
       2. filename is libml-postprocess-<mod>.so  (else: the engine never looks for it)
       3. NewModule is exported unmangled         (else: the factory cannot be resolved)
       4. required GLIBC/GLIBCXX <= device        (else: dlopen "version not found")

Host prerequisites (one-time, manual — this module verifies but never installs
them, because one needs sudo):
  sudo apt-get install g++-aarch64-linux-gnu cmake build-essential

The three module headers (qti-ml-post-process.h, and qti-labels-parser.h /
qti-json-parser.h when used) live in the PLUGIN SOURCE TREE and are in no SDK
sysroot: $SDKTARGETSYSROOT/usr/include contains no qti-ml-post-process.h, so a
working cross-compiler is not sufficient on its own. Resolution order is:
--imsdk-include, the artifact's own include/, the artifact root,
$IMSDK_MODULES_INCLUDE_DIR, then a plugin checkout ($IMSDK_SRC, ~/qimsdk-src,
~/gst-plugins-imsdk, ~/gst-plugins-qimsdk). Preferred is for
qimsdk-model-onboarding to vendor the headers into the artifact's include/, so
the artifact is self-contained and reproducible.

Stdlib only, deliberately: this must run on whatever `python3` is on PATH,
including an SDK-shadowed interpreter with no site-packages.

Returns (setup_and_build_m):
    {
      'success':        bool,          # every gate passed
      'module_name':    str or None,
      'lib_filename':   str or None,   # libml-postprocess-<module>.so
      'so_path':        str or None,   # published library, next to the result
      'build_log':      str,           # full configure + build transcript
      'failure_reason': str or None,
      'steps':          [{'step','status','detail'}],
      ... plus toolchain / include_dir / ABI / needed_libs detail, see _Build
    }

Also runnable on its own for a device-free build (no configs/.env needed):
    python3 workspace_setup_m.py <artifact_dir> [--output-dir <dir>]
    python3 workspace_setup_m.py <artifact_dir> --preflight-only
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys


# ── Host toolchain constants ───────────────────────────────────────────────────

APT_INSTALL = 'sudo apt-get install g++-aarch64-linux-gnu cmake build-essential'

CROSS_CXX = 'aarch64-linux-gnu-g++'
CROSS_CC = 'aarch64-linux-gnu-gcc'

MIN_CMAKE = (3, 8, 2)

REQUIRED_HEADER = 'qti-ml-post-process.h'
OPTIONAL_HEADERS = ('qti-labels-parser.h', 'qti-json-parser.h')

# Checkout roots probed for a plugin source tree, in order.
CHECKOUT_ROOTS = ('$IMSDK_SRC', '~/qimsdk-src', '~/gst-plugins-imsdk', '~/gst-plugins-qimsdk')

# Both layouts: a plain checkout has the plugin dirs at its root, but some docs describe
# the path with a `gstreamer/` prefix. Probe both rather than trusting either.
CHECKOUT_SUBPATHS = (
    'gst-plugin-mlpostprocess/modules',
    'gstreamer/gst-plugin-mlpostprocess/modules',
)

# Inferred from a module confirmed to load on a QLI 2.0 device, not read off a device.
# Override with max_glibc / max_glibcxx when the device's real versions are known —
# deploy_mode_m.py measures them and reports the exact flags to use next time.
DEFAULT_MAX_GLIBC = '2.38'
DEFAULT_MAX_GLIBCXX = '3.4.32'

# A post-process module links no IMSDK library; anything beyond the C++ runtime must also
# exist on the device, and usually does not.
EXPECTED_NEEDED = {
    'libstdc++.so.6', 'libm.so.6', 'libgcc_s.so.1', 'libc.so.6',
    'ld-linux-aarch64.so.1',
}

# Toolchain variables that a sourced Yocto/eSDK environment-setup script exports. CMake
# seeds CMAKE_<LANG>_FLAGS from $CFLAGS/$CXXFLAGS and the compiler from $CC/$CXX on first
# configure, so leaving these in place makes the same command produce different binaries in
# different shells -- measured: -O2 -g -pipe leaking in produced a 1.3 MB library where a
# clean environment produced 147 KB. The toolchain file already pins the compiler; these are
# dropped so the flags are pinned too.
HERMETIC_DROP = (
    'CC', 'CXX', 'CPP', 'AS', 'LD', 'AR', 'NM', 'RANLIB', 'STRIP', 'OBJDUMP', 'OBJCOPY',
    'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'ASFLAGS', 'MAKEFLAGS',
    'PKG_CONFIG_PATH', 'PKG_CONFIG_SYSROOT_DIR', 'PKG_CONFIG_LIBDIR',
    'SDKTARGETSYSROOT', 'CMAKE_TOOLCHAIN_FILE', 'CMAKE_PREFIX_PATH',
)

TOOLCHAIN_FALLBACK = """\
set(CMAKE_SYSTEM_NAME      Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER   aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_C_FLAGS_INIT   "-march=armv8-a")
set(CMAKE_CXX_FLAGS_INIT "-march=armv8-a")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM BEFORE)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
"""

# Pointer used in preflight remedies, so a failure names the doc that explains it.
_PREREQ_DOC = 'SKILL.md "Mode M — Post-Process Module" -> Host prerequisites'


# ── Utils ──────────────────────────────────────────────────────────────────────

def vtuple(text):
    """'3.4.32' -> (3, 4, 32). Non-numeric tails are dropped."""
    parts = []
    for chunk in str(text).split('.'):
        m = re.match(r'^(\d+)', chunk)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts)


def expand(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def run(cmd, timeout=30, env=None):
    """Return (rc, combined output). Never raises for a non-zero exit."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=env)
        return p.returncode, p.stdout.decode('utf-8', 'replace')
    except FileNotFoundError:
        return 127, ''
    except subprocess.TimeoutExpired:
        return 124, ''


def human(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.1f %s' % (n, unit) if unit != 'B' else '%d B' % n
        n /= 1024.0


class Check:
    def __init__(self, name, ok, detail, remedy=None, warn=False):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.remedy = remedy
        self.warn = warn

    def as_dict(self):
        return {
            'check': self.name,
            'status': 'ok' if self.ok else ('warn' if self.warn else 'fail'),
            'detail': self.detail,
            'remedy': self.remedy,
        }


# ── Preflight: host toolchain ──────────────────────────────────────────────────

def check_tools():
    checks = []

    for tool in (CROSS_CXX, CROSS_CC):
        path = shutil.which(tool)
        if path:
            rc, out = run([tool, '-dumpversion'])
            ver = out.strip().splitlines()[0] if rc == 0 and out.strip() else '?'
            checks.append(Check(tool, True, '%s (gcc %s)' % (path, ver)))
        else:
            checks.append(Check(
                tool, False,
                'not found on PATH',
                remedy=APT_INSTALL,
            ))

    cmake = shutil.which('cmake')
    if not cmake:
        checks.append(Check('cmake', False, 'not found on PATH', remedy=APT_INSTALL))
    else:
        rc, out = run(['cmake', '--version'])
        m = re.search(r'cmake version ([\d.]+)', out or '')
        ver = m.group(1) if m else '?'
        if m and vtuple(ver) < MIN_CMAKE:
            checks.append(Check(
                'cmake', False,
                '%s is %s, need >= %s' % (cmake, ver, '.'.join(map(str, MIN_CMAKE))),
                remedy=APT_INSTALL,
            ))
        else:
            detail = '%s (%s)' % (cmake, ver)
            # An SDK-provided cmake works, but flag it: it means an SDK env is sourced,
            # which also shadows python3 and is the usual cause of downstream surprises.
            if '/qcom-esdk/' in cmake or '/sysroots/' in cmake:
                checks.append(Check(
                    'cmake', True,
                    detail + ' -- SDK-provided, not the system cmake',
                    remedy='an SDK environment appears to be sourced; open a clean shell '
                           'for Mode M (see %s)' % _PREREQ_DOC,
                    warn=True,
                ))
            else:
                checks.append(Check('cmake', True, detail))

    # Binutils come with the base system and are multi-target, so they read aarch64
    # objects without a cross-binutils package.
    for tool, pkg in (('make', APT_INSTALL),
                      ('nm', 'sudo apt-get install binutils'),
                      ('objdump', 'sudo apt-get install binutils'),
                      ('readelf', 'sudo apt-get install binutils'),
                      ('file', 'sudo apt-get install file')):
        path = shutil.which(tool)
        checks.append(Check(tool, bool(path), path or 'not found on PATH',
                            remedy=None if path else pkg))

    return checks


# ── Preflight: artifact shape ──────────────────────────────────────────────────

def derive_module_name(artifact):
    """
    Return (module_name, how_it_was_derived).

    The library filename is the module's identity, so this name is load-bearing:
    qtimlpostprocess resolves module=<name> to libml-postprocess-<name>.so by string
    concatenation.
    """
    sources = sorted(glob.glob(os.path.join(artifact, 'ml-postprocess-*.cc')))
    if sources:
        stem = os.path.basename(sources[0])[:-len('.cc')]
        return stem[len('ml-postprocess-'):], 'source filename %s' % os.path.basename(sources[0])

    cml = os.path.join(artifact, 'CMakeLists.txt')
    if os.path.isfile(cml):
        try:
            with open(cml, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError:
            text = ''
        m = re.search(r'set\s*\(\s*TARGET_NAME\s+ml-postprocess-([A-Za-z0-9._-]+)\s*\)',
                      text, re.I)
        if m:
            return m.group(1), 'CMakeLists TARGET_NAME'

    base = os.path.basename(artifact.rstrip('/'))
    if base.startswith('imsdk-postproc-'):
        return base[len('imsdk-postproc-'):], 'artifact folder name'

    return None, None


def check_artifact(artifact):
    checks = []

    cml = os.path.join(artifact, 'CMakeLists.txt')
    checks.append(Check(
        'CMakeLists.txt', os.path.isfile(cml),
        cml if os.path.isfile(cml) else 'missing from the artifact',
        remedy=None if os.path.isfile(cml)
        else 'malformed artifact -- regenerate it with qimsdk-model-onboarding',
    ))

    sources = sorted(glob.glob(os.path.join(artifact, 'ml-postprocess-*.cc')))
    if len(sources) == 1:
        checks.append(Check('module source', True, os.path.basename(sources[0])))
    elif not sources:
        any_cc = sorted(glob.glob(os.path.join(artifact, '*.cc'))
                        + glob.glob(os.path.join(artifact, '*.cpp')))
        checks.append(Check(
            'module source', False,
            'no ml-postprocess-<module>.cc; found: %s'
            % (', '.join(os.path.basename(p) for p in any_cc) or 'nothing'),
            remedy='the source must be named ml-postprocess-<module>.cc so the library '
                   'filename matches the module identity',
        ))
    else:
        checks.append(Check(
            'module source', False,
            'more than one ml-postprocess-*.cc: %s'
            % ', '.join(os.path.basename(p) for p in sources),
            remedy='one module per artifact; split them',
        ))

    name, how = derive_module_name(artifact)
    checks.append(Check(
        'module identity', bool(name),
        'module=%s (from %s) -> libml-postprocess-%s.so' % (name, how, name) if name
        else 'could not derive a module name',
        remedy=None if name else 'name the source ml-postprocess-<module>.cc, or the '
                                 'folder imsdk-postproc-<module>',
    ))

    return checks, name


# ── Preflight: headers ─────────────────────────────────────────────────────────

def resolve_include_dir(artifact, explicit=None):
    """
    Return (include_dir, how_it_was_resolved, paths_checked).

    First hit wins. Never falls back to a guess -- a mismatched qti-ml-post-process.h is
    an ABI mismatch that compiles cleanly and crashes inside Process().
    """
    checked = []

    def try_dir(path, label):
        if not path:
            return None
        full = expand(path)
        checked.append(full)
        if os.path.isfile(os.path.join(full, REQUIRED_HEADER)):
            return (full, label)
        return None

    candidates = []
    if explicit:
        candidates.append((explicit, '--imsdk-include'))
    candidates.append((os.path.join(artifact, 'include'), "artifact include/"))
    candidates.append((artifact, 'artifact root'))
    if os.environ.get('IMSDK_MODULES_INCLUDE_DIR'):
        candidates.append((os.environ['IMSDK_MODULES_INCLUDE_DIR'],
                           '$IMSDK_MODULES_INCLUDE_DIR'))

    for root in CHECKOUT_ROOTS:
        expanded = expand(root)
        # An unset $IMSDK_SRC expands to the literal string; skip it.
        if '$' in expanded or not os.path.isdir(expanded):
            continue
        for sub in CHECKOUT_SUBPATHS:
            candidates.append((os.path.join(expanded, sub), 'checkout %s' % root))

    for path, label in candidates:
        hit = try_dir(path, label)
        if hit:
            return hit[0], hit[1], checked

    return None, None, checked


def check_headers(artifact, explicit=None):
    checks = []
    inc, how, checked = resolve_include_dir(artifact, explicit)

    if not inc:
        checks.append(Check(
            REQUIRED_HEADER, False,
            'not found. Checked:\n    ' + '\n    '.join(checked or ['(nothing)']),
            remedy='vendor the three qti-*.h into the artifact include/, or set '
                   'IMSDK_MODULES_INCLUDE_DIR / IMSDK_SRC to a plugin checkout '
                   '(the headers are in the plugin source tree, never an SDK sysroot '
                   '-- see %s)' % _PREREQ_DOC,
        ))
        return checks, None, None

    checks.append(Check(REQUIRED_HEADER, True, '%s (via %s)' % (inc, how)))

    # If the source #includes an optional header, it has to be resolvable too. The
    # compiler would say so anyway, but a preflight message is far easier to act on.
    src_text = ''
    for path in sorted(glob.glob(os.path.join(artifact, 'ml-postprocess-*.cc'))
                       + glob.glob(os.path.join(artifact, 'ml-postprocess-*.h'))):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                src_text += fh.read()
        except OSError:
            pass

    for header in OPTIONAL_HEADERS:
        if header not in src_text:
            continue
        present = os.path.isfile(os.path.join(inc, header))
        checks.append(Check(
            header, present,
            'required by the source, %s' % ('found' if present else 'MISSING from ' + inc),
            remedy=None if present else 'copy %s alongside %s' % (header, REQUIRED_HEADER),
        ))

    return checks, inc, how


def run_preflight(artifact, explicit_include=None):
    """Host-side Mode M preflight. Returns (report_dict, [Check]).

    Called by preflight_check.py --mode M and by setup_and_build_m below, so the
    build never runs against an environment preflight would have rejected.
    """
    artifact = expand(artifact)
    checks = []

    art_checks, module_name = check_artifact(artifact)
    checks.extend(art_checks)
    checks.extend(check_tools())
    hdr_checks, include_dir, include_how = check_headers(artifact, explicit_include)
    checks.extend(hdr_checks)

    failed = [c for c in checks if not c.ok and not c.warn]

    return {
        'artifact': artifact,
        'artifact_name': os.path.basename(artifact.rstrip('/')),
        'module_name': module_name,
        'lib_filename': 'libml-postprocess-%s.so' % module_name if module_name else None,
        'include_dir': include_dir,
        'include_dir_source': include_how,
        'passed': not failed,
        'checks': [c.as_dict() for c in checks],
    }, checks


# ── Build accumulator ──────────────────────────────────────────────────────────

class _Build:
    """Accumulates steps and the build detail that deploy_mode_m.py folds into result.json."""

    def __init__(self):
        self.data = {
            'success': False,
            'module_name': None,
            'lib_filename': None,
            'toolchain': None,
            'include_dir': None,
            'include_dir_source': None,
            'so_path': None,
            'so_size': None,
            'file_output': None,
            'required_glibc': None,
            'required_glibcxx': None,
            'required_cxxabi': None,
            'abi_ceiling': {},
            'needed_libs': [],
            'warnings': [],
            'gates_skipped': [],
            'hermetic_dropped': [],
            'build_log': '',
            'failure_reason': None,
            'next_step': None,
            'steps': [],
        }

    def step(self, name, status, detail=None):
        self.data['steps'].append({'step': name, 'status': status, 'detail': detail})

    def fail(self, name, reason):
        self.step(name, 'fail', reason)
        self.data['failure_reason'] = reason
        self.data['success'] = False

    def warn(self, message):
        self.data['warnings'].append(message)


# ── Gates ──────────────────────────────────────────────────────────────────────

def gate_arch(res, so_path):
    rc, out = run(['file', '-b', so_path])
    text = (out or '').strip()
    res.data['file_output'] = text
    low = text.lower()

    if 'aarch64' not in low:
        res.fail('gate_arch',
                 'the library is not an aarch64 ELF -- the toolchain file was not applied. '
                 'file reported: %s' % text[:200])
        return False
    if 'shared object' not in low:
        res.fail('gate_arch',
                 'the artifact is not a shared object; qtimlpostprocess can only dlopen a '
                 'shared library. file reported: %s' % text[:200])
        return False

    res.step('gate_arch', 'ok', text[:120])
    return True


def gate_filename(res, so_path, expected):
    actual = os.path.basename(so_path)
    if actual != expected:
        res.fail('gate_filename',
                 'built %s, but qtimlpostprocess resolves module=<name> to %s by string '
                 'concatenation and will never look for any other name. Fix '
                 'set(TARGET_NAME ...) in the artifact CMakeLists -- do not rename the '
                 'output.' % (actual, expected))
        return False
    res.step('gate_filename', 'ok', '%s -> module=%s'
             % (actual, expected[len('libml-postprocess-'):-len('.so')]))
    return True


def gate_symbol(res, so_path):
    rc, out = run(['nm', '-D', '--defined-only', so_path])
    text = out or ''

    if re.search(r'\sNewModule$', text, re.M):
        res.step('gate_symbol', 'ok', 'NewModule exported unmangled (extern "C")')
        return True

    if 'NewModule' in text:
        found = next((ln.strip() for ln in text.splitlines() if 'NewModule' in ln), '?')
        res.fail('gate_symbol',
                 'a NewModule symbol exists but is mangled (C++ linkage), so the engine '
                 'cannot resolve it. Declare it as: extern "C" IModule* '
                 'NewModule(LogCallback logger). Found: %s' % found)
        return False

    res.fail('gate_symbol',
             'no NewModule symbol is exported. The engine resolves the factory by the name '
             'in ML_POST_PROCESS_MODULE_NEW_FUNC. Declare it as: extern "C" IModule* '
             'NewModule(LogCallback logger)')
    return False


def _max_version(pattern, text):
    found = re.findall(pattern, text)
    if not found:
        return None
    return max(found, key=vtuple)


def gate_abi(res, so_path, max_glibc, max_glibcxx, skip):
    rc, out = run(['objdump', '-T', so_path])
    text = out or ''

    glibc = _max_version(r'GLIBC_(\d[\d.]*)', text)
    glibcxx = _max_version(r'GLIBCXX_(\d[\d.]*)', text)
    cxxabi = _max_version(r'CXXABI_(\d[\d.]*)', text)

    res.data['required_glibc'] = glibc
    res.data['required_glibcxx'] = glibcxx
    res.data['required_cxxabi'] = cxxabi
    res.data['abi_ceiling'] = {'glibc': max_glibc, 'glibcxx': max_glibcxx}

    summary = 'GLIBC_%s / GLIBCXX_%s' % (glibc or '-', glibcxx or '-')

    if skip:
        res.data['gates_skipped'].append('gate_abi')
        res.step('gate_abi', 'skip', 'bypassed by --skip-abi-gate; requires %s' % summary)
        return True

    over = []
    if glibc and vtuple(glibc) > vtuple(max_glibc):
        over.append('GLIBC_%s > %s' % (glibc, max_glibc))
    if glibcxx and vtuple(glibcxx) > vtuple(max_glibcxx):
        over.append('GLIBCXX_%s > %s' % (glibcxx, max_glibcxx))

    if over:
        remedy = ('re-run with --static-cxx to drop the libstdc++/libgcc version '
                  'dependencies entirely'
                  if any(o.startswith('GLIBCXX') for o in over)
                  else 'a newer glibc cannot be statically avoided; build against a '
                       'device-matched toolchain or confirm the device really is older')
        res.fail('gate_abi',
                 'the library references symbol versions newer than the device ceiling '
                 '(%s). It would install fine and then fail at dlopen with "version not '
                 'found". %s' % ('; '.join(over), remedy))
        return False

    res.step('gate_abi', 'ok', '%s (ceiling %s / %s)' % (summary, max_glibc, max_glibcxx))
    return True


def gate_needed(res, so_path):
    """Non-fatal: an unexpected NEEDED entry must also exist on the device."""
    rc, out = run(['readelf', '-d', so_path])
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', out or '')
    res.data['needed_libs'] = needed

    unexpected = [lib for lib in needed if lib not in EXPECTED_NEEDED]
    if unexpected:
        msg = ('unexpected NEEDED entries: %s -- each must also be present on the device, '
               'and a post-process module normally links only the C++ runtime'
               % ', '.join(unexpected))
        res.warn(msg)
        res.step('gate_needed', 'warn', msg)
    else:
        res.step('gate_needed', 'ok', ', '.join(needed) or '(none)')
    return True


# ── Build ──────────────────────────────────────────────────────────────────────

def hermetic_env():
    """os.environ minus any sourced-SDK toolchain overrides. Returns (env, dropped)."""
    env = dict(os.environ)
    dropped = []
    for key in list(env):
        if key in HERMETIC_DROP or key.startswith('OECORE_'):
            env.pop(key, None)
            dropped.append(key)
    return env, sorted(dropped)


def emit_toolchain(res, build_root):
    template = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'aarch64-toolchain.cmake')
    dest = os.path.join(build_root, 'aarch64-toolchain.cmake')
    try:
        if os.path.isfile(template):
            shutil.copyfile(template, dest)
            res.step('emit_toolchain', 'ok', dest)
        else:
            with open(dest, 'w', encoding='utf-8') as fh:
                fh.write(TOOLCHAIN_FALLBACK)
            res.step('emit_toolchain', 'ok', '%s (inline fallback)' % dest)
        return dest
    except OSError as exc:
        res.fail('emit_toolchain', 'could not write the toolchain file: %s' % exc)
        return None


def cmake_build(res, artifact, build_dir, toolchain, include_dir, static_cxx):
    """cmake configure + build. Appends the transcript to res.data['build_log']."""
    configure = [
        'cmake', '-S', artifact, '-B', build_dir,
        '-DCMAKE_TOOLCHAIN_FILE=%s' % toolchain,
        '-DIMSDK_MODULES_INCLUDE_DIR=%s' % include_dir,
        # Only so the artifact's install() destination resolves at configure time; the
        # install target is never run.
        '-DCMAKE_INSTALL_LIBDIR=lib',
        '-DCMAKE_BUILD_TYPE=Release',
    ]
    if static_cxx:
        configure.append('-DCMAKE_SHARED_LINKER_FLAGS=-static-libstdc++ -static-libgcc')

    log = []
    env, dropped = hermetic_env()
    if dropped:
        log.append('# neutralised sourced-SDK toolchain vars: %s\n' % ', '.join(dropped))

    def record(cmd, rc, out):
        log.append('$ %s\n[rc=%d]\n%s\n' % (' '.join(cmd), rc, out))

    def flush():
        res.data['build_log'] = '\n'.join(log)

    rc, out = run(configure, timeout=300, env=env)
    record(configure, rc, out)
    if rc != 0:
        flush()
        res.fail('cmake_configure',
                 'cmake configure failed (rc=%d). Last lines:\n%s'
                 % (rc, '\n'.join(out.strip().splitlines()[-15:])))
        return False
    detail = 'toolchain applied, headers at %s' % include_dir
    if dropped:
        detail += '; neutralised %d sourced-SDK var(s)' % len(dropped)
        res.data['hermetic_dropped'] = dropped
    res.step('cmake_configure', 'ok', detail)

    jobs = str(os.cpu_count() or 2)
    build = ['cmake', '--build', build_dir, '--parallel', jobs]
    rc, out = run(build, timeout=1800, env=env)
    record(build, rc, out)
    flush()
    if rc != 0:
        res.fail('cross_compile',
                 'build failed (rc=%d). Last lines:\n%s'
                 % (rc, '\n'.join(out.strip().splitlines()[-20:])))
        return False
    res.step('cross_compile', 'ok', 'built with %s jobs' % jobs)
    return True


def find_library(build_dir, expected):
    """Prefer the exact expected name; fall back to any lib*.so so gate_filename can
    report the mismatch rather than a confusing 'nothing was built'."""
    exact = glob.glob(os.path.join(build_dir, '**', expected), recursive=True)
    if exact:
        return exact[0]
    any_so = glob.glob(os.path.join(build_dir, '**', 'lib*.so'), recursive=True)
    return any_so[0] if any_so else None


# ── Public entry point ─────────────────────────────────────────────────────────

def setup_and_build_m(artifact_path, output_dir, imsdk_include=None,
                      max_glibc=DEFAULT_MAX_GLIBC, max_glibcxx=DEFAULT_MAX_GLIBCXX,
                      static_cxx=False, skip_abi_gate=False, preflight_only=False):
    """
    Preflight, cross-compile and gate an imsdk-postproc-<module>/ artifact.

    Idempotent: the build dir is reused, but a stale library and a stale CMakeCache
    are removed first so a failed rebuild can never be gated as though it were fresh.
    Never touches a device. See the module docstring for the returned dict.
    """
    artifact = expand(artifact_path)
    res = _Build()

    if not os.path.isdir(artifact):
        res.fail('preflight', 'no such artifact directory: %s' % artifact)
        return res.data

    report, checks = run_preflight(artifact, imsdk_include)
    res.data['module_name'] = report['module_name']
    res.data['lib_filename'] = report['lib_filename']
    res.data['include_dir'] = report['include_dir']
    res.data['include_dir_source'] = report['include_dir_source']

    if not report['passed']:
        bad = [c for c in checks if not c.ok and not c.warn]
        lines = ['%s: %s' % (c.name, c.detail) for c in bad]
        remedies = sorted({c.remedy for c in bad if c.remedy})
        res.fail('preflight', 'preflight failed -- %s.%s'
                 % ('; '.join(lines),
                    ('\nRemedy: ' + ' | '.join(remedies)) if remedies else ''))
        res.data['next_step'] = 'fix the preflight failures, then re-run'
        return res.data

    for c in checks:
        if c.warn:
            res.warn('%s: %s' % (c.name, c.detail))

    res.step('preflight', 'ok', 'toolchain, headers and artifact shape verified')
    cross = shutil.which(CROSS_CXX)
    rc, ver = run([CROSS_CXX, '-dumpversion'])
    res.data['toolchain'] = '%s (gcc %s)' % (cross, (ver or '?').strip())

    if preflight_only:
        res.data['next_step'] = 'preflight only -- nothing was built'
        return res.data

    os.makedirs(output_dir, exist_ok=True)
    build_dir = os.path.join(output_dir, 'build')
    toolchain = emit_toolchain(res, output_dir)
    if not toolchain:
        return res.data

    # A stale library from a previous run survives a failed rebuild and would be gated
    # as though it were fresh. Remove any before building.
    for stale in glob.glob(os.path.join(build_dir, '**', 'lib*.so'), recursive=True):
        try:
            os.remove(stale)
        except OSError:
            pass

    # CMake caches CMAKE_<LANG>_FLAGS from the environment on the FIRST configure only, so
    # a build dir created in a shell with an SDK sourced keeps those flags forever after.
    # Dropping the cache costs a fraction of a second for a single-source module and makes
    # the flags reproducible run to run.
    stale_cache = os.path.join(build_dir, 'CMakeCache.txt')
    if os.path.isfile(stale_cache):
        try:
            os.remove(stale_cache)
        except OSError:
            pass

    if not cmake_build(res, artifact, build_dir, toolchain, report['include_dir'],
                       static_cxx):
        res.data['next_step'] = 'read build.log, fix the source, re-run'
        return res.data

    so_path = find_library(build_dir, report['lib_filename'])
    if not so_path:
        res.fail('gate_arch', 'the build produced no shared library')
        return res.data

    ok = gate_arch(res, so_path)
    if ok:
        ok = gate_filename(res, so_path, report['lib_filename'])
    if ok:
        ok = gate_symbol(res, so_path)
    if ok:
        ok = gate_abi(res, so_path, max_glibc, max_glibcxx, skip_abi_gate)
    if ok:
        gate_needed(res, so_path)

    if not ok:
        for name in ('gate_filename', 'gate_symbol', 'gate_abi', 'gate_needed'):
            if not any(s['step'] == name for s in res.data['steps']):
                res.step(name, 'skip', 'not reached')
        res.data['next_step'] = 'address the failed gate, then re-run'
        return res.data

    # Publish the gated library next to the result, so consumers need not know the
    # build-tree layout.
    published = os.path.join(output_dir, report['lib_filename'])
    try:
        shutil.copyfile(so_path, published)
    except OSError:
        published = so_path

    res.data['so_path'] = published
    res.data['so_size'] = human(os.path.getsize(published))
    res.data['success'] = True
    res.data['next_step'] = (
        'NOT installed on any device yet -- deploy_mode_m.py installs the library, '
        'pushes the model and labels, and verifies the module appears in '
        '`gst-inspect-1.0 qtimlpostprocess`\'s module= enum.')
    return res.data


# ── Standalone CLI (device-free build) ─────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Mode M host step: cross-compile an IMSDK post-process module for '
                    'aarch64 and gate it. No device, no SDK, no configs/.env needed. '
                    'deploy_mode_m.py calls this and then installs the result.')
    ap.add_argument('artifact', help='path to imsdk-postproc-<module>/')
    ap.add_argument('--imsdk-include', default=None,
                    help='explicit header dir; wins over all auto-detection')
    ap.add_argument('--output-dir', default=None,
                    help='where the .so and build.log land (default ./qimsdk-build/<module>)')
    ap.add_argument('--max-glibc', default=DEFAULT_MAX_GLIBC,
                    help='device GLIBC ceiling (default %s)' % DEFAULT_MAX_GLIBC)
    ap.add_argument('--max-glibcxx', default=DEFAULT_MAX_GLIBCXX,
                    help='device GLIBCXX ceiling (default %s)' % DEFAULT_MAX_GLIBCXX)
    ap.add_argument('--static-cxx', action='store_true',
                    help='link -static-libstdc++ -static-libgcc (the ABI-gate remedy)')
    ap.add_argument('--skip-abi-gate', action='store_true',
                    help='last resort; the result will record that it was skipped')
    ap.add_argument('--preflight-only', action='store_true', help='run preflight and stop')
    ap.add_argument('--json', default=None, help='also write the build detail here')
    args = ap.parse_args(argv)

    artifact = expand(args.artifact)
    if not os.path.isdir(artifact):
        sys.stderr.write('error: no such artifact directory: %s\n' % artifact)
        return 2

    module_name = derive_module_name(artifact)[0] or 'unknown'
    output_dir = expand(args.output_dir) if args.output_dir else \
        os.path.join(os.getcwd(), 'qimsdk-build', module_name)
    os.makedirs(output_dir, exist_ok=True)

    data = setup_and_build_m(
        artifact, output_dir,
        imsdk_include=args.imsdk_include,
        max_glibc=args.max_glibc, max_glibcxx=args.max_glibcxx,
        static_cxx=args.static_cxx, skip_abi_gate=args.skip_abi_gate,
        preflight_only=args.preflight_only,
    )

    log_path = os.path.join(output_dir, 'build.log')
    if data['build_log']:
        try:
            with open(log_path, 'w', encoding='utf-8') as fh:
                fh.write(data['build_log'])
        except OSError:
            log_path = None
    else:
        log_path = None

    print('-' * 72)
    print('Mode M module build: %s' % (data['module_name'] or os.path.basename(artifact)))
    print('-' * 72)
    for s in data['steps']:
        mark = {'ok': 'ok  ', 'fail': 'FAIL', 'warn': 'WARN', 'skip': '--  '}[s['status']]
        print('  [%s] %-18s %s' % (mark, s['step'], s['detail'] or ''))
    print('-' * 72)
    if data['success']:
        print('PASS  %s (%s)' % (data['so_path'], data['so_size']))
        print('      requires GLIBC_%s / GLIBCXX_%s'
              % (data['required_glibc'] or '-', data['required_glibcxx'] or '-'))
    elif args.preflight_only and not data['failure_reason']:
        print('PASS  preflight only -- ready to build module=%s' % data['module_name'])
    else:
        print('FAIL  %s' % (data['failure_reason'] or 'unknown'))
    for w in data['warnings']:
        print('WARN  %s' % w)
    if data['gates_skipped']:
        print('NOTE  gates skipped: %s' % ', '.join(data['gates_skipped']))
    if log_path:
        print('build log: %s' % log_path)
    if data['next_step']:
        print('next: %s' % data['next_step'])

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)
        print('detail: %s' % os.path.abspath(args.json))

    if data['success'] or (args.preflight_only and not data['failure_reason']):
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
