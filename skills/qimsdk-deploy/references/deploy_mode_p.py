#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""
deploy_mode_p.py — Deploy and run a QIM SDK Python app artifact on a Qualcomm Ubuntu device.

Mode P: push app.py to device, run it with the correct Wayland environment,
capture logs, and pull any output file if present.

No build step — Python apps run directly with python3.

Usage:
  python3 .claude/skills/qimsdk-deploy/references/deploy_mode_p.py \\
      --artifact-path path/to/artifact/

Artifact must contain:
  main.py    — the Python application (imports from qimsdk); app.py accepted as legacy fallback
  README.md  — describes input files, output path, and how to run

Credentials from configs/.env (DEVICE_IP, DEVICE_USER, DEVICE_KEY/PASSWORD, HOST_KEY).

Output per artifact:
  <DEPLOY_OUTPUT_DIR>/<artifact-name>/
  ├── device.log   — full stdout/stderr from python3 app.py (always written)
  ├── result.json  — structured result dict (always written)
  └── output.<ext> — pulled output file (if app writes to a file)

Exits 0 if PLAYING reached and no real errors, 1 otherwise.

Stop behavior:
  qimsdk Pipeline.execute() does not respond to SIGINT or SIGTERM — only SIGKILL
  stops it reliably. The script uses `timeout --signal=SIGKILL` and cleans up
  stale processes with pkill -9 before and after each run.
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
import sys
import threading
import time
import warnings

# Force UTF-8 stdout/stderr on Windows
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


# ── .env loader ───────────────────────────────────────────────────────────────

def _load_dotenv():
    """Load configs/.env — CWD takes priority over script location."""
    cwd = pathlib.Path.cwd()
    here = pathlib.Path(__file__).resolve().parent
    for start in [cwd, here]:
        for candidate in [start] + list(start.parents):
            env_candidate = candidate / 'configs' / '.env'
            if env_candidate.exists():
                env_file = env_candidate
                root = candidate
                break
        else:
            continue
        break
    else:
        return cwd
    loaded = []
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val
            loaded.append(key)
    if loaded:
        print(f'  [env]  Loaded from {env_file}: {", ".join(loaded)}', flush=True)
    return root

REPO_ROOT = _load_dotenv()


# ── Benign log noise ──────────────────────────────────────────────────────────

_BENIGN = [
    'MapGbmBufInfoAddress: Mmap failed',
    'Failed to initialize Wayland EGL display',
    'Failed to initialize X11 EGL display',
    'SetupXcbConnection: Failed to get xcb connection',
    'Initialize: Failed to setup xcb connection',
    'tiling.h WARNING',
    'concat_opts WARNING',
    'Internal data stream error',
    'Got EOS from element',
    'MESA-LOADER: failed to retrieve device information',
    'failed to get driver name',
    'bo cpu address failed',             # benign GEM/DMA noise on Qualcomm devices
    'GEM Handle for BO=',                # buffer object cleanup during shutdown
    'Failed to set RPC polling time',    # benign QNN HTP init noise (same as Mode C/D)
    'Failed to set rpc polling',
    'Failed to set powerConfig',
]


# ── SSH session ───────────────────────────────────────────────────────────────

class _SSH:
    """Minimal Paramiko SSH wrapper — same as other deploy_mode_*.py scripts."""

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
            key_mode = pathlib.Path(self.key_path).stat().st_mode & 0o777
            if key_mode & 0o077:
                raise RuntimeError(
                    f'SSH key {self.key_path} has insecure permissions {oct(key_mode)}. '
                    f'Fix with: chmod 600 "{self.key_path}"'
                )
        if not use_key and not self.password:
            raise RuntimeError(
                'No SSH auth configured — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env'
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
        client.connect(
            self.ip, username=self.user,
            key_filename=self.key_path if use_key else None,
            password=self.password if not use_key else None,
            timeout=15, banner_timeout=20, auth_timeout=15,
            allow_agent=False, look_for_keys=False,
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
        self.connect()
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            self.run(f"mkdir -p '{remote_dir}'")
        with self._sftp.open(remote_path, 'wb') as f:
            f.write(content_bytes)

    def pull(self, remote_path, local_path):
        self.connect()
        local_path = pathlib.Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        lp = str(local_path)
        if sys.platform == 'win32' and not lp.startswith('\\\\?\\'):
            lp = '\\\\?\\' + os.path.abspath(lp)
        self._sftp.get(remote_path, lp)

    def file_size(self, remote_path):
        out, _, _ = self.run(f"stat -c%s '{remote_path}' 2>/dev/null || echo 0")
        try:
            return int(out.strip())
        except ValueError:
            return 0


# ── Artifact parsing ──────────────────────────────────────────────────────────

def _resolve_app_file(artifact_path):
    """Return the app entry-point filename for this artifact.

    The qimsdk-python-app-builder emits main.py. Older/hand-written artifacts may
    use app.py, so it is accepted as a fallback. main.py wins if both exist.
    Returns the bare filename (e.g. 'main.py') or None if neither is present.
    """
    art = pathlib.Path(artifact_path)
    for name in ('main.py', 'app.py'):
        if (art / name).exists():
            return name
    return None


def _parse_artifact(artifact_path):
    """
    Parse the app entry point (main.py, or legacy app.py) and README.md locally.

    Returns dict:
      app_file     : 'main.py' | 'app.py'
      source_type  : 'file-source' | 'camera'
      output_path  : absolute path on device (or None for display-only) — first
                     filesink location (back-compat scalar)
      output_paths : list of ALL filesink locations found in the app source,
                     order-preserved and de-duplicated (single entry for the
                     common single-filesink case)
      rtsp_out     : True if the app source contains a qtirtspbin serving sink

    source_type from the app source (filesrc/qtiqmmfsrc/qticamsrc), README as
    fallback. output_path/output_paths from the app source's filesink location(s),
    README Placeholders table (OUTPUT_FILE row) as fallback — only set if the app
    source contains 'filesink', otherwise None/[] (waylandsink/RTSP-only).
    """
    art = pathlib.Path(artifact_path)
    readme_md = art / 'README.md'

    app_file = _resolve_app_file(art)
    if app_file is None:
        raise ValueError(f'no app entry point found in {artifact_path} (expected main.py or app.py)')
    app_py = art / app_file

    app_text = app_py.read_text(encoding='utf-8', errors='replace')

    # Source type: app source is authoritative. A camera element -> camera. Any
    # other known source (videotestsrc/filesrc/appsrc) -> file-source, and we do
    # NOT consult the README camera keyword (which false-positives on phrases like
    # "no camera required"). Only when the app names no recognizable source do we
    # fall back to the README keyword.
    if re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b', app_text):
        source_type = 'camera'
    elif re.search(r'\bvideotestsrc\b|\bfilesrc\b|\bappsrc\b', app_text):
        source_type = 'file-source'
    elif readme_md.exists():
        readme = readme_md.read_text(encoding='utf-8', errors='replace')
        if re.search(r'\bqtiqmmfsrc\b|\bqticamsrc\b|\bcamera\b', readme, re.IGNORECASE):
            source_type = 'camera'
        else:
            source_type = 'file-source'
    else:
        source_type = 'file-source'

    # Output path(s) — only if app has filesink. Prefer the location(s) straight
    # from the app source (authoritative), then fall back to the README table.
    # output_paths collects ALL filesink locations (order-preserved, deduped);
    # output_path stays the scalar first-one for back-compat.
    output_path = None
    output_paths = []
    has_filesink = bool(re.search(r'\bfilesink\b', app_text, re.IGNORECASE))

    if has_filesink:
        # `.set("location", OUTPUT_FILE)` passes a bare variable name, not a
        # quoted literal — resolve only the variable name(s) actually used as
        # a filesink location argument, so an unrelated INPUT_FILE assignment
        # is never substituted in and mistaken for an output path. Supports
        # the three literal-assignment styles seen in generated apps:
        #   VAR = "/abs/path"
        #   VAR = os.path.expandvars("$HOME/rel/path")
        #   VAR = f"{os.environ['HOME']}/rel/path" / f"{HOME}/rel/path"
        location_vars = set()
        for loc_match in re.finditer(
                r'\.set\(\s*["\']location["\']\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)',
                app_text):
            window_start = max(0, loc_match.start() - 200)
            context = app_text[window_start:loc_match.start()]
            if re.search(r'["\']filesink["\']', context):
                location_vars.add(loc_match.group(1))
        resolved_text = app_text
        for var_name in location_vars:
            m_assign = re.search(
                r'^' + re.escape(var_name) + r'\s*=\s*(.+?)\s*$',
                app_text, re.MULTILINE)
            if not m_assign:
                continue
            rhs = m_assign.group(1)
            m_lit = re.match(r'f?["\'](/[^"\']*)["\']$', rhs)
            m_expandvars = re.match(r'os\.path\.expandvars\(\s*f?["\']\$HOME(/[^"\']*)["\']\s*\)$', rhs)
            m_fstring_home = re.match(r'f["\']\{(?:os\.environ\[.HOME.\]|HOME)\}(/[^"\']*)["\']$', rhs)
            if m_lit:
                var_val = m_lit.group(1)
            elif m_expandvars:
                var_val = '/root' + m_expandvars.group(1)
            elif m_fstring_home:
                var_val = '/root' + m_fstring_home.group(1)
            else:
                continue
            resolved_text = re.sub(
                r'\.set\(\s*["\']location["\']\s*,\s*' + re.escape(var_name) + r'\s*\)',
                f'.set("location", "{var_val}")',
                resolved_text)

        for loc in re.findall(
                r'["\']location["\']\s*[,:]\s*["\'](/(?:root|home/\w+|tmp)/[^"\']+)["\']',
                resolved_text):
            loc = loc.strip()
            if loc not in output_paths:
                output_paths.append(loc)
        if output_paths:
            output_path = output_paths[0]

    if has_filesink and output_path is None and readme_md.exists():
        readme = readme_md.read_text(encoding='utf-8', errors='replace')
        _abs = r'[`\s]*(/(?:root|home/\w+|tmp)/[^\s`|<>\'"\\]+)'
        # OUTPUT_FILE row in table; also matches "Output video |", "Output file |" etc.
        m = re.search(r'(?:OUTPUT_FILE[^|]*\|[^|`/]*|output[\w\s]*[|:]\s*)' + _abs, readme, re.IGNORECASE)
        if not m:
            m = re.search(_abs + r'[^|\n]*\|[^|\n]*OUTPUT_FILE', readme, re.IGNORECASE)
        if not m:
            # Last resort: any .mp4 path not on an INPUT_FILE row
            for line in readme.splitlines():
                if re.search(r'\bINPUT_FILE\b|\bInput video\b', line, re.IGNORECASE):
                    continue
                lm = re.search(r'[`\s](/(?:root|home/\w+|tmp)/[^\s`|<>\'"\\]+\.mp4)', line, re.IGNORECASE)
                if lm:
                    m = lm
                    break
        if m:
            output_path = m.group(1).strip()
            output_paths = [output_path]

    # RTSP-out: a serving sink in the pipeline (qtirtspbin) rather than a filesink.
    rtsp_out = bool(re.search(r'\bqtirtspbin\b', app_text))

    return {
        'app_file': app_file,
        'source_type': source_type,
        'output_path': output_path,
        'output_paths': output_paths,
        'rtsp_out': rtsp_out,
    }


# ── Log scanning ──────────────────────────────────────────────────────────────

def _scan_log(log_text):
    """
    Parse qimsdk Python app output.
    PLAYING detection: '[IMSDK]...[STATE][<name>] PLAYING'
    ERROR filtering: same benign list as other modes.
    Returns (playing_reached, real_error_lines, crash_reason).
    """
    playing = bool(re.search(
        r'\[STATE\].*PLAYING'           # qimsdk Python format
        r'|Setting pipeline to PLAYING'  # gst-launch (shouldn't appear but safe)
        r'|Pipeline state changed from PAUSED to PLAYING',
        log_text
    ))

    crash_reason = None
    if 'Caught SIGSEGV' in log_text:
        crash_reason = (
            'Pipeline crashed with SIGSEGV. Device state issue (GPU/driver memory) — '
            'retried once automatically; if it persists, wait for the device to go idle and re-run.'
        )
    elif 'Caught SIGABRT' in log_text:
        crash_reason = 'Pipeline crashed with SIGABRT.'

    errors = []
    for line in log_text.splitlines():
        stripped = line.strip()
        if not any(tok in stripped.upper() for tok in ('ERROR', 'CRITICAL')):
            continue
        if any(b in stripped for b in _BENIGN):
            continue
        errors.append(stripped)

    return playing, errors, crash_reason


# ── Output helpers ────────────────────────────────────────────────────────────

def _artifact_output_dir(deploy_output_dir, artifact_name):
    d = pathlib.Path(deploy_output_dir) / artifact_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_result(out_dir, result):
    p = out_dir / 'result.json'
    p.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return str(p)


def _human_size(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f} {unit}' if unit != 'bytes' else f'{n} bytes'
        n /= 1024


def _check_moov_atom(ssh, output_path):
    """
    Verify output_path is a finalized, playable MP4 using gst-discoverer-1.0 —
    the same demuxer logic a real player uses, rather than grep'ing raw bytes for
    "moov". An MP4 whose moov atom was never written (app SIGKILL'd mid-stream) is
    unplayable and must be reported honestly, not counted as a pass. Returns True
    if valid. Same helper as Mode A/D.
    """
    disco_out, _, disco_rc = ssh.run(f"gst-discoverer-1.0 '{output_path}' 2>&1")
    return disco_rc == 0 and 'Duration:' in disco_out


def _check_rtsp_listening(ssh):
    """
    Check whether the device has an RTSP server LISTENing on port 8900 by reading
    /proc/net/tcp directly — more reliable than depending on netstat/ss being
    installed on a minimal Yocto/QLI rootfs. /proc/net/tcp entries are hex:
    'local_address' is '<hex IP>:<hex port>' and 'st' (state) is a hex code —
    0A means TCP_LISTEN. Port 8900 decimal = 0x22C4. Returns True if found.
    """
    out, _, _ = ssh.run("cat /proc/net/tcp 2>/dev/null")
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local_addr, state = fields[1], fields[3]
        if ':' not in local_addr:
            continue
        _, port_hex = local_addr.split(':', 1)
        if port_hex.upper() == '22C4' and state.upper() == '0A':
            return True
    return False


def _print_summary(result):
    ok = result['playing_reached'] and not result['error_lines'] and not result.get('failure_reason')
    print(f'\n  +-- Mode P result: {"PASS" if ok else "FAIL"}', flush=True)
    print(f'  |   playing_reached : {result["playing_reached"]}', flush=True)
    print(f'  |   errors          : {len(result["error_lines"])}', flush=True)
    print(f'  |   output          : {result["output_file_size"]}', flush=True)
    if result.get('failure_reason'):
        print(f'  |   failure_reason  : {result["failure_reason"]}', flush=True)
    print(f'  +-- log             : {result.get("log_local_path")}', flush=True)


# ── Core deploy ───────────────────────────────────────────────────────────────

def deploy_mode_p(artifact_path, deploy_output_dir, ssh_cfg, dry_run=False,
                   phase='all', run_timeout=0):
    """
    Mode P deploy: push the app (main.py, or legacy app.py) to the device, run it
    with the qimsdk environment, capture logs, and pull any output file.

    Stop behavior: qimsdk Pipeline.execute() ignores SIGINT and SIGTERM.
    Only SIGKILL stops it. All runs use timeout --signal=SIGKILL.
    """
    artifact_path = pathlib.Path(artifact_path).resolve()
    artifact_name = artifact_path.name
    out_dir = _artifact_output_dir(deploy_output_dir, artifact_name)

    result = {
        'mode':              'P',
        'phase':             phase,
        'artifact':          artifact_name,
        'playing_reached':   False,
        'error_lines':       [],
        'output_file_size':  'missing',
        'output_local_path': None,
        'output_local_paths': [],
        'rtsp_out':          False,
        'rtsp_url':          None,
        'log_local_path':    str(out_dir / 'device.log'),
        'failure_reason':    None,
        'steps': [
            {'step': 'parse_artifact', 'status': 'skip', 'detail': None},
            {'step': 'push_app',       'status': 'skip', 'detail': None},
            {'step': 'run',            'status': 'skip', 'detail': None},
            {'step': 'pull_output',    'status': 'skip', 'detail': None},
        ],
    }

    def _step(name, status, detail=None):
        for s in result['steps']:
            if s['step'] == name:
                s['status'] = status
                s['detail'] = detail

    # ── P0: Parse artifact locally ────────────────────────────────────────────
    try:
        meta = _parse_artifact(artifact_path)
    except Exception as e:
        result['failure_reason'] = f'Artifact parse error: {e}'
        _step('parse_artifact', 'fail', str(e))
        _write_result(out_dir, result)
        print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
        return result

    source_type = meta['source_type']
    output_path = meta['output_path']
    output_paths = meta['output_paths']
    rtsp_out    = meta['rtsp_out']
    app_file    = meta['app_file']
    result['rtsp_out'] = rtsp_out
    # Push to isolated temp dir — avoids home dir clutter and name collisions.
    # The remote filename mirrors the artifact's (main.py or legacy app.py).
    remote_dir = f'/tmp/deploy_p_{artifact_name}'
    remote_app = f'{remote_dir}/{app_file}'

    print(f'\n[Mode P] {artifact_name}', flush=True)
    print(f'  artifact : {artifact_path}', flush=True)
    print(f'  output   : {out_dir}', flush=True)
    print(f'  app      : {app_file}', flush=True)
    print(f'  source   : {source_type}', flush=True)
    print(f'  output   : {output_path or "(none — display-only)"}', flush=True)
    if rtsp_out:
        print(f'  rtsp-out : detected (qtirtspbin)', flush=True)
    _step('parse_artifact', 'ok', f'app={app_file}, source={source_type}')

    if dry_run:
        print('  [dry-run] No device connection.', flush=True)
        result['failure_reason'] = 'dry-run'
        _write_result(out_dir, result)
        return result

    # ── P1: Connect SSH ───────────────────────────────────────────────────────
    ssh = _SSH(
        ip=ssh_cfg['ip'], user=ssh_cfg['user'],
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

    log_lines = []

    try:
        if phase in ('build', 'all'):
            # ── P2: Push app source ───────────────────────────────────────────────
            print(f'  [P2]    Pushing {app_file} -> {remote_app}', flush=True)
            raw = (artifact_path / app_file).read_bytes()
            unix_bytes = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            try:
                ssh.push_bytes(unix_bytes, remote_app)
            except Exception as e:
                result['failure_reason'] = f'Failed to push {app_file}: {e}'
                _step('push_app', 'fail', str(e))
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result

            v_out, _, _ = ssh.run(f"test -f '{remote_app}' && echo OK || echo MISSING")
            if 'OK' not in v_out:
                result['failure_reason'] = f'{app_file} not found on device after push'
                _step('push_app', 'fail', result['failure_reason'])
                _write_result(out_dir, result)
                print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                return result
            print(f'  [P2]    {app_file} pushed and verified', flush=True)
            _step('push_app', 'ok', remote_app)

        if phase == 'build':
            # Build phase stops here — the app has been pushed and verified.
            # Skip P3-P8 (camera pre-run, run, scan, pull); the run phase
            # will pick up from the surviving remote_dir.
            result_path = _write_result(out_dir, result)
            print(f'  [done]  build phase complete — result.json -> {result_path}', flush=True)
            return result

        if phase == 'run':
            # Resilience: if the app wasn't pushed in a prior build phase
            # (or the remote temp dir was cleaned up), push it now.
            v_out, _, _ = ssh.run(f"test -f '{remote_app}' && echo OK || echo MISSING")
            if 'OK' not in v_out:
                print(f'  [P2]    {remote_app} missing — pushing {app_file} (resilience) ...', flush=True)
                raw = (artifact_path / app_file).read_bytes()
                unix_bytes = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                try:
                    ssh.push_bytes(unix_bytes, remote_app)
                except Exception as e:
                    result['failure_reason'] = f'Failed to push {app_file}: {e}'
                    _step('push_app', 'fail', str(e))
                    _write_result(out_dir, result)
                    print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                    return result
                v_out, _, _ = ssh.run(f"test -f '{remote_app}' && echo OK || echo MISSING")
                if 'OK' not in v_out:
                    result['failure_reason'] = f'{app_file} not found on device after push'
                    _step('push_app', 'fail', result['failure_reason'])
                    _write_result(out_dir, result)
                    print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                    return result
                print(f'  [P2]    {app_file} pushed and verified', flush=True)
            _step('push_app', 'ok', remote_app)

        # ── P3: Camera pre-run ────────────────────────────────────────────────
        if source_type == 'camera':
            print('  [P3]    Restarting cam-server (required before camera run) ...', flush=True)
            cam_out, _, _ = ssh.run('sudo systemctl restart cam-server 2>&1', timeout=20)
            log_lines.append(f'[cam-server restart]\n{cam_out}')
            print('  [P3]    Waiting 3s for cam-server to settle ...', flush=True)
            time.sleep(3)

        # ── P4: Ensure output dir(s) ──────────────────────────────────────────
        if output_paths:
            made_dirs = []
            for p in output_paths:
                d = posixpath.dirname(p)
                if d and d not in made_dirs:
                    ssh.run(f"mkdir -p '{d}'", timeout=10)
                    made_dirs.append(d)

        # ── P5: Kill stale processes ──────────────────────────────────────────
        # Use the full remote path for pkill to avoid killing unrelated python3 processes
        kill_pattern = remote_app  # e.g. /tmp/deploy_p_<name>/app.py
        print(f'  [P5]    Killing any stale python3 processes for {remote_dir}/ ...', flush=True)
        ssh.run(f"pkill -9 -f '{kill_pattern}' 2>/dev/null; sleep 1; true", timeout=10)

        # Check non-zombie count
        still_running, _, _ = ssh.run(
            f"ps -eo stat,pid,cmd | grep '{kill_pattern}' | grep -v grep | grep -v '^Z' | wc -l"
        )
        try:
            stale_count = int(still_running.strip())
        except ValueError:
            stale_count = 0
        if stale_count > 0:
            result['failure_reason'] = (
                f'{stale_count} python3 process(es) still running after SIGKILL — '
                f'the device is busy; wait for it to go idle and re-run.'
            )
            _step('run', 'fail', result['failure_reason'])
            _write_result(out_dir, result)
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return result
        print(f'  [P5]    Device clear', flush=True)

        # ── P6: Run ───────────────────────────────────────────────────────────
        # qimsdk Pipeline.execute() ignores SIGINT/SIGTERM — use SIGKILL only.
        #   file-source : 45s (model load ~15s + PLAYING + frames); no natural EOS.
        #   camera      : 30s, launched detached via nohup so a camera pipeline that
        #                 saturates the device and drops the SSH channel does not look
        #                 like a run failure — the log is written on-device and read
        #                 back in a fresh command.
        wayland_env = (
            'export OCL_ICD_FILENAMES=/usr/lib/libOpenCL_adreno.so.1 && '
            'ulimit -n 10000 && '
            'WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1) && '
            'export XDG_RUNTIME_DIR=$(dirname "$WS") && '
            'export WAYLAND_DISPLAY=$(basename "$WS")'
        )
        # PATH needed for non-interactive SSH sessions (python3 may not be on PATH)
        path_env = 'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        env_prefix = f'{path_env} && {wayland_env}'

        camera = source_type == 'camera'
        run_seconds = run_timeout if run_timeout > 0 else (30 if camera else 45)
        rtsp_serve_n = run_timeout if run_timeout > 0 else 30
        remote_log = f'{remote_dir}/run.log'

        def _run_once():
            """Run the app once; return (out, err, rc, elapsed, rtsp_port_listening).
            Camera runs detached via nohup + logfile so an SSH channel drop doesn't
            destroy the result. RTSP-out apps run as a bounded foreground-ish serve
            (SIGKILL timeout) in a background thread so the device's port-8900 LISTEN
            state can be polled while the serve window is open."""
            t0 = time.time()
            if rtsp_out:
                # RTSP-out: bounded foreground-ish serve via the same SIGKILL timeout
                # wrapper, regardless of source_type — the pipeline feeds a qtirtspbin
                # serving sink that must be bounded, not run to natural EOS.
                cmd = (
                    f"{env_prefix} && "
                    f"timeout --signal=SIGKILL --kill-after=5 {rtsp_serve_n} "
                    f"python3 '{remote_app}' 2>&1; exit 0"
                )
                holder = {}

                def _bg():
                    holder['res'] = ssh.run(cmd, timeout=rtsp_serve_n + 20)

                t = threading.Thread(target=_bg)
                t.start()
                # Give the pipeline time to reach PLAYING and bind the RTSP port
                # before checking — but not longer than the serve window itself.
                time.sleep(min(5, rtsp_serve_n))
                port_listening = _check_rtsp_listening(ssh)
                t.join()
                out, err, rc = holder['res']
                return out, err, rc, time.time() - t0, port_listening
            if source_type == 'camera':
                launch = (
                    f"{env_prefix} && "
                    f"nohup sh -c \"timeout --signal=SIGKILL --kill-after=5 {run_seconds} "
                    f"python3 '{remote_app}' > '{remote_log}' 2>&1; echo EXIT:$? >> '{remote_log}'\" "
                    f">/dev/null 2>&1 & echo LAUNCHED"
                )
                ssh.run(launch, timeout=20)
                # Wait out the run window + margin, then read the on-device log back.
                wait_s = run_seconds + 12
                print(f'  [run]   camera launched (nohup) — waiting {wait_s}s then reading device log ...', flush=True)
                time.sleep(wait_s)
                out, _, _ = ssh.run(f"cat '{remote_log}' 2>/dev/null", timeout=30)
                # Anchor to start-of-line so an app that emits "EXIT:0" in its
                # own log output does not shadow the real sentinel line.
                # findall gives all matches; take the last one (the sentinel).
                rc_matches = re.findall(r'^EXIT:(\d+)', out, re.MULTILINE)
                rc = int(rc_matches[-1]) if rc_matches else 0
                return out, '', rc, time.time() - t0, False
            cmd = (
                f"{env_prefix} && "
                f"timeout --signal=SIGKILL --kill-after=5 {run_seconds} "
                f"python3 '{remote_app}' 2>&1; exit 0"
            )
            out, err, rc = ssh.run(cmd, timeout=run_seconds + 20)
            return out, err, rc, time.time() - t0, False

        if rtsp_out:
            label = f'{source_type}+rtsp-out — timeout SIGKILL {rtsp_serve_n}s'
        else:
            label = f'{source_type} — timeout SIGKILL {run_seconds}s'
        print(f'  [run]   {label} ...', flush=True)
        run_out, run_err, run_rc, elapsed, rtsp_port_listening = _run_once()
        print(f'  [run]   Done in {elapsed:.1f}s (exit {run_rc})', flush=True)
        log_lines.append(f'[app run — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')

        # ── P7: Save log and scan ─────────────────────────────────────────────
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

        # SIGSEGV auto-retry once after a settle — a device-state timing issue
        # (GPU/display buffers not yet released), not a code bug. Working pattern
        # from Mode A: re-run, then scan only the retry's own output.
        if crash_reason:
            print('  [WARN]  SIGSEGV detected — waiting 15s for driver to settle, then retrying once ...', flush=True)
            time.sleep(15)
            run_out, run_err, run_rc, elapsed, rtsp_port_listening = _run_once()
            log_lines.append(f'[app run RETRY — exit {run_rc}]\n{(run_out + chr(10) + run_err).strip()}')
            log_path.write_text('\n\n'.join(log_lines), encoding='utf-8')
            playing, error_lines, crash_reason = _scan_log(run_out + '\n' + run_err)
            if rtsp_out and rtsp_port_listening:
                playing = True
            result['playing_reached'] = playing
            result['error_lines']     = error_lines
            if crash_reason:
                result['failure_reason'] = crash_reason
                _step('run', 'fail', crash_reason)
                print(f'  [FAIL]  {crash_reason}', flush=True)
                _write_result(out_dir, result)
                return result

        _step('run', 'ok' if playing else 'fail',
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

        # ── P8: moov-atom check + pull output file(s) (if any) ────────────────
        if not output_paths:
            # Pure rtsp-out (no filesink) or true display-only app — nothing to pull.
            result['output_file_size'] = 'N/A (RTSP stream)' if rtsp_out else 'N/A (no filesink)'
            _step('pull_output', 'ok', result['output_file_size'])
        else:
            if output_path and output_path.endswith('.mp4') and playing:
                if not _check_moov_atom(ssh, output_path):
                    size = ssh.file_size(output_path)
                    result['failure_reason'] = (
                        'Output MP4 is missing/invalid moov atom — the file was not finalized '
                        f'(app likely SIGKILL\'d mid-stream). {size:,} bytes but unplayable.'
                    )
                    result['output_file_size'] = f'{_human_size(size)} (no moov — unplayable)'
                    _step('pull_output', 'fail', result['failure_reason'])
                    print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
                    _write_result(out_dir, result)
                    return result

            any_pull_failed = False
            first_size_label = None
            for i, p in enumerate(output_paths):
                size = ssh.file_size(p)
                ext = posixpath.splitext(p)[1] or '.mp4'
                local_out = out_dir / (f'output{ext}' if i == 0 else f'output_{i + 1}{ext}')

                if size > 0:
                    size_label = _human_size(size)
                    if i == 0:
                        first_size_label = size_label
                    print(f'  [pull]  {p} ({size_label}) -> {local_out}', flush=True)
                    try:
                        ssh.pull(p, str(local_out))
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
                    print(f'  [WARN]  Output file not found: {p}', flush=True)

            result['output_file_size'] = first_size_label if first_size_label else 'missing'
            _step('pull_output', 'fail' if any_pull_failed else 'ok', result['output_file_size'])

    except KeyboardInterrupt:
        result['failure_reason'] = 'Interrupted by user'
        print('\n  [ABORT] Interrupted.', flush=True)
    except Exception as e:
        msg = str(e).strip() or repr(e)
        result['failure_reason'] = msg
        print(f'  [FAIL]  {msg}', flush=True)
    finally:
        if log_lines and not (out_dir / 'device.log').exists():
            try:
                (out_dir / 'device.log').write_text('\n\n'.join(log_lines), encoding='utf-8')
            except Exception:
                pass
        # Clean up the temp deploy dir from device — but not in build phase,
        # since the run phase needs the pushed app to still be there.
        if phase != 'build':
            try:
                ssh.run(f"rm -rf '{remote_dir}'", timeout=5)
            except Exception:
                pass
        try:
            ssh.close()
        except Exception:
            pass

    if phase == 'run':
        # Carry forward the build phase's push_app step status, if available.
        prior_path = out_dir / 'result.json'
        if prior_path.exists():
            try:
                prior = json.loads(prior_path.read_text(encoding='utf-8'))
                prior_push = next(
                    (s for s in prior.get('steps', []) if s.get('step') == 'push_app'), None
                )
                if prior_push and prior_push.get('status') == 'ok':
                    _step('push_app', prior_push['status'], prior_push.get('detail'))
            except Exception:
                pass

    result_path = _write_result(out_dir, result)
    _print_summary(result)
    print(f'  [done]  result.json -> {result_path}', flush=True)
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Mode P: push Python qimsdk app to device, run, capture logs.'
    )
    p.add_argument('--artifact-path', required=True,
                   help='Folder containing main.py (or legacy app.py) and README.md')
    p.add_argument('--output-dir',
                   default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                   help='Local dir for logs and output files')
    p.add_argument('--device-ip',   default=os.environ.get('DEVICE_IP', ''))
    p.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''))
    p.add_argument('--host-key',    default=os.environ.get('HOST_KEY', ''))
    p.add_argument('--dry-run', action='store_true',
                   help='Parse artifact and show plan without connecting to device')
    p.add_argument('--phase', choices=('build', 'run', 'all'), default='all',
                   help='build: push app only. run: assume app already pushed (push if '
                        'missing), then run/scan/pull. all: full deploy (default).')
    p.add_argument('--run-timeout', type=int, default=0,
                   help='Override the file-source run cap in seconds (0 = use default 45s; '
                        'camera timeout is unaffected).')
    p.add_argument('--json', action='store_true',
                   help='Print result JSON to stdout on completion')
    args = p.parse_args()

    errors = []
    if not args.device_ip and not args.dry_run:
        errors.append('DEVICE_IP not set in configs/.env')
    if not args.device_user and not args.dry_run:
        errors.append('DEVICE_USER not set in configs/.env')
    if not os.environ.get('DEVICE_PASSWORD', '') and \
       not os.environ.get('DEVICE_KEY', '') and not args.dry_run:
        errors.append(
            'No device auth configured — set DEVICE_KEY or DEVICE_PASSWORD in configs/.env'
        )
    if not args.output_dir:
        errors.append('DEPLOY_OUTPUT_DIR not set in configs/.env')
    if errors:
        for e in errors:
            print(f'[FAIL]  {e}', flush=True)
        sys.exit(1)

    output_dir = args.output_dir
    if not pathlib.Path(output_dir).is_absolute():
        output_dir = str(REPO_ROOT / output_dir)

    result = deploy_mode_p(
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
        phase=args.phase,
        run_timeout=args.run_timeout,
    )

    if args.json:
        print(json.dumps(result, indent=2))

    if args.phase == 'build':
        push_step = next((s for s in result['steps'] if s['step'] == 'push_app'), None)
        ok = (push_step is not None and push_step['status'] == 'ok'
              and not result.get('failure_reason'))
    elif args.phase == 'run':
        ok = (
            result['playing_reached']
            and not result['error_lines']
            and not result.get('failure_reason')
        )
    else:
        ok = (
            result['playing_reached']
            and not result['error_lines']
            and not result.get('failure_reason')
        )
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
