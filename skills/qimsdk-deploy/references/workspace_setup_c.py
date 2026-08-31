#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
workspace_setup_c.py — Idempotent Mode C workspace setup and app host build.

Handles all workspace states (0-4) for host build on a Linux x86_64 Linux workstation.

State transitions:
  0 → 1: Download and install SDK (via wget on Linux workstation from codelinaro.org)
  1 → 2: git clone https://github.com/qualcomm/gst-plugins-imsdk.git
  2 → 3: cmake configure (. env_setup && cmake -B build -S . -DENABLE_GST_SAMPLE_APPS=1 ...)
  3 → 4: Push source to gst-sample-apps/{binary}/, cmake --build build --target {binary}
  4:     Push source (may have changed), cmake --build --target (incremental, fast)

Returns:
    {
      'success':        bool,
      'imsdk_dir':      str or None,
      'binary_path':    str or None,   # path on linux_workstation
      'build_log':      str,
      'failure_reason': str or None,
    }

No imports from deploy_mode_c — SSH helper duplicated to avoid circular import.
"""

import sys
import pathlib

# Ensure workspace_state (same directory) is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from workspace_state import detect_mode_c_state, _run as _ws_run


# ── cmake flags (verbatim from yocto-build.mdx) ──────────────────────────────

_CMAKE_FLAGS = (
    '-DCMAKE_INSTALL_PREFIX=/usr '
    '-DENABLE_GST_IMSDK_PLUGINS=1 '
    '-DENABLE_GST_PLUGIN_MLTFLITE=1 '
    '-DENABLE_GST_PYTHON_EXAMPLES=1 '
    '-DENABLE_GST_SAMPLE_APPS=1 '
    '-DENABLE_GST_SAMPLE_APPS_CAMERA=1 '
    '-DENABLE_GST_PLUGIN_TOOLS=1 '
    '-DENABLE_GST_CAMERA_PLUGINS=1'
)

# SDK download: detect arch on Linux workstation and download from codelinaro.org
# x86_64 → x86-qli-2.0-standardsdk.zip
# aarch64 → arm-qli-2.0-standardsdk.zip (unlikely for Linux workstation but handled)
_SDK_BASE_URL = 'https://artifacts.codelinaro.org/artifactory/qli-ci/flashable-binaries/meta-qcom/qcom-armv8a'
_SDK_SUBDIR   = 'images/qcom-armv8a/sdk'
_IMSDK_REPO   = 'https://github.com/qualcomm/gst-plugins-imsdk.git'


def setup_and_build_c(ssh_dc, artifact_path, binary_name, build_dir,
                      sdk_path=None, imsdk_path=None):
    """
    Idempotent Mode C workspace setup and app host build.

    Args:
        ssh_dc       : connected SSH object for Linux workstation (paramiko client or _SSH instance)
        artifact_path: pathlib.Path to artifact folder (main.c, CMakeLists.txt, README.md)
        binary_name  : str — binary name (from CMakeLists.txt GST_EXAMPLE_BIN)
        build_dir    : str — LINUX_WORKSTATION_BUILD_DIR (must be absolute path on Linux workstation)
        sdk_path     : optional absolute path on the workstation to an SDK installer —
                       either a .zip (unzipped to find the .sh) or a .sh (run directly,
                       already extracted). Only consulted at state 0. If not set, falls
                       back to the existing arch-zip-lookup + wget-from-codelinaro.org flow.
        imsdk_path   : optional absolute path on the workstation to an existing
                       gst-plugins-imsdk clone. When set, used as imsdk_dir directly and
                       the git clone step is skipped entirely. If not set, falls back to
                       cloning into {build_dir}/gst-plugins-imsdk.

    Returns dict with keys: success, imsdk_dir, binary_path, build_log, failure_reason
    """
    build_log_parts = []
    bd = build_dir.rstrip('/')
    imsdk_override = imsdk_path.rstrip('/') if imsdk_path else None

    def _fail(reason):
        return {
            'success': False,
            'imsdk_dir': None,
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
    state_info = detect_mode_c_state(ssh_dc, bd, binary_name, imsdk_path=imsdk_override)
    state = state_info['state']
    env_script = state_info.get('env_script')
    imsdk_dir  = state_info.get('imsdk_dir') or imsdk_override or f'{bd}/gst-plugins-imsdk'
    print(f'  [setup_c] Workspace state {state}: {state_info["detail"]}', flush=True)

    # ── State 0: SDK not installed ────────────────────────────────────────────
    if state == 0:
        print('  [setup_c] State 0 — installing host SDK ...', flush=True)
        ok, reason, log = _install_sdk(ssh_dc, bd, sdk_path=sdk_path)
        build_log_parts.append(log)
        if not ok:
            return _fail(f'SDK installation failed: {reason}')

        state_info = detect_mode_c_state(ssh_dc, bd, binary_name, imsdk_path=imsdk_override)
        state = state_info['state']
        env_script = state_info.get('env_script')
        print(f'  [setup_c] State after SDK install: {state} — {state_info["detail"]}', flush=True)
        if state == 0:
            return _fail('SDK install did not produce env script. Check Linux workstation internet access and disk space.')

    # ── State 1: SDK installed, repo not present ──────────────────────────────
    if state == 1:
        if imsdk_override:
            # User pointed at an existing clone but CMakeLists.txt wasn't found there —
            # do NOT silently fall back to cloning a fresh copy over it.
            return _fail(
                f'LINUX_WORKSTATION_IMSDK_PATH is set to {imsdk_override} but '
                f'{imsdk_override}/CMakeLists.txt was not found there. Check the path is '
                f'correct and the repo is fully present on the workstation.'
            )

        print(f'  [setup_c] State 1 — cloning gst-plugins-imsdk into {bd}/ ...', flush=True)
        # Guard: if dir exists but is incomplete (partial clone), remove it first
        imsdk_partial = f'{bd}/gst-plugins-imsdk'
        partial_check = _run(f'test -d {imsdk_partial} && test ! -f {imsdk_partial}/CMakeLists.txt && echo PARTIAL || echo OK')
        if 'PARTIAL' in partial_check:
            print(f'  [setup_c] Removing incomplete gst-plugins-imsdk dir before re-clone ...', flush=True)
            _run(f'rm -rf {imsdk_partial}', timeout=30)

        out = _run(f'cd {bd} && git clone {_IMSDK_REPO}', timeout=120)
        build_log_parts.append(f'[git clone]\n{out}')
        exists = _run(f'test -f {bd}/gst-plugins-imsdk/CMakeLists.txt && echo YES || echo NO')
        if 'YES' not in exists:
            return _fail(f'git clone did not create gst-plugins-imsdk/CMakeLists.txt. Output: {out[:500]}')
        imsdk_dir = f'{bd}/gst-plugins-imsdk'
        print(f'  [setup_c] Repo cloned at {imsdk_dir}', flush=True)

        state_info = detect_mode_c_state(ssh_dc, bd, binary_name, imsdk_path=imsdk_override)
        state = state_info['state']
        print(f'  [setup_c] State after clone: {state}', flush=True)

    # ── State 2: repo cloned, cmake not run ──────────────────────────────────
    if state == 2:
        if not env_script:
            env_script = f'{bd}/{_SDK_SUBDIR}/environment-setup-armv8a-qcom-linux'
        if not _run('which cmake 2>/dev/null', timeout=10):
            print('  [setup_c] cmake not found — installing ...', flush=True)
            _run('sudo DEBIAN_FRONTEND=noninteractive apt-get install -y cmake', timeout=120)
        print(f'  [setup_c] State 2 — running cmake configure ...', flush=True)
        # Wipe stale build dir to avoid reusing a corrupt CMakeCache.txt from prior interrupted run
        _run(f'rm -rf {imsdk_dir}/build', timeout=30)
        cmake_cmd = (
            f"bash -c '. {env_script} && "
            f"cd {imsdk_dir} && "
            f"cmake -B build -S . {_CMAKE_FLAGS} 2>&1'"
        )
        out = _run(cmake_cmd, timeout=300)
        build_log_parts.append(f'[cmake configure]\n{out}')
        exists = _run(f'test -f {imsdk_dir}/build/Makefile && echo YES || echo NO')
        if 'YES' not in exists:
            return _fail(f'cmake configure did not produce build/Makefile. See build_log.')
        print(f'  [setup_c] cmake configure OK', flush=True)

        state_info = detect_mode_c_state(ssh_dc, bd, binary_name, imsdk_path=imsdk_override)
        state = state_info['state']
        print(f'  [setup_c] State after cmake: {state}', flush=True)

    # ── States 3 and 4: cmake configured — push source and build ─────────────
    if state in (3, 4):
        if not env_script:
            env_script = f'{bd}/{_SDK_SUBDIR}/environment-setup-armv8a-qcom-linux'

        app_dir_dc = f'{imsdk_dir}/gst-sample-apps/{binary_name}'

        # Clean and push source files
        print(f'  [setup_c] Pushing source to Linux workstation {app_dir_dc}/ ...', flush=True)
        _run(f"rm -rf '{app_dir_dc}' && mkdir -p '{app_dir_dc}'", timeout=15)
        ok, reason = _push_source_files(ssh_dc, artifact_path, app_dir_dc)
        if not ok:
            return _fail(reason)

        # Clean prior build artifacts for this target (stale .o files from old source)
        build_target_dir = f'{imsdk_dir}/build/gst-sample-apps/{binary_name}'
        _run(f"rm -rf '{build_target_dir}'", timeout=15)

    # ── Build ─────────────────────────────────────────────────────────────────
    if not env_script:
        env_script = f'{bd}/{_SDK_SUBDIR}/environment-setup-armv8a-qcom-linux'
    print(f'  [setup_c] Host-building {binary_name} (cmake --build) ...', flush=True)
    build_cmd = (
        f"bash -c '. {env_script} && "
        f"cd {imsdk_dir} && "
        f"cmake -B build -S . {_CMAKE_FLAGS} && "
        f"cmake --build build --target {binary_name} -- -j$(nproc) 2>&1'"
    )
    out = _run(build_cmd, timeout=600)
    build_log_parts.append(f'[host-build]\n{out}')

    if f'Built target {binary_name}' not in out:
        return _fail(f'Host build did not produce "Built target {binary_name}". See build_log.')
    print(f'  [setup_c] Host build OK', flush=True)

    # Verify binary exists on linux_workstation
    binary_path = f'{imsdk_dir}/build/gst-sample-apps/{binary_name}/{binary_name}'
    exists = _run(f'test -f {binary_path} && echo YES || echo NO')
    if 'YES' not in exists:
        return _fail(f'Binary not found at {binary_path} after host build')

    return {
        'success': True,
        'imsdk_dir': imsdk_dir,
        'binary_path': binary_path,
        'build_log': '\n\n'.join(build_log_parts),
        'failure_reason': None,
    }


# ── SDK installation ──────────────────────────────────────────────────────────

def _install_sdk(ssh_dc, build_dir, sdk_path=None):
    """
    Install the host SDK on Linux workstation.

    Source precedence:
      1. sdk_path, if given — either a .sh installer (already extracted, run
         directly) or a .zip (unzipped to find the .sh installer inside).
      2. A zip already present in {build_dir}:
         sdk.zip (generic) or x86-qli-2.0-standardsdk.zip / arm-qli-2.0-standardsdk.zip
         (arch-specific).
      3. wget from codelinaro.org (~3.5 GB). If that also fails (403, network
         blocked), reports clear manual download instructions.

    SDK is installed into {build_dir}/images/qcom-armv8a/sdk/.

    Returns (success: bool, reason: str, log: str).
    """
    def _run(cmd, timeout=30):
        # Wrap in bash -c "..." so redirects and pipes work regardless of the
        # remote login shell (e.g. tcsh on some shared Linux workstations).
        if cmd.startswith('bash -c'):
            wrapped = cmd
        else:
            wrapped = f'bash -c "{cmd}"'
        return _ws_run(ssh_dc, wrapped, timeout=timeout)

    log_parts = []

    # ── sdk_path given — use it directly, skip the zip-lookup/download flow ────
    if sdk_path:
        if 'YES' not in _run(f"test -f '{sdk_path}' && echo YES || echo NO", timeout=10):
            return False, f'LINUX_WORKSTATION_SDK_PATH is set to {sdk_path} but that file was not found on the workstation.', ''

        install_dir = f'{build_dir}/{_SDK_SUBDIR}'
        _run(f'mkdir -p {install_dir}', timeout=10)

        if sdk_path.lower().endswith('.sh'):
            print(f'  [setup_c] Using provided .sh installer: {sdk_path}', flush=True)
            log_parts.append(f'[sdk_path]\nUsing provided .sh installer: {sdk_path}')
            sh_path = sdk_path
        else:
            # Treat anything not ending in .sh as a zip (matches the .zip case explicitly
            # and gives a clear unzip-failure error for anything else, e.g. a mistyped path).
            if not _run('which unzip 2>/dev/null', timeout=10):
                print('  [setup_c] unzip not found — installing ...', flush=True)
                _run('sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unzip', timeout=120)

            print(f'  [setup_c] Unzipping {sdk_path} to extract installer ...', flush=True)
            unzip_tmp = f'{build_dir}/_sdk_unzip_tmp'
            _run(f'rm -rf {unzip_tmp} && mkdir -p {unzip_tmp}', timeout=10)
            out = _run(f"unzip -o '{sdk_path}' -d {unzip_tmp}", timeout=600)
            log_parts.append(f'[unzip]\n{out[:2000]}')

            # Single-quote the bash -c string so *.sh is NOT expanded by the calling
            # shell; find receives it verbatim as the -name pattern. Bypasses the
            # double-quote-wrapping _run() since that would collide with -name "*.sh".
            sh_path = _ws_run(ssh_dc, f"bash -c 'find {unzip_tmp} -name *.sh -type f | head -1'").strip()
            if not sh_path:
                return False, (
                    f'No .sh installer found after unzipping {sdk_path} into {unzip_tmp}. '
                    f'Check LINUX_WORKSTATION_SDK_PATH points at a valid SDK zip.'
                ), '\n\n'.join(log_parts)
            print(f'  [setup_c] Found installer: {sh_path}', flush=True)

        print(f'  [setup_c] Running SDK installer into {install_dir} (this may take a few minutes) ...', flush=True)
        _run(f"chmod +x '{sh_path}'", timeout=15)
        out = _run(f"'{sh_path}' -d {install_dir} -y", timeout=600)
        log_parts.append(f'[sdk install]\n{out}')
        if not sdk_path.lower().endswith('.sh'):
            _run(f'rm -rf {unzip_tmp}', timeout=10)

        env_script = f'{install_dir}/environment-setup-armv8a-qcom-linux'
        if 'YES' not in _run(f'test -f {env_script} && echo YES || echo NO'):
            return False, f'Installer ran but env script not found at {env_script}. See build_log.', '\n\n'.join(log_parts)

        print(f'  [setup_c] SDK installed at {install_dir}', flush=True)
        return True, 'ok', '\n\n'.join(log_parts)

    # ── No sdk_path — existing arch-zip lookup + wget fallback ─────────────────
    # Detect arch
    arch = _run('uname -m')
    if 'x86_64' in arch:
        arch_zip_name = 'x86-qli-2.0-standardsdk.zip'
    elif 'aarch64' in arch:
        arch_zip_name = 'arm-qli-2.0-standardsdk.zip'
    else:
        return False, f'Unexpected Linux workstation arch: {arch!r}', ''

    log_parts = [f'[detect arch]\narch={arch}, arch_zip={arch_zip_name}']

    # ── Find SDK zip ──────────────────────────────────────────────────────────
    # Exact file name. If not present, download from codelinaro.org (public, ~3.5 GB).
    local_zip = f'{build_dir}/{arch_zip_name}'
    if 'YES' in _run(f'test -f {local_zip} && echo YES || echo NO'):
        print(f'  [setup_c] SDK zip found: {local_zip}', flush=True)
        log_parts.append(f'[zip lookup]\nFound at {local_zip}')
    else:
        sdk_url = f'{_SDK_BASE_URL}/{arch_zip_name}'
        print(f'  [setup_c] SDK zip not found — downloading from codelinaro.org (~3.5 GB, this will take several minutes) ...', flush=True)
        print(f'  [setup_c] URL: {sdk_url}', flush=True)
        _run(f'mkdir -p {build_dir}', timeout=10)
        out = _run(f"wget -q -O {local_zip} '{sdk_url}'", timeout=7200)
        log_parts.append(f'[wget]\n{out}')
        if 'YES' not in _run(f'test -s {local_zip} && echo YES || echo NO'):
            _run(f'rm -f {local_zip}', timeout=10)
            return False, (
                f'SDK download failed for {arch_zip_name}.\n'
                f'URL tried: {sdk_url}\n'
                f'Alternatively, manually place the zip at {local_zip} and re-run.'
            ), '\n\n'.join(log_parts)
        print(f'  [setup_c] SDK downloaded to {local_zip}', flush=True)
        log_parts.append(f'[wget]\nDownloaded to {local_zip}')

    # ── Ensure unzip is available ─────────────────────────────────────────────
    if not _run('which unzip 2>/dev/null', timeout=10):
        print('  [setup_c] unzip not found — installing ...', flush=True)
        _run('sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unzip', timeout=120)

    # ── Unzip to find the .sh installer ───────────────────────────────────────
    install_dir = f'{build_dir}/{_SDK_SUBDIR}'
    _run(f'mkdir -p {install_dir}', timeout=10)
    print(f'  [setup_c] Unzipping {local_zip} to extract installer (this may take ~15s) ...', flush=True)
    unzip_tmp = f'{build_dir}/_sdk_unzip_tmp'
    _run(f'rm -rf {unzip_tmp} && mkdir -p {unzip_tmp}', timeout=10)
    out = _run(f'unzip -o {local_zip} -d {unzip_tmp}', timeout=600)  # stdout only, no stderr suppression
    log_parts.append(f'[unzip]\n{out[:2000]}')

    # Single-quote the bash -c string so *.sh is NOT expanded by the calling shell;
    # find receives it verbatim as the -name pattern.
    sh_path = _ws_run(ssh_dc, f"bash -c 'find {unzip_tmp} -name *.sh -type f | head -1'").strip()
    if not sh_path:
        return False, f'No .sh installer found after unzip in {unzip_tmp}. Zip may be corrupt.', '\n\n'.join(log_parts)
    print(f'  [setup_c] Found installer: {sh_path}', flush=True)

    # ── Run installer ─────────────────────────────────────────────────────────
    print(f'  [setup_c] Running SDK installer into {install_dir} (this may take a few minutes) ...', flush=True)
    out = _run(f'bash {sh_path} -d {install_dir} -y', timeout=600)
    log_parts.append(f'[sdk install]\n{out}')
    _run(f'rm -rf {unzip_tmp}', timeout=10)

    env_script = f'{install_dir}/environment-setup-armv8a-qcom-linux'
    if 'YES' not in _run(f'test -f {env_script} && echo YES || echo NO'):
        return False, f'Installer ran but env script not found at {env_script}. See build_log.', '\n\n'.join(log_parts)

    print(f'  [setup_c] SDK installed at {install_dir}', flush=True)
    return True, 'ok', '\n\n'.join(log_parts)


# ── Push source files ─────────────────────────────────────────────────────────

def _push_source_files(ssh_dc, artifact_path, remote_app_dir):
    """
    Push main.c, CMakeLists.txt, README.md to remote_app_dir on Linux workstation.
    Normalizes CRLF, verifies each file after push.
    Returns (success: bool, reason: str).
    """
    for fname in ['main.c', 'CMakeLists.txt', 'README.md']:
        local_f = artifact_path / fname
        if not local_f.exists():
            return False, f'Artifact missing required file: {fname}'
        unix_bytes = local_f.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        remote_f = f'{remote_app_dir}/{fname}'
        try:
            ssh_dc.push_bytes(unix_bytes, remote_f)
        except Exception as e:
            return False, f'Failed to push {fname} to Linux workstation: {e}'
        v = _ws_run(ssh_dc, f"test -f '{remote_f}' && echo OK || echo MISSING")
        if 'OK' not in v:
            return False, f'File not found on Linux workstation after push: {remote_f}'

    return True, 'ok'
