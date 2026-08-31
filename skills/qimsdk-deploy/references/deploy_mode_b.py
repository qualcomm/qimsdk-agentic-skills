#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
deploy_mode_b.py — Deploy and build a C app artifact on a Qualcomm Ubuntu device.

Mode B: push source files to device, register in parent CMakeLists.txt, build on-device
via cmake + make, install, run. Works on Ubuntu devices that have the QIMSDK source tree
at SOURCE_ROOT.

Usage:
  python3 deploy_mode_b.py \\
      --artifact-path outputs/qimsdk-gstreamer-app-builder/c-app/gst-qimsdk-object-detection

Credentials from configs/.env (DEVICE_IP, DEVICE_USER, DEVICE_PASSWORD, HOST_KEY).
See SKILL.md for the full .env template.

Output per artifact:
  <DEPLOY_OUTPUT_DIR>/<artifact-name>/
  ├── device.log    — full app stdout/stderr (always written)
  ├── build.log     — cmake + make output   (always written if build attempted)
  ├── result.json   — structured result dict (always written)
  └── output.<ext>  — pulled output file    (if app has a filesink)

Exits 0 if build passed, PLAYING reached and no real errors, 1 otherwise.

What this script does NOT do:
  - Diagnose why a build or run failed
  - Suggest code fixes or CMake changes
  - Retry failed builds, or failed runs, EXCEPT ONE narrow case: a camera run
    that fails on its first attempt is retried once after restarting cam-server
    (the hardware encoder can be left busy from a prior run — see
    _camera_run_needs_retry in deploy_mode_a.py, reused here).
  - Modify source files before pushing
  Those are the user's responsibility. This script builds, runs, reports, and pulls.
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
import tempfile
import time
import warnings

# Force UTF-8 stdout/stderr on Windows (cp1252 default rejects box-drawing and non-ASCII chars)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure workspace_setup_b and workspace_state (same directory) are importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


# ── Device path constants ──────────────────────────────────────────────────────
# SOURCE_ROOT must be set in configs/.env or via --source-root.
# No default — the path varies per device and must be confirmed by preflight.

_SAMPLE_APPS_SUBDIR      = 'gst-sample-apps'
_BUILD_SUBDIR            = 'build'
_INSTALL_BINDIR          = '/usr/bin'
_OUTPUT_MEDIA_DIR        = '/home/ubuntu/Downloads/qimsdk_samples/media/output'


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
]


# ── SSH session ───────────────────────────────────────────────────────────────

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


# ── Artifact metadata parsing ─────────────────────────────────────────────────

def _parse_artifact(artifact_path):
    """
    Parse CMakeLists.txt and README.md from artifact folder locally.
    Returns dict with: binary_name, source_type, output_path.
    Raises ValueError with exact message if any required file is missing or
    if the binary name cannot be extracted.

    Validates all required source files (main.c, CMakeLists.txt) before any
    SSH connection is opened — failures here are local and fast.
    """
    art = pathlib.Path(artifact_path)
    cmake_file = art / 'CMakeLists.txt'
    readme_file = art / 'README.md'

    if not cmake_file.exists():
        raise ValueError(f'CMakeLists.txt not found in {artifact_path}')

    # Validate main.c is present before attempting SSH — fail fast locally
    main_c = art / 'main.c'
    if not main_c.exists():
        raise ValueError(f'Artifact missing required file: main.c')

    cmake_text = cmake_file.read_text(encoding='utf-8', errors='replace')

    # Extract binary name from: set(GST_EXAMPLE_BIN <name>)
    m = re.search(r'set\s*\(\s*GST_EXAMPLE_BIN\s+(\S+)\s*\)', cmake_text)
    if not m:
        raise ValueError(
            f'GST_EXAMPLE_BIN not found in CMakeLists.txt — '
            f'expected: set(GST_EXAMPLE_BIN <name>)'
        )
    binary_name = m.group(1)

    # Source type and output path from main.c + README
    source_type = 'file-source'
    output_path = None

    main_c_text = main_c.read_text(encoding='utf-8', errors='replace')

    # Source type: check main.c first (most reliable), then README
    if re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b', main_c_text):
        source_type = 'camera'
    elif readme_file.exists():
        readme_src = readme_file.read_text(encoding='utf-8', errors='replace')
        if re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b|\bcamera\b', readme_src, re.IGNORECASE):
            source_type = 'camera'

    # Output path: only attempt to extract if main.c contains filesink.
    # If there is no filesink (waylandsink-only, RTSP-out), output_path stays None.
    has_filesink = bool(re.search(r'\bfilesink\b', main_c_text, re.IGNORECASE))

    if has_filesink and readme_file.exists():
        readme = readme_file.read_text(encoding='utf-8', errors='replace')

        # Extract output file path. Handles multiple README formats:
        #   Markdown table (constant first): | `OUTPUT_FILE` | `/root/Downloads/qimsdk_samples/media/output/out.mp4` |
        #   Markdown table (path first):     | `/home/ubuntu/Downloads/qimsdk_samples/media/output/out.mp4` | `OUTPUT_FILE` |
        #   Inline:                          output: /root/Downloads/qimsdk_samples/media/output/out.mp4
        #   Property:                        location=/home/ubuntu/Downloads/qimsdk_samples/media/output/out.mp4
        _abs_path = r'[`\s]*(/(?:root|home/\w+|tmp)/[^\s`|<>\'\"\\]+)'
        m_out = re.search(
            r'(?:OUTPUT_FILE[^|]*\|[^|`/]*|output[\w\s]*[|:]\s*|location\s*=\s*)' + _abs_path,
            readme, re.IGNORECASE
        )
        if not m_out:
            # Path-first table column: | `/path/to/file.mp4` | ... OUTPUT_FILE ...
            m_out = re.search(
                _abs_path + r'[^|\n]*\|[^|\n]*OUTPUT_FILE',
                readme, re.IGNORECASE
            )
        if not m_out:
            # Last resort: any absolute .mp4 path that is NOT on an INPUT_FILE row
            for line in readme.splitlines():
                if re.search(r'\bINPUT_FILE\b', line, re.IGNORECASE):
                    continue  # skip rows that define the input file
                lm = re.search(
                    r'[`\s](/(?:root|home/\w+|tmp)/[^\s`|<>\'\"\\]+\.mp4)',
                    line, re.IGNORECASE
                )
                if lm:
                    m_out = lm
                    break
        if m_out:
            output_path = m_out.group(1).strip()

    return {
        'binary_name': binary_name,
        'source_type': source_type,
        'output_path': output_path,
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


def _camera_run_needs_retry(playing, crash_reason, moov_found, has_mp4_output):
    """
    Decide whether a camera run looks like it hit a busy hardware encoder
    (stuck cam-server) and should be retried once after a cam-server restart,
    rather than reported as a final failure. Duplicated from deploy_mode_a.py
    (same reasoning — see that module for the full explanation).
    """
    if crash_reason:
        return True  # SIGSEGV/SIGABRT during preroll is a classic stuck-hardware symptom
    if not playing:
        return True  # never reached PLAYING — encoder likely never came up
    if has_mp4_output and not moov_found:
        return True  # ran, but never produced a valid finalized output file
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


def _check_moov_atom(ssh, output_path, max_wait=15):
    """
    Wait up to max_wait seconds for mp4mux to write the moov atom (index) at
    the end of output_path. Duplicated from deploy_mode_a.py (same reasoning).
    Returns True once the moov box is found, False if it never appears.
    """
    waited = 0
    while waited <= max_wait:
        fsize = ssh.file_size(output_path)
        if fsize > 32:
            path_b64 = base64.b64encode(output_path.encode()).decode()
            check_out, _, _ = ssh.run(
                f"python3 -c \""
                f"import base64; p=base64.b64decode('{path_b64}').decode(); "
                f"d=open(p,'rb').read(); "
                f"print('moov' if b'moov' in d else 'no_moov')"
                f"\""
            )
            if 'moov' in check_out:
                return True
        if waited < max_wait:
            print(f'  [mp4]   moov atom not yet written — waiting ({waited}s/{max_wait}s) ...', flush=True)
            time.sleep(2)
            waited += 2
        else:
            break
    return False


def _step(result, name, status, detail=None):
    """Update a step entry in result['steps'] by name."""
    for s in result['steps']:
        if s['step'] == name:
            s['status'] = status
            s['detail'] = detail
            return


def _print_summary(result):
    ok = (
        result.get('build_passed') is True
        and result['playing_reached']
        and not result['error_lines']
        and not result.get('failure_reason')
    )
    status = 'PASS' if ok else 'FAIL'
    print(f'\n  +-- Mode B result: {status}', flush=True)
    print(f'  |   build_passed    : {result["build_passed"]}', flush=True)
    print(f'  |   playing_reached : {result["playing_reached"]}', flush=True)
    print(f'  |   errors          : {len(result["error_lines"])}', flush=True)
    print(f'  |   output          : {result["output_file_size"]}', flush=True)
    if result.get('failure_reason'):
        print(f'  |   failure_reason  : {result["failure_reason"]}', flush=True)
    print(f'  +-- log             : {result.get("log_local_path")}', flush=True)
    if result.get('build_log_path'):
        print(f'  +-- build_log       : {result.get("build_log_path")}', flush=True)


# ── Sudo helper ───────────────────────────────────────────────────────────────

def _sudo_run(ssh, cmd, password, timeout=30):
    """Run a sudo command safely without exposing the password in ps aux.

    If password provided: uses exec_command with sudo -S and stdin write.
    If no password (NOPASSWD device): uses ssh.run('sudo bash -c cmd') which
    has reliable output collection via the battle-tested polling loop.
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
# ── Core deploy ───────────────────────────────────────────────────────────────

def deploy_mode_b(artifact_path, deploy_output_dir, ssh_cfg, source_root=None, dry_run=False):
    """
    Mode B deploy: push C app source to device, build on-device, install, run, pull output.

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
        'mode':              'B',
        'artifact':          artifact_name,
        'build_passed':      'N/A',
        'playing_reached':   False,
        'error_lines':       [],
        'output_file_size':  'missing',
        'output_local_path': None,
        'log_local_path':    str(out_dir / 'device.log'),
        'build_log_path':    None,
        'failure_reason':    None,
        'steps': [
            {'step': 'parse_artifact', 'status': 'skip', 'detail': None},
            {'step': 'clean_app_dir',  'status': 'skip', 'detail': None},
            {'step': 'push_source',    'status': 'skip', 'detail': None},
            {'step': 'cmake',          'status': 'skip', 'detail': None},
            {'step': 'make',           'status': 'skip', 'detail': None},
            {'step': 'install',        'status': 'skip', 'detail': None},
            {'step': 'run',            'status': 'skip', 'detail': None},
            {'step': 'pull_output',    'status': 'skip', 'detail': None},
        ],
    }

    # Resolve device path constants from source_root — auto-discovered via glob if not set
    src_root        = source_root.rstrip('/') if source_root else None
    sample_apps_dir = f'{src_root}/{_SAMPLE_APPS_SUBDIR}' if src_root else None
    build_dir       = f'{src_root}/{_BUILD_SUBDIR}' if src_root else None

    print(f'\n[Mode B] {artifact_name}', flush=True)
    print(f'  artifact    : {artifact_path}', flush=True)
    print(f'  output      : {out_dir}', flush=True)
    print(f'  source_root : {src_root or "(auto-discover via glob)"}', flush=True)

    log_lines  = []
    build_lines = []

    try:
        # ── B0: Parse artifact metadata locally (no SSH yet) ──────────────────
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
        app_dir     = f'{sample_apps_dir}/{binary_name}' if sample_apps_dir else None

        print(f'  binary      : {binary_name}', flush=True)
        print(f'  source      : {source_type}', flush=True)
        print(f'  output      : {output_path or "(none — display-only)"}', flush=True)
        print(f'  app_dir     : {app_dir or "(resolved after device glob)"}', flush=True)

        _step(result, 'parse_artifact', 'ok', f'binary={binary_name}, source={source_type}')

        if dry_run:
            print('  [dry-run] No device connection.', flush=True)
            result['failure_reason'] = 'dry-run'
            _write_result(out_dir, result)
            return result

        # ── B1: Connect SSH ───────────────────────────────────────────────────
        ssh = _SSH(
            ip=ssh_cfg['ip'],
            user=ssh_cfg['user'],
            host_key_fp=ssh_cfg.get('host_key', ''),
            password=ssh_cfg.get('password'),
            key_path=ssh_cfg.get('key_path'),
        )
        try:
            ssh.connect()
        except Exception as e:
            result['failure_reason'] = f'SSH connection failed: {e}'
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result

        pw = ssh.password

        # ── B2–B6: Workspace setup and build (idempotent) ────────────────────
        # workspace_setup_b detects the current workspace state (0-4) and runs
        # only the steps needed: apt setup, cmake configure, push source, make.
        # Safe to re-run — each step is guarded. Fast on subsequent runs (~3s).
        from workspace_setup_b import setup_and_build_b as _setup_b
        setup_result = _setup_b(
            ssh=ssh,
            artifact_path=artifact_path,
            binary_name=binary_name,
            source_root_hint=source_root,
            password=pw,
        )
        build_lines.append(setup_result.get('build_log', ''))
        # Write build.log now (partial — make install appended below)
        build_log_path = out_dir / 'build.log'
        build_log_path.write_text('\n\n'.join(build_lines), encoding='utf-8')
        result['build_log_path'] = str(build_log_path)

        if not setup_result['success']:
            result['failure_reason'] = setup_result['failure_reason']
            result['build_passed']   = False
            _step(result, 'cmake', 'fail', setup_result['failure_reason'])
            _step(result, 'make',  'fail', setup_result['failure_reason'])
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result

        result['build_passed'] = True
        _step(result, 'clean_app_dir', 'ok', 'handled by workspace_setup_b')
        _step(result, 'push_source',   'ok', 'handled by workspace_setup_b')
        _step(result, 'cmake',         'ok', 'handled by workspace_setup_b')
        _step(result, 'make',          'ok', 'handled by workspace_setup_b')

        # Use source_root discovered/validated by workspace_setup_b
        src_root        = setup_result['source_root']
        build_dir       = f'{src_root}/{_BUILD_SUBDIR}'
        sample_apps_dir = f'{src_root}/{_SAMPLE_APPS_SUBDIR}'
        app_dir         = f'{sample_apps_dir}/{binary_name}'

        # ── B7: Install ───────────────────────────────────────────────────────
        # Before install, sweep out any gst-sample-apps/gst-* dirs that have no
        # CMakeLists.txt (broken dirs left by earlier test runs or partial cleanups).
        # cmake re-runs --check-build-system during make install and will fail if it
        # finds a subdirectory referenced in gst-sample-apps/CMakeLists.txt that is
        # missing its own CMakeLists.txt.  This is a pre-install guard; the same
        # logic runs earlier (inside workspace_setup_b) before cmake configure.
        clean_broken_cmd = (
            f"for d in '{sample_apps_dir}'/gst-*/; do "
            f"  if [ -d \"$d\" ] && [ ! -f \"$d/CMakeLists.txt\" ]; then rm -rf \"$d\"; fi; "
            f"done"
        )
        _sudo_run(ssh, clean_broken_cmd, pw, timeout=30)

        print(f'  [B7]    Installing {binary_name} ...', flush=True)
        install_inner = f'cd {build_dir} && make -C gst-sample-apps/{binary_name} install 2>&1'
        inst_out, inst_err, inst_rc = _sudo_run(ssh, install_inner, pw, timeout=60)
        build_lines.append(f'[make install — exit {inst_rc}]\n{inst_out}')
        if inst_err:
            build_lines.append(f'[make install stderr]\n{inst_err}')
        build_log_path.write_text('\n\n'.join(build_lines), encoding='utf-8')

        if inst_rc != 0:
            result['failure_reason'] = 'make install failed'
            _step(result, 'install', 'fail', f'exit {inst_rc}')
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result

        # Verify binary installed — search common install locations.
        # Priority: /usr/<name> first (BINDIR UNINITIALIZED path — always our own
        # freshly compiled binary), then /usr/bin, /usr/local/bin, /usr/sbin.
        # This matters when a standard sample app already exists in /usr/bin from a
        # previous apt install or package — we must run OUR newly compiled binary,
        # not the pre-existing system one.
        misplaced_out, _, _ = ssh.run(f"test -f /usr/{binary_name} && echo /usr/{binary_name} || echo ''")
        misplaced = misplaced_out.strip()
        if misplaced:
            print(f'  [B7]    Binary at /usr/{binary_name} (BINDIR unset in cmake_install.cmake — running from there)', flush=True)
            installed_bin = misplaced
        else:
            find_out, _, _ = ssh.run(
                f"find /usr/bin /usr/local/bin /usr/sbin -maxdepth 1 -name {shlex.quote(binary_name)} 2>/dev/null | head -1"
            )
            installed_bin = find_out.strip()
            if not installed_bin:
                result['failure_reason'] = f'binary {binary_name} not found after install'
                _step(result, 'install', 'fail', result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
        print(f'  [B7]    Installed at {installed_bin}', flush=True)

        # Refresh ldconfig cache so the newly installed shared libs are found at runtime
        _sudo_run(ssh, 'ldconfig 2>&1', pw, timeout=15)
        print(f'  [B7]    ldconfig refreshed', flush=True)
        _step(result, 'install', 'ok', installed_bin)

        # ── B8: Kill stale processes before run ───────────────────────────────
        print(f'  [B8]    Killing stale {binary_name} processes ...', flush=True)
        _sudo_run(
            ssh, f"pkill -9 -f {shlex.quote(binary_name)} 2>/dev/null; true", pw, timeout=10
        )
        ssh.run('sleep 1', timeout=5)

        # Check non-zombie count
        still_running, _, _ = ssh.run(
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
                f'device may need a reboot. Run: sudo reboot'
            )
            _step(result, 'run', 'fail', f'{stale_count} stale process(es) after SIGKILL')
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result
        print(f'  [B8]    Device clear', flush=True)

        # ── B9: Ensure output dir on device ───────────────────────────────────
        ssh.run(f'mkdir -p {_OUTPUT_MEDIA_DIR}', timeout=15)

        # ── B10: Run binary ───────────────────────────────────────────────────
        wayland_prefix = (
            'export OCL_ICD_FILENAMES=/usr/lib/libOpenCL_adreno.so.1 && '
            'ulimit -n 10000 && '
            'WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1) && '
            'export XDG_RUNTIME_DIR=$(dirname "$WS") && '
            'export WAYLAND_DISPLAY=$(basename "$WS")'
        )
        sentinel = f'/tmp/deploy_sentinel_{artifact_name}'
        has_mp4_output = bool(output_path and output_path.endswith('.mp4'))
        retried = False

        if source_type == 'camera':
            # Camera runs get up to 2 attempts: the hardware encoder can be left
            # busy from a prior run, which looks like a run failure but is
            # actually a stuck cam-server. Attempt 1 runs as-is (no restart cost
            # on the common case where the encoder is fine). Only if attempt 1
            # looks like a stuck-encoder failure do we restart cam-server and
            # retry once — a second failure is treated as a real problem.
            run_cmd = (
                f'{wayland_prefix} && '
                f"timeout --signal=SIGINT --kill-after=15 30 {installed_bin} 2>&1; exit 0"
            )
            max_attempts = 2

            for attempt in range(1, max_attempts + 1):
                print(f'  [run]   camera attempt {attempt}/{max_attempts} -- '
                      f'timeout --signal=SIGINT --kill-after=15 30 {installed_bin} ...', flush=True)
                t0 = time.time()
                run_out, run_err, run_rc = ssh.run(run_cmd, timeout=60)  # 30s run + 15s kill-after + 15s buffer
                elapsed = time.time() - t0
                print(f'  [run]   Done in {elapsed:.1f}s (exit {run_rc})', flush=True)
                log_lines.append(f'[app run attempt {attempt} — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')

                log_path = out_dir / 'device.log'
                log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
                result['log_local_path'] = str(log_path)

                # Scan THIS attempt's own output only — not the cumulative log,
                # which would keep surfacing attempt 1's stale errors even
                # after a successful retry on attempt 2.
                attempt_log = run_out + '\n' + run_err
                playing, error_lines, crash_reason = _scan_log(attempt_log)
                result['playing_reached'] = playing
                result['error_lines']     = error_lines

                # Only meaningful once PLAYING was reached — don't wait 15s for a
                # moov atom that was never going to be written.
                moov_found = True
                if has_mp4_output and playing and not crash_reason:
                    moov_found = _check_moov_atom(ssh, output_path)

                needs_retry = (
                    attempt < max_attempts
                    and _camera_run_needs_retry(playing, crash_reason, moov_found, has_mp4_output)
                )
                if not needs_retry:
                    break

                print('  [cam]   Attempt looked like a stuck cam-server — '
                      'restarting cam-server and retrying once ...', flush=True)
                restart_out, _, _ = _sudo_run(
                    ssh, 'systemctl restart cam-server 2>&1', pw, timeout=20
                )
                log_lines.append(f'[cam-server restart — triggered by attempt {attempt} failure]\n{restart_out}')
                print('  [cam]   Waiting 3s for cam-server to settle ...', flush=True)
                time.sleep(3)
                retried = True
        else:
            run_cmd = (
                f'{wayland_prefix} && '
                f'{installed_bin} 2>&1'
            )
            print(f'  [run]   file-source -- {installed_bin} (to natural EOS) ...', flush=True)
            t0 = time.time()
            run_out, run_err, run_rc = ssh.run(run_cmd, timeout=300)
            elapsed = time.time() - t0
            print(f'  [run]   Done in {elapsed:.1f}s (exit {run_rc})', flush=True)
            log_lines.append(f'[app run — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')

            log_path = out_dir / 'device.log'
            log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
            result['log_local_path'] = str(log_path)

            combined = '\n'.join(log_lines)
            playing, error_lines, crash_reason = _scan_log(combined)
            result['playing_reached'] = playing
            result['error_lines']     = error_lines

            moov_found = True
            if has_mp4_output and playing and not crash_reason:
                moov_found = _check_moov_atom(ssh, output_path)

        # ── B11: Report run result ────────────────────────────────────────────
        print(f'  [log]   Saved -> {log_path}', flush=True)

        if crash_reason:
            result['failure_reason'] = crash_reason
            _step(result, 'run', 'fail', crash_reason)
            print(f'  [FAIL]  {crash_reason}', flush=True)
            _write_result(out_dir, result)
            return result

        run_detail = f'{elapsed:.1f}s, exit {run_rc}, playing={playing}'
        if retried:
            run_detail += ', succeeded after cam-server restart' if playing else ', still failed after cam-server restart'
        _step(result, 'run', 'ok' if playing else 'fail', run_detail)
        print(f'  [log]   playing_reached: {playing}', flush=True)
        if error_lines:
            print(f'  [log]   {len(error_lines)} ERROR line(s):', flush=True)
            for ln in error_lines[:5]:
                print(f'           {ln}', flush=True)
            if len(error_lines) > 5:
                print(f'           ... ({len(error_lines) - 5} more in device.log)', flush=True)

        # ── B12: Final moov-atom failure check ────────────────────────────────
        # moov_found reflects the LAST attempt made above (camera) or the single
        # attempt (file-source) — if it's still missing, report a real failure.
        if has_mp4_output and not moov_found:
            result['failure_reason'] = (
                f'Output MP4 is missing moov atom — mp4mux did not finalize. '
                f'File has {ssh.file_size(output_path):,} bytes of video data but is unplayable.'
            )
            result['output_file_size'] = f'{_human_size(ssh.file_size(output_path))} (no moov — unplayable)'
            _step(result, 'pull_output', 'fail', result['failure_reason'])
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            _write_result(out_dir, result)
            return result

        # ── B13: Check output file size + pull ────────────────────────────────
        if output_path:
            size = ssh.file_size(output_path)

            if size > 0:
                result['output_file_size'] = _human_size(size)
                ext = posixpath.splitext(output_path)[1] or '.mp4'
                local_out = out_dir / f'output{ext}'
                print(f'  [pull]  {output_path} ({result["output_file_size"]}) -> {local_out}', flush=True)
                try:
                    ssh.pull(output_path, str(local_out))
                    result['output_local_path'] = str(local_out)
                except Exception as e:
                    result['error_lines'].append(f'Output pull failed: {e}')
                    print(f'  [WARN]  Pull failed: {e}', flush=True)
                _step(result, 'pull_output', 'ok', result['output_file_size'])

            elif size == 0:
                result['output_file_size'] = '0 bytes'
                print(f'  [WARN]  Output file is 0 bytes: {output_path}', flush=True)
                # Look for any MP4 written after sentinel — covers pipelines that
                # hardcode a different output path than what's in README
                find_out, _, _ = ssh.run(
                    f"find '{posixpath.dirname(output_path)}' -name '*.mp4' "
                    f"-newer '{sentinel}' 2>/dev/null | head -1"
                )
                if find_out.strip():
                    fallback = find_out.strip()
                    fb_size = ssh.file_size(fallback)
                    if fb_size > 0:
                        result['output_file_size'] = _human_size(fb_size)
                        local_out = out_dir / 'output_fallback.mp4'
                        print(f'  [pull]  fallback: {fallback} ({result["output_file_size"]}) -> {local_out}', flush=True)
                        try:
                            ssh.pull(fallback, str(local_out))
                            result['output_local_path'] = str(local_out)
                        except Exception as e:
                            result['error_lines'].append(f'Fallback pull failed: {e}')
                            print(f'  [WARN]  Fallback pull failed: {e}', flush=True)
                        _step(result, 'pull_output', 'ok', result['output_file_size'])
                    else:
                        _step(result, 'pull_output', 'fail', result['output_file_size'])
                else:
                    _step(result, 'pull_output', 'fail', result['output_file_size'])
            else:
                result['output_file_size'] = 'missing'
                print(f'  [WARN]  Output file not found on device: {output_path}', flush=True)
                _step(result, 'pull_output', 'fail', result['output_file_size'])
        else:
            result['output_file_size'] = 'N/A (no filesink)'
            _step(result, 'pull_output', 'ok', 'N/A — display-only')

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
        try:
            ssh.close()
        except Exception:
            pass

    result_path = _write_result(out_dir, result)
    _print_summary(result)
    print(f'  [done]  result.json -> {result_path}', flush=True)
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Mode B: push C app to Ubuntu device, build on-device, run, pull output.'
    )
    p.add_argument('--artifact-path', required=True,
                   help='Folder containing main.c, CMakeLists.txt, README.md')
    p.add_argument('--output-dir',
                   default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                   help='Local dir for logs and output files (DEPLOY_OUTPUT_DIR in configs/.env)')
    p.add_argument('--device-ip',   default=os.environ.get('DEVICE_IP', ''))
    p.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''))
    p.add_argument('--host-key',    default=os.environ.get('HOST_KEY', ''))
    p.add_argument('--source-root', default=os.environ.get('SOURCE_ROOT', ''),
                   help='QIMSDK source tree root on device (SOURCE_ROOT in configs/.env)')
    p.add_argument('--dry-run', action='store_true',
                   help='Parse artifact and show plan without connecting to device')
    p.add_argument('--json', action='store_true',
                   help='Print result JSON to stdout on completion')
    args = p.parse_args()

    # Mandatory field enforcement — fail immediately with exact message
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
    # SOURCE_ROOT is optional — auto-discovered via glob if not set
    if errors:
        for e in errors:
            print(f'[FAIL]  {e}', flush=True)
        sys.exit(1)

    # Resolve relative output_dir against repo root
    output_dir = args.output_dir
    if not pathlib.Path(output_dir).is_absolute():
        output_dir = str(REPO_ROOT / output_dir)

    result = deploy_mode_b(
        artifact_path=args.artifact_path,
        deploy_output_dir=output_dir,
        ssh_cfg={
            'ip':       args.device_ip,
            'user':     args.device_user,
            'password': os.environ.get('DEVICE_PASSWORD', '') or None,
            'key_path': os.environ.get('DEVICE_KEY', '') or None,
            'host_key': args.host_key,
        },
        source_root=args.source_root or None,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, indent=2))

    ok = (
        result.get('build_passed') is True
        and result['playing_reached']
        and not result['error_lines']
        and not result.get('failure_reason')
    )
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
