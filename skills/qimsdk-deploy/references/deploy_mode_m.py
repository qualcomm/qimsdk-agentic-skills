#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""
deploy_mode_m.py — Cross-compile an IMSDK qtimlpostprocess post-process module to
aarch64 on this Linux/WSL host, install it on a QLI device, and prove it registered.

Mode M targets the qimsdk-model-onboarding artifact contract:
  imsdk-postproc-<module>/ — ml-postprocess-<module>.cc + CMakeLists.txt for a
  qtimlpostprocess post-process MODULE, not a full application.

Two things make Mode M structurally different from Modes A/B/C/D/P:
  - There is no separate Linux workstation. The cross-compile runs locally, on
    whatever Linux/WSL host runs the skill, with the distro cross-compiler (see
    workspace_setup_m.py). Only the install step needs SSH.
  - There is no run/pull step. The deliverable is a shared library registered
    with the plugin, not a pipeline execution. Building a pipeline that uses
    module=<name> is qimsdk-gstreamer-app-builder's job.

The definition of done is NOT a successful copy -- it is the module appearing in
`gst-inspect-1.0 qtimlpostprocess`'s module= enum. A copy proves the bytes moved;
only the enum proves the plugin accepted the library. `module_registered: true` in
result.json is the only pass.

Usage:
  python3 deploy_mode_m.py --artifact-path outputs/qimsdk-model-onboarding/imsdk-postproc-yolov9-det
  python3 deploy_mode_m.py --artifact-path <path> --dry-run
  python3 deploy_mode_m.py --artifact-path <path> --force --static-cxx

Credentials from configs/.env (DEVICE_IP, DEVICE_USER, DEVICE_KEY/DEVICE_PASSWORD,
HOST_KEY, DEPLOY_OUTPUT_DIR, and the optional Mode M keys DEVICE_MODELS_DIR /
DEVICE_LABELS_DIR). Never passed on the command line -- that would put the
password in shell history and in `ps` output while the command runs. DEVICE_USER
must be root: QLI images ship without sudo, so no other account can write the
module directory.

Output per artifact:
  <DEPLOY_OUTPUT_DIR>/<artifact-name>/
  ├── device.log                     — install + gst-inspect transcript (always written)
  ├── build.log                      — cmake configure + cross-build output
  ├── result.json                    — structured result dict (always written)
  ├── libml-postprocess-<module>.so  — the gated aarch64 library
  └── build/                         — cmake build tree (reused across runs)

Exits 0 if the module is registered on the device, 1 otherwise.

What this script does NOT do:
  - Diagnose why a build failed or why a module refused to load
  - Suggest code, CMake or decode fixes (that is qimsdk-model-onboarding)
  - Run a pipeline that uses the module
  - Install anything on the device other than the library, model and labels
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
import subprocess
import sys
import time
import warnings

# Force UTF-8 stdout/stderr on Windows (cp1252 default rejects box-drawing chars).
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure workspace_setup_m (same directory) is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


# ── Device path constants ──────────────────────────────────────────────────────

# Used only when the compiled-in path cannot be read off the device's own plugin.
FALLBACK_MODULES_DIR = '/usr/lib/imsdk/qtimlpostprocess/modules'

DEFAULT_MODELS_DIR = '/root/models'
DEFAULT_LABELS_DIR = '/root/labels'

MODEL_SUFFIXES = ('.tflite', '.dlc', '.onnx', '.bin', '.pte')

_ELF_MAGIC = b'\x7fELF'
_ET_DYN = 3
_ELF_MACHINES = {0xB7: 'aarch64', 0x3E: 'x86-64', 0x28: 'arm', 0x03: 'x86'}

# Modules known to ship in some QLI images. Only used to sharpen the warning text -- the
# real guard is "does the target already exist", because this list cannot be authoritative:
# a QLI 2.99 image was observed shipping 34 modules including detr-resnet50, yolov9-det and
# yolo26, none of which are in the qimsdk source tree's 27. Never treat absence from this
# list as evidence that a file is safe to replace.
KNOWN_VENDOR_MODULES = {
    'yolov5', 'yolov8', 'yolo-nas', 'ssd-mobilenet', 'east-textdt', 'mediapipe-pose',
    'palmd', 'qfd', 'qpd', 'mobilenet', 'mobilenet-softmax', 'ocr', 'ocr-recognizer',
    'qfr', 'qfr-softmax', 'deeplab-argmax', 'midas-v2', 'yolov8-seg', 'wave2vec',
    'yamnet', 'easy-ocr-detector', 'detr-resnet50', 'yolov9-det', 'yolov9', 'yolo26',
    'face-det-lite', 'hlandmark', 'hrnet', 'lite-3dmm', 'mediapipe-face', 'posenet',
    'mediapipe-pose-landmark', 'srnet', 'tensor',
}

_STEPS = (
    'parse_artifact', 'preflight', 'emit_toolchain', 'cmake_configure', 'cross_compile',
    'gate_arch', 'gate_filename', 'gate_symbol', 'gate_abi', 'gate_needed',
    'connect', 'interrogate_device', 'abi_recheck', 'install_module',
    'push_model', 'push_labels', 'ldd', 'verify_registration', 'wiring',
)


# ── .env loader ───────────────────────────────────────────────────────────────

def _load_dotenv():
    """Load configs/.env into os.environ. Shell env takes precedence over .env.

    Search order: walk up from CWD (user's working dir wins), then from the
    script location (in-repo/installed-skill fallback).
    """
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


# ── Device config (configs/.env, same keys as every other mode) ───────────────

class DeviceConfig:
    """Resolved device connection values, plus the redaction the result relies on.

    Mode M reads the same configs/.env keys as Modes A/B/C/D/P. Two keys are
    Mode-M-only and optional: DEVICE_MODELS_DIR and DEVICE_LABELS_DIR, which say
    where the model and labels files are pushed.

    DEVICE_HOST_KEY is accepted as an alias for HOST_KEY so an older Mode M
    config keeps working; HOST_KEY wins when both are set.
    """

    def __init__(self, values):
        self.values = values

    @property
    def secrets(self):
        """Values that must never appear in output."""
        return [v for k, v in self.values.items() if k == 'DEVICE_PASSWORD' and v]

    def redact(self, text):
        """Replace every secret value with a marker.

        Applied to all console output, device.log and result.json. A password that
        reaches a log file is as leaked as one in shell history, and device.log is the
        first thing a user pastes into a bug report.
        """
        if text is None:
            return None
        out = str(text)
        for secret in self.secrets:
            if secret:
                out = out.replace(secret, '***')
        return out

    def summary(self):
        auth = 'key (%s)' % self.values['DEVICE_KEY'] if self.values.get('DEVICE_KEY') \
            else ('password' if self.values.get('DEVICE_PASSWORD') else 'NONE')
        pin = 'pinned' if self.values.get('HOST_KEY') else 'not pinned'
        return '%s@%s  auth=%s  host-key=%s  models=%s  labels=%s' % (
            self.values['DEVICE_USER'], self.values['DEVICE_IP'], auth, pin,
            self.values['DEVICE_MODELS_DIR'], self.values['DEVICE_LABELS_DIR'])


def load_device_config():
    """Read Mode M's device config out of the environment (configs/.env already loaded).

    Returns (DeviceConfig, [missing key descriptions]). Never raises: the caller
    reports the missing keys the same way every other mode does, so the config
    wizard in SKILL.md can fill exactly those in.
    """
    env = os.environ.get
    values = {
        'DEVICE_IP': env('DEVICE_IP', '') or '',
        'DEVICE_USER': env('DEVICE_USER', '') or '',
        'DEVICE_PASSWORD': env('DEVICE_PASSWORD', '') or '',
        'DEVICE_KEY': env('DEVICE_KEY', '') or '',
        'HOST_KEY': (env('HOST_KEY', '') or env('DEVICE_HOST_KEY', '') or '').strip(),
        'DEVICE_MODELS_DIR': env('DEVICE_MODELS_DIR', '') or DEFAULT_MODELS_DIR,
        'DEVICE_LABELS_DIR': env('DEVICE_LABELS_DIR', '') or DEFAULT_LABELS_DIR,
    }

    missing = []
    if not values['DEVICE_IP']:
        missing.append('DEVICE_IP not set in configs/.env')
    if not values['DEVICE_USER']:
        missing.append('DEVICE_USER not set in configs/.env (Mode M requires root)')
    if not values['DEVICE_PASSWORD'] and not values['DEVICE_KEY']:
        missing.append('No device auth configured — set DEVICE_KEY or DEVICE_PASSWORD '
                       'in configs/.env')
    return DeviceConfig(values), missing


# ── Utils ──────────────────────────────────────────────────────────────────────

def _human_size(n):
    n = float(n)
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.1f %s' % (n, unit) if unit != 'bytes' else '%d bytes' % n
        n /= 1024.0


def vtuple(text):
    parts = []
    for chunk in str(text).split('.'):
        m = re.match(r'^(\d+)', chunk)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts)


def elf_info(path):
    """(machine_name_or_None, is_shared_object) read straight from the ELF header."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(20)
    except OSError:
        return None, False
    if len(head) < 20 or head[:4] != _ELF_MAGIC:
        return None, False
    order = 'little' if head[5] == 1 else 'big'
    e_type = int.from_bytes(head[16:18], order)
    e_machine = int.from_bytes(head[18:20], order)
    return _ELF_MACHINES.get(e_machine, 'unknown(0x%x)' % e_machine), e_type == _ET_DYN


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def max_version(pattern, text):
    found = re.findall(pattern, text or '')
    return max(found, key=vtuple) if found else None


def _artifact_output_dir(deploy_output_dir, artifact_name):
    d = pathlib.Path(deploy_output_dir) / artifact_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_result(out_dir, result, cfg):
    p = pathlib.Path(out_dir) / 'result.json'
    payload = json.loads(cfg.redact(json.dumps(result)))
    p.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return str(p)


def _step(result, name, status, detail=None):
    for s in result['steps']:
        if s['step'] == name:
            s['status'] = status
            s['detail'] = detail
            return


def _fail(result, name, reason):
    _step(result, name, 'fail', reason)
    result['failure_reason'] = reason


def _warn(result, msg):
    result['warnings'].append(msg)


# ── SSH session (device — key or password auth) ────────────────────────────────

class _SSH:
    """
    Paramiko wrapper for the device.

    Deliberately does NOT call load_system_host_keys(), and passes
    allow_agent=False / look_for_keys=False: this never reads or writes
    ~/.ssh/known_hosts, so reflashing the device cannot produce the
    REMOTE HOST IDENTIFICATION HAS CHANGED wall of text, and there is no stale
    fingerprint to clear. Set HOST_KEY to opt into strict pinning.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.ip = cfg.values['DEVICE_IP']
        self.user = cfg.values['DEVICE_USER']
        self.password = cfg.values.get('DEVICE_PASSWORD') or None
        key = cfg.values.get('DEVICE_KEY')
        self.key_path = str(pathlib.Path(key).expanduser()) if key else None
        self.pin = (cfg.values.get('HOST_KEY') or '').strip()
        self.host_key_fp = None
        self._client = None
        self._sftp = None

    def connect(self):
        if self._client and self._client.get_transport() \
                and self._client.get_transport().is_active():
            return
        try:
            import paramiko
            warnings.filterwarnings('ignore', category=UserWarning, module='paramiko')
        except ImportError:
            raise RuntimeError(
                'paramiko is not installed for this interpreter (%s). Install it with: '
                'pip install -r %s'
                % (sys.executable,
                   pathlib.Path(__file__).resolve().parent / 'requirements.txt'))

        use_key = bool(self.key_path and pathlib.Path(self.key_path).exists())
        if use_key and os.name == 'posix':
            mode = pathlib.Path(self.key_path).stat().st_mode & 0o777
            if mode & 0o077:
                raise RuntimeError(
                    'SSH key %s has insecure permissions %s. Fix with: chmod 600 %s'
                    % (self.key_path, oct(mode), self.key_path))
        if not use_key and not self.password:
            raise RuntimeError(
                'No SSH auth configured — set DEVICE_KEY or DEVICE_PASSWORD in '
                'configs/.env\n  See ssh-setup.md for setup instructions.')

        pin = self.pin
        captured = {}

        class _FPPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = 'SHA256:' + base64.b64encode(
                    hashlib.sha256(key.asbytes()).digest()).rstrip(b'=').decode()
                captured['fp'] = actual
                if pin and actual != pin:
                    raise paramiko.SSHException(
                        'Host key mismatch for %s: got %s, HOST_KEY pins %s. '
                        'If the device was reflashed this is expected — update or unset '
                        'HOST_KEY in configs/.env.' % (hostname, actual, pin))

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_FPPolicy())
        try:
            client.connect(
                self.ip, username=self.user,
                key_filename=self.key_path if use_key else None,
                password=None if use_key else self.password,
                timeout=15, banner_timeout=20, auth_timeout=15,
                allow_agent=False, look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            kind = 'key' if use_key else 'password'
            hint = (' Try password auth: set DEVICE_PASSWORD and remove DEVICE_KEY.'
                    if use_key else ' Try key auth: set DEVICE_KEY — see ssh-setup.md.')
            raise RuntimeError(
                'SSH %s authentication failed for %s@%s. Check DEVICE_%s in '
                'configs/.env.%s' % (kind, self.user, self.ip, kind.upper(), hint))
        except OSError as exc:
            raise RuntimeError(
                'cannot reach %s@%s: %s. Is the device powered on and on the network?'
                % (self.user, self.ip, exc))
        self._client = client
        self._sftp = client.open_sftp()
        self.host_key_fp = captured.get('fp')
        return 'key (%s)' % os.path.basename(self.key_path) if use_key else 'password'

    def close(self):
        for obj in (self._sftp, self._client):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        self._sftp = self._client = None

    def run(self, cmd, timeout=30):
        """(stdout, stderr, rc). rc is None when the command timed out."""
        self.connect()
        _in, out_f, _err_f = self._client.exec_command(cmd)
        chan = out_f.channel
        deadline = time.time() + timeout
        chunks, err_chunks = [], []
        timed_out = False
        while True:
            got = False
            while chan.recv_ready():
                data = chan.recv(65536)
                if data:
                    chunks.append(data)
                    got = True
            while chan.recv_stderr_ready():
                data = chan.recv_stderr(65536)
                if data:
                    err_chunks.append(data)
                    got = True
            if got:
                continue
            if chan.exit_status_ready() and not chan.recv_ready() \
                    and not chan.recv_stderr_ready():
                break
            if time.time() > deadline:
                timed_out = True
                break
            time.sleep(0.05)
        out = b''.join(chunks).decode('utf-8', 'replace').strip()
        err = b''.join(err_chunks).decode('utf-8', 'replace').strip()
        if timed_out:
            try:
                chan.close()
            except Exception:
                pass
            return out, err, None
        return out, err, chan.recv_exit_status()

    def push(self, local_path, remote_path):
        self.connect()
        self._sftp.put(str(local_path), remote_path)

    def exists(self, remote_path):
        out, _, _ = self.run("test -e '%s' && echo YES || echo NO" % remote_path)
        return 'YES' in out


# ── Artifact parsing ───────────────────────────────────────────────────────────

def _parse_artifact(artifact, model_override=None):
    """Module identity plus the model/labels/settings files that ship alongside it.

    The library itself is not resolved here — it is produced by the build step, or
    passed with --so.
    """
    art = pathlib.Path(artifact)
    if not art.is_dir():
        raise ValueError('artifact path is not a directory: %s' % artifact)

    module_name = None
    source_file = None
    for cc in sorted(art.glob('ml-postprocess-*.cc')):
        module_name = cc.stem[len('ml-postprocess-'):]
        source_file = cc
        break
    if not module_name:
        cml = art / 'CMakeLists.txt'
        if cml.is_file():
            text = cml.read_text(encoding='utf-8', errors='replace')
            m = re.search(r'set\s*\(\s*TARGET_NAME\s+"?ml-postprocess-([A-Za-z0-9_.\-]+)"?\s*\)',
                          text) or \
                re.search(r'add_library\s*\(\s*"?ml-postprocess-([A-Za-z0-9_.\-]+)"?', text)
            if m:
                module_name = m.group(1)
    if not module_name and art.name.startswith('imsdk-postproc-'):
        module_name = art.name[len('imsdk-postproc-'):]
    if not module_name:
        raise ValueError(
            'could not determine the module name from %s. Expected '
            'ml-postprocess-<name>.cc, set(TARGET_NAME ml-postprocess-<name>) in '
            'CMakeLists.txt, or a folder named imsdk-postproc-<name>.' % artifact)

    lib_filename = 'libml-postprocess-%s.so' % module_name

    model_sha256 = model_filename = model_io_path = None
    mio = art / 'model-io.json'
    if mio.is_file():
        try:
            data = json.loads(mio.read_text(encoding='utf-8', errors='replace'))
            model_sha256 = data.get('sha256')
            model_filename = data.get('model_filename')
            model_io_path = data.get('model_path')
        except (json.JSONDecodeError, OSError):
            pass

    model_file = model_note = None
    if model_override:
        model_file = pathlib.Path(model_override).expanduser()
        if not model_file.is_file():
            raise ValueError('--model does not exist: %s' % model_file)
        model_note = '--model'
    else:
        inside = sorted(p for p in art.iterdir()
                        if p.is_file() and p.suffix.lower() in MODEL_SUFFIXES)
        if inside:
            model_file, model_note = inside[0], 'found in artifact'
        elif model_io_path and pathlib.Path(model_io_path).is_file():
            model_file, model_note = pathlib.Path(model_io_path), 'model_path from model-io.json'

    labels_file = art / 'labels.json'
    labels_file = labels_file if labels_file.is_file() else None
    settings_file = art / 'settings.json'
    settings_json = None
    if settings_file.is_file():
        try:
            settings_json = json.dumps(
                json.loads(settings_file.read_text(encoding='utf-8', errors='replace')),
                separators=(', ', ': '))
        except (json.JSONDecodeError, OSError):
            settings_json = None
    else:
        settings_file = None

    postproc_type = None
    if source_file and source_file.is_file():
        m = re.search(r'"type"\s*:\s*"([A-Za-z0-9_\-]+)"',
                      source_file.read_text(encoding='utf-8', errors='replace'))
        if m:
            postproc_type = m.group(1)

    return {
        'module_name': module_name,
        'lib_filename': lib_filename,
        'so_file': None,
        'model_file': model_file,
        'model_note': model_note,
        'model_sha256': model_sha256,
        'model_filename': model_filename,
        'labels_file': labels_file,
        'settings_file': settings_file,
        'settings_json': settings_json,
        'postproc_type': postproc_type,
    }


def _resolve_prebuilt_so(artifact, out_dir, lib_filename, so_override=None):
    """Find an already-built library when the build step is skipped."""
    art = pathlib.Path(artifact)
    checked = []
    if so_override:
        so_file = pathlib.Path(so_override).expanduser()
        if not so_file.is_file():
            raise ValueError('--so does not exist: %s' % so_file)
        return so_file
    for cand in (pathlib.Path(out_dir) / lib_filename,
                 art / lib_filename,
                 pathlib.Path.cwd() / 'qimsdk-build' / lib_filename,
                 art / 'build' / lib_filename):
        checked.append(str(cand))
        if cand.is_file():
            return cand
    raise ValueError(
        'no %s found, and --skip-build was requested. Drop --skip-build to '
        'cross-compile it, or pass --so <path>.\nChecked:\n    %s'
        % (lib_filename, '\n    '.join(checked)))


def module_in_enum(inspect_text, module_name):
    """
    True when module_name is a value of qtimlpostprocess's module property.

    gst-inspect renders an enum as '(0): yolov5  - YOLOv5 detection', so collect those
    tokens and match exactly. A bare substring search false-positives on the name
    appearing in a description, a path or the plugin filename.
    """
    values = re.findall(r'^\s*\(\s*-?\d+\s*\)\s*:\s*([A-Za-z0-9_\-.]+)',
                        inspect_text or '', re.MULTILINE)
    if values:
        return module_name in values
    m = re.search(r'^\s*module\s*:.*?(?=^\s*\S+\s*:|\Z)',
                  inspect_text or '', re.MULTILINE | re.DOTALL)
    if m:
        return re.search(r'\b%s\b' % re.escape(module_name), m.group(0)) is not None
    return False


# ── Device steps ───────────────────────────────────────────────────────────────

def interrogate(ssh, result):
    """Root check first, then OS, plugin, module dir, and the real ABI ceiling."""
    uid, _, _ = ssh.run('id -u')
    if uid.strip() != '0':
        _fail(result, 'interrogate_device',
              'connected as uid %s, but this device has no sudo, so only root can '
              'write %s and the model/label directories. Set DEVICE_USER=root in '
              'configs/.env.' % (uid.strip() or '?', FALLBACK_MODULES_DIR))
        return None

    osr, _, _ = ssh.run("grep -h PRETTY_NAME /etc/os-release 2>/dev/null | head -1")
    m = re.search(r'PRETTY_NAME="?([^"\n]+)', osr or '')
    result['device_os'] = m.group(1) if m else (osr or None)

    inspect, _, rc = ssh.run('gst-inspect-1.0 qtimlpostprocess 2>&1', timeout=60)
    if rc != 0 or 'No such element' in (inspect or ''):
        _fail(result, 'interrogate_device',
              'gst-inspect-1.0 cannot inspect qtimlpostprocess on this device, so no '
              'module can load. Output: %s' % (inspect or '')[:200])
        return None

    # The module directory is compiled into the plugin (GST_ML_MODULES_DIR) and is not
    # runtime-configurable, so read it out of the plugin rather than assuming. A sibling
    # plugin family legitimately uses a different directory; guessing wrong yields a
    # copy that succeeds and a module that never loads.
    modules_dir = source = None
    # The colon is optional: gst-inspect aligns this field with whitespace on some
    # builds ('Filename    /usr/lib/...') and uses 'Filename: /usr/lib/...' on others.
    # Requiring the colon silently loses the plugin path and forces the assumed fallback.
    fm = re.search(r'^\s*Filename\s*:?\s+(\S+)', inspect or '', re.MULTILINE)
    if fm:
        plugin_so = fm.group(1)
        out, _, _ = ssh.run(
            "strings '%s' 2>/dev/null | grep -E '^/.*modules$' | sort -u" % plugin_so)
        cands = [c.strip() for c in (out or '').splitlines() if c.strip().startswith('/')]
        preferred = [c for c in cands if 'qtimlpostprocess' in c] or \
                    [c for c in cands if 'imsdk' in c]
        if preferred:
            modules_dir, source = preferred[0], 'compiled into %s' % plugin_so
        elif cands:
            modules_dir, source = cands[0], 'only /…/modules string in %s' % plugin_so
    if not modules_dir:
        modules_dir, source = FALLBACK_MODULES_DIR, 'assumed (could not read from plugin)'
        _warn(result, 'could not read the module dir from the plugin binary; assumed %s'
              % FALLBACK_MODULES_DIR)
    result['modules_dir'] = modules_dir
    result['modules_dir_source'] = source

    ldd, _, _ = ssh.run('ldd --version 2>&1 | head -1')
    gm = re.search(r'(\d+\.\d+(?:\.\d+)?)\s*$', (ldd or '').strip())
    if not gm:
        out, _, _ = ssh.run(
            "strings /usr/lib/libc.so.6 2>/dev/null | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1")
        gm = re.search(r'GLIBC_([\d.]+)', out or '')
    result['device_glibc'] = gm.group(1) if gm else None

    out, _, _ = ssh.run(
        "for f in /usr/lib/libstdc++.so.6 /usr/lib64/libstdc++.so.6 "
        "/usr/lib/*/libstdc++.so.6; do [ -e \"$f\" ] && strings \"$f\" 2>/dev/null; done "
        "| grep -oE 'GLIBCXX_[0-9.]+' | sort -uV | tail -1")
    cm = re.search(r'GLIBCXX_([\d.]+)', out or '')
    result['device_glibcxx'] = cm.group(1) if cm else None

    detail = 'uid=0, %s, modules=%s (%s), glibc=%s, libstdc++=%s' % (
        result['device_os'] or '?', modules_dir, source,
        result['device_glibc'] or '?', result['device_glibcxx'] or '?')
    _step(result, 'interrogate_device', 'ok', detail)
    return modules_dir


def abi_recheck(result, so_path):
    """Re-gate the .so against the device's MEASURED versions, not an inferred ceiling."""
    machine, is_dyn = elf_info(so_path)
    if machine != 'aarch64' or not is_dyn:
        _fail(result, 'abi_recheck',
              'the library is %s%s, not an aarch64 shared object. This is a host build; '
              'let the build step cross-compile it instead of passing --so a host build.'
              % (machine or 'not an ELF', '' if is_dyn else ' (not a shared object)'))
        return False

    try:
        out = subprocess.run(['objdump', '-T', str(so_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=60).stdout.decode('utf-8', 'replace')
    except (OSError, subprocess.SubprocessError):
        out = ''
    need_glibc = max_version(r'GLIBC_(\d[\d.]*)', out)
    need_glibcxx = max_version(r'GLIBCXX_(\d[\d.]*)', out)
    result['so_required_glibc'] = need_glibc
    result['so_required_glibcxx'] = need_glibcxx

    dev_glibc = result['device_glibc']
    dev_glibcxx = result['device_glibcxx']
    if dev_glibc or dev_glibcxx:
        result['suggested_build_flags'] = ' '.join(
            filter(None, ['--max-glibc %s' % dev_glibc if dev_glibc else None,
                          '--max-glibcxx %s' % dev_glibcxx if dev_glibcxx else None]))

    over = []
    if need_glibc and dev_glibc and vtuple(need_glibc) > vtuple(dev_glibc):
        over.append('GLIBC_%s > device %s' % (need_glibc, dev_glibc))
    if need_glibcxx and dev_glibcxx and vtuple(need_glibcxx) > vtuple(dev_glibcxx):
        over.append('GLIBCXX_%s > device %s' % (need_glibcxx, dev_glibcxx))

    if over:
        result['abi_ok'] = False
        _fail(result, 'abi_recheck',
              'the library needs symbol versions this device does not provide (%s). It '
              'would install and then fail at dlopen with "version not found". Rebuild '
              'with --static-cxx, or with %s.'
              % ('; '.join(over), result['suggested_build_flags'] or 'a lower ceiling'))
        return False

    if not (dev_glibc or dev_glibcxx):
        _warn(result, 'could not measure the device C/C++ runtime versions; ABI not re-checked')
        _step(result, 'abi_recheck', 'warn',
              'device versions unreadable; needs GLIBC_%s / GLIBCXX_%s'
              % (need_glibc or '-', need_glibcxx or '-'))
        return True

    result['abi_ok'] = True
    _step(result, 'abi_recheck', 'ok', 'needs GLIBC_%s / GLIBCXX_%s; device provides %s / %s'
          % (need_glibc or '-', need_glibcxx or '-', dev_glibc or '?', dev_glibcxx or '?'))
    return True


def install_file(ssh, local, remote, mode='0644'):
    """
    Stage inside the TARGET directory, then rename.

    Two reasons this is not a direct SFTP write and not staged through /tmp:
      - an interrupted transfer must never leave a truncated file at the path the
        plugin will dlopen;
      - `mv` within one directory is an atomic rename, whereas /tmp is often a separate
        filesystem where mv degrades to copy-then-unlink.
    """
    target_dir = posixpath.dirname(remote)
    staged = posixpath.join(target_dir, '.qimsdk-tmp-%s' % posixpath.basename(remote))
    ssh.run("mkdir -p '%s'" % target_dir)
    try:
        ssh.push(local, staged)
    except Exception as exc:
        ssh.run("rm -f '%s'" % staged)
        return False, 'SFTP push to %s failed: %s' % (staged, exc)
    out, err, rc = ssh.run("mv -f '%s' '%s' && chmod %s '%s'"
                           % (staged, remote, mode, remote))
    if rc != 0:
        ssh.run("rm -f '%s'" % staged)
        return False, 'install to %s failed: %s' % (remote, (err or out or '')[:300])
    if not ssh.exists(remote):
        return False, '%s is not present after install' % remote
    return True, remote


def check_space(ssh, remote_dir, need_bytes, result):
    """
    Advisory only. A QLI rootfs commonly reports 0 available while remaining writable,
    because root may consume the filesystem's reserved blocks. So a shortfall is worth
    reporting -- a full rootfs is a real operational problem -- but it must not block an
    install that would in fact succeed.
    """
    out, _, _ = ssh.run("mkdir -p '%s' && df -kP '%s' | tail -1" % (remote_dir, remote_dir))
    parts = (out or '').split()
    if len(parts) >= 4 and parts[3].isdigit():
        free = int(parts[3]) * 1024
        if free < need_bytes * 1.1:
            _warn(result,
                  '%s reports %s available for a %s file (filesystem %s). Writes as '
                  'root may still succeed using reserved blocks, but this device is '
                  'short on space.'
                  % (remote_dir, _human_size(free), _human_size(need_bytes), parts[0]))
            return False
    return True


# ── Core deploy ───────────────────────────────────────────────────────────────

def deploy_mode_m(
    artifact_path,
    deploy_output_dir,
    cfg,
    so_override=None,
    model_override=None,
    imsdk_include=None,
    max_glibc=None,
    max_glibcxx=None,
    static_cxx=False,
    skip_abi_gate=False,
    skip_build=False,
    skip_model=False,
    skip_labels=False,
    force=False,
    dry_run=False,
):
    """
    Mode M deploy: cross-compile the module locally, install it on the QLI device,
    and verify it appears in qtimlpostprocess's module= enum.

    Builds and installs as-is and reports what happened. Does not diagnose, fix or
    modify source, and never retries. Always writes result.json + device.log.
    """
    import workspace_setup_m as wsm

    artifact_path = pathlib.Path(artifact_path).resolve()
    artifact_name = artifact_path.name
    out_dir = _artifact_output_dir(deploy_output_dir, artifact_name)

    result = {
        'mode': 'M',
        'artifact': artifact_name,
        'artifact_path': str(artifact_path),
        'module_name': None,
        'lib_filename': None,
        'build_passed': False,
        # Mode M has no run step — the deliverable is a registered module, so PASS is
        # decided by module_registered, never by playing_reached.
        'playing_reached': 'N/A (no run step)',
        'error_lines': [],
        'output_file_size': 'N/A (module install)',
        'output_local_path': None,
        'log_local_path': str(out_dir / 'device.log'),
        'build_log_path': None,
        # Build detail (from workspace_setup_m)
        'toolchain': None,
        'include_dir': None,
        'include_dir_source': None,
        'so_path': None,
        'so_size': None,
        'so_sha256': None,
        'file_output': None,
        'required_glibc': None,
        'required_glibcxx': None,
        'abi_ceiling': {},
        'needed_libs': [],
        'gates_skipped': [],
        'hermetic_dropped': [],
        # Device detail
        'device': None,
        'device_os': None,
        'auth': None,
        'host_key_fp': None,
        'modules_dir': None,
        'modules_dir_source': None,
        'device_glibc': None,
        'device_glibcxx': None,
        'so_required_glibc': None,
        'so_required_glibcxx': None,
        'abi_ok': None,
        'suggested_build_flags': None,
        'module_device_path': None,
        'backup_path': None,
        'unchanged': False,
        'model_device_path': None,
        'labels_device_path': None,
        'settings_device_path': None,
        'module_registered': False,
        'registry_cleared': False,
        'unresolved_deps': [],
        'wiring': None,
        'dry_run': dry_run,
        'warnings': [],
        'failure_reason': None,
        'next_step': None,
        'steps': [{'step': s, 'status': 'skip', 'detail': None} for s in _STEPS],
    }

    print(f'\n[Mode M] {artifact_name}', flush=True)
    print(f'  artifact      : {artifact_path}', flush=True)
    print(f'  output        : {out_dir}', flush=True)
    print(f'  device        : {cfg.values["DEVICE_USER"]}@{cfg.values["DEVICE_IP"]}', flush=True)

    log = []
    ssh = None

    def finish():
        """Write device.log + result.json, print the summary, and return the result.

        Every exit path goes through here, so result.json always exists — including
        when the build failed before the device was ever contacted.
        """
        try:
            (out_dir / 'device.log').write_text(cfg.redact('\n'.join(log)),
                                               encoding='utf-8')
        except OSError:
            pass
        path = _write_result(out_dir, result, cfg)
        _print_summary(result, cfg)
        print(f'  [done]  result.json -> {path}', flush=True)
        return result

    # ── M0: Parse artifact metadata locally (no SSH, no build yet) ────────────
    try:
        meta = _parse_artifact(artifact_path, model_override)
    except ValueError as exc:
        _fail(result, 'parse_artifact', str(exc))
        return finish()

    result['module_name'] = meta['module_name']
    result['lib_filename'] = meta['lib_filename']
    result['device'] = '%s@%s' % (cfg.values['DEVICE_USER'], cfg.values['DEVICE_IP'])
    _step(result, 'parse_artifact', 'ok', 'module=%s, model=%s, labels=%s'
          % (meta['module_name'], meta['model_note'] or 'none',
             'yes' if meta['labels_file'] else 'no'))

    # ── M1: Cross-compile and gate (local, no device) ─────────────────────────
    if skip_build:
        try:
            meta['so_file'] = _resolve_prebuilt_so(
                artifact_path, out_dir, meta['lib_filename'], so_override)
        except ValueError as exc:
            _fail(result, 'preflight', str(exc))
            return finish()
        result['build_passed'] = 'N/A (--skip-build)'
        result['so_path'] = str(meta['so_file'])
        for name in ('preflight', 'emit_toolchain', 'cmake_configure', 'cross_compile',
                     'gate_arch', 'gate_filename', 'gate_symbol', 'gate_abi',
                     'gate_needed'):
            _step(result, name, 'skip', 'build skipped by --skip-build')
        _warn(result, 'build skipped by --skip-build: the four build gates did not run '
                      'against %s' % meta['so_file'])
        print(f'  [M1]    build skipped — using {meta["so_file"]}', flush=True)
    elif so_override:
        meta['so_file'] = pathlib.Path(so_override).expanduser()
        if not meta['so_file'].is_file():
            _fail(result, 'preflight', '--so does not exist: %s' % meta['so_file'])
            return finish()
        result['build_passed'] = 'N/A (--so)'
        result['so_path'] = str(meta['so_file'])
        for name in ('preflight', 'emit_toolchain', 'cmake_configure', 'cross_compile',
                     'gate_arch', 'gate_filename', 'gate_symbol', 'gate_abi',
                     'gate_needed'):
            _step(result, name, 'skip', 'explicit --so supplied; nothing was built')
        _warn(result, '--so bypassed the build and its four gates for %s' % meta['so_file'])
        print(f'  [M1]    using supplied library {meta["so_file"]}', flush=True)
    else:
        print('  [M1]    cross-compiling for aarch64 ...', flush=True)
        build = wsm.setup_and_build_m(
            str(artifact_path), str(out_dir),
            imsdk_include=imsdk_include,
            max_glibc=max_glibc or wsm.DEFAULT_MAX_GLIBC,
            max_glibcxx=max_glibcxx or wsm.DEFAULT_MAX_GLIBCXX,
            static_cxx=static_cxx, skip_abi_gate=skip_abi_gate,
        )

        for key in ('toolchain', 'include_dir', 'include_dir_source', 'so_path',
                    'so_size', 'file_output', 'required_glibc', 'required_glibcxx',
                    'abi_ceiling', 'needed_libs', 'gates_skipped', 'hermetic_dropped'):
            result[key] = build[key]
        for s in build['steps']:
            _step(result, s['step'], s['status'], s['detail'])
        for w in build['warnings']:
            _warn(result, w)

        if build['build_log']:
            build_log_path = out_dir / 'build.log'
            try:
                build_log_path.write_text(build['build_log'], encoding='utf-8')
                result['build_log_path'] = str(build_log_path)
            except OSError:
                pass

        result['build_passed'] = bool(build['success'])
        if not build['success']:
            result['failure_reason'] = build['failure_reason']
            result['next_step'] = build['next_step']
            print(f'  [FAIL]  {result["failure_reason"]}', flush=True)
            return finish()

        meta['so_file'] = pathlib.Path(build['so_path'])
        result['output_local_path'] = build['so_path']
        print(f'  [M1]    gated OK — {build["so_path"]} ({build["so_size"]})', flush=True)

    result['output_local_path'] = result['output_local_path'] or str(meta['so_file'])

    # ── M2: Connect to the device ────────────────────────────────────────────
    ssh = _SSH(cfg)
    try:
        auth = ssh.connect()
    except RuntimeError as exc:
        _fail(result, 'connect', cfg.redact(str(exc)))
        return finish()
    result['auth'] = auth
    result['host_key_fp'] = ssh.host_key_fp
    _step(result, 'connect', 'ok', '%s via %s (host key %s%s)'
          % (result['device'], auth, ssh.host_key_fp or '?',
             ', pinned' if ssh.pin else ', not pinned'))
    print(f'  [ssh]   Connected to {result["device"]} ({auth})', flush=True)

    try:
        # ── M3: Interrogate the device (read-only) ───────────────────────────
        modules_dir = interrogate(ssh, result)
        if not modules_dir:
            return finish()

        # ── M4: Re-gate the ABI against the real device ──────────────────────
        if not abi_recheck(result, meta['so_file']):
            return finish()

        # Everything above is read-only on the device. M5 is the first write, and it can
        # replace a module shipped in the OS image, so offer a stopping point here.
        if dry_run:
            for name in ('install_module', 'push_model', 'push_labels', 'ldd',
                         'verify_registration', 'wiring'):
                _step(result, name, 'skip', 'dry run -- nothing was written')
            result['next_step'] = (
                'Dry run only: nothing was installed and the module is NOT registered. '
                'Re-run without --dry-run to install.')
            return finish()

        # ── M5: Install the module ───────────────────────────────────────────
        target = posixpath.join(modules_dir, meta['lib_filename'])
        result['module_device_path'] = target
        local_so_sha = sha256_of(meta['so_file'])
        result['so_sha256'] = local_so_sha
        if ssh.exists(target):
            info, _, _ = ssh.run("stat -c '%%s bytes, mtime %%y' '%s'" % target)
            vendor = meta['module_name'] in KNOWN_VENDOR_MODULES
            if not force:
                _fail(result, 'install_module',
                      '%s already exists (%s). It may be a module shipped in the OS '
                      'image%s, or an earlier deploy of your own -- this cannot tell '
                      'them apart. Re-run with --force to replace it; a timestamped '
                      'backup is kept either way.'
                      % (target, info.strip() or 'unknown',
                         ' -- "%s" is known to ship in some QLI images'
                         % meta['module_name'] if vendor else ''))
                return finish()

            # Skip the backup when the installed bytes are already identical. Repeat
            # deploys are the normal iteration case, and on a QLI rootfs -- routinely at
            # 100% with only root's reserved blocks left -- accumulating a copy per run
            # is a real cost for no recoverable state.
            dev_sha, _, sha_rc = ssh.run("sha256sum '%s' 2>/dev/null | cut -d' ' -f1" % target)
            identical = sha_rc == 0 and dev_sha.strip() == local_so_sha
            if identical:
                result['unchanged'] = True
                _warn(result, 'the installed module is already byte-identical to this build; '
                              'reinstalling without a new backup')
            else:
                backup = '%s.bak-%s' % (target,
                                        time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
                out, err, rc = ssh.run("cp -p '%s' '%s'" % (target, backup))
                if rc != 0:
                    _fail(result, 'install_module', 'could not back up %s: %s'
                          % (target, (err or out or '')[:200]))
                    return finish()
                result['backup_path'] = backup
                log.append('[backup] %s -> %s' % (target, backup))
                # The .so.bak-<ts> suffix matters: the plugin resolves modules by the
                # exact name libml-postprocess-<n>.so, so a backup can never be picked up
                # as a module. Do not rename backups to end in .so.
                _warn(result,
                      'replaced an existing module at %s (mtime %s); original saved to %s'
                      % (target,
                         (info.strip().split('mtime ', 1)[-1] if 'mtime' in info else '?'),
                         backup))
                stale, _, _ = ssh.run("ls -1 '%s'.bak-* 2>/dev/null | wc -l" % target)
                try:
                    if int(stale.strip()) > 3:
                        _warn(result,
                              '%s backups of this module are now on the device; prune '
                              'them with: rm %s.bak-*' % (stale.strip(), target))
                except ValueError:
                    pass

        check_space(ssh, modules_dir, os.path.getsize(meta['so_file']), result)
        ok, detail = install_file(ssh, meta['so_file'], target, mode='0644')
        if not ok:
            _fail(result, 'install_module', detail)
            return finish()
        _step(result, 'install_module', 'ok', '%s (%s)%s'
              % (target, _human_size(os.path.getsize(meta['so_file'])),
                 '; backup %s' % result['backup_path'] if result['backup_path'] else ''))
        print(f'  [M5]    installed {target}', flush=True)

        # ── M6: Push the model ───────────────────────────────────────────────
        if skip_model or not meta['model_file']:
            reason = 'skipped by flag' if skip_model else \
                'no model found (artifact has none and model-io.json path did not resolve)'
            _step(result, 'push_model', 'skip', reason)
            if not skip_model:
                _warn(result, 'no model pushed -- %s' % reason)
        else:
            local_sha = sha256_of(meta['model_file'])
            if meta['model_sha256'] and local_sha != meta['model_sha256']:
                _fail(result, 'push_model',
                      'the model at %s does not match the one the decode was written '
                      'against (model-io.json sha256 %s, found %s). Pushing a different '
                      'model is a silent correctness bug -- pass --model explicitly.'
                      % (meta['model_file'], meta['model_sha256'][:16], local_sha[:16]))
                return finish()
            models_dir = cfg.values['DEVICE_MODELS_DIR']
            dest = posixpath.join(models_dir, os.path.basename(str(meta['model_file'])))
            check_space(ssh, models_dir, os.path.getsize(meta['model_file']), result)
            ok, detail = install_file(ssh, meta['model_file'], dest, mode='0644')
            if not ok:
                _fail(result, 'push_model', detail)
                return finish()
            result['model_device_path'] = dest
            _step(result, 'push_model', 'ok', '%s (%s, sha256 verified%s)'
                  % (dest, _human_size(os.path.getsize(meta['model_file'])),
                     '' if meta['model_sha256'] else ', no reference sha in model-io.json'))

        # ── M7: Push labels + settings ───────────────────────────────────────
        if skip_labels or not (meta['labels_file'] or meta['settings_file']):
            _step(result, 'push_labels', 'skip',
                  'skipped by flag' if skip_labels else 'artifact has neither')
        else:
            labels_dir = cfg.values['DEVICE_LABELS_DIR']
            bits = []
            if meta['labels_file']:
                dest = posixpath.join(labels_dir, '%s.json' % meta['module_name'])
                ok, detail = install_file(ssh, meta['labels_file'], dest, mode='0644')
                if not ok:
                    _fail(result, 'push_labels', detail)
                    return finish()
                result['labels_device_path'] = dest
                bits.append(dest)
            if meta['settings_file']:
                dest = posixpath.join(labels_dir, '%s.settings.json' % meta['module_name'])
                ok, _ = install_file(ssh, meta['settings_file'], dest, mode='0644')
                if ok:
                    result['settings_device_path'] = dest
                    bits.append('%s (reference only; settings= takes JSON inline)' % dest)
            _step(result, 'push_labels', 'ok', '; '.join(bits))

        # ── M8: ldd ──────────────────────────────────────────────────────────
        # Before gst-inspect: an unresolved symbol is the most common reason a module
        # installs cleanly and then refuses to load, and ldd names it exactly whereas
        # gst-inspect only shows an absence. Minimal QLI images often carry no ldd,
        # readelf or objdump at all, in which case this diagnostic is simply
        # unavailable -- M9 remains the authoritative check either way.
        ldd_out, ldd_err, _ = ssh.run("ldd '%s' 2>&1" % target, timeout=60)
        ldd_text = ((ldd_out or '') + '\n' + (ldd_err or '')).strip()
        log.append('[ldd]\n%s' % ldd_text)
        if re.search(r'(command not found|not found:\s*ldd|No such file or directory)',
                     ldd_text) and '=>' not in ldd_text:
            _step(result, 'ldd', 'skip',
                  'ldd is not present on this device; dependency resolution is proven '
                  'instead by the registration check below')
        else:
            missing = [ln.strip() for ln in ldd_text.splitlines() if 'not found' in ln]
            result['unresolved_deps'] = missing
            if missing:
                _step(result, 'ldd', 'warn',
                      '%d unresolved: %s' % (len(missing), '; '.join(missing[:3])))
                _warn(result, 'unresolved dependencies: %s' % '; '.join(missing))
            else:
                _step(result, 'ldd', 'ok', 'all dependencies resolve on the device')

        # ── M9: Verify registration — the only proof of success ──────────────
        def inspect_once():
            out, err, rc = ssh.run('gst-inspect-1.0 qtimlpostprocess 2>&1', timeout=60)
            text = ((out or '') + '\n' + (err or '')).strip()
            log.append('[gst-inspect rc=%s]\n%s' % (rc, text))
            return text

        text = inspect_once()
        registered = module_in_enum(text, meta['module_name'])
        if not registered:
            # A cached registry masks a correct install. The cache is a derived artifact,
            # so clearing and re-inspecting is safe -- but say that it happened.
            ssh.run('rm -rf ~/.cache/gstreamer-1.0 2>/dev/null; true')
            result['registry_cleared'] = True
            text = inspect_once()
            registered = module_in_enum(text, meta['module_name'])

        result['module_registered'] = registered
        if not registered:
            unresolved = result['unresolved_deps']
            extra = (' %d unresolved dependency(ies) -- see ldd in device.log.'
                     % len(unresolved)) if unresolved else ''
            _fail(result, 'verify_registration',
                  'installed to %s but "%s" does not appear in qtimlpostprocess\'s '
                  'module= enum, even after clearing the GStreamer registry cache. The '
                  'library failed to dlopen or its Caps() JSON did not parse.%s Re-run on '
                  'the device with GST_DEBUG=qtimlpostprocess:5 to see the load error.'
                  % (target, meta['module_name'], extra))
            return finish()
        _step(result, 'verify_registration', 'ok',
              'module=%s present in the enum%s'
              % (meta['module_name'],
                 ' (after clearing the registry cache)' if result['registry_cleared'] else ''))

        # ── M10: Wiring snippet ──────────────────────────────────────────────
        wiring = ['qtimlpostprocess module=%s' % meta['module_name']]
        if result['labels_device_path']:
            wiring.append('labels=%s' % result['labels_device_path'])
        if meta['settings_json']:
            wiring.append("settings='%s'" % meta['settings_json'])
        result['wiring'] = ' \\\n    '.join(wiring)
        _step(result, 'wiring', 'ok', 'ready')
        result['next_step'] = (
            'Module is registered. Build a pipeline with qimsdk-gstreamer-app-builder, or '
            'test directly with a gst-launch pipeline using the wiring above.')
        return finish()
    finally:
        if ssh:
            ssh.close()


def _print_summary(result, cfg):
    d = result
    print('')
    print('  +-- Mode M result: %s' % ('PASS' if d['module_registered'] else
                                       ('DRY RUN' if d['dry_run'] else 'FAIL')))
    print('  |   build_passed      : %s' % d['build_passed'])
    print('  |   module_registered : %s' % d['module_registered'])
    if d['module_device_path']:
        print('  |   module           : %s' % d['module_device_path'])
    if d['model_device_path']:
        print('  |   model            : %s' % d['model_device_path'])
    if d['labels_device_path']:
        print('  |   labels           : %s' % d['labels_device_path'])
    if d['backup_path']:
        print('  |   backup           : %s' % d['backup_path'])
    if d['dry_run']:
        print('  |   dry run          : nothing was written; module is NOT registered')
        print('  |   would install to : %s/%s' % (d['modules_dir'], d['lib_filename']))
    if d['failure_reason']:
        print('  |   failure_reason   : %s' % cfg.redact(d['failure_reason']))
    if d['device_glibc'] or d['device_glibcxx']:
        print('  |   device ABI       : GLIBC_%s / GLIBCXX_%s'
              % (d['device_glibc'] or '?', d['device_glibcxx'] or '?'))
        if d['suggested_build_flags']:
            print('  |   measured ceiling : %s' % d['suggested_build_flags'])
    if d['gates_skipped']:
        print('  |   gates skipped    : %s' % ', '.join(d['gates_skipped']))
    for w in d['warnings']:
        print('  |   WARN             : %s' % cfg.redact(w))
    if d['build_log_path']:
        print('  |   build_log        : %s' % d['build_log_path'])
    print('  +-- log               : %s' % d['log_local_path'])
    if d['wiring']:
        print('')
        print('      %s' % d['wiring'].replace('\n', '\n      '))
        print('')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Mode M: cross-compile an IMSDK post-process module to aarch64 on '
                    'this host, install it on the QLI device, and verify it registers '
                    'in gst-inspect-1.0 qtimlpostprocess.')
    p.add_argument('--artifact-path', required=True,
                   help='Folder containing ml-postprocess-<module>.cc + CMakeLists.txt '
                        '(imsdk-postproc-<module>/ from qimsdk-model-onboarding)')
    p.add_argument('--output-dir', default=os.environ.get('DEPLOY_OUTPUT_DIR', ''),
                   help='Local dir for the library, logs and result.json '
                        '(DEPLOY_OUTPUT_DIR in configs/.env)')
    p.add_argument('--device-ip', default=os.environ.get('DEVICE_IP', ''))
    p.add_argument('--device-user', default=os.environ.get('DEVICE_USER', ''))
    p.add_argument('--host-key', default=os.environ.get('HOST_KEY', ''))
    # Build-side flags (passed through to workspace_setup_m)
    p.add_argument('--imsdk-include', default=None,
                   help='Explicit dir holding qti-ml-post-process.h; wins over detection')
    p.add_argument('--max-glibc', default=None,
                   help='Device GLIBC ceiling for the ABI gate (deploy measures the real '
                        'value and reports it for the next build)')
    p.add_argument('--max-glibcxx', default=None, help='Device GLIBCXX ceiling')
    p.add_argument('--static-cxx', action='store_true',
                   help='Link -static-libstdc++ -static-libgcc (the ABI-gate remedy)')
    p.add_argument('--skip-abi-gate', action='store_true',
                   help='Last resort; result.json records that the gate was skipped')
    p.add_argument('--skip-build', action='store_true',
                   help='Install an already-built library (looked up in the output dir, '
                        'the artifact, then ./qimsdk-build/) instead of cross-compiling')
    # Device-side flags
    p.add_argument('--so', default=None,
                   help='Explicit path to an aarch64 .so; skips the build and its gates')
    p.add_argument('--model', default=None, help='Explicit path to the model file')
    p.add_argument('--force', action='store_true',
                   help='Replace an existing module (a timestamped backup is always kept)')
    p.add_argument('--skip-model', action='store_true', help='Do not push the model')
    p.add_argument('--skip-labels', action='store_true',
                   help='Do not push labels/settings')
    p.add_argument('--dry-run', action='store_true',
                   help='Build, then stop after the read-only device checks: resolve the '
                        'module directory and measure the ABI, write nothing')
    p.add_argument('--json', action='store_true',
                   help='Print result JSON to stdout on completion')
    args = p.parse_args()

    # Credentials are read from configs/.env, never taken as arguments — a password on
    # the command line lands in shell history and in `ps` output while the command runs.
    if args.device_ip:
        os.environ['DEVICE_IP'] = args.device_ip
    if args.device_user:
        os.environ['DEVICE_USER'] = args.device_user
    if args.host_key:
        os.environ['HOST_KEY'] = args.host_key

    cfg, missing = load_device_config()
    errors = list(missing)
    if not args.output_dir:
        errors.append('DEPLOY_OUTPUT_DIR not set in configs/.env')
    if errors:
        for e in errors:
            print(f'[FAIL]  {e}', flush=True)
        print('[FAIL]  Run the config wizard (see SKILL.md "Before You Start") and '
              're-run preflight: preflight_check.py --mode M', flush=True)
        sys.exit(1)

    output_dir = args.output_dir
    if output_dir and not pathlib.Path(output_dir).is_absolute():
        output_dir = str(REPO_ROOT / output_dir)

    result = deploy_mode_m(
        artifact_path=args.artifact_path,
        deploy_output_dir=output_dir,
        cfg=cfg,
        so_override=args.so,
        model_override=args.model,
        imsdk_include=args.imsdk_include,
        max_glibc=args.max_glibc,
        max_glibcxx=args.max_glibcxx,
        static_cxx=args.static_cxx,
        skip_abi_gate=args.skip_abi_gate,
        skip_build=args.skip_build,
        skip_model=args.skip_model,
        skip_labels=args.skip_labels,
        force=args.force,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(json.loads(cfg.redact(json.dumps(result))), indent=2))

    # A dry run is not a pass in the meaningful sense — nothing was installed — but it
    # is not a failure either, so it exits 0 with module_registered false.
    if result['dry_run']:
        sys.exit(0 if not result['failure_reason'] else 1)
    sys.exit(0 if result['module_registered'] else 1)


if __name__ == '__main__':
    main()
