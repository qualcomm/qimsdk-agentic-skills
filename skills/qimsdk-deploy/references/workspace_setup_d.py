#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
workspace_setup_d.py — Idempotent Mode D workspace setup and standalone app build.

Mode D cross-builds a qimsdk-cpp-app-builder C++ app (main.cc using the
qti::Pipeline / <qti/imsdk.h> C++ API) against the Yocto standard SDK on a
Linux or WSL x86_64 workstation, then the deploy script ships the ARM64 binary
to the QLI device.

Contrast with Mode C (gstreamer-app-builder C sample apps):
  - Mode C clones gst-plugins-imsdk and builds the app INSIDE that source tree.
  - Mode D builds each app OUT OF TREE as a standalone SDK consumer, in its own
    dir, using a generated wrapper CMakeLists.txt that resolves libqtiimsdk.so
    from the SDK target sysroot via find_library(). Nothing shared is mutated.

Recipe (Yocto standard SDK build guide):
  1. Install host tools (build-essential cmake unzip) — best effort.
  2. Unzip the SDK package and run its .sh installer into {BUILD_DIR}/qcom-sdk.
  3. Source {BUILD_DIR}/qcom-sdk/environment-setup-*-qcom-linux.
  4. cmake -S . -B build && cmake --build build   (in the per-app dir)
libqtiimsdk.so ships inside the SDK target sysroot — there is NO SDK-from-source
build step (contrast Mode C's git clone + full cmake of the plugin tree).

State transitions (see workspace_state.detect_mode_d_state):
  0 → 1: Install Yocto SDK (fetch zip → unzip → run .sh installer)
  1 → 2: Push main.cc + generated wrapper CMakeLists.txt to per-app dir
  2 → 3: cmake configure (. env && cmake -S . -B build)
  3 → 4: cmake --build build  (produces build/{target})
  4:     Re-push source (may have changed) + cmake --build incrementally (fast)

Returns:
    {
      'success':        bool,
      'app_dir':        str or None,   # per-app dir on workstation
      'binary_path':    str or None,   # {app_dir}/build/{target} on workstation
      'build_log':      str,
      'failure_reason': str or None,
    }

No imports from deploy_mode_d — SSH helper duplicated to avoid circular import.

The SDK source is configurable via LINUX_WORKSTATION_SDK_URL in configs/.env.
It may be a file:// path (e.g. a network share) or an http(s):// URL. If unset,
the default Yocto SDK zip is downloaded from the Artifactory URL below.
"""

import os
import sys
import pathlib

# Ensure workspace_state (same directory) is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from workspace_state import detect_mode_d_state, _find_sdk_env_script, _run as _ws_run


# ── Constants ──────────────────────────────────────────────────────────────────

_SDK_INSTALL_SUBDIR = 'qcom-sdk'          # {build_dir}/qcom-sdk  (installer -d target)
_CPP_APPS_SUBDIR    = 'qimsdk-cpp-apps'   # {build_dir}/qimsdk-cpp-apps/{target}/

# Default SDK source used when no explicit SDK path/URL or build-dir zip exists.
# The Artifactory directory contains the Yocto SDK package; download the zip and
# extract its installer on the workstation.
_SDK_DEFAULT_URL = (
    'https://artifacts.codelinaro.org/artifactory/qli-ci/flashable-binaries/'
    'meta-qcom/qcom-armv8a/qcom-yocto-sdk-deploy-0807.zip'
)

# Wrapper CMakeLists.txt for a standalone SDK-consumer build.
# The artifact's own CMakeLists.txt links a bare `qtiimsdk` target that only exists
# inside the full IMSDK source tree; out of tree we must resolve the installed
# library with find_library() against the SDK target sysroot.
_WRAPPER_CMAKE = """\
cmake_minimum_required(VERSION 3.20)

project({target} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_library(QTIIMSDK_LIBRARY
  NAMES qtiimsdk
  PATHS "$ENV{{SDKTARGETSYSROOT}}/usr/lib"
  REQUIRED
)

add_executable({target} main.cc)

target_link_libraries({target}
  PRIVATE
    "${{QTIIMSDK_LIBRARY}}"
)
"""


def setup_and_build_d(ssh_dc, artifact_path, target_name, build_dir, sdk_url=None, sdk_path=None):
    """
    Idempotent Mode D workspace setup and standalone host build.

    Args:
        ssh_dc      : connected SSH object for the workstation (paramiko client or _SSH instance)
        artifact_path: pathlib.Path to artifact folder (main.cc, CMakeLists.txt, README.md)
        target_name : str — CMake TEST_TARGET from the artifact's CMakeLists.txt
        build_dir   : str — LINUX_WORKSTATION_BUILD_DIR (absolute path on workstation)
        sdk_url     : str or None — LINUX_WORKSTATION_SDK_URL (file:// or http(s)://)
        sdk_path    : str or None — LINUX_WORKSTATION_SDK_PATH, an absolute path on the
                      workstation to an SDK installer already present — a .zip (unzipped
                      automatically to find the installer inside) or a .sh (already
                      extracted, run directly). Takes precedence over the build_dir zip
                      lookup and sdk_url fetch. Only consulted at state 0 (SDK not yet
                      installed); ignored otherwise. Same key/semantics as Mode C's
                      LINUX_WORKSTATION_SDK_PATH — shared across both modes.

    Returns dict with keys: success, app_dir, binary_path, build_log, failure_reason
    """
    build_log_parts = []
    bd = build_dir.rstrip('/')
    sdk_dir = f'{bd}/{_SDK_INSTALL_SUBDIR}'
    app_dir = f'{bd}/{_CPP_APPS_SUBDIR}/{target_name}'

    def _fail(reason):
        return {
            'success': False,
            'app_dir': app_dir,
            'binary_path': None,
            'build_log': '\n\n'.join(build_log_parts),
            'failure_reason': reason,
        }

    def _run(cmd, timeout=20):
        # Wrap in bash -c "..." so redirects and pipes work regardless of the
        # remote login shell (e.g. tcsh on some shared Linux workstations).
        # Skip wrapping if cmd already starts with 'bash -c' to avoid double-nesting.
        if cmd.startswith('bash -c'):
            wrapped = cmd
        else:
            wrapped = f'bash -c "{cmd}"'
        return _ws_run(ssh_dc, wrapped, timeout=timeout)

    # ── Detect current state ──────────────────────────────────────────────────
    state_info = detect_mode_d_state(ssh_dc, bd, target_name)
    state = state_info['state']
    env_script = state_info.get('env_script')
    print(f'  [setup_d] Workspace state {state}: {state_info["detail"]}', flush=True)

    # ── State 0: Yocto SDK not installed ──────────────────────────────────────
    if state == 0:
        print('  [setup_d] State 0 — installing Yocto SDK ...', flush=True)
        ok, reason, log = _install_sdk(ssh_dc, bd, sdk_dir, sdk_url, sdk_path=sdk_path)
        build_log_parts.append(log)
        if not ok:
            return _fail(f'Yocto SDK installation failed: {reason}')

        state_info = detect_mode_d_state(ssh_dc, bd, target_name)
        state = state_info['state']
        env_script = state_info.get('env_script')
        print(f'  [setup_d] State after SDK install: {state} — {state_info["detail"]}', flush=True)
        if state == 0:
            return _fail(
                'SDK install did not produce an environment-setup script. '
                'Check workstation disk space and that the SDK zip contains a valid installer.'
            )

    if not env_script:
        env_script = _find_sdk_env_script(ssh_dc, sdk_dir)
    if not env_script:
        return _fail(f'SDK env script not found under {sdk_dir} after install')

    # ── States 1-4: push source ──────────────────────────────────────────────
    # Always re-push source (idempotent, cheap) so a changed main.cc triggers
    # a rebuild on the next cmake --build. Clean ONLY the source files (not
    # build/) so cmake can do an incremental build at state 3/4. A full
    # reconfigure is only triggered when build/CMakeCache.txt is absent (states
    # 1/2), which also happens after the stale-source wipe at state 4 where we
    # want a clean slate to avoid orphaned object files from a prior source.
    print(f'  [setup_d] Pushing source to workstation {app_dir}/ ...', flush=True)
    # Remove only the source files (not build/); recreate the app_dir shell.
    # This avoids orphaned .cc/.h from prior runs while preserving the cmake
    # cache and compiled objects for an incremental build.
    _run(f"find '{app_dir}' -maxdepth 1 -type f -delete && mkdir -p '{app_dir}'", timeout=15)
    ok, reason = _push_source_files(ssh_dc, artifact_path, app_dir, target_name)
    if not ok:
        return _fail(reason)

    state_info = detect_mode_d_state(ssh_dc, bd, target_name)
    state = state_info['state']
    print(f'  [setup_d] State after source push: {state}', flush=True)

    # ── Configure (states 1 and 2: cmake not yet run) ─────────────────────────
    # Wipe build/ before configuring — a stale CMakeCache.txt from a different
    # prior run would pin wrong source paths. (Fast: single-app out-of-tree build.)
    if state in (1, 2):
        print(f'  [setup_d] Configuring (cmake -S . -B build) ...', flush=True)
        _run(f"rm -rf '{app_dir}/build'", timeout=30)
        cmake_cmd = (
            f"bash -c '. {env_script} && cd {app_dir} && "
            f"cmake -S . -B build 2>&1'"
        )
        out = _run(cmake_cmd, timeout=300)
        build_log_parts.append(f'[cmake configure]\n{out}')
        if 'YES' not in _run(f"test -f '{app_dir}/build/CMakeCache.txt' && echo YES || echo NO"):
            return _fail('cmake configure did not produce build/CMakeCache.txt. See build_log.')
        print('  [setup_d] cmake configure OK', flush=True)

        state_info = detect_mode_d_state(ssh_dc, bd, target_name)
        state = state_info['state']
        print(f'  [setup_d] State after cmake: {state}', flush=True)

    # ── Build (states 3 and 4: cmake already configured) ─────────────────────
    # State 3: binary not yet built — cmake --build produces it.
    # State 4: binary exists — re-push source (done above) + incremental build.
    # Unlike state 1/2 we do NOT wipe build/ here — cmake --build is incremental
    # and only recompiles files whose source changed. Fast on subsequent runs.
    print(f'  [setup_d] Cross-building {target_name} (cmake --build) ...', flush=True)
    build_cmd = (
        f"bash -c '. {env_script} && cd {app_dir} && "
        f"cmake --build build -- -j$(nproc) 2>&1'"
    )
    out = _run(build_cmd, timeout=600)
    build_log_parts.append(f'[host-build]\n{out}')

    # Primary check: cmake must print "Built target {target_name}" — same as Mode C.
    # Checking only binary existence is insufficient: a cmake error (e.g. corrupt
    # CMakeCache) returns non-zero but leaves a stale binary from a prior run,
    # causing a false success.
    if f'Built target {target_name}' not in out:
        return _fail(
            f'Cross-build did not produce "Built target {target_name}". '
            f'See build_log for the cmake --build output.'
        )

    binary_path = f'{app_dir}/build/{target_name}'
    if 'YES' not in _run(f"test -f '{binary_path}' && echo YES || echo NO"):
        return _fail(
            f'Binary not found at {binary_path} after build (cmake reported success but file missing).'
        )

    # Sanity: confirm the binary is an aarch64 ELF (catches a mis-sourced env / host build).
    file_out = _run(f"file '{binary_path}'")
    build_log_parts.append(f'[file]\n{file_out}')
    if 'aarch64' not in file_out.lower():
        return _fail(
            f'Built binary is not an aarch64 ELF — the SDK cross-environment was not '
            f'sourced correctly. file reported: {file_out.strip()[:200]}'
        )

    print(f'  [setup_d] Cross-build OK — {binary_path}', flush=True)
    return {
        'success': True,
        'app_dir': app_dir,
        'binary_path': binary_path,
        'build_log': '\n\n'.join(build_log_parts),
        'failure_reason': None,
    }


# ── SDK installation ────────────────────────────────────────────────────────────

def _install_sdk(ssh_dc, build_dir, sdk_dir, sdk_url, sdk_path=None):
    """
    Install the Yocto standard SDK on the workstation from a configurable source.

    Source precedence:
      1. sdk_path, if given — an absolute path ON THE WORKSTATION to an SDK
         installer already present: a .sh (already extracted, run directly) or
         a .zip (unzipped automatically to find the installer inside). Skips
         the build_dir zip lookup and sdk_url fetch entirely.
      2. A zip already present in {build_dir} (qcom-yocto-sdk*.zip or sdk.zip)
      3. sdk_url (file:// path or http(s):// URL) fetched into {build_dir}
      4. The default Yocto SDK zip from Artifactory fetched into {build_dir}

    Steps: ensure unzip/cmake → unzip package → run the .sh installer with
    `-d {sdk_dir} -y` → verify environment-setup-*-qcom-linux exists.

    Returns (success: bool, reason: str, log: str).
    """
    def _run(cmd, timeout=30):
        if cmd.startswith('bash -c'):
            wrapped = cmd
        else:
            wrapped = f'bash -c "{cmd}"'
        return _ws_run(ssh_dc, wrapped, timeout=timeout)

    log_parts = []
    _run(f'mkdir -p {build_dir}', timeout=10)

    # ── sdk_path given (or just discovered above) — use it directly ──────────────
    if sdk_path:
        if 'YES' not in _run(f"test -f '{sdk_path}' && echo YES || echo NO", timeout=10):
            return False, (
                f'LINUX_WORKSTATION_SDK_PATH is set to {sdk_path} but that file '
                f'was not found on the workstation.'
            ), ''

        if not _run('which unzip 2>/dev/null'):
            print('  [setup_d] unzip not found — installing (best effort) ...', flush=True)
            apt_out = _run('sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unzip', timeout=180)
            log_parts.append(f'[apt install unzip]\n{apt_out[:500]}')

        if sdk_path.lower().endswith('.sh'):
            print(f'  [setup_d] Using provided .sh installer: {sdk_path}', flush=True)
            log_parts.append(f'[sdk_path]\nUsing provided .sh installer: {sdk_path}')
            sh_path = sdk_path
        else:
            unzip_tmp = f'{build_dir}/_sdk_unzip_tmp'
            _run(f'rm -rf {unzip_tmp} && mkdir -p {unzip_tmp}', timeout=15)
            print(f'  [setup_d] Unzipping {sdk_path} ...', flush=True)
            unzip_out = _run(f"unzip -o '{sdk_path}' -d {unzip_tmp}", timeout=600)
            log_parts.append(f'[unzip]\n{unzip_out[:2000]}')

            sh_path = _ws_run(ssh_dc, f"bash -c 'find {unzip_tmp} -name *.sh -type f | head -1'").strip()
            if not sh_path:
                return False, (
                    f'No .sh installer found after unzipping {sdk_path}. '
                    f'Check LINUX_WORKSTATION_SDK_PATH points at a valid SDK zip.'
                ), '\n\n'.join(log_parts)
            sh_path = sh_path.splitlines()[0].strip()
            print(f'  [setup_d] Found installer: {sh_path}', flush=True)

        print(f'  [setup_d] Running SDK installer into {sdk_dir} (this may take a few minutes) ...', flush=True)
        _run(f"chmod +x '{sh_path}'", timeout=15)
        install_out = _run(f"'{sh_path}' -d {sdk_dir} -y", timeout=1800)
        log_parts.append(f'[sdk install]\n{install_out[-3000:]}')
        if not sdk_path.lower().endswith('.sh'):
            _run(f'rm -rf {unzip_tmp}', timeout=15)

        env_script = _find_sdk_env_script(ssh_dc, sdk_dir)
        if not env_script:
            return False, (
                f'Installer ran but no environment-setup-*-qcom-linux found under {sdk_dir}. '
                f'See build_log for installer output.'
            ), '\n\n'.join(log_parts)

        print(f'  [setup_d] SDK installed — env script: {env_script}', flush=True)
        return True, 'ok', '\n\n'.join(log_parts)

    # ── No sdk_path — existing zip-lookup + sdk_url fetch flow ─────────────────
    # ── Ensure unzip is available (best effort) ───────────────────────────────
    # cmake is NOT installed here — the Yocto SDK ships cmake inside its own
    # x86_64 host sysroot (sysroots/x86_64-qcomsdk-linux/usr/bin/cmake) and
    # puts it on PATH when the environment-setup script is sourced. Attempting
    # a system cmake install via apt is unnecessary and silently fails on most
    # workstations that lack passwordless sudo.
    if not _run('which unzip 2>/dev/null'):
        print('  [setup_d] unzip not found — installing (best effort) ...', flush=True)
        apt_out = _run('sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unzip', timeout=180)
        log_parts.append(f'[apt install unzip]\n{apt_out[:500]}')

    # ── Locate the SDK zip ─────────────────────────────────────────────────────
    existing = _run(
        f"ls {build_dir}/qcom-yocto-sdk*.zip {build_dir}/sdk.zip 2>/dev/null | head -1"
    ).strip()
    if existing:
        local_zip = existing.splitlines()[0].strip()
        print(f'  [setup_d] SDK zip found: {local_zip}', flush=True)
        log_parts.append(f'[zip lookup]\nFound at {local_zip}')
    else:
        if not sdk_url:
            sdk_url = _SDK_DEFAULT_URL
            print(f'  [setup_d] No local SDK zip or explicit URL — downloading the default Yocto SDK from Artifactory ...', flush=True)
            print(f'  [setup_d] URL: {sdk_url}', flush=True)

        local_zip = f'{build_dir}/qcom-yocto-sdk-deploy.zip'
        ok, reason, fetch_log = _fetch_sdk_zip(ssh_dc, sdk_url, local_zip)
        log_parts.append(fetch_log)
        if not ok:
            return False, reason, '\n\n'.join(log_parts)

    # ── Unzip to a temp dir to find the .sh installer ─────────────────────────
    unzip_tmp = f'{build_dir}/_sdk_unzip_tmp'
    _run(f'rm -rf {unzip_tmp} && mkdir -p {unzip_tmp}', timeout=15)
    print(f'  [setup_d] Unzipping {local_zip} ...', flush=True)
    unzip_out = _run(f'unzip -o {local_zip} -d {unzip_tmp}', timeout=600)
    log_parts.append(f'[unzip]\n{unzip_out[:2000]}')

    # Single-quote the bash -c string so *.sh is NOT expanded by the calling shell;
    # find receives it verbatim as the -name pattern, which is what we want.
    sh_path = _ws_run(ssh_dc, f"bash -c 'find {unzip_tmp} -name *.sh -type f | head -1'").strip()
    if not sh_path:
        return False, f'No .sh installer found after unzip in {unzip_tmp}. Zip may be corrupt.', '\n\n'.join(log_parts)
    sh_path = sh_path.splitlines()[0].strip()
    print(f'  [setup_d] Found installer: {sh_path}', flush=True)

    # ── Run the installer non-interactively into sdk_dir ──────────────────────
    print(f'  [setup_d] Running SDK installer into {sdk_dir} (this may take a few minutes) ...', flush=True)
    _run(f'chmod +x {sh_path}', timeout=15)
    # -d <dir>: install location, -y: non-interactive accept
    install_out = _run(f'{sh_path} -d {sdk_dir} -y', timeout=1800)
    log_parts.append(f'[sdk install]\n{install_out[-3000:]}')
    _run(f'rm -rf {unzip_tmp}', timeout=15)

    env_script = _find_sdk_env_script(ssh_dc, sdk_dir)
    if not env_script:
        return False, (
            f'Installer ran but no environment-setup-*-qcom-linux found under {sdk_dir}. '
            f'See build_log for installer output.'
        ), '\n\n'.join(log_parts)

    print(f'  [setup_d] SDK installed — env script: {env_script}', flush=True)
    return True, 'ok', '\n\n'.join(log_parts)


def _fetch_sdk_zip(ssh_dc, sdk_url, local_zip):
    """
    Fetch the SDK zip onto the workstation from sdk_url into local_zip.

    Supports:
      - file://<path>  — copied with cp (path is a mount/share visible on the workstation)
      - http(s)://...  — downloaded with wget

    Returns (success: bool, reason: str, log: str).
    """
    def _run(cmd, timeout=30):
        if cmd.startswith('bash -c'):
            wrapped = cmd
        else:
            wrapped = f'bash -c "{cmd}"'
        return _ws_run(ssh_dc, wrapped, timeout=timeout)

    if sdk_url.startswith('file://'):
        src = sdk_url[len('file://'):]
        print(f'  [setup_d] Copying SDK zip from {src} (this may take a while, ~4 GB) ...', flush=True)
        out = _run(f"cp '{src}' '{local_zip}'", timeout=7200)
        if 'YES' not in _run(f'test -s {local_zip} && echo YES || echo NO'):
            _run(f'rm -f {local_zip}', timeout=10)
            return False, (
                f'Failed to copy SDK zip from {src}. Ensure the path is mounted/visible on the '
                f'workstation. cp output: {out.strip()[:300]}'
            ), f'[fetch file://]\n{out[:500]}'
        return True, 'ok', f'[fetch file://]\nCopied {src} -> {local_zip}'

    if sdk_url.startswith('http://') or sdk_url.startswith('https://'):
        print(f'  [setup_d] Downloading SDK zip from {sdk_url} (this may take several minutes) ...', flush=True)
        out = _run(f"wget -q -O '{local_zip}' '{sdk_url}'", timeout=7200)
        if 'YES' not in _run(f'test -s {local_zip} && echo YES || echo NO'):
            _run(f'rm -f {local_zip}', timeout=10)
            return False, f'SDK download failed from {sdk_url}.', f'[fetch http]\n{out[:500]}'
        return True, 'ok', f'[fetch http]\nDownloaded {sdk_url} -> {local_zip}'

    return False, (
        f'Unsupported LINUX_WORKSTATION_SDK_URL scheme: {sdk_url!r}. '
        f'Use file://<path> or http(s)://<url>.'
    ), ''


# ── Push source files ─────────────────────────────────────────────────────────

def _push_source_files(ssh_dc, artifact_path, remote_app_dir, target_name):
    """
    Push main.cc (from the artifact) and a generated wrapper CMakeLists.txt to
    remote_app_dir on the workstation. Normalizes CRLF, verifies after push.

    The artifact's own CMakeLists.txt is intentionally NOT used for the build —
    it links a bare `qtiimsdk` target that only resolves inside the full IMSDK
    source tree. We generate a standalone wrapper instead. The
    original CMakeLists.txt is still pushed as CMakeLists.artifact.txt for
    reference/debugging.

    Returns (success: bool, reason: str).
    """
    main_cc = artifact_path / 'main.cc'
    if not main_cc.exists():
        return False, 'Artifact missing required file: main.cc'

    # Push main.cc (CRLF-normalized)
    unix_bytes = main_cc.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    remote_main = f'{remote_app_dir}/main.cc'
    try:
        ssh_dc.push_bytes(unix_bytes, remote_main)
    except Exception as e:
        return False, f'Failed to push main.cc to workstation: {e}'
    if 'OK' not in _ws_run(ssh_dc, f"test -f '{remote_main}' && echo OK || echo MISSING"):
        return False, f'main.cc not found on workstation after push: {remote_main}'

    # Generate + push the standalone wrapper CMakeLists.txt
    wrapper = _WRAPPER_CMAKE.format(target=target_name).encode('utf-8')
    remote_cmake = f'{remote_app_dir}/CMakeLists.txt'
    try:
        ssh_dc.push_bytes(wrapper, remote_cmake)
    except Exception as e:
        return False, f'Failed to push wrapper CMakeLists.txt to workstation: {e}'
    if 'OK' not in _ws_run(ssh_dc, f"test -f '{remote_cmake}' && echo OK || echo MISSING"):
        return False, f'CMakeLists.txt not found on workstation after push: {remote_cmake}'

    # Push the artifact's original CMakeLists.txt for reference (non-fatal)
    art_cmake = artifact_path / 'CMakeLists.txt'
    if art_cmake.exists():
        try:
            ref_bytes = art_cmake.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            ssh_dc.push_bytes(ref_bytes, f'{remote_app_dir}/CMakeLists.artifact.txt')
        except Exception:
            pass  # reference only — never fail the build for this

    return True, 'ok'
