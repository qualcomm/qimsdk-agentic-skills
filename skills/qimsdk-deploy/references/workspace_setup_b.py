#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
workspace_setup_b.py — Idempotent Mode B workspace setup and app build.

Handles all workspace states (0-4) for Ubuntu on-device builds.
Detects current state and runs only the steps needed. Safe to re-run.

State transitions:
  0 → 1: apt setup (add-repo, build-dep, install base-dev + sample-apps, source)
  1 → 2: cmake configure with full SDK flags
  2 → 3: push source to gst-sample-apps/{binary}/, wipe build, cmake reconfigure
  3 → 4: make -C gst-sample-apps/{binary} -j$(nproc)
  4:     push source (may have changed), make (incremental, fast if unchanged)

Returns:
    {
      'success':        bool,
      'source_root':    str or None,
      'binary_path':    str or None,
      'build_log':      str,
      'failure_reason': str or None,
    }

No imports from deploy_mode_b — sudo helper is duplicated here to avoid
circular import (deploy_mode_b imports workspace_setup_b at call time).
"""

import shlex
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from workspace_state import detect_mode_b_state, _run as _ws_run


# ── cmake flags (verbatim from ubuntu-build.mdx) ─────────────────────────────

_CMAKE_FLAGS = (
    '-DCMAKE_INSTALL_PREFIX=/usr '
    '-DCMAKE_BUILD_TYPE=None '
    '-DCMAKE_INSTALL_SYSCONFDIR=/etc '
    '-DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=ON '
    '-DCMAKE_SKIP_INSTALL_ALL_DEPENDENCY=ON '
    '"-GUnix Makefiles" '
    '-DCMAKE_VERBOSE_MAKEFILE=ON '
    '-DCMAKE_INSTALL_LIBDIR=lib/aarch64-linux-gnu '
    '-DENABLE_GST_SAMPLE_APPS=ON '
    '-DGST_VERSION_REQUIRED=1.20.1 '
    '-DSYSROOT_INCDIR=/usr/include '
    '-DSYSROOT_LIBDIR=/usr/lib '
    '-DGST_PLUGINS_QTI_OSS_INSTALL_BINDIR:PATH=/usr/bin '
    '-DGST_PLUGINS_QTI_OSS_INSTALL_LIBDIR=/usr/lib/aarch64-linux-gnu '
    '-DGST_PLUGINS_QTI_OSS_INSTALL_INCDIR=/usr/include '
    '-DGST_PLUGINS_QTI_OSS_INSTALL_CONFIG=/etc/configs '
    '-DGST_PLUGINS_QTI_OSS_LICENSE=BSD '
    '-DGST_PLUGINS_QTI_OSS_VERSION=2.0.0 '
    '-DGST_PLUGINS_QTI_OSS_PACKAGE=gstreamer1.0-plugins-qcom-oss '
    '"-DGST_PLUGINS_QTI_OSS_SUMMARY=Qualcomm open-source GStreamer Plug-ins" '
    '-DGST_PLUGINS_QTI_OSS_ORIGIN=http://www.qualcomm.com '
    '-DGST_IMAGE_MAX_WIDTH=5184 '
    '-DGST_IMAGE_MAX_HEIGHT=3880 '
    '-DGST_VIDEO_MAX_WIDTH=5184 '
    '-DGST_VIDEO_MAX_HEIGHT=3880 '
    '-DGST_VIDEO_MAX_FPS=120/1 '
    '-DCAMERA_METADATA_VERSION=1.0 '
    '-DGST_VIDEO_TYPE_SUPPORT=TRUE '
    '-DEIS_MODES_ENABLE=TRUE '
    '-DVHDR_MODES_ENABLE=TRUE '
    '-DFEATURE_OFFLINE_IFE_SUPPORT=TRUE'
)


# ── Best-effort apt steps ────────────────────────────────────────────────────
# apt State-0 steps whose non-zero exit is logged but does NOT abort the build.
#  - add-apt-repository: exits non-zero if the PPA is already present (harmless).
#  - apt-install-sample-apps: sample-apps/python-examples package names can vary
#    by PPA snapshot; the source-tree build may already supply utils, so a miss
#    here should not block the build (it will surface later as a compile error if
#    the header is genuinely absent).
_BEST_EFFORT_APT = {'add-apt-repository', 'apt-install-sample-apps'}


# ── sudo helper (duplicated from deploy_mode_b to avoid circular import) ─────

def _sudo_run(ssh, cmd, password, timeout=30):
    """Run a sudo command. Password is passed via stdin — never appears in ps.
    Returns (stdout+stderr, '', exit_code) for API compat with _SSH.run()."""
    if not password:
        return ssh.run(f'sudo bash -c {shlex.quote(cmd)}', timeout=timeout)
    ssh.connect()
    stdin, stdout, stderr = ssh._client.exec_command(
        f'sudo -S bash -c {shlex.quote(cmd)}', timeout=timeout
    )
    stdin.write(password + '\n')
    stdin.flush()
    stdin.close()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    rc  = stdout.channel.recv_exit_status()
    return out + err, '', rc


# ── Main entry ────────────────────────────────────────────────────────────────

def setup_and_build_b(ssh, artifact_path, binary_name, source_root_hint, password=None):
    """
    Idempotent Mode B workspace setup and app build.

    Args:
        ssh             : connected _SSH instance (from deploy_mode_b.py)
        artifact_path   : pathlib.Path to artifact folder (main.c, CMakeLists.txt, README.md)
        binary_name     : str — binary name (from CMakeLists.txt GST_EXAMPLE_BIN)
        source_root_hint: str or None — SOURCE_ROOT from .env; auto-discovered if None
        password        : str or None — sudo password (None if NOPASSWD)

    Returns dict with keys: success, source_root, binary_path, build_log, failure_reason
    """
    build_log_parts = []
    pw = password
    # Resolve SSH user once — needed for chown calls that run via sudo
    # (inside sudo bash -c, $(whoami) expands to root, not the SSH user)
    ssh_user = _ws_run(ssh, 'id -un').strip() or 'ubuntu'

    def _fail(reason):
        return {
            'success': False,
            'source_root': None,
            'binary_path': None,
            'build_log': '\n\n'.join(build_log_parts),
            'failure_reason': reason,
        }

    def _sudo(cmd, timeout=30):
        out, _, rc = _sudo_run(ssh, cmd, pw, timeout=timeout)
        return out, rc

    # ── Detect current state ──────────────────────────────────────────────────
    state_info = detect_mode_b_state(ssh, source_root_hint, binary_name)
    state = state_info['state']
    src   = state_info.get('source_root')
    print(f'  [setup_b] Workspace state {state}: {state_info["detail"]}', flush=True)

    # ── State 0: need full apt setup ──────────────────────────────────────────
    if state == 0:
        print('  [setup_b] State 0 — running apt setup (this may take several minutes) ...', flush=True)

        # Resolve device home dir dynamically (not hardcoded to /home/ubuntu)
        device_home = _ws_run(ssh, 'echo $HOME').strip() or '/home/ubuntu'

        # Check if source was already partially downloaded (idempotent)
        existing_src = _ws_run(ssh, f'ls -d {device_home}/gst-plugins-qti-oss-* 2>/dev/null | sort -V | tail -1').strip()

        steps = [
            ('add-apt-repository',
             'DEBIAN_FRONTEND=noninteractive apt-add-repository -s -y ppa:ubuntu-qcom-iot/qcom-ppa'),
            ('apt-build-dep',
             'DEBIAN_FRONTEND=noninteractive apt-get build-dep -y gst-plugins-qti-oss'),
            ('apt-install-base',
             'DEBIAN_FRONTEND=noninteractive apt-get install -y libgstreamer1.0-plugins-qcom-base-dev'),
            # Sample-apps + python-examples: generated Mode B apps #include
            # <gst/sampleapps/gst_sample_apps_utils.h> and link gstappsutils, whose
            # headers/libs ship in these packages (installation.mdx "Sample applications
            # and Python examples"). Best-effort — package names can vary by PPA snapshot
            # and the source-tree build may already supply utils, so a non-zero exit here
            # must not abort the build (see _BEST_EFFORT_APT below).
            ('apt-install-sample-apps',
             'DEBIAN_FRONTEND=noninteractive apt-get install -y '
             'gstreamer1.0-qcom-sample-apps gstreamer1.0-qcom-python-examples'),
        ]
        # Only run apt source if not already downloaded
        if not existing_src:
            steps.append(('apt-source', f'cd {device_home} && apt-get source gst-plugins-qti-oss'))

        for name, cmd in steps:
            print(f'  [setup_b] apt: {name} ...', flush=True)
            out, rc = _sudo(cmd, timeout=600)
            build_log_parts.append(f'[{name} — exit {rc}]\n{out}')
            if rc != 0 and name not in _BEST_EFFORT_APT:
                return _fail(f'apt setup step "{name}" failed (exit {rc}). See build_log.')
            print(f'  [setup_b] {name}: exit {rc}', flush=True)

        # Re-detect with glob
        state_info = detect_mode_b_state(ssh, None, binary_name)
        state = state_info['state']
        src   = state_info.get('source_root')
        print(f'  [setup_b] State after apt setup: {state} — {state_info["detail"]}', flush=True)
        if state == 0:
            return _fail('apt source did not create expected source dir. Check device internet and PPA access.')

    # ── State 1: cmake not run yet ────────────────────────────────────────────
    if state == 1:
        if not src:
            return _fail('SOURCE_ROOT could not be determined')
        build_dir   = f'{src}/build'
        sample_apps = f'{src}/gst-sample-apps'

        if binary_name:
            _clean_stale_app_dirs(ssh, sample_apps, binary_name, pw)
        _clean_broken_app_dirs(ssh, sample_apps, pw)

        print(f'  [setup_b] State 1 — running cmake in {build_dir} (2-3 minutes) ...', flush=True)
        out, rc = _sudo(f"mkdir -p '{build_dir}' && chown {ssh_user}:{ssh_user} '{build_dir}' && echo OK", timeout=30)
        if 'OK' not in out:
            return _fail(f'Could not create build dir {build_dir}: {out.strip()}')

        cmake_cmd = f'cd {shlex.quote(build_dir)} && cmake {_CMAKE_FLAGS} .. 2>&1'
        out, rc = _sudo(cmake_cmd, timeout=360)
        build_log_parts.append(f'[cmake state1 — exit {rc}]\n{out}')
        if rc != 0:
            return _fail(f'cmake failed (exit {rc}). See build_log.')
        print(f'  [setup_b] cmake OK (exit {rc})', flush=True)

        state_info = detect_mode_b_state(ssh, src, binary_name)
        state = state_info['state']
        print(f'  [setup_b] State after cmake: {state}', flush=True)

    # ── State 2: cmake configured, app not yet registered ────────────────────
    if state == 2:
        if not src:
            return _fail('SOURCE_ROOT could not be determined')
        build_dir   = f'{src}/build'
        sample_apps = f'{src}/gst-sample-apps'
        app_dir     = f'{sample_apps}/{binary_name}'

        print(f'  [setup_b] State 2 — pushing source to {app_dir}/ ...', flush=True)
        ok, reason = _push_source_files(ssh, artifact_path, app_dir, pw)
        if not ok:
            return _fail(reason)

        _clean_stale_app_dirs(ssh, sample_apps, binary_name, pw)
        # Also remove any broken app dirs (dirs without CMakeLists.txt) that would
        # cause cmake to fail with "does not contain a CMakeLists.txt" error
        _clean_broken_app_dirs(ssh, sample_apps, pw)

        # Wipe build dir — cmake uses GLOB discovery and caches results
        print(f'  [setup_b] Wiping build dir for cmake reconfigure ...', flush=True)
        out, rc = _sudo(
            f"rm -rf '{build_dir}' && mkdir -p '{build_dir}' && chown {ssh_user}:{ssh_user} '{build_dir}' && echo OK",
            timeout=60
        )
        if 'OK' not in out:
            return _fail(f'Could not recreate build dir {build_dir}: {out.strip()}')

        print(f'  [setup_b] Running cmake reconfigure (2-3 minutes) ...', flush=True)
        cmake_cmd = f'cd {shlex.quote(build_dir)} && cmake {_CMAKE_FLAGS} .. 2>&1'
        out, rc = _sudo(cmake_cmd, timeout=360)
        build_log_parts.append(f'[cmake state2 — exit {rc}]\n{out}')
        if rc != 0:
            return _fail(f'cmake failed (exit {rc}). See build_log.')
        print(f'  [setup_b] cmake OK (exit {rc})', flush=True)

        state_info = detect_mode_b_state(ssh, src, binary_name)
        state = state_info['state']
        if state < 3:
            return _fail(f'cmake reconfigure did not register {binary_name} — verify app source dir name matches binary name')
        print(f'  [setup_b] App registered (state {state})', flush=True)

    # ── States 3 and 4: app registered — push source and make ────────────────
    if state in (3, 4):
        if not src:
            return _fail('SOURCE_ROOT could not be determined')
        sample_apps = f'{src}/gst-sample-apps'
        app_dir     = f'{sample_apps}/{binary_name}'

        print(f'  [setup_b] Pushing source to {app_dir}/ ...', flush=True)
        ok, reason = _push_source_files(ssh, artifact_path, app_dir, pw)
        if not ok:
            return _fail(reason)

        # Clean up any gst-* subdirs that have no CMakeLists.txt before building.
        # Such dirs can be left by earlier test runs or partial cleanups and cause
        # cmake's --check-build-system (triggered by make) to fail.
        _clean_broken_app_dirs(ssh, sample_apps, pw)

    # ── Build ─────────────────────────────────────────────────────────────────
    if not src:
        return _fail('SOURCE_ROOT could not be determined at build step')
    build_dir = f'{src}/build'

    # Every generated C app does #include <gst/sampleapps/gst_sample_apps_utils.h>,
    # which only resolves if the header is installed at
    # /usr/include/gstreamer-1.0/gst/sampleapps/. That install is done by the
    # libgstreamer1.0-qcom-sample-apps-utils-dev package — NOT by
    # gstreamer1.0-qcom-sample-apps-utils (which ships only the .so) or by
    # building gstappsutils from source (whose build-interface include path is
    # flat, at the source dir root, not under gst/sampleapps/). This check runs
    # on every build regardless of workspace state, so it self-heals devices
    # that were already at state 1+ before this check existed. Idempotent —
    # apt-get install on an already-installed package is a fast no-op.
    header_check = _ws_run(
        ssh, 'test -f /usr/include/gstreamer-1.0/gst/sampleapps/gst_sample_apps_utils.h && echo FOUND || echo MISSING'
    )
    if 'FOUND' not in header_check:
        print('  [setup_b] gst_sample_apps_utils.h not installed — installing '
              'libgstreamer1.0-qcom-sample-apps-utils-dev ...', flush=True)
        out, rc = _sudo(
            'DEBIAN_FRONTEND=noninteractive apt-get install -y libgstreamer1.0-qcom-sample-apps-utils-dev',
            timeout=120
        )
        build_log_parts.append(f'[apt-install-sample-apps-utils-dev — exit {rc}]\n{out}')
        if rc != 0:
            return _fail(
                'libgstreamer1.0-qcom-sample-apps-utils-dev install failed '
                f'(exit {rc}). Generated apps cannot compile without gst_sample_apps_utils.h '
                'at /usr/include/gstreamer-1.0/gst/sampleapps/. See build_log.'
            )
        print('  [setup_b] gst_sample_apps_utils.h installed', flush=True)

    print(f'  [setup_b] Building {binary_name} (make -j$(nproc)) ...', flush=True)
    make_cmd = f'cd {shlex.quote(build_dir)} && make -C gst-sample-apps/{shlex.quote(binary_name)} -j$(nproc) 2>&1'
    out, rc = _sudo(make_cmd, timeout=600)  # 10min — ARM builds can be slow
    build_log_parts.append(f'[make — exit {rc}]\n{out}')
    if rc != 0:
        return _fail(f'make failed (exit {rc}). See build_log.')
    print(f'  [setup_b] make OK (exit {rc})', flush=True)

    # Verify binary is present and executable
    binary_path = f'{build_dir}/gst-sample-apps/{binary_name}/{binary_name}'
    exists = _ws_run(ssh, f"test -x '{binary_path}' && echo YES || echo NO")
    if 'YES' not in exists:
        # Fallback: check -f (not executable bit) in case permissions are wrong
        exists_f = _ws_run(ssh, f"test -f '{binary_path}' && echo YES || echo NO")
        if 'YES' not in exists_f:
            return _fail(f'Binary not found after make at {binary_path}')
        # File exists but not executable — chmod it
        _sudo(f"chmod +x '{binary_path}'", timeout=10)
        print(f'  [setup_b] Fixed binary permissions at {binary_path}', flush=True)

    return {
        'success': True,
        'source_root': src,
        'binary_path': binary_path,
        'build_log': '\n\n'.join(build_log_parts),
        'failure_reason': None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _push_source_files(ssh, artifact_path, remote_app_dir, password=None):
    """
    Push main.c, CMakeLists.txt, README.md to remote_app_dir.
    Normalizes CRLF, verifies each file after push.
    Returns (success: bool, reason: str).
    """
    # Use actual SSH user (not sudo-escalated root which is what $(whoami) gives inside sudo bash -c)
    ssh_user = _ws_run(ssh, 'id -un').strip() or 'ubuntu'
    out, _, _ = _sudo_run(
        ssh,
        f"mkdir -p '{remote_app_dir}' && chown {ssh_user}:{ssh_user} '{remote_app_dir}' && "
        f"rm -f '{remote_app_dir}/main.c' '{remote_app_dir}/CMakeLists.txt' '{remote_app_dir}/README.md' && echo OK",
        password, timeout=30
    )
    if 'OK' not in out:
        return False, f'Could not prepare remote app dir {remote_app_dir}: {out.strip()}'

    for fname in ['main.c', 'CMakeLists.txt', 'README.md']:
        local_f = artifact_path / fname
        if not local_f.exists():
            return False, f'Artifact missing required file: {fname}'
        unix_bytes = local_f.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        remote_f = f'{remote_app_dir}/{fname}'
        try:
            ssh.push_bytes(unix_bytes, remote_f)
        except Exception as e:
            return False, f'Failed to push {fname}: {e}'
        v = _ws_run(ssh, f"test -f '{remote_f}' && echo OK || echo MISSING")
        if 'OK' not in v:
            return False, f'File not found on device after push: {remote_f}'

    return True, 'ok'


def _clean_stale_app_dirs(ssh, sample_apps_dir, binary_name, password=None):
    """
    Remove gst-sample-apps subdirs (other than binary_name/) that declare the
    same GST_EXAMPLE_BIN target. Stale dirs cause cmake duplicate-target errors.
    Uses a permissive grep pattern to handle varied CMakeLists.txt formatting.
    """
    # Grep for the binary name in GST_EXAMPLE_BIN regardless of spacing/case
    grep_cmd = f"grep -rl 'GST_EXAMPLE_BIN' '{sample_apps_dir}' 2>/dev/null | xargs grep -l '{binary_name}' 2>/dev/null"
    out = _ws_run(ssh, grep_cmd)
    for path in out.splitlines():
        path = path.strip()
        if not path or not path.endswith('/CMakeLists.txt'):
            continue
        stale_dir = path[:-len('/CMakeLists.txt')]
        # Safety: only remove dirs inside sample_apps_dir
        if not stale_dir.startswith(sample_apps_dir):
            continue
        if stale_dir == f'{sample_apps_dir}/{binary_name}':
            continue
        print(f'  [setup_b] Removing stale app dir: {stale_dir}', flush=True)
        _sudo_run(ssh, f"rm -rf '{stale_dir}'", password, timeout=30)


def _clean_broken_app_dirs(ssh, sample_apps_dir, password=None):
    """
    Remove any gst-sample-apps/gst-* subdirs that have no CMakeLists.txt.
    These broken dirs (e.g. from partial test runs) cause cmake to fail with
    'directory does not contain a CMakeLists.txt' during GLOB-based discovery.
    """
    # Find gst-* subdirs that are missing CMakeLists.txt
    cmd = (
        f"for d in '{sample_apps_dir}'/gst-*/; do "
        f"  if [ -d \"$d\" ] && [ ! -f \"$d/CMakeLists.txt\" ]; then echo \"$d\"; fi; "
        f"done"
    )
    out = _ws_run(ssh, cmd)
    for path in out.splitlines():
        path = path.strip().rstrip('/')
        if not path or not path.startswith(sample_apps_dir):
            continue
        print(f'  [setup_b] Removing broken app dir (no CMakeLists.txt): {path}', flush=True)
        _sudo_run(ssh, f"rm -rf '{path}'", password, timeout=30)
