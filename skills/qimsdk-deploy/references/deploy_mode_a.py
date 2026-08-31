#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
deploy_mode_a.py — Deploy and run a gst-launch pipeline on a Qualcomm Linux device.

Mode A: push pipeline.sh to device, run it, pull logs and any output files.
No build step. Works on any device with GStreamer and QIMSDK plugins installed.

Usage:
  python3 deploy_mode_a.py \\
      --artifact-path outputs/qimsdk-gstreamer-app-builder/gst-launch/qnn-htp-object-detection-file

Credentials from configs/.env (DEVICE_IP, DEVICE_USER, DEVICE_PASSWORD, HOST_KEY).
See SKILL.md for the full .env template.

Output per artifact:
  <DEPLOY_OUTPUT_DIR>/<artifact-name>/
  ├── device.log    — full GStreamer stdout/stderr (always written)
  ├── result.json   — structured result dict     (always written)
  └── output.<ext>  — pulled output file         (if pipeline has a filesink)

Exits 0 if PLAYING reached and no real errors, 1 otherwise.

What this script does NOT do:
  - Diagnose why a pipeline failed
  - Suggest pipeline fixes or changes
  - Retry failed runs, EXCEPT ONE narrow case: a camera pipeline that fails on
    its first attempt is retried once after restarting cam-server (the hardware
    encoder can be left busy from a prior run — see _run_camera_pipeline).
  - Modify the pipeline.sh before running
  Those are the user's responsibility. This script runs, reports, and pulls.
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
import time
import warnings

# Force UTF-8 stdout/stderr on Windows (cp1252 default rejects box-drawing and non-ASCII chars)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


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
        long-running commands (e.g. a 15s pipeline) correctly — blocking
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

    def file_size(self, remote_path):
        """Return remote file size in bytes, or 0 if missing/empty/inaccessible."""
        out, _, _ = self.run(f"stat -c%s '{remote_path}' 2>/dev/null || echo 0")
        try:
            return int(out.strip())
        except ValueError:
            return 0


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


# ── Artifact parsing ──────────────────────────────────────────────────────────

def _parse_pipeline(artifact_path):
    """
    Parse pipeline.sh locally without touching the device. Extracts:
      source_type  : 'file-source' | 'camera' | 'rtsp'
      output_path  : remote filesink path (or None for display-only)
      wayland_needed: True if waylandsink present
      rtsp_out     : True if qtirtspbin present (RTSP serving sink)
      input_files  : list of filesrc location= paths (may have $HOME variables)

    Note: source_type describes where frames ENTER the pipeline, never where they
    leave it. `rtspsrc` is an RTSP input source (source_type='rtsp'); `qtirtspbin`
    is an RTSP output sink (rtsp_out=True) that runs like any live sink and does
    NOT make the pipeline an 'rtsp' source. A camera→qtirtspbin pipeline is a
    'camera' source; a file→qtirtspbin pipeline is a 'file-source'.
    """
    art = pathlib.Path(artifact_path)
    pipeline_sh = art / 'pipeline.sh'
    readme_md   = art / 'README.md'

    if not pipeline_sh.exists():
        raise FileNotFoundError(f'pipeline.sh not found in {artifact_path}')

    content = pipeline_sh.read_text(encoding='utf-8', errors='replace')

    # RTSP serving sink (output side) — orthogonal to source_type
    rtsp_out = bool(re.search(r'\bqtirtspbin\b', content))

    # Source type — determined by the input element only (never by qtirtspbin)
    if re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b|\bv4l2src\b', content):
        source_type = 'camera'
    elif re.search(r'\brtspsrc\b', content):
        source_type = 'rtsp'
    else:
        source_type = 'file-source'

    # Output path — first filesink location= value
    output_path = None
    m = re.search(r'\bfilesink\s+location\s*=\s*["\']?([^\s"\'\\]+)', content)
    if m:
        output_path = m.group(1).strip('\'"')

    # Wayland display sink
    wayland_needed = bool(re.search(r'\bwaylandsink\b', content))

    # Input files — all filesrc location= values, deduplicated
    input_files = []
    for raw in re.findall(r'\bfilesrc\s+location\s*=\s*["\']?([^\s"\'!\\]+)', content):
        p = raw.strip('\'"')
        if p and p not in input_files:
            input_files.append(p)

    # Cross-check source type against README (README may be clearer than pipeline.sh).
    # Only refine when pipeline.sh gave no positive signal (file-source fallback), and
    # only using INPUT hints — rtspsrc (RTSP input) or camera. `qtirtspbin`/generic
    # "RTSP" in the README describes the output sink and must not flip the source type.
    if readme_md.exists() and source_type == 'file-source':
        readme = readme_md.read_text(encoding='utf-8', errors='replace')
        # Check RTSP before camera — RTSP is more specific.
        # Match only actual element/URL usage, not the bare word "RTSP" in
        # prose (e.g. "no RTSP-metadata channel needed" would false-positive).
        if re.search(r'\brtspsrc\b|\bqtirtspbin\b|\brtsp://', readme, re.IGNORECASE):
            source_type = 'rtsp'
        elif re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b|\bcamera\b|\bISP\b', readme, re.IGNORECASE):
            source_type = 'camera'

    return {
        'source_type':    source_type,
        'output_path':    output_path,
        'wayland_needed': wayland_needed,
        'rtsp_out':       rtsp_out,
        'input_files':    input_files,
    }


def _check_input_files(ssh, input_files):
    """
    Verify every filesrc input path exists on device before running.
    Shell variables ($HOME) are expanded on device via `eval echo`.
    Returns (all_ok, missing_list).
    """
    missing = []
    for path in input_files:
        out, _, _ = ssh.run(f'eval echo {path}')
        expanded = out.strip() or path
        result, _, _ = ssh.run(f'test -f "{expanded}" && echo EXISTS || echo MISSING')
        if result.strip() != 'EXISTS':
            missing.append(f'{path} (expanded: {expanded})')
    return len(missing) == 0, missing


# ── Log scanning ──────────────────────────────────────────────────────────────

def _scan_log(log_text):
    """
    Parse GStreamer output. Returns (playing_reached, real_error_lines, crash_reason).

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
    Decide whether a camera pipeline attempt looks like it hit a busy hardware
    encoder (stuck cam-server) and should be retried once after a cam-server
    restart, rather than reported as a final failure.

    Camera pipelines are the only ones that pay the cam-server-restart cost —
    a busy encoder from a prior run is a known, self-healing condition, not a
    real pipeline bug (see SKILL.md 'Key Pitfalls'). We reuse the existing
    playing_reached / crash / moov-atom signals instead of matching driver
    message text (e.g. 'QMMF Recorder StartCamera Failed'), which can vary
    across firmware versions and isn't reliably present in the log verbatim.

    Returns True if a retry is warranted, False if the run looks fine or the
    failure looks like a real bug (not worth masking with a retry).
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
    Verify output_path is a valid, finalized MP4 (moov atom present and
    readable) using gst-discoverer-1.0 — the same demuxer logic a real player
    uses, rather than grep'ing raw bytes for the string "moov" (which can
    false-positive/negative on padding and doesn't distinguish a truncated
    file from a complete one). By the time this is called, run_pipeline has
    already returned, meaning gst-launch-1.0 received SIGINT, ran EOS, and
    tore the pipeline down to GST_STATE_NULL on-device — mp4mux writes its
    trailer synchronously during that teardown, so there's no remaining race
    to poll for; one check is sufficient. Returns True if valid, False
    otherwise. max_wait is accepted for signature compatibility with callers
    that previously relied on a polling wait, but is unused.
    """
    disco_out, _, disco_rc = ssh.run(f"gst-discoverer-1.0 '{output_path}' 2>&1")
    return disco_rc == 0 and 'Duration:' in disco_out


def _step(result, name, status, detail=None):
    """Update a step entry in result['steps'] by name."""
    for s in result['steps']:
        if s['step'] == name:
            s['status'] = status
            s['detail'] = detail
            return


def _print_summary(result):
    ok = result['playing_reached'] and not result['error_lines'] and not result.get('failure_reason')
    status = 'PASS' if ok else 'FAIL'
    print(f'\n  +-- Mode A result: {status}', flush=True)
    print(f'  |   playing_reached : {result["playing_reached"]}', flush=True)
    print(f'  |   errors          : {len(result["error_lines"])}', flush=True)
    print(f'  |   output          : {result["output_file_size"]}', flush=True)
    if result.get('failure_reason'):
        print(f'  |   failure_reason  : {result["failure_reason"]}', flush=True)
    print(f'  +-- log             : {result.get("log_local_path")}', flush=True)


# ── Core deploy ───────────────────────────────────────────────────────────────

def deploy_mode_a(artifact_path, deploy_output_dir, ssh_cfg, dry_run=False):
    """
    Mode A deploy: push pipeline.sh, run it, pull logs and output.

    This function runs the pipeline as-is and reports what happened.
    It does NOT diagnose failures, suggest fixes, modify the pipeline,
    or retry. All that is the user's responsibility.

    Always returns a result dict and always writes result.json and device.log,
    even on failure, so failures are captured for the caller to inspect.
    """
    artifact_path = pathlib.Path(artifact_path).resolve()
    artifact_name = artifact_path.name
    out_dir = _artifact_output_dir(deploy_output_dir, artifact_name)

    print(f'\n[Mode A] {artifact_name}', flush=True)
    print(f'  artifact : {artifact_path}', flush=True)
    print(f'  output   : {out_dir}', flush=True)

    result = {
        'mode':              'A',
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
            {'step': 'verify_inputs',  'status': 'skip', 'detail': None},
            {'step': 'push_pipeline',  'status': 'skip', 'detail': None},
            {'step': 'setup_device',   'status': 'skip', 'detail': None},
            {'step': 'run_pipeline',   'status': 'skip', 'detail': None},
            {'step': 'pull_output',    'status': 'skip', 'detail': None},
        ],
    }

    # ── A0: Parse artifact locally ────────────────────────────────────────────
    # Validate required artifact files before any device connection attempt.
    readme_md = artifact_path / 'README.md'
    if not readme_md.exists():
        result['failure_reason'] = (
            f'README.md not found in artifact: {artifact_path}. '
            f'Each artifact must contain both pipeline.sh and README.md.'
        )
        _step(result, 'parse_artifact', 'fail', 'README.md missing')
        _write_result(out_dir, result)
        print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
        return result

    try:
        meta = _parse_pipeline(artifact_path)
    except Exception as e:
        result['failure_reason'] = f'Artifact parse error: {e}'
        _step(result, 'parse_artifact', 'fail', str(e))
        _write_result(out_dir, result)
        print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
        return result

    source_type = meta['source_type']
    output_path = meta['output_path']
    rtsp_out    = meta['rtsp_out']
    print(f'  source   : {source_type}', flush=True)
    print(f'  output   : {output_path or ("rtsp://<device-ip> (qtirtspbin serving sink)" if rtsp_out else "(none — display-only)")}', flush=True)
    if meta['wayland_needed']:
        print(f'  wayland  : yes (env will be set before run)', flush=True)

    # RTSP INPUT source pipelines (rtspsrc) — two cases:
    # 1. RTSP_SOURCE_URL set in env: a loopback source is already running on the device.
    #    Run the pipeline normally — it will connect to the loopback source.
    # 2. No RTSP_SOURCE_URL: cannot run automatically — print manual instructions and stop.
    # NOTE: this applies ONLY to rtspsrc (RTSP input). An RTSP serving SINK (qtirtspbin)
    # fed by a camera/file source runs automatically like any live sink — it is handled
    # by the normal run path below, same as waylandsink.
    if source_type == 'rtsp':
        rtsp_source_url = os.environ.get('RTSP_SOURCE_URL', '').strip()
        if not rtsp_source_url:
            _step(result, 'parse_artifact', 'ok', 'rtsp input — manual run required')
            result['failure_reason'] = 'RTSP: requires human review — see instructions below'
            _write_result(out_dir, result)
            print(
                '\n  [RTSP]  This pipeline uses an RTSP input source (rtspsrc) and cannot be run automatically.\n'
                '\n'
                '  To run it manually:\n'
                '  1. SSH into the device: ssh ubuntu@' + ssh_cfg.get('ip', '<DEVICE_IP>') + '\n'
                '  2. Raise fd limit:   ulimit -n 10000\n'
                '  3. Set Wayland env:  WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1)\n'
                '                       export XDG_RUNTIME_DIR=$(dirname "$WS")\n'
                '                       export WAYLAND_DISPLAY=$(basename "$WS")\n'
                '  4. Run:              bash ~/pipeline.sh\n'
                '     (push it first:   pscp pipeline.sh ubuntu@<ip>:~/pipeline.sh)\n'
                '\n'
                '  To verify it worked:\n'
                '  - Check that "Setting pipeline to PLAYING" appears in the output\n'
                '  - If the pipeline has a filesink, check the output file size:\n'
                '    ls -lh /home/ubuntu/Downloads/qimsdk_samples/media/output/\n'
                '  - Pull the output file:  pscp ubuntu@<ip>:/home/ubuntu/Downloads/qimsdk_samples/media/output/<file> .\n'
                '\n'
                '  When done, report back what you saw and paste the pipeline output.\n',
                flush=True
            )
            return result
        # Loopback source is running — proceed as a normal run
        print(f'  rtsp-src : loopback source at {rtsp_source_url}', flush=True)

    _step(result, 'parse_artifact', 'ok', f'source={source_type}, output={output_path or "display-only"}')

    if dry_run:
        print('  [dry-run] No device connection.', flush=True)
        result['failure_reason'] = 'dry-run'
        _write_result(out_dir, result)
        return result

    # ── Connect ───────────────────────────────────────────────────────────────
    ssh = _SSH(
        ip=ssh_cfg['ip'],
        user=ssh_cfg['user'],
        host_key_fp=ssh_cfg.get('host_key', ''),
        password=ssh_cfg.get('password'),
        key_path=ssh_cfg.get('key_path'),
    )

    log_lines = []

    try:
        ssh.connect()

        # Expand $HOME (and any other shell vars) in output_path on device now —
        # everything downstream (mkdir, moov-atom check, file_size, pull) needs
        # the real absolute path. Expanding late (only at pull time) meant the
        # moov-atom check ran `stat` against the literal string "$HOME/Downloads/qimsdk_samples/..." and
        # always got 0 back, misreporting a real, fully-written output file as
        # a 0-byte failure.
        if output_path and ('$HOME' in output_path or '$' in output_path):
            expanded, _, _ = ssh.run(f'eval echo {output_path}')
            expanded = expanded.strip()
            if expanded:
                output_path = expanded

        # ── A1: Verify input files on device before doing anything else ───────
        input_files = meta.get('input_files', [])
        if input_files and source_type == 'file-source':
            print(f'  [check] Verifying {len(input_files)} input file(s) on device ...', flush=True)
            files_ok, missing = _check_input_files(ssh, input_files)
            if not files_ok:
                result['failure_reason'] = (
                    f'Input file(s) not found on device: {missing}.'
                )
                _step(result, 'verify_inputs', 'fail', str(missing))
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            _step(result, 'verify_inputs', 'ok', f'{len(input_files)} file(s) present')
            print(f'  [check] Input file(s) present on device', flush=True)
        else:
            _step(result, 'verify_inputs', 'ok', 'N/A — no filesrc inputs')

        # ── A2: Push pipeline.sh, normalizing CRLF -> LF ─────────────────────
        # Windows editors produce \r\n. bash on Linux rejects \r as a command.
        remote_sh = f'/home/{ssh.user}/pipeline.sh'
        print(f'  [push]  pipeline.sh -> {remote_sh}', flush=True)
        try:
            raw = (artifact_path / 'pipeline.sh').read_bytes()
            unix_bytes = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            ssh.push_bytes(unix_bytes, remote_sh)
        except Exception as e:
            result['failure_reason'] = f'Failed to push pipeline.sh: {e}'
            _step(result, 'push_pipeline', 'fail', str(e))
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result
        _step(result, 'push_pipeline', 'ok', remote_sh)

        # ── A3: chmod +x and mkdir -p for output dir ──────────────────────────
        ssh.run(f"chmod +x '{remote_sh}'")
        if output_path:
            out_remote_dir = posixpath.dirname(output_path)
            if out_remote_dir:
                ok_out, _, _ = ssh.run(f"mkdir -p '{out_remote_dir}' && echo OK")
                if 'OK' not in ok_out:
                    result['failure_reason'] = (
                        f'Could not create output directory on device: {out_remote_dir}'
                    )
                    _step(result, 'setup_device', 'fail', result['failure_reason'])
                    _write_result(out_dir, result)
                    print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                    return result

        # ── A3b: Kill stale GStreamer processes ───────────────────────────────────
        # Stale gst-launch-1.0 processes hold hardware resources (v4l2 decoder,
        # QNN HTP/DSP backend). New runs get stuck in PREROLLING while prior
        # processes own those resources.
        #
        # IMPORTANT: Do NOT try SIGINT first. Processes stuck waiting on hardware
        # are in uninterruptible sleep and ignore SIGINT entirely. Go straight to
        # sudo SIGKILL. SIGKILL is safe here — these processes are stuck in
        # PREROLLING and have not written any output to corrupt.
        #
        # Zombie processes (state Z) are already dead — they hold no resources.
        # Exclude them from the liveness check or they cause false FAILs.
        print('  [clean] Killing stale GStreamer processes ...', flush=True)
        pw = ssh.password
        _sudo_run(ssh, "pkill -9 -f 'gst-launch-1.0' 2>/dev/null; true", pw, timeout=10)
        _sudo_run(ssh, "pkill -9 -f 'bash.*pipeline.sh' 2>/dev/null; true", pw, timeout=10)
        ssh.run('sleep 2', timeout=15)

        # Check only non-zombie live processes remain
        still_running, _, _ = ssh.run(
            "ps -eo stat,pid,cmd | grep 'gst-launch' | grep -v grep | grep -v '^Z' | wc -l"
        )
        stale_count = 0
        try:
            stale_count = int(still_running.strip())
        except ValueError:
            pass

        if stale_count > 0:
            result['failure_reason'] = (
                f'{stale_count} gst-launch-1.0 process(es) still running after SIGKILL — '
                f'device may need a reboot. Run: sudo reboot'
            )
            _step(result, 'setup_device', 'fail', f'{stale_count} stale process(es) after SIGKILL')
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result

        print('  [clean] Device clear', flush=True)
        _step(result, 'setup_device', 'ok', 'device clear')

        # ── A4: Run with Wayland env + SIGINT timeout ─────────────────────────
        # Source /etc/profile first: paramiko opens a non-interactive SSH session
        # which does NOT source /etc/profile or ~/.profile. Without this, tools
        # installed in /usr/local/bin (including gst-launch-1.0) are not on PATH.
        # Wayland env is always set — qtivoverlay probes display even for file-output.
        # SIGINT 15s for all gst-launch pipelines: enough for PLAYING + inference +
        # output write. SIGINT (not SIGTERM) lets GStreamer flush the MP4 moov atom.
        #
        # Camera pipelines get up to 2 attempts: the hardware encoder can be left
        # busy from a prior run (see SKILL.md 'Key Pitfalls'), which looks like a
        # pipeline failure but is actually a stuck cam-server. Attempt 1 runs as-is
        # (no restart cost on the common case where the encoder is fine). Only if
        # attempt 1 looks like a stuck-encoder failure do we restart cam-server and
        # retry once — a second failure is treated as a real problem, not retried again.
        sentinel = f'/tmp/deploy_sentinel_{artifact_name}'
        ssh.run(f"touch '{sentinel}'")

        run_cmd = (
            '. /etc/profile 2>/dev/null || true && '
            'export OCL_ICD_FILENAMES=/usr/lib/libOpenCL_adreno.so.1 && '
            'ulimit -n 10000 && '
            'WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1) && '
            'export XDG_RUNTIME_DIR=$(dirname "$WS") && '
            'export WAYLAND_DISPLAY=$(basename "$WS") && '
            f"timeout --signal=SIGINT --kill-after=15 45 bash '{remote_sh}' 2>&1; "
            "echo 'EXIT:$?'"
        )
        label = 'camera' if source_type == 'camera' else 'file-source'
        if rtsp_out:
            label += '+rtsp-out'

        max_attempts = 2 if source_type == 'camera' else 1
        has_mp4_output = bool(output_path and output_path.endswith('.mp4'))
        retried = False

        for attempt in range(1, max_attempts + 1):
            print(f'  [run]   {label} attempt {attempt}/{max_attempts} -- '
                  f'timeout --signal=SIGINT --kill-after=15 45 ...', flush=True)

            t0 = time.time()
            out, err, rc = ssh.run(run_cmd, timeout=80)  # 80s wall: 45s run + 15s kill-after + 20s buffer
            elapsed = time.time() - t0
            print(f'  [run]   Done in {elapsed:.1f}s (exit {rc})', flush=True)

            log_lines.append(f'[pipeline run attempt {attempt} — exit {rc}]\n{(out + chr(10) + err).strip()}')

            # ── A5: Save device log ───────────────────────────────────────────
            # Cumulative log across all attempts, for diagnostics.
            log_path = out_dir / 'device.log'
            log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
            result['log_local_path'] = str(log_path)

            # ── A6: Scan THIS attempt's own output only — report what happened
            # on the attempt that actually decides pass/fail, nothing more.
            # Scanning the cumulative log would keep surfacing attempt 1's
            # stale errors even after a successful retry on attempt 2.
            attempt_log = out + '\n' + err
            playing, error_lines, crash_reason = _scan_log(attempt_log)
            result['playing_reached'] = playing
            result['error_lines']     = error_lines

            # ── A7: Verify MP4 moov atom (only meaningful once PLAYING was reached —
            # if the pipeline never even started, don't waste 15s waiting for a
            # moov atom that was never going to be written) ───────────────────
            moov_found = True  # vacuously true when there's no MP4 output to check
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
                ssh, 'systemctl restart cam-server 2>&1', ssh.password, timeout=20
            )
            log_lines.append(f'[cam-server restart — triggered by attempt {attempt} failure]\n{restart_out}')
            print('  [cam]   Waiting 3s for cam-server to settle ...', flush=True)
            time.sleep(3)
            retried = True

        print(f'  [log]   Saved -> {log_path}', flush=True)

        if crash_reason:
            # Surface crash immediately — no point checking output file
            result['failure_reason'] = crash_reason
            _step(result, 'run_pipeline', 'fail', crash_reason)
            print(f'  [FAIL]  {crash_reason}', flush=True)
            _write_result(out_dir, result)
            return result

        run_detail = f'{elapsed:.1f}s, exit {rc}, playing={playing}'
        if retried:
            run_detail += ', succeeded after cam-server restart' if playing else ', still failed after cam-server restart'
        _step(result, 'run_pipeline', 'ok' if playing else 'fail', run_detail)
        print(f'  [log]   playing_reached: {playing}', flush=True)
        if error_lines:
            print(f'  [log]   {len(error_lines)} ERROR line(s):', flush=True)
            for ln in error_lines[:5]:
                print(f'           {ln}', flush=True)
            if len(error_lines) > 5:
                print(f'           ... ({len(error_lines) - 5} more in device.log)', flush=True)

        # ── A7b: Final moov-atom failure check ────────────────────────────────
        # moov_found reflects the LAST attempt made above — if it's still missing
        # after all attempts, report it as a real failure (unplayable output).
        if has_mp4_output and not moov_found:
            # File has data but no moov — pipeline was killed before EOS flush completed
            result['failure_reason'] = (
                f'Output MP4 is missing moov atom — mp4mux did not finalize. '
                f'The pipeline was likely killed before EOS flush completed. '
                f'File has {ssh.file_size(output_path):,} bytes of video data but is unplayable.'
            )
            result['output_file_size'] = f'{_human_size(ssh.file_size(output_path))} (no moov — unplayable)'
            _step(result, 'pull_output', 'fail', result['failure_reason'])
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            _write_result(out_dir, result)
            return result

        # ── A8: Check output file size + pull ─────────────────────────────────
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
                    # Report pull failure — don't retry or look for alternatives
                    result['error_lines'].append(f'Output pull failed: {e}')
                    print(f'  [WARN]  Pull failed: {e}', flush=True)
                _step(result, 'pull_output', 'ok', result['output_file_size'])

            elif size == 0:
                result['output_file_size'] = '0 bytes'
                print(f'  [WARN]  Output file is 0 bytes: {output_path}', flush=True)
                # Look for any MP4 written after sentinel — covers pipelines that
                # hardcode a different output path than what filesink shows
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
            result['output_file_size'] = 'N/A (display-only — no filesink)'
            _step(result, 'pull_output', 'ok', 'N/A — display-only')

    except KeyboardInterrupt:
        result['failure_reason'] = 'Interrupted by user'
        print('\n  [ABORT] Interrupted.', flush=True)
    except Exception as e:
        msg = str(e).strip() or repr(e)
        result['failure_reason'] = msg
        print(f'  [FAIL]  {msg}', flush=True)
    finally:
        # Save whatever log we have even if we're failing out
        if log_lines and not (out_dir / 'device.log').exists():
            try:
                (out_dir / 'device.log').write_text('\n\n'.join(log_lines), encoding='utf-8')
            except Exception:
                pass
        ssh.close()

    result_path = _write_result(out_dir, result)
    _print_summary(result)
    print(f'  [done]  result.json -> {result_path}', flush=True)
    return result


# ── Preflight gate ────────────────────────────────────────────────────────────

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Mode A: push gst-launch pipeline.sh to device, run, pull output.'
    )
    p.add_argument('--artifact-path', required=True,
                   help='Folder containing pipeline.sh + README.md')
    p.add_argument('--output-dir',
                   default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                   help='Local dir for logs and output files (DEPLOY_OUTPUT_DIR in configs/.env)')
    p.add_argument('--device-ip',   default=os.environ.get('DEVICE_IP', ''))
    p.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''))
    p.add_argument('--host-key',    default=os.environ.get('HOST_KEY', ''))
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
    if errors:
        for e in errors:
            print(f'[FAIL]  {e}', flush=True)
        sys.exit(1)

    # Resolve relative output_dir against repo root
    output_dir = args.output_dir
    if not pathlib.Path(output_dir).is_absolute():
        output_dir = str(REPO_ROOT / output_dir)

    result = deploy_mode_a(
        artifact_path=args.artifact_path,
        deploy_output_dir=output_dir,
        ssh_cfg={
            'ip':       args.device_ip,
            'user':     args.device_user,
            'password': os.environ.get('DEVICE_PASSWORD', '') or None,
            'key_path': os.environ.get('DEVICE_KEY', '') or None,
            'host_key': args.host_key,
        },
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, indent=2))

    ok = result['playing_reached'] and not result['error_lines'] and not result.get('failure_reason')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
