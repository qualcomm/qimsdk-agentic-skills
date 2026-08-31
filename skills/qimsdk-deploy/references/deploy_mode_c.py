#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
deploy_mode_c.py — Host-build a C app artifact on a Linux workstation,
deploy the ARM64 binary to a QLI/Yocto device, and run it.

Mode C uses TWO SSH connections:
  - ssh_dc  : Paramiko key-auth connection to Linux workstation (Linux x86_64)
  - ssh_dev : Paramiko password-auth connection to QLI device (same as Mode A/B)

Usage:
  python3 deploy_mode_c.py \\
      --artifact-path outputs/qimsdk-gstreamer-dev/c-app/gst-qimsdk-object-detection

Credentials from configs/.env (DEVICE_IP, DEVICE_USER, DEVICE_PASSWORD, HOST_KEY,
LINUX_WORKSTATION_HOST, LINUX_WORKSTATION_USER, LINUX_WORKSTATION_BUILD_DIR).
See SKILL.md for the full .env template.

Output per artifact:
  <DEPLOY_OUTPUT_DIR>/<artifact-name>/
  ├── device.log    — full app stdout/stderr (always written)
  ├── build.log     — cmake + cross-build output (always written if build attempted)
  ├── result.json   — structured result dict (always written)
  └── output.<ext>  — pulled output file    (if app has a filesink)

Exits 0 if build passed, PLAYING reached and no real errors, 1 otherwise.

What this script does NOT do:
  - Diagnose why a build or run failed
  - Suggest code fixes or CMake changes
  - Retry failed builds or runs
  - Modify source files before pushing
  Those are the user's responsibility. This script host-builds, deploys, runs, reports, and pulls.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import pathlib
import posixpath
import re
import shlex
import sys
import threading
import time
import warnings

# Force UTF-8 stdout/stderr on Windows (cp1252 default rejects box-drawing and non-ASCII chars).
# Only do this when running as __main__ — replacing sys.stdout at import time causes
# ValueError('I/O operation on closed file') at CPython shutdown when used as a module.
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure workspace_setup_c and workspace_state (same directory) are importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


# ── Device path constants ──────────────────────────────────────────────────────
_SAMPLE_APPS_SUBDIR = 'gst-sample-apps'
_INSTALL_BINDIR     = '/usr/bin'
# Output dir is derived from the artifact's parsed output_path at deploy time.
# This fallback is used only when no output path is found in the artifact README.
_OUTPUT_MEDIA_DIR   = '/root/Downloads/qimsdk_samples/media/output'


# ── .env loader ───────────────────────────────────────────────────────────────

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

REPO_ROOT = _load_dotenv()


# ── Benign GStreamer log noise — never counted as real errors ──────────────────
# These appear in normal healthy runs. Reporting them as errors would be misleading.

_BENIGN = [
    'MapGbmBufInfoAddress: Mmap failed',
    'Failed to initialize Wayland EGL display',  # harmless if no display
    'Failed to initialize X11 EGL display',
    'SetupXcbConnection: Failed to get xcb connection',
    'Initialize: Failed to setup xcb connection',
    'tiling.h WARNING',
    'concat_opts WARNING',
    'Internal data stream error',   # normal with mp4mux + gst-launch -e flag at EOS
    'Got EOS from element',
    'MESA-LOADER: failed to retrieve device information',
    'failed to get driver name',
    'Failed to set RPC polling time',    # benign QNN HTP init noise on multi-stream pipelines
    'Failed to set rpc polling',         # same
    'Failed to set powerConfig',         # same
    'bo cpu address failed',             # benign GEM/DMA noise on Qualcomm devices
    'GEM Handle for BO=',
]


# ── SSH session (device — key or password auth) ───────────────────────────────

class _SSH:
    """
    Minimal Paramiko SSH wrapper for deploy operations.
    Verifies host key fingerprint on connect.
    Auth priority: key_path (if set and file exists) → password → fail.
    All commands are run through run(); all file transfers through push()/pull().
    """

    def __init__(self, ip, user, host_key_fp, password=None, key_path=None):
        self.ip          = ip
        self.user        = user
        self.password    = password
        self.key_path    = str(pathlib.Path(key_path).expanduser()) if key_path else None
        self.host_key_fp = (host_key_fp or '').strip()
        self._client     = None
        self._sftp       = None

    def connect(self):
        try:
            import paramiko
            warnings.filterwarnings('ignore', category=UserWarning, module='paramiko')
        except ImportError:
            raise RuntimeError('paramiko not installed. Run: pip install paramiko')

        if self._client and self._client.get_transport() and \
                self._client.get_transport().is_active():
            return

        use_key = bool(self.key_path and pathlib.Path(self.key_path).exists())
        if use_key and sys.platform != 'win32':
            key_stat = pathlib.Path(self.key_path).stat()
            key_mode = key_stat.st_mode & 0o777
            if key_mode & 0o077:  # world or group readable/writable
                raise RuntimeError(
                    f'SSH key {self.key_path} has insecure permissions {oct(key_mode)}. '
                    f'Fix with: chmod 600 "{self.key_path}"'
                )
        if not use_key and not self.password:
            raise RuntimeError(
                'No SSH auth configured — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env\n'
                '  See ssh-setup.md for setup instructions.'
            )

        expected_fp = self.host_key_fp

        class _FPPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = 'SHA256:' + base64.b64encode(
                    hashlib.sha256(key.asbytes()).digest()
                ).rstrip(b'=').decode()
                if expected_fp and actual != expected_fp:
                    raise paramiko.SSHException(
                        f'Host key mismatch for {hostname}: '
                        f'got {actual}, expected {expected_fp}.'
                    )

        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_FPPolicy())
        try:
            client.connect(
                self.ip, username=self.user,
                key_filename=self.key_path if use_key else None,
                password=self.password if not use_key else None,
                timeout=15, banner_timeout=20, auth_timeout=15,
                allow_agent=False, look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            auth_type = 'key' if use_key else 'password'
            fallback_hint = (
                ' Try password auth: set DEVICE_PASSWORD and remove DEVICE_KEY.'
                if use_key else
                ' Try key auth: set DEVICE_KEY — see ssh-setup.md.'
            )
            raise RuntimeError(
                f'SSH {auth_type} authentication failed for {self.user}@{self.ip}.{fallback_hint}'
            )
        self._client = client
        self._sftp   = client.open_sftp()
        auth_label = f'key ({pathlib.Path(self.key_path).name})' if use_key else 'password'
        print(f'  [ssh]  Connected to {self.user}@{self.ip} ({auth_label})', flush=True)

    def close(self):
        if self._sftp:
            try: self._sftp.close()
            except Exception: pass
            self._sftp = None
        if self._client:
            try: self._client.close()
            except Exception: pass
            self._client = None

    def run(self, cmd, timeout=30):
        """
        Run a command. Returns (stdout_str, stderr_str, exit_code).

        Uses a recv() polling loop with a wall-clock deadline. This handles
        long-running commands (e.g. a cmake run) correctly — blocking
        stdout.read() with a per-read timeout drops output on long runs.
        """
        self.connect()
        stdin, stdout, stderr = self._client.exec_command(cmd)
        channel = stdout.channel

        deadline = time.time() + timeout
        chunks = []
        while True:
            if channel.recv_ready():
                data = channel.recv(65536)
                if data:
                    chunks.append(data)
                    continue
            if channel.closed and not channel.recv_ready():
                break
            if time.time() > deadline:
                channel.close()
                break
            time.sleep(0.05)

        out = b''.join(chunks).decode('utf-8', errors='replace').strip()

        err_chunks = []
        while channel.recv_stderr_ready():
            data = channel.recv_stderr(65536)
            if data:
                err_chunks.append(data)
        err = b''.join(err_chunks).decode('utf-8', errors='replace').strip()

        rc = channel.recv_exit_status()
        return out, err, rc

    def push_bytes(self, content_bytes, remote_path):
        """Push bytes directly to a remote path via SFTP (no temp file needed)."""
        self.connect()
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p '{remote_dir}'")
        with self._sftp.open(remote_path, 'wb') as f:
            f.write(content_bytes)

    def push(self, local_path, remote_path):
        """Push a local file to device via SFTP."""
        self.connect()
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p '{remote_dir}'")
        lp = local_path
        if sys.platform == 'win32' and not str(lp).startswith('\\\\?\\'):
            lp = '\\\\?\\' + os.path.abspath(str(lp))
        self._sftp.put(str(lp), remote_path)

    def pull(self, remote_path, local_path):
        """Pull a file from device to local via SFTP."""
        self.connect()
        local_path = pathlib.Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        lp = str(local_path)
        if sys.platform == 'win32' and not lp.startswith('\\\\?\\'):
            lp = '\\\\?\\' + os.path.abspath(lp)
        self._sftp.get(remote_path, lp)

    def pull_to_bytes(self, remote_path):
        """Pull a remote file and return its content as bytes."""
        self.connect()
        buf = io.BytesIO()
        self._sftp.getfo(remote_path, buf)
        return buf.getvalue()

    def push_bytes_to(self, content_bytes, remote_path):
        """Push bytes to remote path (alias for push_bytes with clearer name)."""
        self.push_bytes(content_bytes, remote_path)

    def file_size(self, remote_path):
        """Return remote file size in bytes, or 0 if missing/empty/inaccessible."""
        out, _, _ = self.run(f"stat -c%s '{remote_path}' 2>/dev/null || echo 0")
        try:
            return int(out.strip())
        except ValueError:
            return 0


# ── SSH session (key auth, Linux workstation) ────────────────────────────────────────

class _SSH_Key:
    """Paramiko SSH wrapper for Linux workstation — supports key auth or password auth."""
    def __init__(self, host, user, key_path=None, password=None, port=22):
        self.host = host
        self.user = user
        self.port = int(port) if port else 22
        self.key_path = pathlib.Path(key_path).expanduser() if key_path else None
        self.password = password
        self._client = None
        self._sftp = None

    def connect(self):
        import paramiko
        warnings.filterwarnings('ignore', category=UserWarning, module='paramiko')
        if self._client and self._client.get_transport() and \
                self._client.get_transport().is_active():
            return
        client = paramiko.SSHClient()
        # Log the Linux workstation host key fingerprint on first connect so users can
        # verify it matches their known workstation key.
        _seen_fp = []

        class _LogFPPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, c, hostname, key):
                import base64, hashlib
                fp = 'SHA256:' + base64.b64encode(
                    hashlib.sha256(key.asbytes()).digest()
                ).rstrip(b'=').decode()
                _seen_fp.append(fp)
                c.get_host_keys().add(hostname, key.get_name(), key)

        client.set_missing_host_key_policy(_LogFPPolicy())
        # Prefer key auth if key file exists, fall back to password
        use_key = self.key_path and self.key_path.exists()
        if not use_key and not self.password:
            raise RuntimeError(
                f'No auth available for Linux workstation {self.host}: '
                f'key not found at {self.key_path} and LINUX_WORKSTATION_PASSWORD not set.\n'
                '  See ssh-setup.md Step C'
            )
        try:
            client.connect(
                self.host, port=self.port, username=self.user,
                key_filename=str(self.key_path) if use_key else None,
                password=self.password if not use_key else None,
                timeout=15, banner_timeout=20, auth_timeout=15,
                allow_agent=False, look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            auth_type = 'key' if use_key else 'password'
            fallback_hint = (
                ' Try password: set LINUX_WORKSTATION_PASSWORD.'
                if use_key else
                ' Try key auth: set LINUX_WORKSTATION_KEY — see ssh-setup.md Step C.'
            )
            raise RuntimeError(
                f'SSH {auth_type} authentication failed for {self.user}@{self.host}:{self.port}.{fallback_hint}'
            )
        self._client = client
        self._sftp = client.open_sftp()
        auth_label = f'key ({self.key_path.name})' if use_key else 'password'
        fp_note = f' [host key: {_seen_fp[0]}]' if _seen_fp else ''
        print(f'  [ssh-dc] Connected to {self.user}@{self.host}:{self.port} ({auth_label}){fp_note}', flush=True)

    def close(self):
        if self._sftp:
            try: self._sftp.close()
            except Exception: pass
            self._sftp = None
        if self._client:
            try: self._client.close()
            except Exception: pass
            self._client = None

    def run(self, cmd, timeout=30):
        """
        Run command on Linux workstation. Returns (stdout+stderr, '', exit_code).

        Combines stdout and stderr into the first return value so callers
        always get complete output regardless of which fd the remote command
        writes to (git uses stderr, most others use stdout). The second
        return value is always '' for API compatibility with _SSH.run().
        """
        self.connect()
        stdin, stdout, stderr = self._client.exec_command(cmd)
        stdout.channel.settimeout(None)  # blocking — no per-recv deadline
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        rc  = stdout.channel.recv_exit_status()
        return (out + err).strip(), '', rc

    def push_bytes(self, content_bytes, remote_path):
        self.connect()
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p '{remote_dir}'")
        with self._sftp.open(remote_path, 'wb') as f:
            f.write(content_bytes)

    def push(self, local_path, remote_path):
        self.connect()
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p '{remote_dir}'")
        lp = local_path
        if sys.platform == 'win32' and not str(lp).startswith('\\\\?\\'):
            lp = '\\\\?\\' + os.path.abspath(str(lp))
        self._sftp.put(str(lp), remote_path)

    def pull(self, remote_path, local_path):
        self.connect()
        local_path = pathlib.Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        lp = str(local_path)
        if sys.platform == 'win32' and not lp.startswith('\\\\?\\'):
            lp = '\\\\?\\' + os.path.abspath(lp)
        self._sftp.get(remote_path, lp)

    def pull_to_bytes(self, remote_path):
        self.connect()
        import io as _io
        buf = _io.BytesIO()
        self._sftp.getfo(remote_path, buf)
        return buf.getvalue()

    def file_size(self, remote_path):
        out, _, _ = self.run(f"stat -c%s '{remote_path}' 2>/dev/null || echo 0")
        try:
            return int(out.strip())
        except ValueError:
            return 0


# ── Artifact metadata parsing ─────────────────────────────────────────────────

def _parse_artifact(artifact_path):
    """
    Parse CMakeLists.txt and README.md from artifact folder locally.
    Returns dict with: binary_name, source_type, output_path, output_paths, rtsp_out.
    Raises ValueError with exact message if binary name cannot be extracted.
    """
    art = pathlib.Path(artifact_path)
    cmake_file = art / 'CMakeLists.txt'
    readme_file = art / 'README.md'

    if not cmake_file.exists():
        raise ValueError(f'CMakeLists.txt not found in {artifact_path}')

    cmake_text = cmake_file.read_text(encoding='utf-8', errors='replace')

    # Extract binary name from: set(GST_EXAMPLE_BIN <name>)
    m = re.search(r'set\s*\(\s*GST_EXAMPLE_BIN\s+(\S+)\s*\)', cmake_text)
    if not m:
        raise ValueError(
            f'GST_EXAMPLE_BIN not found in CMakeLists.txt — '
            f'expected: set(GST_EXAMPLE_BIN <name>)'
        )
    binary_name = m.group(1)

    # Source type: prefer main.c (authoritative source element); fall back to the
    # README keyword only when main.c names no recognizable source (avoids a
    # false "camera" match on phrases like "no camera required").
    source_type = 'file-source'

    main_c = art / 'main.c'
    main_text = main_c.read_text(encoding='utf-8', errors='replace') if main_c.exists() else ''
    main_has_camera = bool(re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b', main_text))
    main_has_known_source = main_has_camera or bool(
        re.search(r'\bvideotestsrc\b|\bfilesrc\b|\bappsrc\b', main_text))
    if main_has_camera:
        source_type = 'camera'

    # RTSP-out detection: qtirtspbin is a serving sink (streams instead of/besides
    # writing a file).
    rtsp_out = bool(re.search(r'\bqtirtspbin\b', main_text))

    # ALL filesink locations straight from main.c are authoritative for output
    # paths, order-preserved and de-duplicated. output_path stays a scalar for
    # back-compat = the first one found.
    output_paths = []
    for loc in re.findall(r'"location"\s*,\s*"(/(?:root|home/\w+|tmp)/[^"]+)"', main_text):
        loc = loc.strip()
        if loc not in output_paths:
            output_paths.append(loc)

    if readme_file.exists():
        readme = readme_file.read_text(encoding='utf-8', errors='replace')

        if not main_has_known_source and re.search(
                r'\bqtiqmmfsrc\b|\bqticamsrc\b|\bcamera\b', readme, re.IGNORECASE):
            source_type = 'camera'

        if not output_paths:
            m_out = re.search(
                r'(?:OUTPUT_FILE[^|]*\|[^|`/]*|output[\w\s]*[|:]\s*|location\s*=\s*)'
                r'[`\s]*(/(?:root|home/\w+|tmp)/[^\s`|<>\'\"\\]+)',
                readme,
                re.IGNORECASE
            )
            if m_out:
                output_paths.append(m_out.group(1).strip())

    output_path = output_paths[0] if output_paths else None

    return {
        'binary_name': binary_name,
        'source_type': source_type,
        'output_path': output_path,
        'output_paths': output_paths,
        'rtsp_out': rtsp_out,
    }


# ── Log scanning ──────────────────────────────────────────────────────────────

def _scan_log(log_text):
    """
    Parse GStreamer C app output. Returns (playing_reached, real_error_lines, crash_reason).

    PLAYING detection covers both log formats:
      gst-launch: 'Setting pipeline to PLAYING ...'
      C app:      'Pipeline state changed from PAUSED to PLAYING'

    ERROR lines are filtered against _BENIGN — benign noise is never reported.

    crash_reason is set if the process crashed (SIGSEGV etc.) — this means dirty
    device state, not a pipeline bug. The caller should surface it clearly.
    """
    playing = bool(re.search(
        r'Setting pipeline to PLAYING'
        r'|Pipeline state changed from PAUSED to PLAYING'
        r'|State changed.*PAUSED.*PLAYING',
        log_text
    ))

    # Crash detection — SIGSEGV during preroll = dirty device state
    crash_reason = None
    if 'Caught SIGSEGV' in log_text:
        crash_reason = (
            'Pipeline crashed with SIGSEGV during preroll. '
            'This is a device state issue (GPU/driver memory corruption), not a pipeline bug.'
        )
    elif 'Caught SIGABRT' in log_text:
        crash_reason = (
            'Pipeline crashed with SIGABRT.'
        )

    errors = []
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('ERROR:'):
            continue
        if any(b in stripped for b in _BENIGN):
            continue
        errors.append(stripped)

    return playing, errors, crash_reason


def _check_moov_atom(ssh, output_path):
    """
    Verify output_path is a finalized, playable MP4 using gst-discoverer-1.0 —
    the same demuxer logic a real player uses, rather than grep'ing raw bytes for
    "moov". By the time this is called the run has returned (SIGINT/EOS → NULL
    on-device), so mp4mux has written its trailer synchronously; one check is
    enough. Returns True if valid.
    """
    disco_out, _, disco_rc = ssh.run(f"gst-discoverer-1.0 '{output_path}' 2>&1")
    return disco_rc == 0 and 'Duration:' in disco_out


def _check_rtsp_port_listening(ssh):
    """
    Check whether RTSP serving port 8900 is LISTEN-ing on the device, via
    /proc/net/tcp — `ss` is not available on QLI 2.0 devices. Port 8900 in
    hex (uppercase, 4-digit) is 22C4; TCP state LISTEN is 0A. Local address
    field format is '<hex-addr>:<hex-port>'.
    """
    out, _, _ = ssh.run("cat /proc/net/tcp 2>/dev/null")
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        local_addr, state = cols[1], cols[3]
        if ':' not in local_addr:
            continue
        port_hex = local_addr.split(':')[1].upper()
        if port_hex == '22C4' and state.upper() == '0A':
            return True
    return False


# ── Output helpers ────────────────────────────────────────────────────────────

def _artifact_output_dir(deploy_output_dir, artifact_name):
    d = pathlib.Path(deploy_output_dir) / artifact_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_result(out_dir, result):
    """Write result.json. Always called — even on failure."""
    p = out_dir / 'result.json'
    p.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return str(p)


def _human_size(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f} {unit}' if unit != 'bytes' else f'{n} bytes'
        n /= 1024


def _step(result, name, status, detail=None):
    """Update a step entry in result['steps'] by name."""
    for s in result['steps']:
        if s['step'] == name:
            s['status'] = status
            s['detail'] = detail
            return


def _print_summary(result, phase='all'):
    if phase == 'build':
        # Build-phase-only calls never execute the binary, so playing_reached
        # is always its unset default (False) — judging PASS/FAIL on it here
        # would misreport a clean build as a run failure. Report build status
        # only; the run phase's own call prints the real PASS/FAIL verdict.
        ok = result.get('build_passed') is True and not result.get('failure_reason')
        status = 'PASS' if ok else 'FAIL'
        print(f'\n  +-- Mode C build: {status} (not run in this phase)', flush=True)
        print(f'  |   build_passed    : {result["build_passed"]}', flush=True)
        if result.get('failure_reason'):
            print(f'  |   failure_reason  : {result["failure_reason"]}', flush=True)
        if result.get('linux_workstation_host'):
            print(f'  |   Linux workstation      : {result["linux_workstation_host"]}', flush=True)
        if result.get('build_log_path'):
            print(f'  +-- build_log       : {result.get("build_log_path")}', flush=True)
        return
    ok = (
        result.get('build_passed') is True
        and result['playing_reached']
        and not result['error_lines']
        and not result.get('failure_reason')
    )
    status = 'PASS' if ok else 'FAIL'
    print(f'\n  +-- Mode C result: {status}', flush=True)
    print(f'  |   build_passed    : {result["build_passed"]}', flush=True)
    print(f'  |   playing_reached : {result["playing_reached"]}', flush=True)
    print(f'  |   errors          : {len(result["error_lines"])}', flush=True)
    print(f'  |   output          : {result["output_file_size"]}', flush=True)
    if result.get('failure_reason'):
        print(f'  |   failure_reason  : {result["failure_reason"]}', flush=True)
    if result.get('linux_workstation_host'):
        print(f'  |   Linux workstation      : {result["linux_workstation_host"]}', flush=True)
    print(f'  +-- log             : {result.get("log_local_path")}', flush=True)
    if result.get('build_log_path'):
        print(f'  +-- build_log       : {result.get("build_log_path")}', flush=True)


# ── Sudo helper ───────────────────────────────────────────────────────────────

def _sudo_run(ssh, cmd, password, timeout=30):
    """Run a sudo command safely without exposing the password in ps aux.

    If password provided: uses exec_command with sudo -S and stdin write.
    If no password (NOPASSWD device): uses ssh.run('sudo bash -c cmd').
    """
    import shlex as _shlex
    if not password:
        return ssh.run(f'sudo bash -c {_shlex.quote(cmd)}', timeout=timeout)

    ssh.connect()
    stdin, stdout, stderr = ssh._client.exec_command(
        f'sudo -S bash -c {_shlex.quote(cmd)}', timeout=timeout
    )
    stdin.write(password + '\n')
    stdin.flush()
    stdin.close()
    out_bytes = stdout.read()
    err_bytes = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return (
        out_bytes.decode('utf-8', errors='replace'),
        err_bytes.decode('utf-8', errors='replace'),
        rc
    )


# ── Core deploy ───────────────────────────────────────────────────────────────

def deploy_mode_c(
    artifact_path,
    deploy_output_dir,
    ssh_cfg,
    linux_workstation_host,
    linux_workstation_user,
    linux_workstation_key=None,
    linux_workstation_password=None,
    linux_workstation_port=22,
    build_dir=None,
    sdk_path=None,
    imsdk_path=None,
    dry_run=False,
    phase='all',
    run_timeout=0,
):
    """
    Mode C deploy: host-build C app on Linux workstation, push ARM64 binary to QLI device, run, pull.

    phase — 'build' (C0-C1, setup_and_build_c, C6-C9, then stop), 'run' (C0, connect
    device only, verify binary present, C9-C13), or 'all' (default, current behavior
    unchanged). run_timeout — if >0, overrides the file-source run's 300s cap in
    _run_on_device (camera timeout is untouched).

    Two SSH connections:
      ssh_dc  — key-auth to Linux workstation (Linux x86_64 host compiler)
      ssh_dev — password-auth to QLI device (same as Mode A/B)

    sdk_path / imsdk_path (both optional, from LINUX_WORKSTATION_SDK_PATH /
    LINUX_WORKSTATION_IMSDK_PATH in configs/.env) let the caller point at an
    SDK installer or an existing gst-plugins-imsdk clone already present on
    the workstation, instead of downloading/cloning fresh. See
    workspace_setup_c.setup_and_build_c for the exact precedence rules.

    This function builds and runs the app as-is and reports what happened.
    It does NOT diagnose failures, suggest fixes, modify source files,
    or retry. All that is the user's responsibility.

    Always returns a result dict and always writes result.json and device.log,
    even on failure, so failures are captured for the caller to inspect.
    """
    artifact_path = pathlib.Path(artifact_path).resolve()
    artifact_name = artifact_path.name
    out_dir = _artifact_output_dir(deploy_output_dir, artifact_name)

    result = {
        'mode':              'C',
        'phase':             phase,
        'artifact':          artifact_name,
        'build_passed':      'N/A',
        'playing_reached':   False,
        'error_lines':       [],
        'output_file_size':  'missing',
        'output_local_path': None,
        'output_local_paths': [],
        'rtsp_out':          False,
        'rtsp_url':          None,
        'log_local_path':    str(out_dir / 'device.log'),
        'build_log_path':    None,
        'failure_reason':    None,
        'linux_workstation_host':   linux_workstation_host,
        'steps': [
            {'step': 'parse_artifact', 'status': 'skip', 'detail': None},
            {'step': 'sdk_verify',     'status': 'skip', 'detail': None},
            {'step': 'imsdk_check',    'status': 'skip', 'detail': None},
            {'step': 'push_source',    'status': 'skip', 'detail': None},
            {'step': 'cross_compile',  'status': 'skip', 'detail': None},
            {'step': 'pull_binary',    'status': 'skip', 'detail': None},
            {'step': 'push_to_device', 'status': 'skip', 'detail': None},
            {'step': 'run',            'status': 'skip', 'detail': None},
            {'step': 'pull_output',    'status': 'skip', 'detail': None},
        ],
    }

    print(f'\n[Mode C] {artifact_name}', flush=True)
    print(f'  artifact      : {artifact_path}', flush=True)
    print(f'  output        : {out_dir}', flush=True)
    print(f'  Linux workstation    : {linux_workstation_user}@{linux_workstation_host}', flush=True)

    log_lines   = []
    build_lines = []
    ssh_dc  = None
    ssh_dev = None

    try:
        # ── C0: Parse artifact metadata locally (no SSH yet) ──────────────────
        try:
            meta = _parse_artifact(artifact_path)
        except Exception as e:
            result['failure_reason'] = f'Artifact parse error: {e}'
            _step(result, 'parse_artifact', 'fail', str(e))
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result

        binary_name = meta['binary_name']
        source_type = meta['source_type']
        output_path = meta['output_path']
        output_paths = meta['output_paths']
        rtsp_out = meta['rtsp_out']
        result['rtsp_out'] = rtsp_out

        print(f'  binary        : {binary_name}', flush=True)
        print(f'  source        : {source_type}', flush=True)
        print(f'  output        : {output_path or "(none — display-only)"}', flush=True)
        if rtsp_out:
            print(f'  rtsp-out      : detected (qtirtspbin)', flush=True)

        _step(result, 'parse_artifact', 'ok', f'binary={binary_name}, source={source_type}')

        if dry_run:
            print('  [dry-run] No device connection.', flush=True)
            result['failure_reason'] = 'dry-run'
            _write_result(out_dir, result)
            return result

        device_bin_path = f'{_INSTALL_BINDIR}/{binary_name}'
        pw = None
        local_bin = None

        if phase in ('build', 'all'):
            if not linux_workstation_host:
                result['failure_reason'] = 'LINUX_WORKSTATION_HOST not set in configs/.env'
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            # ── C1–C5: Workspace setup and host build (idempotent) ──────────
            # workspace_setup_c detects current state (0-4) and runs only what's
            # needed: SDK install, git clone, cmake configure, push source, build.
            # Safe to re-run — each step is guarded. Fast on subsequent runs (~4s).
            print(f'  [C1]    Connecting to Linux workstation {linux_workstation_host} ...', flush=True)
            ssh_dc = _SSH_Key(
                host=linux_workstation_host, user=linux_workstation_user,
                key_path=linux_workstation_key, password=linux_workstation_password,
                port=linux_workstation_port,
            )
            try:
                ssh_dc.connect()
            except Exception as e:
                result['failure_reason'] = f'Linux workstation SSH connection failed: {e}'
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            from workspace_setup_c import setup_and_build_c as _setup_c
            setup_result = _setup_c(
                ssh_dc=ssh_dc,
                artifact_path=artifact_path,
                binary_name=binary_name,
                build_dir=build_dir,
                sdk_path=sdk_path,
                imsdk_path=imsdk_path,
            )
            build_lines.append(setup_result.get('build_log', ''))
            build_log_path = out_dir / 'build.log'
            build_log_path.write_text('\n\n'.join(build_lines), encoding='utf-8')
            result['build_log_path'] = str(build_log_path)

            if not setup_result['success']:
                result['failure_reason'] = setup_result['failure_reason']
                result['build_passed']   = False
                _step(result, 'sdk_verify',    'fail', setup_result['failure_reason'])
                _step(result, 'imsdk_check',   'fail', setup_result['failure_reason'])
                _step(result, 'push_source',   'fail', setup_result['failure_reason'])
                _step(result, 'cross_compile', 'fail', setup_result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            result['build_passed'] = True
            imsdk_dir = setup_result['imsdk_dir']
            _step(result, 'sdk_verify',    'ok', 'handled by workspace_setup_c')
            _step(result, 'imsdk_check',   'ok', 'handled by workspace_setup_c')
            _step(result, 'push_source',   'ok', 'handled by workspace_setup_c')
            _step(result, 'cross_compile', 'ok', 'handled by workspace_setup_c')

            # ── C6: Pull binary from Linux workstation to local ───────────────────────────
            remote_bin_path = f'{imsdk_dir}/build/{_SAMPLE_APPS_SUBDIR}/{binary_name}/{binary_name}'
            print(f'  [C6]    Pulling binary from Linux workstation: {remote_bin_path}', flush=True)

            # Staging dir for the pulled binary. On Windows, use a short absolute path to
            # avoid SFTP long-path failures (> 260 chars). On Linux, /tmp works fine.
            local_tmp = pathlib.Path('C:/tmp/qimsdk_compiled') if sys.platform == 'win32' \
                else pathlib.Path('/tmp/qimsdk_compiled')
            local_tmp.mkdir(parents=True, exist_ok=True)
            local_bin = local_tmp / binary_name

            try:
                ssh_dc.pull(remote_bin_path, str(local_bin))
            except Exception as e:
                result['failure_reason'] = f'Failed to pull binary from Linux workstation: {e}'
                _step(result, 'pull_binary', 'fail', str(e))
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            if not local_bin.exists() or local_bin.stat().st_size == 0:
                result['failure_reason'] = 'binary not found at expected path after build'
                _step(result, 'pull_binary', 'fail', result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            print(f'  [C6]    Binary pulled: {local_bin} ({_human_size(local_bin.stat().st_size)})', flush=True)
            _step(result, 'pull_binary', 'ok', f'{local_bin} ({_human_size(local_bin.stat().st_size)})')

            # ── C7: Push binary to QLI device ─────────────────────────────────────
            print(f'  [C7]    Connecting to QLI device {ssh_cfg["ip"]} ...', flush=True)
            ssh_dev = _SSH(
                ip=ssh_cfg['ip'],
                user=ssh_cfg['user'],
                host_key_fp=ssh_cfg.get('host_key', ''),
                password=ssh_cfg.get('password'),
                key_path=ssh_cfg.get('key_path'),
            )
            try:
                ssh_dev.connect()
            except Exception as e:
                result['failure_reason'] = f'SSH connection to device failed: {e}'
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            tmp_bin_path = f'/tmp/{binary_name}'
            pw = ssh_dev.password
            print(f'  [C7]    Pushing binary to device (via /tmp): {device_bin_path}', flush=True)
            try:
                # Push to /tmp first (writable without root/sudo), then move to /usr/bin.
                # Use plain mv if already root; sudo mv if not (sudo may not exist on QLI root).
                ssh_dev.push(str(local_bin), tmp_bin_path)
                if ssh_dev.user == 'root':
                    mv_cmd = f"mv '{tmp_bin_path}' '{device_bin_path}' 2>&1"
                    mv_out, mv_err, mv_rc = ssh_dev.run(mv_cmd)
                else:
                    mv_out, mv_err, mv_rc = _sudo_run(
                        ssh_dev, f"mv '{tmp_bin_path}' '{device_bin_path}' 2>&1", pw
                    )
                if mv_rc != 0:
                    raise RuntimeError(f'sudo mv failed: {mv_err or mv_out}')
            except Exception as e:
                result['failure_reason'] = f'Failed to push binary to device: {e}'
                _step(result, 'push_to_device', 'fail', str(e))
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            if ssh_dev.user == 'root':
                ssh_dev.run(f"chmod +x {device_bin_path}")
            else:
                _sudo_run(ssh_dev, f"chmod +x {device_bin_path}", pw)

            # Verify binary on device
            verify_out, _, _ = ssh_dev.run(
                f"test -f '{device_bin_path}' && echo OK || echo MISSING"
            )
            if 'MISSING' in verify_out:
                result['failure_reason'] = f'binary not found on device after push: {device_bin_path}'
                _step(result, 'push_to_device', 'fail', result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            print(f'  [C7]    Binary verified on device at {device_bin_path}', flush=True)
            _step(result, 'push_to_device', 'ok', device_bin_path)

            # ── C8: Ensure output dir on device ───────────────────────────────────
            ssh_dev.run(f'mkdir -p {_OUTPUT_MEDIA_DIR}', timeout=15)

        if phase == 'run':
            # ── C0(run): connect DEVICE only, skip workstation + build entirely ───
            print(f'  [C1]    Connecting to QLI device {ssh_cfg["ip"]} (run phase — skipping build) ...', flush=True)
            ssh_dev = _SSH(
                ip=ssh_cfg['ip'],
                user=ssh_cfg['user'],
                host_key_fp=ssh_cfg.get('host_key', ''),
                password=ssh_cfg.get('password'),
                key_path=ssh_cfg.get('key_path'),
            )
            try:
                ssh_dev.connect()
            except Exception as e:
                result['failure_reason'] = f'SSH connection to device failed: {e}'
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            pw = ssh_dev.password

            verify_out, _, _ = ssh_dev.run(
                f"test -f '{device_bin_path}' && echo OK || echo MISSING"
            )
            if 'MISSING' in verify_out:
                result['failure_reason'] = (
                    f'binary not on device ({device_bin_path}) — run --phase build first'
                )
                _step(result, 'push_to_device', 'fail', result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            print(f'  [C1]    Binary verified present on device at {device_bin_path}', flush=True)
            _step(result, 'push_to_device', 'ok', device_bin_path)

        if phase in ('build', 'run', 'all'):
            # ── C9: Kill stale processes on device ────────────────────────────────
            pw = ssh_dev.password
            print(f'  [C9]    Killing stale {binary_name} processes ...', flush=True)
            _sudo_run(
                ssh_dev, f"pkill -9 -f {shlex.quote(binary_name)} 2>/dev/null; true", pw, timeout=10
            )
            ssh_dev.run('sleep 1', timeout=5)

            still_running, _, _ = ssh_dev.run(
                f"ps -eo stat,pid,cmd | grep {shlex.quote(binary_name)} | grep -v grep | grep -v '^Z' | wc -l"
            )
            stale_count = 0
            try:
                stale_count = int(still_running.strip())
            except ValueError:
                pass

            if stale_count > 0:
                result['failure_reason'] = (
                    f'{stale_count} {binary_name} process(es) still running after SIGKILL — '
                    f'the device is busy; wait for it to go idle and re-run.'
                )
                _step(result, 'run', 'fail', f'{stale_count} stale process(es) after SIGKILL')
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            print(f'  [C9]    Device clear', flush=True)

        if phase == 'build':
            # Build phase stops here — binary is pushed, device is clear, ready for run phase.
            _write_result(out_dir, result)
            _print_summary(result, phase='build')
            print(f'  [done]  build phase complete — binary at {device_bin_path}', flush=True)
            return result

        if phase in ('run', 'all'):
            # ── C10: Run binary on device ─────────────────────────────────────────
            wayland_prefix = (
                'export OCL_ICD_FILENAMES=/usr/lib/libOpenCL_adreno.so.1 && '
                'ulimit -n 10000 && '
                'WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1) && '
                'export XDG_RUNTIME_DIR=$(dirname "$WS") && '
                'export WAYLAND_DISPLAY=$(basename "$WS")'
            )
            file_source_timeout = run_timeout if run_timeout > 0 else 300
            rtsp_serve_n = run_timeout if run_timeout > 0 else 30

            def _run_on_device():
                if rtsp_out:
                    # RTSP-out: bounded foreground serve, same camera-style timeout
                    # wrapper regardless of source_type (camera/file-source both feed
                    # a qtirtspbin serving sink that must be bounded, not run to EOS).
                    cmd = (
                        f'{wayland_prefix} && '
                        f"timeout --signal=SIGINT --kill-after=15 {rtsp_serve_n} "
                        f"{device_bin_path} 2>&1; exit 0"
                    )
                    holder = {}

                    def _bg():
                        holder['res'] = ssh_dev.run(cmd, timeout=rtsp_serve_n + 30)

                    t = threading.Thread(target=_bg)
                    t.start()
                    # Give the pipeline time to reach PLAYING and bind the RTSP port
                    # before checking — but not longer than the serve window itself.
                    time.sleep(min(5, rtsp_serve_n))
                    port_listening = _check_rtsp_port_listening(ssh_dev)
                    t.join()
                    return holder['res'], cmd, port_listening
                if source_type == 'camera':
                    cmd = (
                        f'{wayland_prefix} && '
                        f"timeout --signal=SIGINT --kill-after=15 30 {device_bin_path} 2>&1; exit 0"
                    )
                    return ssh_dev.run(cmd, timeout=60), cmd, False  # 30s run + 15s kill-after + 15s buffer
                cmd = f'{wayland_prefix} && {device_bin_path} 2>&1'
                return ssh_dev.run(cmd, timeout=file_source_timeout), cmd, False

            if source_type == 'camera':
                # Restart cam-server first
                print('  [C10]   Restarting cam-server (required before camera run) ...', flush=True)
                cam_out, _, _ = _sudo_run(
                    ssh_dev, 'systemctl restart cam-server 2>&1', pw, timeout=20
                )
                log_lines.append(f'[cam-server restart]\n{cam_out}')
                print('  [C10]   Waiting 3s for cam-server to settle ...', flush=True)
                time.sleep(3)
                if rtsp_out:
                    print(f'  [run]   camera+rtsp-out -- timeout --signal=SIGINT --kill-after=15 {rtsp_serve_n} {device_bin_path} ...', flush=True)
                else:
                    print(f'  [run]   camera -- timeout --signal=SIGINT --kill-after=15 30 {device_bin_path} ...', flush=True)
            elif rtsp_out:
                print(f'  [run]   file-source+rtsp-out -- timeout --signal=SIGINT --kill-after=15 {rtsp_serve_n} {device_bin_path} ...', flush=True)
            else:
                print(f'  [run]   file-source -- {device_bin_path} (to natural EOS) ...', flush=True)

            t0 = time.time()
            (run_out, run_err, run_rc), run_cmd, rtsp_port_listening = _run_on_device()
            elapsed = time.time() - t0
            print(f'  [run]   Done in {elapsed:.1f}s (exit {run_rc})', flush=True)
            log_lines.append(f'[app run — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')

            # ── C11: Save device log and scan ─────────────────────────────────────
            log_path = out_dir / 'device.log'
            log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
            result['log_local_path'] = str(log_path)
            print(f'  [log]   Saved -> {log_path}', flush=True)

            combined = '\n'.join(log_lines)
            playing, error_lines, crash_reason = _scan_log(combined)
            if rtsp_out and rtsp_port_listening:
                playing = True
                print('  [log]   RTSP port 8900 was LISTEN-ing during serve — playing_reached=True', flush=True)
            result['playing_reached'] = playing
            result['error_lines']     = error_lines

            # SIGSEGV auto-retry once after a settle — a device-state timing issue,
            # not a code bug (GPU/display buffers not yet released from a prior run).
            # Same working pattern as Mode A/D: re-run and rescan.
            if crash_reason:
                print('  [WARN]  SIGSEGV detected — waiting 15s for driver to settle, then retrying once ...', flush=True)
                time.sleep(15)
                print(f'  [run]   retry after SIGSEGV -- {run_cmd}', flush=True)
                t0 = time.time()
                (run_out, run_err, run_rc), run_cmd, rtsp_port_listening = _run_on_device()
                elapsed = time.time() - t0
                log_lines.append(f'[app run RETRY — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')
                log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
                # Scan only the retry attempt's output — don't resurface the first
                # attempt's stale crash/errors after a clean retry.
                playing, error_lines, crash_reason = _scan_log(run_out + '\n' + run_err)
                if rtsp_out and rtsp_port_listening:
                    playing = True
                result['playing_reached'] = playing
                result['error_lines']     = error_lines
                if crash_reason:
                    result['failure_reason'] = crash_reason
                    _step(result, 'run', 'fail', crash_reason)
                    print(f'  [FAIL]  {crash_reason}', flush=True)
                    _write_result(out_dir, result)
                    return result

            _step(result, 'run', 'ok' if playing else 'fail',
                  f'{elapsed:.1f}s, exit {run_rc}, playing={playing}')
            print(f'  [log]   playing_reached: {playing}', flush=True)
            if error_lines:
                print(f'  [log]   {len(error_lines)} ERROR line(s):', flush=True)
                for ln in error_lines[:5]:
                    print(f'           {ln}', flush=True)
                if len(error_lines) > 5:
                    print(f'           ... ({len(error_lines) - 5} more in device.log)', flush=True)

            if rtsp_out:
                result['rtsp_url'] = f'rtsp://{ssh_cfg["ip"]}:8900/live'
                print(f'  [rtsp]  rtsp_url: {result["rtsp_url"]}', flush=True)

            if not output_paths:
                # Pure rtsp-out (no filesink) or true display-only app — nothing to pull.
                result['output_file_size'] = 'N/A (RTSP stream)' if rtsp_out else 'N/A (no filesink)'
                _step(result, 'pull_output', 'ok',
                      'N/A — RTSP stream' if rtsp_out else 'N/A — display-only')
            else:
                # ── C12: Verify MP4 moov atom before pull ─────────────────────────
                # Host-built C apps use mp4mux — moov written at end after EOS. Uses the
                # real GStreamer demuxer (gst-discoverer-1.0) rather than a raw byte search —
                # confirms the container is actually well-formed, not just that the bytes
                # "moov" appear somewhere in the file. Applied to the primary (first)
                # output path only, matching prior single-output behavior.
                if output_path and output_path.endswith('.mp4') and playing:
                    if not _check_moov_atom(ssh_dev, output_path):
                        size = ssh_dev.file_size(output_path)
                        result['failure_reason'] = (
                            'Output MP4 is missing/invalid moov atom — mp4mux did not finalize. '
                            f'File has {size:,} bytes but is unplayable.'
                        )
                        result['output_file_size'] = f'{_human_size(size)} (no moov — unplayable)'
                        _step(result, 'pull_output', 'fail', result['failure_reason'])
                        print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                        _write_result(out_dir, result)
                        return result

                # ── C13: Check output file size + pull, for every output_paths entry ──
                any_pull_failed = False
                first_size_label = None
                for i, p in enumerate(output_paths):
                    size = ssh_dev.file_size(p)
                    ext = posixpath.splitext(p)[1] or '.mp4'
                    local_out = out_dir / (f'output{ext}' if i == 0 else f'output_{i + 1}{ext}')

                    if size > 0:
                        size_label = _human_size(size)
                        if i == 0:
                            first_size_label = size_label
                        print(f'  [pull]  {p} ({size_label}) -> {local_out}', flush=True)
                        try:
                            ssh_dev.pull(p, str(local_out))
                            result['output_local_paths'].append(str(local_out))
                            if result['output_local_path'] is None:
                                result['output_local_path'] = str(local_out)
                        except Exception as e:
                            any_pull_failed = True
                            result['error_lines'].append(f'Output pull failed ({p}): {e}')
                            print(f'  [WARN]  Pull failed: {e}', flush=True)
                    elif size == 0:
                        any_pull_failed = True
                        if i == 0:
                            first_size_label = '0 bytes'
                        print(f'  [WARN]  Output file is 0 bytes: {p}', flush=True)
                    else:
                        any_pull_failed = True
                        if i == 0:
                            first_size_label = 'missing'
                        print(f'  [WARN]  Output file not found on device: {p}', flush=True)

                result['output_file_size'] = first_size_label if first_size_label else 'missing'
                _step(result, 'pull_output', 'fail' if any_pull_failed else 'ok',
                      result['output_file_size'])

            if phase == 'run':
                # Carry forward build_passed/build_log_path from the prior build-phase
                # result.json so the final json is complete.
                prior_result_path = out_dir / 'result.json'
                if prior_result_path.exists():
                    try:
                        prior = json.loads(prior_result_path.read_text(encoding='utf-8'))
                        if prior.get('build_passed') is not None:
                            result['build_passed'] = prior.get('build_passed')
                        if prior.get('build_log_path'):
                            result['build_log_path'] = prior.get('build_log_path')
                    except Exception:
                        pass

    except KeyboardInterrupt:
        result['failure_reason'] = 'Interrupted by user'
        print('\n  [ABORT] Interrupted.', flush=True)
    except Exception as e:
        msg = str(e).strip() or repr(e)
        result['failure_reason'] = msg
        print(f'  [FAIL]  {msg}', flush=True)
    finally:
        # Save whatever logs we have even if failing out
        if log_lines and not (out_dir / 'device.log').exists():
            try:
                (out_dir / 'device.log').write_text('\n\n'.join(log_lines), encoding='utf-8')
            except Exception:
                pass
        if build_lines:
            build_log_path = out_dir / 'build.log'
            try:
                build_log_path.write_text('\n\n'.join(build_lines), encoding='utf-8')
                result['build_log_path'] = str(build_log_path)
            except Exception:
                pass
        for conn in [ssh_dc, ssh_dev]:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ── C12: Write result.json (always) ──────────────────────────────────────
    result_path = _write_result(out_dir, result)
    _print_summary(result)
    print(f'  [done]  result.json -> {result_path}', flush=True)
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Mode C: host-build C app on Linux workstation, deploy ARM64 binary to QLI device, run.'
    )
    p.add_argument('--artifact-path', required=True,
                   help='Folder containing main.c, CMakeLists.txt, README.md')
    p.add_argument('--output-dir',
                   default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                   help='Local dir for logs and output files (DEPLOY_OUTPUT_DIR in configs/.env)')
    p.add_argument('--device-ip',   default=os.environ.get('DEVICE_IP', ''))
    p.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''))
    p.add_argument('--host-key',    default=os.environ.get('HOST_KEY', ''))
    p.add_argument('--linux-workstation-host',
                   default=os.environ.get('LINUX_WORKSTATION_HOST', ''),
                   help='Hostname/IP of the Linux workstation')
    p.add_argument('--linux-workstation-user',
                   default=os.environ.get('LINUX_WORKSTATION_USER', ''),
                   help='SSH username on linux_workstation')
    p.add_argument('--linux-workstation-key',
                   default=os.environ.get('LINUX_WORKSTATION_KEY', ''),
                   help='Path to SSH private key for Linux workstation (LINUX_WORKSTATION_KEY in configs/.env)')
    p.add_argument('--linux-workstation-password',
                   default=os.environ.get('LINUX_WORKSTATION_PASSWORD', ''),
                   help='SSH password for Linux workstation (LINUX_WORKSTATION_PASSWORD in configs/.env — used if key not available)')
    p.add_argument('--linux-workstation-build-dir',
                   default=os.environ.get('LINUX_WORKSTATION_BUILD_DIR', ''),
                   help='Build dir on Linux workstation for cloning (default: /usr2/<user>/qimsdk-build)')
    p.add_argument('--linux-workstation-sdk-path',
                   default=os.environ.get('LINUX_WORKSTATION_SDK_PATH', ''),
                   help='Optional: path on the workstation to an SDK installer (.zip or .sh). '
                        'If not set, downloaded from codelinaro.org (LINUX_WORKSTATION_SDK_PATH)')
    p.add_argument('--linux-workstation-imsdk-path',
                   default=os.environ.get('LINUX_WORKSTATION_IMSDK_PATH', ''),
                   help='Optional: path on the workstation to an existing gst-plugins-imsdk clone. '
                        'If not set, cloned from GitHub (LINUX_WORKSTATION_IMSDK_PATH)')
    p.add_argument('--linux-workstation-port',
                   default=int(os.environ.get('LINUX_WORKSTATION_PORT', '22') or '22'),
                   type=int,
                   help='SSH port on Linux workstation (LINUX_WORKSTATION_PORT in configs/.env, default 22; WSL typically 2222)')
    p.add_argument('--dry-run', action='store_true',
                   help='Parse artifact and show plan without connecting to any host')
    p.add_argument('--phase', choices=['build', 'run', 'all'], default='all',
                   help='build: host-build + push binary to device, then stop. '
                        'run: skip build/workstation, verify binary present on device, run + pull. '
                        'all (default): current end-to-end behavior, unchanged.')
    p.add_argument('--run-timeout', type=int, default=0,
                   help='Override the file-source run timeout in seconds (0 = script default, '
                        'currently 300s). Camera timeout is not affected.')
    p.add_argument('--json', action='store_true',
                   help='Print result JSON to stdout on completion')
    args = p.parse_args()

    # Mandatory field enforcement — fail immediately with exact message
    # Workstation fields are only required when this phase actually needs to build
    # (phase in build/all); the run phase connects to the device only.
    needs_workstation = args.phase in ('build', 'all')
    errors = []
    if not args.device_ip and not args.dry_run:
        errors.append('DEVICE_IP not set in configs/.env')
    if not args.device_user and not args.dry_run:
        errors.append('DEVICE_USER not set in configs/.env')
    if not os.environ.get('DEVICE_PASSWORD', '') and \
       not os.environ.get('DEVICE_KEY', '') and not args.dry_run:
        errors.append(
            'No device auth configured — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env\n'
            '  See ssh-setup.md'
        )
    if not args.output_dir:
        errors.append('DEPLOY_OUTPUT_DIR not set in configs/.env')
    if not args.linux_workstation_host and not args.dry_run and needs_workstation:
        errors.append('LINUX_WORKSTATION_HOST not set in configs/.env')
    if not args.linux_workstation_user and not args.dry_run and needs_workstation:
        errors.append('LINUX_WORKSTATION_USER not set in configs/.env')
    if not args.linux_workstation_build_dir and not args.dry_run and needs_workstation:
        errors.append('LINUX_WORKSTATION_BUILD_DIR not set in configs/.env')
    if args.linux_workstation_build_dir and not args.linux_workstation_build_dir.startswith('/') \
            and not args.dry_run and needs_workstation:
        errors.append(f'LINUX_WORKSTATION_BUILD_DIR must be an absolute path starting with / — got: {args.linux_workstation_build_dir}')
    if errors:
        for e in errors:
            print(f'[FAIL]  {e}', flush=True)
        sys.exit(1)

    # Resolve relative output_dir against repo root
    output_dir = args.output_dir
    if output_dir and not pathlib.Path(output_dir).is_absolute():
        output_dir = str(REPO_ROOT / output_dir)

    # Require LINUX_WORKSTATION_BUILD_DIR — no defaults (only when building)
    build_dir = args.linux_workstation_build_dir
    if not build_dir and not args.dry_run and needs_workstation:
        print('[FAIL]  LINUX_WORKSTATION_BUILD_DIR not set in configs/.env', flush=True)
        sys.exit(1)

    result = deploy_mode_c(
        artifact_path=args.artifact_path,
        deploy_output_dir=output_dir,
        ssh_cfg={
            'ip':       args.device_ip,
            'user':     args.device_user,
            'password': os.environ.get('DEVICE_PASSWORD', '') or None,
            'key_path': os.environ.get('DEVICE_KEY', '') or None,
            'host_key': args.host_key,
        },
        linux_workstation_host=args.linux_workstation_host,
        linux_workstation_user=args.linux_workstation_user,
        linux_workstation_key=args.linux_workstation_key or None,
        linux_workstation_password=os.environ.get('LINUX_WORKSTATION_PASSWORD', '') or args.linux_workstation_password or None,
        linux_workstation_port=args.linux_workstation_port,
        build_dir=build_dir,
        sdk_path=args.linux_workstation_sdk_path or None,
        imsdk_path=args.linux_workstation_imsdk_path or None,
        dry_run=args.dry_run,
        phase=args.phase,
        run_timeout=args.run_timeout,
    )

    if args.json:
        print(json.dumps(result, indent=2))

    if args.phase == 'build':
        ok = (
            result.get('build_passed') is True
            and not result.get('failure_reason')
        )
    elif args.phase == 'run':
        ok = (
            result.get('build_passed') in (True, 'N/A')
            and result.get('playing_reached')
            and not result.get('error_lines')
            and not result.get('failure_reason')
        )
    else:
        ok = (
            result.get('build_passed') is True
            and result['playing_reached']
            and not result['error_lines']
            and not result.get('failure_reason')
        )
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
