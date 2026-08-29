#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
workspace_state.py — Detect workspace setup state for Mode B and Mode C.

State detection is pure SSH inspection — no side effects, no changes.

Mode B states (Ubuntu on-device):
  0  SOURCE_ROOT dir does not exist
  1  SOURCE_ROOT exists, no build/Makefile (cmake not run)
  2  build/Makefile exists (cmake configured, app not registered)
  3  build/gst-sample-apps/{binary}/Makefile exists (app registered)
  4  build/gst-sample-apps/{binary}/{binary} exists (binary built)

Mode C states (Linux workstation host build):
  0  SDK env script not found at {BUILD_DIR}/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux
  1  SDK installed, no gst-plugins-imsdk/CMakeLists.txt (at imsdk_path if given, else {BUILD_DIR}/gst-plugins-imsdk)
  2  Repo cloned, no gst-plugins-imsdk/build/Makefile (cmake not run)
  3  cmake configured, binary not yet built
  4  build/gst-sample-apps/{binary}/{binary} exists

Mode D states (Linux/WSL workstation host build of a standalone IMSDK C++ SDK app):
  0  Yocto SDK env script not found under {BUILD_DIR}/qcom-sdk/environment-setup-*-qcom-linux
  1  SDK installed, per-app source not yet pushed to {BUILD_DIR}/qimsdk-cpp-apps/{target}/CMakeLists.txt
  2  App source pushed, no build/CMakeCache.txt (cmake not configured)
  3  cmake configured, binary not yet built
  4  {BUILD_DIR}/qimsdk-cpp-apps/{target}/build/{target} exists
Unlike Mode C there is no git clone and no SDK-from-source build: libqtiimsdk.so ships
inside the Yocto SDK target sysroot, and each app is built out-of-tree as a standalone
SDK consumer (a generated wrapper CMakeLists.txt resolves qtiimsdk via find_library).

All functions take a connected SSH client object with a .run(cmd, timeout) method
that returns (stdout, stderr, exit_code). The _ssh_blocking helper wraps a
paramiko client for callers that pass a raw paramiko.SSHClient.
"""

import posixpath


# ── SSH helper ────────────────────────────────────────────────────────────────

def _run(ssh, cmd, timeout=10):
    """Run cmd on ssh. Accepts either _SSH instance (deploy scripts) or raw paramiko client."""
    if hasattr(ssh, 'run'):
        out, _, _ = ssh.run(cmd, timeout=timeout)
        return out.strip()
    # Use get_pty=False + channel.recv_exit_status() so the wall-clock timeout governs
    # total command duration rather than socket inactivity. exec_command(timeout=) sets a
    # read inactivity timeout which kills long-running silent commands (e.g. wget of 3.5 GB).
    import time
    _, stdout, _ = ssh.exec_command(cmd)
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    while not channel.exit_status_ready():
        if time.monotonic() > deadline:
            channel.close()
            return ''
        time.sleep(0.5)
    return stdout.read().decode('utf-8', errors='replace').strip()


def _exists(ssh, path, is_file=False, timeout=10):
    """Return True if path exists on remote. is_file=True checks -f, else -d.
    Path is single-quoted to handle spaces in directory names."""
    flag = '-f' if is_file else '-d'
    out = _run(ssh, f"test {flag} '{path}' && echo YES || echo NO", timeout=timeout)
    return 'YES' in out  # substring check — robust against stderr noise in combined streams


# ── Mode B ────────────────────────────────────────────────────────────────────

_SOURCE_ROOT_PATTERN = '/home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-*'
_SAMPLE_APPS_SUBDIR  = 'gst-sample-apps'
_BUILD_SUBDIR        = 'build'


def detect_mode_b_state(ssh, source_root_hint=None, binary_name=None):
    """
    Detect Mode B workspace state on an Ubuntu device.

    Args:
        ssh            : connected SSH object (paramiko client or _SSH instance)
        source_root_hint: explicit path; if None, discovered by glob
        binary_name    : optional binary name to check states 3 and 4

    Returns dict:
        {
          'state':       int 0-4,
          'source_root': str or None,
          'detail':      str,
        }
    """
    if source_root_hint:
        src = source_root_hint.rstrip('/')
    else:
        # Select newest version when multiple match (sort -V = version sort)
        glob_out = _run(ssh, f'ls -d {_SOURCE_ROOT_PATTERN} 2>/dev/null | sort -V | tail -1')
        src = glob_out.strip() if glob_out else None

    if not src or not _exists(ssh, src):
        return {'state': 0, 'source_root': None,
                'detail': 'SOURCE_ROOT not found — workspace not set up'}

    build_makefile = f'{src}/{_BUILD_SUBDIR}/Makefile'
    if not _exists(ssh, build_makefile, is_file=True):
        return {'state': 1, 'source_root': src,
                'detail': f'{src} exists, but build/Makefile not found — cmake not run'}

    if not binary_name:
        return {'state': 2, 'source_root': src,
                'detail': f'cmake configured at {src}/build — no binary_name provided for further check'}

    app_makefile = f'{src}/{_BUILD_SUBDIR}/{_SAMPLE_APPS_SUBDIR}/{binary_name}/Makefile'
    if not _exists(ssh, app_makefile, is_file=True):
        return {'state': 2, 'source_root': src,
                'detail': f'cmake configured, but {binary_name} not registered (no build/gst-sample-apps/{binary_name}/Makefile)'}

    binary_path = f'{src}/{_BUILD_SUBDIR}/{_SAMPLE_APPS_SUBDIR}/{binary_name}/{binary_name}'
    if not _exists(ssh, binary_path, is_file=True):
        return {'state': 3, 'source_root': src,
                'detail': f'{binary_name} registered in cmake but binary not yet compiled'}

    return {'state': 4, 'source_root': src,
            'detail': f'Binary ready at {binary_path}',
            'binary_path': binary_path}


# ── Mode C ────────────────────────────────────────────────────────────────────

_SDK_ENV_RELPATH    = 'images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux'
_IMSDK_REPO_SUBDIR  = 'gst-plugins-imsdk'
_IMSDK_BUILD_SUBDIR = 'build'


def detect_mode_c_state(ssh_dc, build_dir, binary_name=None, imsdk_path=None):
    """
    Detect Mode C workspace state on a Linux workstation Linux x86_64 host.

    Args:
        ssh_dc      : connected SSH object (paramiko client or _SSH instance)
        build_dir   : absolute path to LINUX_WORKSTATION_BUILD_DIR
        binary_name : optional binary name to check states 3 and 4
        imsdk_path  : optional absolute path to an existing gst-plugins-imsdk clone
                      on the workstation. When set, used as imsdk_dir instead of
                      deriving it as {build_dir}/gst-plugins-imsdk. State 1 checks
                      for CMakeLists.txt at this path; states 2-4 use it for all
                      build sub-paths.

    Returns dict:
        {
          'state':       int 0-4,
          'env_script':  str or None,
          'imsdk_dir':   str or None,
          'detail':      str,
        }
    """
    bd = build_dir.rstrip('/')
    env_script = f'{bd}/{_SDK_ENV_RELPATH}'
    # imsdk_dir: caller-supplied path wins over the default derived location.
    imsdk_dir = imsdk_path.rstrip('/') if imsdk_path else f'{bd}/{_IMSDK_REPO_SUBDIR}'

    if not _exists(ssh_dc, env_script, is_file=True):
        return {'state': 0, 'env_script': None, 'imsdk_dir': None,
                'detail': f'SDK env script not found at {env_script}'}

    imsdk_cmake = f'{imsdk_dir}/CMakeLists.txt'
    if not _exists(ssh_dc, imsdk_cmake, is_file=True):
        return {'state': 1, 'env_script': env_script, 'imsdk_dir': None,
                'detail': f'SDK installed but gst-plugins-imsdk not found at {imsdk_dir}'}

    build_makefile = f'{imsdk_dir}/{_IMSDK_BUILD_SUBDIR}/Makefile'
    if not _exists(ssh_dc, build_makefile, is_file=True):
        return {'state': 2, 'env_script': env_script, 'imsdk_dir': imsdk_dir,
                'detail': f'Repo cloned but build/Makefile not found — cmake not run'}

    if not binary_name:
        return {'state': 3, 'env_script': env_script, 'imsdk_dir': imsdk_dir,
                'detail': f'cmake configured at {imsdk_dir}/build — no binary_name provided for further check'}

    binary_path = posixpath.join(imsdk_dir, _IMSDK_BUILD_SUBDIR, 'gst-sample-apps', binary_name, binary_name)
    if not _exists(ssh_dc, binary_path, is_file=True):
        return {'state': 3, 'env_script': env_script, 'imsdk_dir': imsdk_dir,
                'detail': f'cmake configured but {binary_name} not yet compiled'}

    return {'state': 4, 'env_script': env_script, 'imsdk_dir': imsdk_dir,
            'detail': f'Binary ready at {binary_path}',
            'binary_path': binary_path}


# ── Mode D ────────────────────────────────────────────────────────────────────
# Standalone IMSDK C++ SDK app, cross-built against the Yocto standard SDK.
# The SDK is installed under {BUILD_DIR}/qcom-sdk and its environment-setup script
# has a version-specific name (e.g. environment-setup-armv8-2a-qcom-linux), so we
# discover it by glob rather than hardcoding. Each app is built out-of-tree in its
# own dir under {BUILD_DIR}/qimsdk-cpp-apps/{target}/ — no shared source tree is
# mutated (contrast Mode C, which registers apps inside gst-plugins-imsdk).

_SDK_INSTALL_SUBDIR = 'qcom-sdk'
_CPP_APPS_SUBDIR    = 'qimsdk-cpp-apps'


def _find_sdk_env_script(ssh_dc, sdk_install_dir):
    """Return the path to the Yocto SDK environment-setup script under sdk_install_dir,
    or None if not found. The script name embeds the machine tuple (e.g.
    environment-setup-armv8-2a-qcom-linux), so it is discovered by glob.

    Wrapped in bash -c so it works regardless of the remote login shell (e.g. tcsh
    rejects '2>/dev/null' as 'Ambiguous output redirect' without bash).
    """
    out = _run(
        ssh_dc,
        f"bash -c 'ls {sdk_install_dir}/environment-setup-*-qcom-linux 2>/dev/null | head -1'",
    )
    path = out.strip().splitlines()[0].strip() if out.strip() else ''
    # Guard against shell-error text coming back as a path (e.g. "Ambiguous output redirect.")
    if path and path.startswith('/'):
        return path
    return None


def detect_mode_d_state(ssh_dc, build_dir, target_name=None):
    """
    Detect Mode D workspace state on a Linux/WSL x86_64 workstation.

    Args:
        ssh_dc      : connected SSH object (paramiko client or _SSH instance)
        build_dir   : absolute path to LINUX_WORKSTATION_BUILD_DIR
        target_name : optional CMake TEST_TARGET name to check states 2-4

    Returns dict:
        {
          'state':       int 0-4,
          'env_script':  str or None,
          'sdk_dir':     str or None,   # SDK install dir ({build_dir}/qcom-sdk)
          'app_dir':     str or None,   # per-app build dir (state >= 1, if target given)
          'detail':      str,
          'binary_path': str,           # only at state 4
        }
    """
    bd = build_dir.rstrip('/')
    sdk_dir = f'{bd}/{_SDK_INSTALL_SUBDIR}'

    env_script = _find_sdk_env_script(ssh_dc, sdk_dir)
    if not env_script:
        return {'state': 0, 'env_script': None, 'sdk_dir': None, 'app_dir': None,
                'detail': f'Yocto SDK env script not found under {sdk_dir} — SDK not installed'}

    if not target_name:
        return {'state': 1, 'env_script': env_script, 'sdk_dir': sdk_dir, 'app_dir': None,
                'detail': f'SDK installed at {sdk_dir} — no target_name provided for further check'}

    app_dir = f'{bd}/{_CPP_APPS_SUBDIR}/{target_name}'
    app_cmake = f'{app_dir}/CMakeLists.txt'
    if not _exists(ssh_dc, app_cmake, is_file=True):
        return {'state': 1, 'env_script': env_script, 'sdk_dir': sdk_dir, 'app_dir': app_dir,
                'detail': f'SDK installed but app source not pushed to {app_dir}'}

    build_cache = f'{app_dir}/build/CMakeCache.txt'
    if not _exists(ssh_dc, build_cache, is_file=True):
        return {'state': 2, 'env_script': env_script, 'sdk_dir': sdk_dir, 'app_dir': app_dir,
                'detail': f'App source pushed but build/ not configured (no CMakeCache.txt)'}

    binary_path = f'{app_dir}/build/{target_name}'
    if not _exists(ssh_dc, binary_path, is_file=True):
        return {'state': 3, 'env_script': env_script, 'sdk_dir': sdk_dir, 'app_dir': app_dir,
                'detail': f'cmake configured but {target_name} not yet compiled'}

    return {'state': 4, 'env_script': env_script, 'sdk_dir': sdk_dir, 'app_dir': app_dir,
            'detail': f'Binary ready at {binary_path}',
            'binary_path': binary_path}
