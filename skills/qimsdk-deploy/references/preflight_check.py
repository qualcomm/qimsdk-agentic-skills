#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
preflight_check.py — Deploy skill preflight subcommand.

Verifies all prerequisites for qimsdk-deploy before any deploy run.
Prints a formatted [ OK ] / [WARN] / [FAIL] report and exits 0 if ready, 1 if not.

── How to invoke ────────────────────────────────────────────────────────────────

As the deploy skill's preflight subcommand (interactive):
  python preflight_check.py --mode A
  python preflight_check.py --mode B
  python preflight_check.py --mode C

From the eval skill (before KPI 1 runs):
  python preflight_check.py --mode A

From any other skill or script:
  python preflight_check.py --mode <A|B|C>
  # exit code: 0 = ready, 1 = not ready

── Credentials (configs/.env) ───────────────────────────────────────────────────

All credentials are read from configs/.env in the repo root (gitignored).
The script loads this file automatically — no shell export needed.

Required for all modes:
  DEVICE_IP=<device-ip>
  DEVICE_USER=ubuntu
  DEVICE_PASSWORD=<password>
  HOST_KEY=SHA256:cKvXMnoKhO6g+fnn17WzmrpnfSSXW+MwzwFNiUu/gC4

If HOST_KEY is not set, preflight connects anyway and shows the actual fingerprint.

Mode B additional:
  SOURCE_ROOT=<path-to-qimsdk-source-tree>

Mode C additional:
  LINUX_WORKSTATION_HOST=<linux-workstation-hostname>
  LINUX_WORKSTATION_USER=<username>
  LINUX_WORKSTATION_KEY=~/.ssh/id_ed25519_workstation
  LINUX_WORKSTATION_BUILD_DIR=<path-to-build-dir>  (environment-setup-armv8a-qcom-linux must be at {BUILD_DIR}/images/qcom-armv8a/sdk/ and gst-plugins-imsdk must be at {BUILD_DIR}/gst-plugins-imsdk/)

── Behavior on failure ───────────────────────────────────────────────────────────

Print the exact error and the fix instruction. Stop. Do NOT attempt workarounds.
"""

import os
import re
import sys
import io
import socket
import shutil
import pathlib
import argparse
import subprocess

# Force UTF-8 stdout on Windows (cp1252 default breaks box-drawing chars in output)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def _load_dotenv():
    """Load configs/.env into os.environ. Shell env takes precedence over .env.

    Search order for configs/.env:
      1. Walk up from CWD — user's working directory has priority
      2. Walk up from script file — fallback for in-repo/installed-skill runs
    The user's working directory always wins over the skill install location.
    """
    cwd = pathlib.Path.cwd()
    here = pathlib.Path(__file__).resolve().parent

    # Walk CWD first (user directory wins), then script location (in-repo fallback)
    for start in [cwd, here]:
        for candidate in [start] + list(start.parents):
            env_candidate = candidate / "configs" / ".env"
            if env_candidate.exists():
                env_file = env_candidate
                root = candidate
                break
        else:
            continue
        break
    else:
        return cwd  # no .env anywhere

    loaded = []
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val
            loaded.append(key)
    if loaded:
        print(f"  [env]  Loaded from {env_file}: {', '.join(loaded)}", flush=True)
    return root

_REPO_ROOT = _load_dotenv()


# ── Colour helpers ────────────────────────────────────────────────────────────

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
RESET  = '\033[0m'
BOLD   = '\033[1m'

def ok(msg):   return f'{GREEN}[ OK ]{RESET}  {msg}'
def fail(msg): return f'{RED}[FAIL]{RESET}  {msg}'
def warn(msg): return f'{YELLOW}[WARN]{RESET}  {msg}'
def bold(msg): return f'{BOLD}{msg}{RESET}'
def indent(msg): return f'       {msg}'


# ── Python version ────────────────────────────────────────────────────────────

def check_host_platform():
    """Verify the host machine is Windows (deploy scripts tested on Windows)."""
    if sys.platform != 'win32':
        return False, (
            f'Host platform is {sys.platform!r} — deploy scripts are tested on Windows. '
            'Run on a Windows machine.'
        )
    return True, 'Windows host confirmed'


def check_device_os(client, expected_distro=None):
    """
    Verify the device is Linux aarch64. Optionally assert a specific distro.
    expected_distro: 'ubuntu' | None (Mode A requires only Linux; Mode B requires Ubuntu)
    Returns (ok, message).
    """
    try:
        _, stdout, _ = client.exec_command('uname -s && uname -m', timeout=10)
        out = stdout.read().decode('utf-8', errors='replace').strip().splitlines()
        kernel = out[0].strip() if len(out) > 0 else ''
        arch   = out[1].strip() if len(out) > 1 else ''
    except Exception as e:
        return False, f'Could not run uname on device: {e}'

    if kernel != 'Linux':
        return False, f'Device kernel is {kernel!r} — expected Linux'
    if arch != 'aarch64':
        return False, f'Device arch is {arch!r} — expected aarch64'

    if expected_distro:
        try:
            _, stdout, _ = client.exec_command(
                "grep '^ID=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"'",
                timeout=10
            )
            distro_id = stdout.read().decode('utf-8', errors='replace').strip().lower()
        except Exception as e:
            return False, f'Could not read /etc/os-release: {e}'

        if distro_id != expected_distro.lower():
            return False, (
                f'Device OS is {distro_id!r} — expected {expected_distro!r} '
                f'(set DEPLOY_MODE={expected_distro} only when targeting a {expected_distro} device)'
            )
        return True, f'Device OS: Linux aarch64 ({distro_id})'

    return True, f'Device OS: Linux {arch}'


def check_python():
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 8):
        return False, f'Python {major}.{minor} found — Python 3.8+ required.'
    return True, f'Python {major}.{minor}'


# ── Python dependencies ───────────────────────────────────────────────────────

def install_requirements(requirements_path):
    """Auto-install Python deps. Returns (ok, message)."""
    if not requirements_path.exists():
        return False, (
            f'requirements.txt not found at {requirements_path}.'
        )
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-r', str(requirements_path),
         '--quiet', '--disable-pip-version-check'],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        return False, (
            f'pip install failed:\n{result.stderr.strip()}'
        )
    return True, f'paramiko installed from {requirements_path.name}'


def check_imports():
    """Verify paramiko is importable after install attempt."""
    missing = []
    for pkg in ['paramiko']:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        return False, (
            f'Still missing after install: {", ".join(missing)}.'
        )
    return True, 'paramiko importable'


# ── PuTTY tools (Windows interactive use) ────────────────────────────────────

def check_putty():
    """Check plink and pscp are available. Returns dict: tool -> (found, path)."""
    results = {}
    for tool in ['plink', 'pscp']:
        found, path = shutil.which(tool) is not None, shutil.which(tool) or ''
        if not found:
            for win_path in [
                pathlib.Path(r'C:\Program Files\PuTTY') / f'{tool}.exe',
                pathlib.Path(r'C:\Program Files (x86)\PuTTY') / f'{tool}.exe',
            ]:
                if win_path.exists():
                    found, path = True, str(win_path)
                    break
        results[tool] = (found, path)
    return results


# ── Local filesystem checks ───────────────────────────────────────────────────

def check_local_artifact(artifact_path, mode):
    """
    Verify the local artifact folder exists and contains the required files.
    Mode B/C: needs main.c + CMakeLists.txt + README.md
    Mode A:   needs pipeline.sh + README.md
    """
    p = pathlib.Path(artifact_path)
    if not p.exists():
        return False, (
            f'Artifact path not found: {artifact_path}.'
        )
    if not p.is_dir():
        return False, (
            f'Artifact path is not a directory: {artifact_path}.'
        )

    if mode == 'C':
        required = ['main.c', 'CMakeLists.txt', 'README.md']
    elif mode == 'B':
        required = ['main.c', 'CMakeLists.txt', 'README.md']
    elif mode == 'D':
        required = ['main.cc', 'CMakeLists.txt', 'README.md']
    elif mode == 'P':
        # python-app-builder emits main.py; app.py accepted as legacy fallback.
        if not ((p / 'main.py').exists() or (p / 'app.py').exists()):
            return False, (
                f'Artifact folder {artifact_path} is missing an app entry point '
                f'(expected main.py or app.py).'
            )
        if not (p / 'README.md').exists():
            return False, f'Artifact folder {artifact_path} is missing: README.md.'
        app_file = 'main.py' if (p / 'main.py').exists() else 'app.py'
        return True, f'{artifact_path} — {app_file}, README.md present'
    else:
        required = ['pipeline.sh', 'README.md']

    missing = [f for f in required if not (p / f).exists()]
    if missing:
        return False, (
            f'Artifact folder {artifact_path} is missing: {", ".join(missing)}.'
        )
    return True, f'{artifact_path} — {", ".join(required)} present'


def check_local_output_dir(output_dir, repo_root=None):
    """
    Ensure local output directory exists and is writable.
    If output_dir is relative, it is resolved relative to repo_root (where configs/.env lives).
    Creates the directory if it does not exist yet (mkdir -p — this is expected first-run behavior).
    """
    p = pathlib.Path(output_dir)
    if not p.is_absolute() and repo_root:
        p = pathlib.Path(repo_root) / p
    p = p.resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, (
            f'Cannot create local output directory {p}: {e}.'
        )
    test_file = p / '.preflight_write_test'
    try:
        test_file.write_text('test', encoding='utf-8')
        test_file.unlink()
    except Exception as e:
        return False, (
            f'Local output directory {p} is not writable: {e}.'
        )
    return True, f'{p} — exists and writable'


def check_local_tmp_writable():
    """Mode C (Windows): verify C:/tmp/ is writable for pscp long-path workaround."""
    tmp = pathlib.Path('C:/tmp/qimsdk_preflight_test')
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text('test', encoding='utf-8')
        tmp.unlink()
        return True, 'C:/tmp/ is writable (pscp long-path workaround will work)'
    except Exception as e:
        return False, (
            f'C:/tmp/ is not writable: {e}.'
        )


# ── Network / SSH ─────────────────────────────────────────────────────────────

def check_tcp(host, port=22, timeout=5):
    """Check TCP reachability. Returns (ok, message)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f'{host}:{port} reachable'
    except socket.timeout:
        return False, (
            f'TCP connection to {host}:{port} timed out after {timeout}s.'
        )
    except OSError as e:
        return False, (
            f'TCP connection to {host}:{port} failed: {e}.'
        )


def _fingerprint(pkey):
    """Return SHA256:<base64> fingerprint of a paramiko PKey object."""
    import hashlib, base64
    digest = hashlib.sha256(pkey.asbytes()).digest()
    return 'SHA256:' + base64.b64encode(digest).rstrip(b'=').decode()


def check_device_auth_method(device_key, device_password, mode):
    """
    Check that at least one auth method is configured for the device.
    Returns (ok, message, severity) where severity is 'ok', 'warn', or 'fail'.
    """
    key_path = pathlib.Path(device_key).expanduser() if device_key else None
    key_exists = bool(key_path and key_path.exists())
    has_password = bool(device_password)

    # Key path set but file missing
    if device_key and not key_exists:
        return False, (
            f'DEVICE_KEY file not found: {device_key}\n'
            '       Check the path in configs/.env or regenerate the key — see ssh-setup.md Step A'
        ), 'fail'

    if not key_exists and not has_password:
        return False, (
            'No device auth configured — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env\n'
            '       See ssh-setup.md (in the same references/ folder)'
        ), 'fail'

    if key_path and key_path.exists() and sys.platform != 'win32':
        mode = key_path.stat().st_mode & 0o777
        if mode & 0o077:
            return False, (
                f'SSH key {key_path} has insecure permissions {oct(mode)} — '
                f'run: chmod 600 "{key_path}"'
            ), 'fail'

    if key_exists:
        msg = f'Key auth configured ({key_path.name})'
        if has_password:
            msg += ' — password kept as fallback'
        return True, msg, 'ok'

    # password only
    return True, (
        'Password auth configured — consider adding DEVICE_KEY for better security (ssh-setup.md)'
    ), 'warn'


def check_linux_workstation_auth_method(linux_workstation_key, linux_workstation_password):
    """
    Check that at least one auth method is configured for Linux workstation (Mode C only).
    """
    has_key = bool(linux_workstation_key and pathlib.Path(linux_workstation_key).expanduser().exists())
    has_password = bool(linux_workstation_password)

    if not has_key and not has_password:
        return False, (
            'No Linux workstation auth configured — set LINUX_WORKSTATION_KEY or LINUX_WORKSTATION_PASSWORD in configs/.env\n'
            '       See ssh-setup.md Step C (in the same references/ folder)'
        ), 'fail'

    if has_key:
        key_name = pathlib.Path(linux_workstation_key).expanduser().name
        msg = f'Key auth configured ({key_name})'
        if has_password:
            msg += ' — password kept as fallback'
        return True, msg, 'ok'

    return True, (
        'Password auth configured — consider adding LINUX_WORKSTATION_KEY for better security (ssh-setup.md)'
    ), 'warn'


def check_ssh_login(host, user, password, host_key, timeout=10, key_path=None):
    """
    SSH login check. Returns (ok, message, device_info_lines[, key_warn]).
    Tries key auth first if key_path provided, falls back to password.
    Connects with AutoAddPolicy, then compares the actual server fingerprint
    against the configured HOST_KEY. Surfaces a WARN if they don't match.
    Runs uname, os-release, and ls ~ to confirm filesystem access.
    """
    try:
        import paramiko
        import warnings
        warnings.filterwarnings('ignore', category=UserWarning, module='paramiko')
    except ImportError:
        return False, 'paramiko not available — run pip install again', []

    use_key = bool(key_path and pathlib.Path(key_path).expanduser().exists())

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host, username=user,
            key_filename=str(pathlib.Path(key_path).expanduser()) if use_key else None,
            password=password if not use_key else None,
            timeout=timeout, allow_agent=False, look_for_keys=False,
        )
    except paramiko.AuthenticationException:
        auth_type = 'key' if use_key else 'password'
        fallback = (
            ' Try password: ensure DEVICE_PASSWORD is set.'
            if use_key else
            ' Try key auth: see ssh-setup.md.'
        )
        return False, f'SSH {auth_type} authentication failed for {user}@{host}.{fallback}', []
    except paramiko.SSHException as e:
        return False, (
            f'SSH error connecting to {host}: {e}.'
        ), []
    except Exception as e:
        return False, (
            f'SSH connection to {host} failed: {e}.'
        ), []

    # Verify host key fingerprint
    key_warn = None
    try:
        transport = client.get_transport()
        remote_key = transport.get_remote_server_key()
        actual_fp = _fingerprint(remote_key)
        configured_fp = (host_key or '').strip()
        if not configured_fp:
            # No HOST_KEY provided — tell user what the device's actual key is
            key_warn = (
                f'HOST_KEY not configured. Device presented: {actual_fp}\n'
                f'       Add to configs/.env:  HOST_KEY={actual_fp}'
            )
        elif actual_fp != configured_fp:
            key_warn = (
                f'Host key mismatch for {host}!\n'
                f'       Configured: {configured_fp}\n'
                f'       Actual:     {actual_fp}'
            )
    except Exception:
        pass

    info_lines = []
    try:
        _, stdout, _ = client.exec_command(
            'uname -a && echo "---OS---" && cat /etc/os-release 2>/dev/null | head -5 '
            '&& echo "---HOME LS---" && ls ~ 2>&1 | head -10',
            timeout=10
        )
        raw = stdout.read().decode('utf-8', errors='replace').strip()
        info_lines = raw.splitlines()
    except Exception:
        info_lines = ['(could not read device info)']
    finally:
        client.close()

    auth_label = f'key ({pathlib.Path(key_path).expanduser().name})' if use_key else 'password'
    msg = f'SSH login OK as {user}@{host} ({auth_label})'
    if key_warn:
        # Return ok=True but attach the warning so the caller can surface it
        return True, msg, info_lines, key_warn
    return True, msg, info_lines


# ── Config helpers ────────────────────────────────────────────────────────────

def check_host_key(host_key):
    if not host_key or not host_key.strip():
        return False, (
            'HOST_KEY not set — the script will connect and show the actual key for you to save. '
            'Add HOST_KEY=<SHA256:...> to configs/.env after the first run.'
        ), 'warn'
    return True, 'host key present'


def check_password_env():
    pw = os.environ.get('DEVICE_PASSWORD', '')
    if not pw:
        return False, (
            'DEVICE_PASSWORD env var not set.'
        )
    return True, 'DEVICE_PASSWORD env var set'


# ── Device tool/path checks (shared SSH client) ───────────────────────────────

def _ssh_run(client, cmd, timeout=10):
    """Run a command on an open SSH client. Returns stdout string."""
    _, stdout, _ = client.exec_command(cmd, timeout=timeout)
    return stdout.read().decode('utf-8', errors='replace').strip()


def check_device_cmake(client):
    out = _ssh_run(client, 'which cmake 2>&1 && cmake --version 2>&1 | head -1')
    if 'cmake version' in out.lower():
        # Extract the version line regardless of which line which returned
        version_line = next((l for l in out.splitlines() if 'cmake version' in l.lower()), out.splitlines()[0])
        return True, f'cmake found: {version_line.strip()}'
    if out.startswith('/') and out.strip():
        return True, f'cmake found: {out.splitlines()[0].strip()}'
    return False, (
        'cmake not found on device.'
    )


def check_device_make(client):
    out = _ssh_run(client, 'which make 2>&1 && make --version 2>&1 | head -1')
    if 'gnu make' in out.lower():
        version_line = next((l for l in out.splitlines() if 'gnu make' in l.lower()), out.splitlines()[0])
        return True, f'make found: {version_line.strip()}'
    if out.startswith('/') and out.strip():
        return True, f'make found: {out.splitlines()[0].strip()}'
    return False, (
        'make not found on device.'
    )


def check_device_sudo(client, password):
    """Test that the device user can sudo (required for cmake, make install, chown).
    If password is None (key-only auth), tries sudo without password first."""
    stdin, stdout, stderr = client.exec_command('sudo -S true 2>&1', timeout=10)
    if password:
        stdin.write(password + '\n')
        stdin.flush()
    stdin.close()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    combined = (out + err).lower()
    if 'incorrect password' in combined or 'authentication failure' in combined:
        return False, (
            'sudo authentication failed on device.'
        )
    if 'not allowed' in combined or 'not in sudoers' in combined or 'sudoers' in combined:
        return False, (
            'Device user does not have sudo access.'
        )
    return True, 'sudo access confirmed'


def check_device_timeout(client):
    out = _ssh_run(client, 'which timeout 2>&1')
    if out.startswith('/'):
        return True, f'timeout found at {out}'
    return False, (
        'timeout command not found on device.'
    )


def check_device_build_dir(client, source_root):
    """Check BUILD_DIR (SOURCE_ROOT/build) exists — cmake runs from here."""
    build_dir = f'{source_root}/build'
    out = _ssh_run(client, f'test -d {build_dir} && echo EXISTS || echo NOT_EXISTS')
    if out == 'EXISTS':
        return True, f'{build_dir} exists'
    return False, (
        f'Build directory not found: {build_dir}.'
    )


def check_device_output_dir(client, output_dir='/home/ubuntu/Downloads/qimsdk_samples/media/output'):
    """Ensure output directory exists on device (mkdir -p — safe even if already there)."""
    out = _ssh_run(client, f'mkdir -p {output_dir} && echo OK || echo FAIL')
    if 'OK' in out:
        return True, f'{output_dir} ready (mkdir -p)'
    return False, (
        f'Could not create output directory {output_dir} on device.'
    )


def check_device_cam_server(client):
    """WARN-only: check cam-server is active (it gets restarted before every run anyway)."""
    out = _ssh_run(client, 'systemctl is-active cam-server 2>&1')
    if 'active' in out and 'inactive' not in out:
        return True, f'cam-server is active'
    return False, (
        f'cam-server is {out.strip() or "not found"} — '
        'it will be restarted before each camera pipeline run (this is WARN not FAIL).'
    )


def check_artifact_files_on_device(client, artifact_path):
    """
    Parse pipeline.sh from the artifact and verify every input file and model path
    referenced by filesrc location= exists on device. Shell variables ($HOME) are
    expanded on device via `eval echo`.
    Returns list of (path, expanded, exists) tuples.
    """
    import re
    artifact_path = pathlib.Path(artifact_path)
    pipeline_sh = artifact_path / 'pipeline.sh'
    if not pipeline_sh.exists():
        return []

    content = pipeline_sh.read_text(encoding='utf-8', errors='replace')

    # Collect all file paths: filesrc location= and common model/label options
    paths = []
    for raw in re.findall(r'\bfilesrc\s+location\s*=\s*["\']?([^\s"\'!\\]+)', content):
        p = raw.strip('\'"')
        if p and p not in paths:
            paths.append(p)
    # model= and labels= paths (tflite, dlc, json, etc.)
    for raw in re.findall(r'\b(?:model|labels|label-path)\s*=\s*["\']?([^\s"\'\\]+)', content):
        p = raw.strip('\'"')
        if p and p not in paths:
            paths.append(p)

    results = []
    for path in paths:
        expanded = _ssh_run(client, f'eval echo {path}').strip() or path
        exists = _ssh_run(client, f'test -f "{expanded}" && echo EXISTS || echo MISSING').strip() == 'EXISTS'
        results.append((path, expanded, exists))
    return results


def check_device_gst_launch(client):
    """Verify gst-launch-1.0 is installed and return its version."""
    out = _ssh_run(client, 'which gst-launch-1.0 2>&1 && gst-launch-1.0 --version 2>&1 | head -2')
    if 'gst-launch-1.0' in out and ('version' in out.lower() or out.startswith('/')):
        version_line = next((l for l in out.splitlines() if 'version' in l.lower()), out.splitlines()[0])
        return True, f'gst-launch-1.0 found: {version_line.strip()}'
    return False, (
        'gst-launch-1.0 not found on device.'
    )


def check_device_disk_space(client, output_dir='/home/ubuntu/Downloads/qimsdk_samples/media/output', min_mb=500):
    """WARN if available disk space at output dir is less than min_mb MB."""
    # Get the filesystem where output_dir lives (or its closest existing parent)
    out = _ssh_run(
        client,
        f'df -BM {output_dir} 2>/dev/null || df -BM $(dirname {output_dir}) 2>/dev/null || df -BM /home',
        timeout=10
    )
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].endswith('M'):
            try:
                avail_mb = int(parts[3].rstrip('M'))
                filesystem = parts[0]
                if avail_mb < min_mb:
                    return False, (
                        f'Low disk space: only {avail_mb}MB available on {filesystem} '
                        f'(need at least {min_mb}MB for output files).'
                    ), 'warn'
                return True, f'{avail_mb}MB available on {filesystem} at {output_dir}'
            except ValueError:
                continue
    return False, 'Could not parse disk space — verify manually.', 'warn'


def check_device_camera_plugin(client, deploy_mode):
    """FAIL: verify the camera GStreamer plugin is installed for the target platform."""
    plugin = 'qtiqmmfsrc' if deploy_mode == 'ubuntu' else 'qticamsrc'
    out = _ssh_run(client, f'gst-inspect-1.0 {plugin} 2>/dev/null | head -1')
    if out.strip():
        return True, f'Camera plugin {plugin} is available'
    return False, (
        f'Camera plugin {plugin} not found — camera pipelines will fail. '
        f'Install the QIMSDK GStreamer plugins package for this device.'
    )


def check_device_usb_camera(client):
    """FAIL: verify at least one USB camera device node exists."""
    out = _ssh_run(client, 'ls /dev/video* 2>/dev/null')
    devices = [d.strip() for d in out.splitlines() if d.strip().startswith('/dev/video')]
    if devices:
        return True, f'USB camera device(s): {", ".join(devices)}'
    return False, (
        'No /dev/video* devices found — USB camera pipeline will fail. '
        'Connect a USB camera and verify it appears as /dev/video*.'
    )


def check_device_wayland(client):
    """FAIL: verify a Wayland socket exists anywhere under /run (any owning user)."""
    out = _ssh_run(client, 'find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1')
    socket = out.strip()
    if socket:
        return True, f'Wayland socket: {socket}'
    return False, (
        'No Wayland socket found anywhere under /run — '
        'display pipelines (waylandsink) will fail. '
        'Ensure a graphical session is running on the device.'
    )


def check_device_git(client):
    out = _ssh_run(client, 'which git 2>&1')
    if out.startswith('/'):
        return True, f'git at {out}'
    return False, (
        'git not found on device.'
    )


def check_device_network(client):
    out = _ssh_run(
        client,
        'curl -s --max-time 5 https://github.com -o /dev/null -w "%{http_code}" 2>&1',
        timeout=15
    )
    if out.startswith('2') or out.startswith('3'):
        return True, 'device can reach github.com'
    return False, (
        f'Device cannot reach github.com (http code: {out!r}).'
    )


def check_device_usr_bin_writable(client):
    """Check /usr/bin is writable; if not, attempt mount -o remount,rw /usr.

    Returns (ok, msg) where ok=True means writable (after remount if needed).
    A successful remount returns a WARN tuple (True, msg, 'warn') to signal the
    caller that writable state was restored but won't persist across reboots.
    """
    writable = _ssh_run(client, 'touch /usr/bin/.preflight_write_test 2>&1 && rm -f /usr/bin/.preflight_write_test && echo OK || echo RO')
    if 'OK' in writable:
        return True, '/usr/bin is writable'

    # Attempt remount
    remount = _ssh_run(client, 'mount -o remount,rw /usr 2>&1 && echo OK || echo FAIL')
    if 'OK' not in remount:
        return False, (
            '/usr/bin is read-only and remount failed. Run `mount -o remount,rw /usr` '
            'manually on the device before deploying.'
        )

    # Verify writable after remount
    verify = _ssh_run(client, 'touch /usr/bin/.preflight_write_test 2>&1 && rm -f /usr/bin/.preflight_write_test && echo OK || echo RO')
    if 'OK' in verify:
        return True, '/usr/bin was read-only — remounted rw (NOTE: does not persist across reboots)', 'warn'

    return False, '/usr/bin is read-only and could not be made writable. Run `mount -o remount,rw /usr` manually.'


def check_source_root(client, source_root):
    """Check QIMSDK source tree and gst-sample-apps exist on device."""
    out = _ssh_run(
        client,
        f'test -d {source_root}/gst-sample-apps && echo EXISTS || echo NOT_EXISTS'
    )
    if out == 'EXISTS':
        return True, f'{source_root}/gst-sample-apps found'
    # WARN not FAIL — device may still work for gst-launch (Mode A fallback)
    return False, (
        f'QIMSDK source tree not found at {source_root}.'
    ), 'warn'


# ── Mode C — Linux workstation checks ────────────────────────────────────────────────

def _linux_workstation_client(host, user, key_path=None, password=None, timeout=10, port=22):
    """Open a paramiko client to Linux workstation. Prefers key auth; falls back to password."""
    import paramiko
    use_key = False
    if key_path:
        kp = pathlib.Path(key_path).expanduser()
        if kp.exists():
            use_key = True
        elif not password:
            return None, f'SSH key not found at {kp} and LINUX_WORKSTATION_PASSWORD not set.'
    elif not password:
        return None, 'No auth available: LINUX_WORKSTATION_KEY not set and LINUX_WORKSTATION_PASSWORD not set.'

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host, username=user, port=port,
            key_filename=str(pathlib.Path(key_path).expanduser()) if use_key else None,
            password=password if not use_key else None,
            timeout=timeout, allow_agent=False, look_for_keys=False,
        )
        return client, None
    except paramiko.AuthenticationException:
        method = 'key' if use_key else 'password'
        return None, f'SSH {method} auth failed for {user}@{host}:{port}.'
    except Exception as e:
        return None, f'SSH to Linux workstation {host}:{port} failed: {e}.'


def check_linux_workstation_ssh(host, user, key_path, password=None, timeout=10, port=22):
    """SSH login + ls $HOME to confirm filesystem access. Returns (ok, msg, home_ls_lines)."""
    try:
        import paramiko
    except ImportError:
        return False, 'paramiko not available', []

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, err, []

    ls_lines = []
    try:
        out = _ssh_run(client, 'echo connected && echo "---HOME LS---" && ls $HOME 2>&1 | head -10')
        ls_lines = out.splitlines()
    except Exception:
        ls_lines = ['(could not ls home directory)']
    finally:
        client.close()

    return True, f'SSH login OK as {user}@{host}:{port}', ls_lines


def check_sdk_env(host, user, key_path, build_dir, password=None, timeout=15, port=22):
    """
    Verify SDK env script exists in build_dir and compiler works.

    The SDK env script is always named environment-setup-armv8a-qcom-linux
    after the standard install — no config needed for the script name.
    """
    try:
        import paramiko
    except ImportError:
        return False, 'paramiko not available'

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, f'Could not connect to Linux workstation to check SDK: {err}'

    try:
        env_script = f'{build_dir}/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux'

        found = _ssh_run(client, f'test -f {env_script} && echo FOUND || echo NOT_FOUND')
        if found != 'FOUND':
            # SDK isn't at the configured path — before concluding it needs
            # provisioning, check common alternate mount points on the same
            # workstation. Misconfigured LINUX_WORKSTATION_BUILD_DIR (e.g.
            # /home/<user>/... when the SDK actually lives under the shared
            # /local/mnt/workspace/<user>/... mount) otherwise silently
            # triggers an unnecessary multi-GB re-download on first deploy.
            candidates = [
                f'/local/mnt/workspace/{user}/qimsdk-build',
                f'/home/{user}/qimsdk-build',
                f'/local/mnt/workspace/{user}',
                f'/home/{user}',
            ]
            found_elsewhere = []
            for cand in candidates:
                if cand == build_dir:
                    continue
                probe = _ssh_run(
                    client,
                    f'test -f {cand}/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux '
                    f'&& echo FOUND || echo NOT_FOUND'
                )
                if probe == 'FOUND':
                    found_elsewhere.append(cand)
            if found_elsewhere:
                return False, (
                    f'SDK env script not found at {env_script}, but a valid SDK IS installed at: '
                    f'{", ".join(found_elsewhere)}. LINUX_WORKSTATION_BUILD_DIR is very likely '
                    f'misconfigured — update configs/.env to point there instead of triggering an '
                    f'unnecessary re-download.'
                )
            return False, (
                f'SDK env script not found at {env_script}.'
            )

        out = _ssh_run(
            client,
            f"bash -c '. {env_script} && aarch64-qcom-linux-gcc --version 2>&1 | head -1'",
            timeout=15
        )
        compiler_ok = 'aarch64-qcom-linux-gcc' in out and ('(GCC)' in out or 'gcc version' in out.lower())
        if compiler_ok:
            return True, f'SDK validated ({env_script}): {out}'
        return False, f'SDK env script found at {env_script} but compiler not working. Output: {out!r}.'
    finally:
        client.close()


def check_qimsdk_sdk_env(host, user, key_path, build_dir, password=None, timeout=20, port=22):
    """
    Mode D: verify the Yocto standard SDK is installed under {build_dir}/qcom-sdk,
    that the cross C++ compiler works, and that libqtiimsdk + <qti/imsdk.h> resolve
    in the SDK target sysroot (so a standalone SDK-consumer build can link).

    The env-setup script name is version-specific (e.g.
    environment-setup-armv8-2a-qcom-linux), so it is discovered by glob.

    Returns (ok: bool, msg: str, severity: 'ok'|'warn'|'fail').
    """
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return False, 'paramiko not available', 'fail'

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, f'Could not connect to workstation to check SDK: {err}', 'fail'

    try:
        sdk_dir = f'{build_dir}/qcom-sdk'
        env_script = _ssh_run(
            client,
            f'ls {sdk_dir}/environment-setup-*-qcom-linux 2>/dev/null | head -1'
        ).strip()
        if not env_script:
            # Not installed at the configured path — check common alternate
            # workstation mounts before assuming it needs provisioning.
            # A misconfigured LINUX_WORKSTATION_BUILD_DIR otherwise silently
            # triggers an unnecessary multi-GB re-download on first deploy.
            candidates = [
                f'/local/mnt/workspace/{user}/qimsdk-build',
                f'/home/{user}/qimsdk-build',
            ]
            found_elsewhere = []
            for cand in candidates:
                cand_sdk_dir = f'{cand}/qcom-sdk'
                if cand_sdk_dir == sdk_dir:
                    continue
                probe_script = _ssh_run(
                    client,
                    f'ls {cand_sdk_dir}/environment-setup-*-qcom-linux 2>/dev/null | head -1'
                ).strip()
                if probe_script:
                    found_elsewhere.append(cand)
            if found_elsewhere:
                return False, (
                    f'Yocto SDK not found under {sdk_dir}, but a valid SDK IS installed under: '
                    f'{", ".join(f"{c}/qcom-sdk" for c in found_elsewhere)}. '
                    f'LINUX_WORKSTATION_BUILD_DIR is very likely misconfigured — update '
                    f'configs/.env to point there instead of triggering an unnecessary re-download.'
                ), 'fail'
            # Not installed anywhere checked — deploy auto-provisions from the SDK zip on first run.
            return False, (
                f'Yocto SDK not found under {sdk_dir}'
            ), 'warn'

        # Confirm the cross C++ compiler runs and sysroot ships libqtiimsdk + header.
        probe = _ssh_run(
            client,
            f"bash -c '. {env_script} && "
            f'echo CXX=$CXX && '
            f'"$CXX" --version 2>&1 | head -1 && '
            f'echo SYSROOT=$SDKTARGETSYSROOT && '
            f'ls "$SDKTARGETSYSROOT"/usr/lib/libqtiimsdk.so* 2>/dev/null | head -1 && '
            f'(find "$SDKTARGETSYSROOT" -name imsdk.h 2>/dev/null | head -1)\'',
            timeout=timeout,
        )
        has_lib = 'libqtiimsdk.so' in probe
        has_hdr = 'imsdk.h' in probe
        compiler_ok = 'qcom-linux' in probe and ('(GCC)' in probe or 'clang version' in probe.lower()
                                                 or 'gcc version' in probe.lower())
        if compiler_ok and has_lib and has_hdr:
            return True, f'Yocto SDK validated ({env_script}); libqtiimsdk + imsdk.h resolvable in sysroot', 'ok'
        if compiler_ok and not (has_lib and has_hdr):
            # Toolchain is fine but the SDK sysroot lacks the C++ IMSDK dev files —
            # a standalone build's find_library(qtiimsdk) will fail. WARN loudly.
            missing = ', '.join(
                m for m, present in (('libqtiimsdk.so', has_lib), ('imsdk.h', has_hdr)) if not present
            )
            return False, (
                f'SDK toolchain works but sysroot is missing {missing} — a standalone '
                f'cpp-app-builder build cannot link qtiimsdk. Probe: {probe!r}'
            ), 'warn'
        return False, f'SDK env found at {env_script} but cross compiler not working. Probe: {probe!r}', 'fail'
    finally:
        client.close()


def check_linux_workstation_disk(host, user, key_path, build_dir=None, password=None, timeout=10, port=22):
    """
    Check available disk space on Linux workstation.
    SDK ~5GB + imsdk repo ~500MB + build artifacts ~2GB = ~7.5GB needed.
    WARN if <8GB available on the filesystem containing build_dir.
    """
    try:
        import paramiko
    except ImportError:
        return False, 'paramiko not available'

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, f'Could not connect to Linux workstation to check disk: {err}'

    try:
        # Check the filesystem the build will actually use, fall back to $HOME
        target = build_dir if build_dir else '$HOME'
        out = _ssh_run(
            client,
            f"df -BG {target} 2>/dev/null || df -BG $HOME",
            timeout=10
        )
        # Parse available column from df output (column 4, strip 'G')
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[3].endswith('G'):
                try:
                    avail_gb = int(parts[3].rstrip('G'))
                    filesystem = parts[0]
                    if avail_gb < 8:
                        return False, (
                            f'Only {avail_gb}GB available on {filesystem} (need ~8GB for SDK+build).'
                        ), 'warn'
                    return True, f'{avail_gb}GB available on {filesystem}'
                except ValueError:
                    continue
        return False, 'Could not parse disk space from df output — verify manually.', 'warn'
    finally:
        client.close()


def check_linux_workstation_imsdk_dir(host, user, key_path, imsdk_dir, password=None, timeout=10, port=22):
    """Verify gst-plugins-imsdk exists at imsdk_dir and ls gst-sample-apps."""
    try:
        import paramiko
    except ImportError:
        return False, 'paramiko not available'

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, f'Could not connect to Linux workstation to check imsdk dir: {err}'

    try:
        exists = _ssh_run(
            client,
            f'test -d {imsdk_dir}/gst-sample-apps && echo EXISTS || echo NOT_EXISTS'
        )
        if exists != 'EXISTS':
            return False, (
                f'gst-plugins-imsdk not found at {imsdk_dir} (missing gst-sample-apps/).'
            )
        ls_out = _ssh_run(client, f'ls {imsdk_dir}/gst-sample-apps 2>&1 | head -5')
        return True, f'{imsdk_dir}/gst-sample-apps/ found:\n       {ls_out.replace(chr(10), chr(10) + "       ")}'
    finally:
        client.close()


def check_linux_workstation_path_exists(host, user, key_path, path, password=None, timeout=10, port=22):
    """Verify a specific file exists on the workstation (e.g. LINUX_WORKSTATION_SDK_PATH).
    Wrapped in bash -c so it works regardless of the remote login shell (tcsh/sh/bash)."""
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return False, 'paramiko not available'

    client, err = _linux_workstation_client(host, user, key_path, password, timeout, port=port)
    if client is None:
        return False, f'Could not connect to workstation to check path: {err}'

    try:
        out = _ssh_run(client, f"bash -c \"test -f '{path}' && echo EXISTS || echo NOT_EXISTS\"")
        if 'EXISTS' in out and 'NOT_EXISTS' not in out:
            return True, f'{path} found on workstation'
        return False, f'{path} not found on workstation'
    finally:
        client.close()


# ── Main report ───────────────────────────────────────────────────────────────


def run(mode, device_ip, device_user, host_key,
        source_root, linux_workstation_host, linux_workstation_user,
        linux_workstation_key_path, linux_workstation_password,
        linux_workstation_build_dir, linux_workstation_port,
        artifact_path, output_dir,
        linux_workstation_imsdk_path=None,
        linux_workstation_sdk_path=None):

    requirements_path = pathlib.Path(__file__).parent / 'requirements.txt'
    all_ok = True
    lines = []

    def add(line): lines.append(line)
    def section(title): lines.extend(['', bold(title)])
    def stop():
        add('')
        add(f'{RED}{BOLD}  Stopping — resolve the FAIL above before continuing.{RESET}')
        add('')
        print('\n'.join(lines))
        sys.exit(1)

    add('')
    add(bold('=' * 64))
    add(bold(f'    qimsdk-deploy Pre-flight Check  (Mode {mode})'))
    add(bold('=' * 64))

    # ── 1. Python + deps ─────────────────────────────────────────────────────
    section('Environment')
    host_ok, host_msg = check_host_platform()
    add(ok(host_msg) if host_ok else fail(host_msg))
    if not host_ok:
        stop()

    py_ok, py_msg = check_python()
    add(ok(py_msg) if py_ok else fail(py_msg))
    if not py_ok:
        stop()

    req_ok, req_msg = install_requirements(requirements_path)
    add(ok(req_msg) if req_ok else fail(req_msg))
    if not req_ok:
        stop()

    imp_ok, imp_msg = check_imports()
    add(ok(imp_msg) if imp_ok else fail(imp_msg))
    if not imp_ok:
        stop()

    # ── 2. PuTTY tools (optional — deploy scripts use paramiko, not PuTTY) ─────
    if sys.platform == 'win32':
        section('PuTTY Tools (optional — deploy scripts use paramiko)')
        putty = check_putty()
        for tool, (found, path) in putty.items():
            add(ok(f'{tool:<8} {path}') if found
                else warn(f'{tool:<8} not found — not required for deploy (paramiko handles SSH/SCP). '
                          f'Only needed for manual plink/pscp commands.'))

    # ── 3. Local artifact path ────────────────────────────────────────────────
    section('Local Artifact')
    if artifact_path:
        art_ok, art_msg = check_local_artifact(artifact_path, mode)
        add(ok(art_msg) if art_ok else fail(art_msg))
        if not art_ok:
            all_ok = False
            stop()
    else:
        add(warn(
            'No artifact path provided — skipping artifact check. '
            'Pass --artifact-path <folder> to verify the artifact before deploying.'
        ))

    # ── 4. Local output directory ─────────────────────────────────────────────
    section('Local Output Directory')
    out_ok, out_msg = check_local_output_dir(output_dir, repo_root=_REPO_ROOT)
    add(ok(out_msg) if out_ok else fail(out_msg))
    if not out_ok:
        all_ok = False
        stop()

    # ── 5. Local C:/tmp/ writable (Mode C/D, Windows) ────────────────────────
    if mode in ('C', 'D') and sys.platform == 'win32':
        tmp_ok, tmp_msg = check_local_tmp_writable()
        add(ok(tmp_msg) if tmp_ok else fail(tmp_msg))
        if not tmp_ok:
            all_ok = False

    # ── 6. Device credentials ─────────────────────────────────────────────────
    section('Device Credentials')
    hk_result = check_host_key(host_key)
    hk_ok, hk_msg = hk_result[0], hk_result[1]
    hk_is_warn = len(hk_result) == 3 and hk_result[2] == 'warn'
    add(ok(hk_msg) if hk_ok else (warn(hk_msg) if hk_is_warn else fail(hk_msg)))
    if not hk_ok and not hk_is_warn:
        all_ok = False

    device_key = os.environ.get('DEVICE_KEY', '') or None
    device_password = os.environ.get('DEVICE_PASSWORD', '') or None
    auth_ok, auth_msg, auth_sev = check_device_auth_method(device_key, device_password, mode)
    add(ok(auth_msg) if auth_sev == 'ok' else (warn(auth_msg) if auth_sev == 'warn' else fail(auth_msg)))
    if not auth_ok:
        all_ok = False

    if not all_ok:
        stop()

    # ── 7. Device TCP + SSH login + ls ────────────────────────────────────────
    section('Device Network')
    tcp_ok, tcp_msg = check_tcp(device_ip)
    add(ok(tcp_msg) if tcp_ok else fail(tcp_msg))
    if not tcp_ok:
        stop()

    password = os.environ.get('DEVICE_PASSWORD', '') or None
    ssh_result = check_ssh_login(device_ip, device_user, password, host_key, key_path=device_key)
    ssh_ok, ssh_msg = ssh_result[0], ssh_result[1]
    device_info = ssh_result[2]
    key_warn = ssh_result[3] if len(ssh_result) == 4 else None
    add(ok(ssh_msg) if ssh_ok else fail(ssh_msg))
    if key_warn:
        add(warn(key_warn))
    if not ssh_ok:
        stop()

    # ── 8. Device info (show for user confirmation) ───────────────────────────
    add('')
    add(bold('Device Information — please confirm this is the correct device:'))
    for line in device_info:
        add(indent(line))
    add('')

    # ── 9. Mode-specific device checks via shared SSH client ─────────────────
    try:
        import paramiko
        _use_key = bool(device_key and pathlib.Path(device_key).expanduser().exists())
        device_client = paramiko.SSHClient()
        device_client.set_missing_host_key_policy(paramiko.WarningPolicy())
        device_client.connect(
            hostname=device_ip, username=device_user,
            key_filename=str(pathlib.Path(device_key).expanduser()) if _use_key else None,
            password=password if not _use_key else None,
            timeout=10, allow_agent=False, look_for_keys=False,
        )
    except Exception as e:
        add(fail(f'Could not open SSH session for device checks: {e}'))
        stop()

    try:
        # ── 9a. Device OS check (all modes) ──────────────────────────────────
        section('Device OS')
        expected_distro = 'ubuntu' if mode == 'B' else None
        os_ok, os_msg = check_device_os(device_client, expected_distro=expected_distro)
        add(ok(os_msg) if os_ok else fail(os_msg))
        if not os_ok:
            all_ok = False
            stop()

        if mode == 'B':
            section('Mode B — Device Build Environment')

            # SOURCE_ROOT is optional for Mode B — auto-discover via glob if not
            # set, same pattern deploy_mode_b.py/workspace_state.py use. Absence
            # of a source tree is informational (WARN), never a hard FAIL —
            # deploy_mode_b.py auto-provisions it from scratch on first deploy.
            if not source_root:
                glob_out = _ssh_run(
                    device_client,
                    "ls -d /home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-* 2>/dev/null | sort -V | tail -1"
                )
                source_root = glob_out.strip() or None

            for check_fn, args, is_warn in [
                (check_device_cmake,      (device_client,),                          False),
                (check_device_make,       (device_client,),                          False),
                (check_device_sudo,       (device_client, password),                 False),
                (check_device_timeout,    (device_client,),                          False),
                (check_device_git,        (device_client,),                          False),
                (check_device_network,    (device_client,),                          True),  # WARN
            ]:
                result = check_fn(*args)
                r_ok, r_msg = result[0], result[1]
                add(ok(r_msg) if r_ok else (warn(r_msg) if is_warn else fail(r_msg)))
                if not r_ok and not is_warn:
                    all_ok = False

            # source_root — informational: no source tree yet is fine, deploy
            # auto-provisions it. Only fail if a tree exists but build/ is broken.
            if not source_root:
                add(warn('QIMSDK source tree not found — will be auto-provisioned on first deploy (apt setup, cmake, build).'))
            else:
                build_dir_result = check_device_build_dir(device_client, source_root)
                bd_ok, bd_msg = build_dir_result[0], build_dir_result[1]
                add(ok(bd_msg) if bd_ok else warn(bd_msg + ' — cmake has not been run yet; deploy will configure it.'))

                src_result = check_source_root(device_client, source_root)
                src_ok, src_msg = src_result[0], src_result[1]
                src_is_warn = len(src_result) == 3 and src_result[2] == 'warn'
                add(ok(src_msg) if src_ok else (warn(src_msg) if src_is_warn else fail(src_msg)))
                if not src_ok and not src_is_warn:
                    all_ok = False

            # /usr/bin writable check — remount if needed
            usr_result = check_device_usr_bin_writable(device_client)
            usr_ok, usr_msg = usr_result[0], usr_result[1]
            is_usr_warn = len(usr_result) == 3 and usr_result[2] == 'warn'
            add(ok(usr_msg) if usr_ok and not is_usr_warn else (warn(usr_msg) if is_usr_warn else fail(usr_msg)))
            if not usr_ok:
                all_ok = False

            # output dir — mkdir -p
            odev_ok, odev_msg = check_device_output_dir(device_client)
            add(ok(odev_msg) if odev_ok else fail(odev_msg))
            if not odev_ok:
                all_ok = False

            # cam-server — WARN only
            cam_ok, cam_msg = check_device_cam_server(device_client)
            add(ok(cam_msg) if cam_ok else warn(cam_msg))

        elif mode == 'A':
            section('Mode A — Device Checks')

            t_ok, t_msg = check_device_timeout(device_client)
            add(ok(t_msg) if t_ok else fail(t_msg))
            if not t_ok:
                all_ok = False

            # gst-launch-1.0 — FAIL if missing (Mode A cannot work without it)
            gst_ok, gst_msg = check_device_gst_launch(device_client)
            add(ok(gst_msg) if gst_ok else fail(gst_msg))
            if not gst_ok:
                all_ok = False

            odev_ok, odev_msg = check_device_output_dir(device_client)
            add(ok(odev_msg) if odev_ok else fail(odev_msg))
            if not odev_ok:
                all_ok = False

            # disk space — WARN if low
            disk_result = check_device_disk_space(device_client)
            disk_ok, disk_msg = disk_result[0], disk_result[1]
            is_disk_warn = len(disk_result) == 3 and disk_result[2] == 'warn'
            add(ok(disk_msg) if disk_ok else (warn(disk_msg) if is_disk_warn else fail(disk_msg)))
            if not disk_ok and not is_disk_warn:
                all_ok = False

            # cam-server — WARN only
            cam_ok, cam_msg = check_device_cam_server(device_client)
            add(ok(cam_msg) if cam_ok else warn(cam_msg))

            # Artifact file/model check — only when artifact path was provided
            if artifact_path:
                section('Mode A — Artifact Files on Device')
                file_results = check_artifact_files_on_device(device_client, artifact_path)
                if not file_results:
                    add(warn('No input files or model paths found in pipeline.sh — verify manually.'))
                else:
                    for path, expanded, exists in file_results:
                        if exists:
                            add(ok(f'{expanded}'))
                        else:
                            add(fail(f'{expanded} — not found on device'))
                            all_ok = False

            # Suite readiness — only for diverse-io (needs camera, USB cam, Wayland)
            suite = os.environ.get('SUITE', '')
            deploy_mode = os.environ.get('DEPLOY_MODE', 'ubuntu').lower()
            if suite == 'diverse-io':
                section('Suite Readiness — diverse-io')

                plug_ok, plug_msg = check_device_camera_plugin(device_client, deploy_mode)
                add(ok(plug_msg) if plug_ok else fail(plug_msg))
                if not plug_ok:
                    all_ok = False

                usb_ok, usb_msg = check_device_usb_camera(device_client)
                add(ok(usb_msg) if usb_ok else fail(usb_msg))
                if not usb_ok:
                    all_ok = False

                wl_ok, wl_msg = check_device_wayland(device_client)
                add(ok(wl_msg) if wl_ok else fail(wl_msg))
                if not wl_ok:
                    all_ok = False

        elif mode == 'C':
            section('Mode C — Device Checks')

            t_ok, t_msg = check_device_timeout(device_client)
            add(ok(t_msg) if t_ok else fail(t_msg))
            if not t_ok:
                all_ok = False

            usr_result = check_device_usr_bin_writable(device_client)
            usr_ok, usr_msg = usr_result[0], usr_result[1]
            is_usr_warn = len(usr_result) == 3 and usr_result[2] == 'warn'
            add(ok(usr_msg) if usr_ok and not is_usr_warn else (warn(usr_msg) if is_usr_warn else fail(usr_msg)))
            if not usr_ok:
                all_ok = False

            odev_ok, odev_msg = check_device_output_dir(device_client)
            add(ok(odev_msg) if odev_ok else fail(odev_msg))
            if not odev_ok:
                all_ok = False

        elif mode == 'D':
            section('Mode D — Device Checks (C++ SDK app)')

            t_ok, t_msg = check_device_timeout(device_client)
            add(ok(t_msg) if t_ok else fail(t_msg))
            if not t_ok:
                all_ok = False

            usr_result = check_device_usr_bin_writable(device_client)
            usr_ok, usr_msg = usr_result[0], usr_result[1]
            is_usr_warn = len(usr_result) == 3 and usr_result[2] == 'warn'
            add(ok(usr_msg) if usr_ok and not is_usr_warn else (warn(usr_msg) if is_usr_warn else fail(usr_msg)))
            if not usr_ok:
                all_ok = False

            odev_ok, odev_msg = check_device_output_dir(device_client)
            add(ok(odev_msg) if odev_ok else fail(odev_msg))
            if not odev_ok:
                all_ok = False

            # Runtime lib: a cpp-app-builder binary dynamically links libqtiimsdk.so.1.
            # If it's absent the binary won't start — WARN (device-provisioning issue,
            # not a build problem) so the user can install the C++ IMSDK runtime.
            lib_out = _ssh_run(
                device_client,
                'ls /usr/lib/libqtiimsdk.so* 2>/dev/null | head -1; '
                'find / -name "libqtiimsdk.so*" 2>/dev/null | head -1'
            )
            if 'libqtiimsdk.so' in lib_out:
                add(ok(f'libqtiimsdk runtime present: {lib_out.strip().splitlines()[0]}'))
            else:
                add(warn(
                    'libqtiimsdk.so not found on device — a Mode D C++ binary links it at '
                    'runtime and will fail to start until the C++ IMSDK runtime library is '
                    'installed on the device (or shipped alongside the binary).'
                ))

            # cam-server — WARN only (restarted before every camera run anyway)
            cam_ok, cam_msg = check_device_cam_server(device_client)
            add(ok(cam_msg) if cam_ok else warn(cam_msg))

        elif mode == 'P':
            section('Mode P — Python App Device Checks')

            # python3 installed — FAIL if missing
            py_out = _ssh_run(device_client, 'python3 --version 2>&1')
            py_ok = 'Python' in py_out and '3.' in py_out
            add(ok(f'python3: {py_out.strip()}') if py_ok
                else fail('python3 not found on device. Install Python 3.'))
            if not py_ok:
                all_ok = False

            # qimsdk importable — FAIL if missing (no auto-install for Mode P)
            # Check the LAST line only, not a substring anywhere in the output: on
            # ModuleNotFoundError, Python's traceback echoes the failing source line
            # (which contains the same 'qimsdk ok' marker text as source, not output),
            # so a bare substring check false-positives on the traceback itself.
            qimsdk_out = _ssh_run(device_client,
                'python3 -c "from qimsdk import Pipeline; print(\'qimsdk ok\')" 2>&1')
            qimsdk_last_line = qimsdk_out.strip().splitlines()[-1].strip() if qimsdk_out.strip() else ''
            qimsdk_ok = qimsdk_last_line == 'qimsdk ok'
            add(ok('qimsdk (Pipeline) importable') if qimsdk_ok
                else fail(
                    f'qimsdk not importable: {qimsdk_out.strip()[:120]}\n'
                    '       Install the QIM SDK Python package on the device before deploying.\n'
                    '       See device setup documentation for installation steps.'
                ))
            if not qimsdk_ok:
                all_ok = False

            # timeout command
            t_ok, t_msg = check_device_timeout(device_client)
            add(ok(t_msg) if t_ok else fail(t_msg))
            if not t_ok:
                all_ok = False

            # output dir writable
            odev_ok, odev_msg = check_device_output_dir(device_client)
            add(ok(odev_msg) if odev_ok else fail(odev_msg))
            if not odev_ok:
                all_ok = False

            # disk space — WARN
            disk_result = check_device_disk_space(device_client)
            disk_ok, disk_msg = disk_result[0], disk_result[1]
            is_disk_warn = len(disk_result) == 3 and disk_result[2] == 'warn'
            add(ok(disk_msg) if disk_ok else (warn(disk_msg) if is_disk_warn else fail(disk_msg)))
            if not disk_ok and not is_disk_warn:
                all_ok = False

            # cam-server — WARN only
            cam_ok, cam_msg = check_device_cam_server(device_client)
            add(ok(cam_msg) if cam_ok else warn(cam_msg))

            # Artifact input files on device (if artifact path provided)
            if artifact_path:
                section('Mode P — Artifact Files on Device')
                # Parse the app entry point for file paths (main.py primary, app.py legacy fallback)
                art = pathlib.Path(artifact_path)
                app_py = art / 'main.py'
                if not app_py.exists():
                    app_py = art / 'app.py'
                readme_md = art / 'README.md'
                input_paths = []
                if app_py.exists():
                    app_text = app_py.read_text(encoding='utf-8', errors='replace')
                    # Extract string literals that look like absolute device paths
                    for m in re.finditer(r'["\'](/(?:home/\w+|root)/[^"\']+)["\']', app_text):
                        p = m.group(1)
                        # Only include paths that look like input files (models, labels, video)
                        if any(p.endswith(ext) for ext in ('.tflite', '.onnx', '.json', '.mp4', '.h264', '.yuv')):
                            if p not in input_paths:
                                input_paths.append(p)
                if not input_paths and readme_md.exists():
                    # Fallback: parse README paths that are NOT output paths
                    readme = readme_md.read_text(encoding='utf-8', errors='replace')
                    for line in readme.splitlines():
                        if re.search(r'output|OUTPUT', line):
                            continue
                        for m in re.finditer(r'`(/(?:home/\w+|root)/[^`]+)`', line):
                            p = m.group(1)
                            if p not in input_paths:
                                input_paths.append(p)
                if not input_paths:
                    add(warn(f'No input file paths found in {app_py.name} — verify input files exist manually.'))
                else:
                    for path in input_paths:
                        expanded = _ssh_run(device_client, f'eval echo {path}').strip() or path
                        exists = _ssh_run(device_client, f'test -f "{expanded}" && echo EXISTS || echo MISSING').strip() == 'EXISTS'
                        if exists:
                            add(ok(f'{expanded}'))
                        else:
                            add(fail(f'{expanded} — not found on device'))
                            all_ok = False

    finally:
        device_client.close()

    # ── 10. Mode C — Linux workstation checks ────────────────────────────────────────
    if mode == 'C':
        section('Mode C — Linux workstation')

        if not linux_workstation_host:
            add(fail('--linux-workstation-host is required for Mode C.'))
            all_ok = False

        # Linux workstation auth method check
        dc_key = os.environ.get('LINUX_WORKSTATION_KEY', '') or None
        dc_password = os.environ.get('LINUX_WORKSTATION_PASSWORD', '') or None
        dc_auth_ok, dc_auth_msg, dc_auth_sev = check_linux_workstation_auth_method(dc_key, dc_password)
        add(ok(dc_auth_msg) if dc_auth_sev == 'ok' else (warn(dc_auth_msg) if dc_auth_sev == 'warn' else fail(dc_auth_msg)))
        if not dc_auth_ok:
            all_ok = False
            stop()
        else:
            dc_tcp_ok, dc_tcp_msg = check_tcp(linux_workstation_host, port=linux_workstation_port)
            add(ok(dc_tcp_msg) if dc_tcp_ok else fail(dc_tcp_msg))
            if not dc_tcp_ok:
                all_ok = False
            else:
                dc_ssh_ok, dc_ssh_msg, dc_ls = check_linux_workstation_ssh(
                    linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                    password=linux_workstation_password, port=linux_workstation_port
                )
                add(ok(dc_ssh_msg) if dc_ssh_ok else fail(dc_ssh_msg))
                if not dc_ssh_ok:
                    all_ok = False
                else:
                    add(bold('  Linux workstation home directory:'))
                    for line in dc_ls:
                        add(indent(line))
                    add('')

                    sdk_ok, sdk_msg = check_sdk_env(
                        linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                        linux_workstation_build_dir, password=linux_workstation_password,
                        port=linux_workstation_port
                    )
                    # SDK not found is INFO not FAIL — deploy auto-provisions from state 0
                    add(ok(sdk_msg) if sdk_ok else warn(sdk_msg + ' — will be auto-provisioned on first deploy (download + install)'))

                    disk_result = check_linux_workstation_disk(
                        linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                        build_dir=linux_workstation_build_dir,
                        password=linux_workstation_password,
                        port=linux_workstation_port
                    )
                    disk_ok, disk_msg = disk_result[0], disk_result[1]
                    is_warn = len(disk_result) == 3 and disk_result[2] == 'warn'
                    add(ok(disk_msg) if disk_ok else (warn(disk_msg) if is_warn else fail(disk_msg)))
                    if not disk_ok and not is_warn:
                        all_ok = False

                    # imsdk_dir: LINUX_WORKSTATION_IMSDK_PATH overrides the derived default
                    derived_imsdk = linux_workstation_imsdk_path.rstrip('/') if linux_workstation_imsdk_path \
                        else (f'{linux_workstation_build_dir}/gst-plugins-imsdk' if linux_workstation_build_dir else None)
                    if derived_imsdk:
                        imsdk_ok, imsdk_msg = check_linux_workstation_imsdk_dir(
                            linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                            derived_imsdk, password=linux_workstation_password,
                            port=linux_workstation_port
                        )
                        if imsdk_ok:
                            add(ok(imsdk_msg))
                        elif linux_workstation_imsdk_path:
                            # User pointed at a specific path — missing repo there is a real
                            # problem, not something deploy will silently fix by cloning.
                            add(fail(imsdk_msg + f' — LINUX_WORKSTATION_IMSDK_PATH is set to '
                                                  f'{linux_workstation_imsdk_path}; check the path is correct.'))
                            all_ok = False
                        else:
                            # No override — imsdk not found is INFO not FAIL — deploy auto-provisions via git clone
                            add(warn(imsdk_msg + ' — will be auto-provisioned on first deploy (git clone)'))

    # ── 11. Mode D — workstation checks (Yocto SDK, standalone C++ build) ─────────
    if mode == 'D':
        section('Mode D — Linux/WSL workstation (Yocto SDK)')

        if not linux_workstation_host:
            add(fail('--linux-workstation-host is required for Mode D.'))
            all_ok = False

        dc_key = os.environ.get('LINUX_WORKSTATION_KEY', '') or None
        dc_password = os.environ.get('LINUX_WORKSTATION_PASSWORD', '') or None
        dc_auth_ok, dc_auth_msg, dc_auth_sev = check_linux_workstation_auth_method(dc_key, dc_password)
        add(ok(dc_auth_msg) if dc_auth_sev == 'ok' else (warn(dc_auth_msg) if dc_auth_sev == 'warn' else fail(dc_auth_msg)))
        if not dc_auth_ok:
            all_ok = False
            stop()
        else:
            dc_tcp_ok, dc_tcp_msg = check_tcp(linux_workstation_host, port=linux_workstation_port)
            add(ok(dc_tcp_msg) if dc_tcp_ok else fail(dc_tcp_msg))
            if not dc_tcp_ok:
                all_ok = False
            else:
                dc_ssh_ok, dc_ssh_msg, dc_ls = check_linux_workstation_ssh(
                    linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                    password=linux_workstation_password, port=linux_workstation_port
                )
                add(ok(dc_ssh_msg) if dc_ssh_ok else fail(dc_ssh_msg))
                if not dc_ssh_ok:
                    all_ok = False
                else:
                    add(bold('  Workstation home directory:'))
                    for line in dc_ls:
                        add(indent(line))
                    add('')

                    # Yocto SDK + sysroot qtiimsdk resolvability.
                    sdk_ok, sdk_msg, sdk_sev = check_qimsdk_sdk_env(
                        linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                        linux_workstation_build_dir, password=linux_workstation_password,
                        port=linux_workstation_port
                    )
                    if sdk_sev == 'ok':
                        add(ok(sdk_msg))
                    elif sdk_sev == 'warn':
                        # Not installed yet → auto-provisioned on first deploy. Where it
                        # comes from depends on LINUX_WORKSTATION_SDK_PATH / _SDK_URL.
                        if linux_workstation_sdk_path:
                            # User pointed at a specific installer — verify it exists there.
                            path_ok, path_msg = check_linux_workstation_path_exists(
                                linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                                linux_workstation_sdk_path, password=linux_workstation_password,
                                port=linux_workstation_port
                            )
                            if path_ok:
                                add(warn(sdk_msg + f' — will be installed on first deploy from '
                                                   f'LINUX_WORKSTATION_SDK_PATH ({linux_workstation_sdk_path})'))
                            else:
                                add(fail(sdk_msg + f' — LINUX_WORKSTATION_SDK_PATH is set to '
                                                   f'{linux_workstation_sdk_path} but that file was not found '
                                                   f'on the workstation; check the path is correct.'))
                                all_ok = False
                        else:
                            add(warn(sdk_msg + ' — will be auto-provisioned on first deploy (unzip + install)'))
                    else:
                        add(fail(sdk_msg))
                        all_ok = False

                    disk_result = check_linux_workstation_disk(
                        linux_workstation_host, linux_workstation_user, linux_workstation_key_path,
                        build_dir=linux_workstation_build_dir,
                        password=linux_workstation_password,
                        port=linux_workstation_port
                    )
                    disk_ok, disk_msg = disk_result[0], disk_result[1]
                    is_warn = len(disk_result) == 3 and disk_result[2] == 'warn'
                    add(ok(disk_msg) if disk_ok else (warn(disk_msg) if is_warn else fail(disk_msg)))
                    if not disk_ok and not is_warn:
                        all_ok = False

    # ── Summary ───────────────────────────────────────────────────────────────
    add('')
    add(bold('-' * 64))
    if all_ok:
        add(f'{GREEN}{BOLD}  All checks passed. Ready to deploy.{RESET}')
    else:
        add(f'{RED}{BOLD}  One or more checks FAILED. Resolve the issues above and re-run.{RESET}')
    add(bold('-' * 64))
    add('')

    print('\n'.join(lines))
    return 0 if all_ok else 1


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='qimsdk-deploy pre-flight check')
    parser.add_argument('--mode', required=True, choices=['A', 'B', 'C', 'P', 'D'],
                        help='Deploy mode: A (gst-launch), B (Ubuntu on-device build), C (host build, '
                             'gstreamer-app-builder), P (Python qimsdk app), D (host build, cpp-app-builder)')
    parser.add_argument('--device-ip',   default=os.environ.get('DEVICE_IP', ''),
                        help='Device IP (or DEVICE_IP in configs/.env)')
    parser.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''),
                        help='Device SSH user (or DEVICE_USER in configs/.env)')
    parser.add_argument('--host-key',    default=os.environ.get('HOST_KEY', ''),
                        help='SSH host key fingerprint (or HOST_KEY in configs/.env)')
    parser.add_argument('--source-root', default=os.environ.get('SOURCE_ROOT', ''),
                        help='Mode B: QIMSDK source root on device (or SOURCE_ROOT in configs/.env)')
    parser.add_argument('--linux-workstation-host', default=os.environ.get('LINUX_WORKSTATION_HOST', ''),
                        help='Mode C/D: Linux/WSL workstation hostname or IP')
    parser.add_argument('--linux-workstation-user', default=os.environ.get('LINUX_WORKSTATION_USER', ''),
                        help='Mode C/D: Linux/WSL workstation SSH user')
    parser.add_argument('--linux-workstation-key',  default=os.environ.get('LINUX_WORKSTATION_KEY', ''),
                        help='Mode C/D: SSH key path for the workstation (optional if LINUX_WORKSTATION_PASSWORD set)')
    parser.add_argument('--linux-workstation-password', default=os.environ.get('LINUX_WORKSTATION_PASSWORD', ''),
                        help='Mode C/D: SSH password for the workstation (LINUX_WORKSTATION_PASSWORD in configs/.env)')
    parser.add_argument('--linux-workstation-build-dir',
                        default=os.environ.get('LINUX_WORKSTATION_BUILD_DIR', ''),
                        help='Mode C/D: build/workspace dir on the workstation (LINUX_WORKSTATION_BUILD_DIR)')
    parser.add_argument('--linux-workstation-imsdk-path',
                        default=os.environ.get('LINUX_WORKSTATION_IMSDK_PATH', ''),
                        help='Mode C: path on the workstation to an existing gst-plugins-imsdk clone '
                             '(LINUX_WORKSTATION_IMSDK_PATH). If unset, checks the default derived location.')
    parser.add_argument('--linux-workstation-sdk-path',
                        default=os.environ.get('LINUX_WORKSTATION_SDK_PATH', ''),
                        help='Mode C/D: path on the workstation to an SDK installer already present — '
                             '.zip or .sh (LINUX_WORKSTATION_SDK_PATH). If unset, SDK is auto-provisioned.')
    parser.add_argument('--linux-workstation-port',
                        default=int(os.environ.get('LINUX_WORKSTATION_PORT', '22') or '22'),
                        type=int,
                        help='Mode C/D: SSH port on the workstation (LINUX_WORKSTATION_PORT, default 22; WSL typically 2222)')
    parser.add_argument('--artifact-path', default=os.environ.get('ARTIFACT_PATH', ''),
                        help='Optional: path to local artifact folder to verify before deploy')
    parser.add_argument('--output-dir',  default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                        help='Local directory for pulled logs and output files (or DEPLOY_OUTPUT_DIR in configs/.env)')
    args = parser.parse_args()

    # ── Mandatory field checks — fail immediately with exact fix instruction ───
    errors = []
    if not args.device_ip:
        errors.append('DEVICE_IP is not set.')
    if not args.device_user:
        errors.append('DEVICE_USER is not set.')
    if not os.environ.get('DEVICE_PASSWORD', '') and not os.environ.get('DEVICE_KEY', ''):
        errors.append('No device auth — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env.')
    if not args.output_dir:
        errors.append('DEPLOY_OUTPUT_DIR is not set.')
    if args.mode in ('C', 'D') and not args.linux_workstation_host:
        errors.append(f'LINUX_WORKSTATION_HOST is not set (required for Mode {args.mode}).')
    if args.mode in ('C', 'D') and not (os.environ.get('LINUX_WORKSTATION_PASSWORD','') or args.linux_workstation_key):
        errors.append(f'LINUX_WORKSTATION_PASSWORD or LINUX_WORKSTATION_KEY is not set (required for Mode {args.mode}).')
    if args.mode in ('C', 'D') and not args.linux_workstation_build_dir:
        errors.append(f'LINUX_WORKSTATION_BUILD_DIR is not set (required for Mode {args.mode}).')
    if args.mode in ('C', 'D') and args.linux_workstation_build_dir and not args.linux_workstation_build_dir.startswith('/'):
        errors.append(f'LINUX_WORKSTATION_BUILD_DIR must be an absolute path starting with / — got: {args.linux_workstation_build_dir}')

    if errors:
        print(f'\n{RED}{BOLD}  Cannot run preflight — required config is missing:{RESET}')
        for e in errors:
            print(f'  {RED}[FAIL]{RESET}  {e}')
        print(f'\n  Create or update configs/.env in the repo root. See SKILL.md for the full template.\n')
        sys.exit(1)

    sys.exit(run(
        mode=args.mode,
        device_ip=args.device_ip,
        device_user=args.device_user,
        host_key=args.host_key,
        source_root=args.source_root,
        linux_workstation_host=args.linux_workstation_host,
        linux_workstation_user=args.linux_workstation_user,
        linux_workstation_key_path=args.linux_workstation_key,
        linux_workstation_password=args.linux_workstation_password or os.environ.get('LINUX_WORKSTATION_PASSWORD', '') or None,
        linux_workstation_build_dir=args.linux_workstation_build_dir,
        linux_workstation_port=args.linux_workstation_port,
        artifact_path=args.artifact_path,
        output_dir=args.output_dir,
        linux_workstation_imsdk_path=args.linux_workstation_imsdk_path or None,
        linux_workstation_sdk_path=args.linux_workstation_sdk_path or None,
    ))
