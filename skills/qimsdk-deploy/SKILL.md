---
name: qimsdk-deploy
description: "Deploy, build, run, and retrieve QIM SDK GStreamer C apps, gst-launch pipelines, QIM SDK C++ apps, and Python qimsdk apps on Qualcomm Linux devices. Five modes: Mode A (gst-launch — push and run directly, no build), Mode B (Ubuntu on-device build — QIMSDK source tree on device), Mode C (host build — gstreamer-app-builder C sample app, build on a Linux workstation (x86_64 or aarch64, arch auto-detected) using the host SDK, deploy to QLI device), Mode P (Python qimsdk app — push main.py and run, no build), Mode D (host build — cpp-app-builder C++ SDK app, standalone build against the Yocto SDK on a Linux x86_64/WSL workstation, deploy to QLI device). Returns structured result for eval scoring."
---

# QIM SDK Deploy Skill

## Operating Principle — Fail Fast, Don't Fix

When anything goes wrong — a missing password, a failed SSH connection, a path that doesn't exist, a missing config field — **stop immediately and report the exact error to the user**. Do not attempt workarounds, retries, alternative paths, or recovery steps unless explicitly documented in this skill. The user is responsible for fixing environment and configuration issues. Once fixed, they re-invoke the skill.

This keeps the skill predictable and token-efficient. Silent fallbacks hide real problems.

---

## ABSOLUTE RULES — NO EXCEPTIONS

These rules override any instinct to "be helpful by trying something else." They apply to every invocation, every mode, every step.

1. **NEVER improvise.** Run the reference scripts exactly as documented. Do not write ad-hoc commands to work around a failure.

2. **STOP on any failure.** Report the exact error message. Do not try an alternative path. The correct response to failure is: show the error, name the script/step that failed, and stop. "Let me try X instead" is NEVER the correct response.

3. **ALWAYS ask for mode and artifact path before doing anything.** Do not read `.env`, do not run preflight, do not check anything until you know: (a) Mode A / B / C / P / D, (b) the artifact path. Then read `configs/.env` for only the keys that mode needs.

4. **NEVER proceed to deploy without a passing preflight for the current mode and current `.env`.** Re-read `configs/.env` from disk immediately before every preflight and every deploy — never reuse values remembered from earlier in the conversation, and never pass device credentials as command-line flags (let the script read the file, so a mid-session edit always takes effect). If preflight has not been run: run it first. If deploying a different mode than preflight was last run for: re-run preflight for the current mode.

5. **NEVER commit anything.** Leave git completely to the user.

6. **NEVER hardcode credentials.** All credentials come from `configs/.env`.

7. **NEVER skip the config wizard.** If `configs/.env` is incomplete, the wizard runs before anything else, every time.

8. **NEVER install, fix, or modify anything on the device or linux workstation during a deploy or eval run.** When a pipeline, build, or device error occurs, show the exact error output and stop — do not attempt to fix it. Exception: preflight and SSH connection setup (host key, key copy, config wizard) — these are one-time setup steps the skill guides the user through so they can get connected.

9. **NEVER run two deploys in parallel if they compete for an exclusive hardware resource.** The device has one of each piece of hardware — two pipelines sharing the same resource will fail or corrupt each other's output. Camera pipelines (e.g. ISP camera, USB camera) are the most common case and must always run sequentially. When in doubt, run sequentially.

10. **NEVER ask the user to "press Enter" or "click" anything.** This skill runs through a chat interface — the user answers by typing. Every prompt must end with a concrete question expecting a typed reply, never a keypress or button action.

---

## Deploy flow

The skill always deploys. Preflight runs automatically before every deploy — the user never needs to ask for it explicitly. The two steps the skill runs internally are:

### Step A — preflight (automatic, before every deploy)

All deploy scripts live in the `references/` folder next to this SKILL.md file. Use whichever path this file was loaded from — do not hardcode a path. For example, if this SKILL.md is at `.claude/skills/qimsdk-deploy/SKILL.md`, the scripts are at `.claude/skills/qimsdk-deploy/references/`.

```bash
python <skill-references-dir>/preflight_check.py \
    --mode <A|B|C|P|D> \
    [--artifact-path <path>]
```

Checks: Python env, PuTTY tools, credentials, TCP reachability, SSH login, device OS confirmation,
mode-specific tool/path checks (cmake, make, sudo, timeout, source tree, output dir, cam-server),
and for Mode C/D: workstation SSH, SDK validation, disk space.

**Always run preflight before the first deploy and whenever the device or environment changes.**
Credentials are read from `configs/.env` — see below.

**What preflight checks per mode — and what to do if it fails:**

Mode A requires on device:
- `timeout` command
- `gst-launch-1.0` installed
- Output directory writable
- All input files and model/label paths from `pipeline.sh` present on device

Mode B requires on device:
- `cmake` installed
- `make` installed
- `sudo` access for the device user
- `timeout` command
- Internet access (for `apt-get source` on first deploy if workspace not yet provisioned)
- `SOURCE_ROOT` in `.env` is optional — auto-discovered via glob if not set

Note: Mode B deploy auto-provisions the workspace from scratch (apt setup, cmake configure, build)
if `SOURCE_ROOT` does not exist. Preflight reports the current workspace state as informational;
it does NOT fail if cmake has not been run or the source tree is absent.

Mode C requires on linux workstation:
- SSH key auth to linux workstation
- ~8GB free disk on local (non-NFS) filesystem (for SDK + repo + build artifacts)

Note: Mode C deploy auto-provisions the workspace from scratch (SDK download+install, git clone,
cmake configure, host-build) if not already set up. The SDK zip
(`x86-qli-2.0-standardsdk.zip` for x86_64 hosts) is downloaded from codelinaro.org if absent.
If the download fails (network blocked), place the zip manually at
`{LINUX_WORKSTATION_BUILD_DIR}/x86-qli-2.0-standardsdk.zip` and re-run.

Mode P requires on device:
- `python3` installed
- `qimsdk` Python package installed (`from qimsdk import Pipeline` must work)
- `timeout` command
- Wayland compositor running (for waylanksink pipelines)

Note: Mode P is **fail-fast only** — if `qimsdk` is not installed, preflight fails with a clear
message. There is no auto-install. The user must install the QIM SDK Python package on the device
before deploying. Unlike Modes B/C/D, Mode P does no workspace provisioning.

Mode D requires on the Linux/WSL workstation:
- SSH key or password auth to the workstation
- ~5GB free disk on local (non-NFS) filesystem (for the Yocto SDK)

Mode D requires on device:
- `libqtiimsdk.so` runtime library present (WARN only) — a Mode D binary dynamically links it;
  if preflight WARNs it's missing, install the C++ IMSDK runtime on the device before deploying
  or the binary will fail to start.

Note: Mode D deploy auto-provisions the Yocto SDK (unzip + run the installer into
`{LINUX_WORKSTATION_BUILD_DIR}/qcom-sdk`) if not already installed, then builds each app
**standalone** (out of tree) against the installed SDK's target sysroot — no shared source tree
is cloned or mutated, unlike Mode C. See "Mode D — C++ Standalone Host Build" below.

**Preflight failure means deploy will fail.** Every [FAIL] in preflight output must be resolved before running deploy. Do not proceed with deploy if preflight exits non-zero.

### Step B — deploy

All scripts are in `references/` next to this SKILL.md — use the same path used for preflight above.

```bash
# Mode A (gst-launch)
python <skill-references-dir>/deploy_mode_a.py \
    --artifact-path <path/to/artifact> \
    [--output-dir outputs/deploy]

# Mode B (Ubuntu on-device C app build)
python <skill-references-dir>/deploy_mode_b.py \
    --artifact-path <path/to/artifact> \
    [--output-dir outputs/deploy]

# Mode C (host build on linux workstation)
python <skill-references-dir>/deploy_mode_c.py \
    --artifact-path <path/to/artifact> \
    [--output-dir outputs/deploy]

# Mode P (Python qimsdk app — main.py, or legacy app.py)
python <skill-references-dir>/deploy_mode_p.py \
    --artifact-path <path/to/artifact> \
    [--output-dir outputs/deploy]

# Mode D (host build on Linux/WSL workstation — cpp-app-builder C++ SDK app)
python <skill-references-dir>/deploy_mode_d.py \
    --artifact-path <path/to/artifact> \
    [--output-dir outputs/deploy]
```

Each script reads credentials from `configs/.env` (same as preflight).

---

## Before You Start — `configs/.env`

**`configs/.env` is the single source of truth for all credentials and device connection details.** It is read fresh on EVERY invocation — no values are carried between sessions or conversation turns.

This file is gitignored and never committed. Create it once from the sample — `cp configs/.env.sample configs/.env` — then fill in your values. Both preflight and deploy scripts read it automatically.

### If `configs/.env` is missing or incomplete — run the config wizard

When `configs/.env` does not exist, or when required keys for the selected mode are missing, run the **interactive config wizard** before doing anything else:

1. **Mode is already known from Step 0** — only ask for keys that mode needs (see table below)
2. For each required key for that mode (in order), ask one at a time:
   - State what the key is for
   - Explain how to find its value
   - Show an example
   - Wait for the user's answer
   - **End every prompt with a concrete question expecting a typed answer.** Never tell the user to "press Enter", "hit return", or "click" anything — this skill is driven through a chat interface where the user replies with text; there is no keypress or button to trigger.
3. After collecting all values, write them to `configs/.env` with inline comments
4. Tell the user: "Config saved to `configs/.env`. Running preflight for Mode <X> now."
5. Proceed to preflight — do not skip it.

**Mode C wizard — ask this first before collecting any LINUX_WORKSTATION_* keys:**

> "For Mode C, I need a Linux x86_64 machine to run the host build. Two options:
> - **Remote Linux workstation** — a Linux machine on the network (e.g. `hu-gaurmeht-lv`)
> - **WSL (Windows Subsystem for Linux)** — Ubuntu running locally on this Windows machine
>
> Which are you using?"

**If they answer WSL:**
1. Ask first: "What is your WSL username? Run `whoami` inside your WSL terminal and tell me the output."

2. Once you have the username, tell them: "Before I can connect, WSL needs its SSH server running. Run these inside your WSL terminal (Ubuntu app):
   ```bash
   sudo service ssh start
   sudo service ssh status
   ```
   It should say `active (running)`. Keep the WSL window open (minimize it — don't exit).

   Also confirm passwordless sudo is configured (needed for auto-installing build tools):
   ```bash
   sudo whoami
   ```
   It should print `root` without a password prompt. If it asks for a password, run (replace `<wsl-username>` with the username you just told me):
   ```bash
   echo "<wsl-username> ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/<wsl-username>
   ```
   Let me know once SSH is running and sudo is passwordless."

3. Ask for port: "What port is WSL SSH on? Run inside WSL: `grep -E '^Port' /etc/ssh/sshd_config 2>/dev/null || echo '22'`"

3. Verify connectivity from PowerShell — tell the user to run:
   ```powershell
   ssh -p <port> <wsl-username>@localhost "echo connected"
   ```
   If it asks for a password that's fine (key not copied yet). If connection is refused, sshd isn't running — go back to step 1.

4. Copy the SSH key to WSL. Tell the user to run in PowerShell:
   ```powershell
   Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub" | ssh -p <port> <wsl-username>@localhost "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"
   ```
   Enter WSL password when prompted. No output = success. Then verify key auth works:
   ```powershell
   ssh -p <port> -i "$HOME\.ssh\id_ed25519_qimsdk" <wsl-username>@localhost "echo connected"
   ```
   Should print `connected` with no password prompt.

5. Ask for build dir: "Where should build files go in WSL? Pick a path on WSL local disk with ~10GB free, e.g. `/home/<wsl-username>/qimsdk-build`"

6. Write to `configs/.env`:
   - `LINUX_WORKSTATION_HOST=localhost`
   - `LINUX_WORKSTATION_USER=<wsl-username>`
   - `LINUX_WORKSTATION_PORT=<port>`
   - `LINUX_WORKSTATION_KEY=C:/Users/<windows-username>/.ssh/id_ed25519_qimsdk`
   - `LINUX_WORKSTATION_BUILD_DIR=<chosen-path>`

**If they answer remote Linux workstation:**
- Proceed with the keys table as normal (ask `LINUX_WORKSTATION_HOST`, `LINUX_WORKSTATION_USER`, etc. one at a time).



**`configs/.env` is the right place for ALL config** including Mode C linux workstation details. The file is gitignored and never committed. It persists across sessions so you don't have to re-enter values every time.

### Keys required per mode

**Mode A — gst-launch (always required):**
| Key | What it is | How to find it | Example |
|-----|-----------|---------------|---------|
| `DEVICE_IP` | IP of the target device | `ip addr` on device, or router DHCP | `192.168.1.100` |
| `DEVICE_USER` | SSH login username | `ubuntu` (Ubuntu) or `root` (QLI) | `ubuntu` |
| `DEVICE_KEY` | SSH private key path (preferred) | Check existing keys: `Get-ChildItem $HOME\.ssh\*.pub` in PowerShell. If `id_ed25519_qimsdk.pub` exists, use `C:/Users/<you>/.ssh/id_ed25519_qimsdk`. Otherwise generate: `ssh-keygen -t ed25519 -C "qimsdk-deploy" -f "$HOME\.ssh\id_ed25519_qimsdk"` | `C:/Users/<you>/.ssh/id_ed25519_qimsdk` |
| `DEVICE_PASSWORD` | SSH password (fallback if no key) | Device administrator | `<password>` |
| `HOST_KEY` | SSH host key fingerprint | **Preferred: leave blank and run preflight — it prints the fingerprint automatically.** If you need it before preflight: `ssh <DEVICE_USER>@<DEVICE_IP> "for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf \$f 2>/dev/null; done"` — pick ed25519 if present, else ecdsa. | `SHA256:...` |
| `DEPLOY_OUTPUT_DIR` | Local folder for logs and output | Default is fine | `outputs/deploy` |

> Set either `DEVICE_KEY` or `DEVICE_PASSWORD` (or both — key is tried first). If neither is set, preflight fails with instructions to see `ssh-setup.md`.

**Mode B — adds:**
| Key | What it is | How to find it | Example |
|-----|-----------|---------------|---------|
| `SOURCE_ROOT` | QIMSDK source tree path on device | SSH in, `ls ~ \| grep gst-plugins` | `/home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-<version>` |

**Mode C — adds (instead of SOURCE_ROOT):**
| Key | What it is | How to find it | Example |
|-----|-----------|---------------|---------|
| `LINUX_WORKSTATION_HOST` | Linux host-build workstation hostname/IP (x86_64 or aarch64 — arch auto-detected) | Remote machine: `hostname` on the machine. WSL: `localhost` | `my-linux-workstation` or `localhost` (WSL) |
| `LINUX_WORKSTATION_USER` | SSH username on linux workstation | `whoami` on the machine | `myusername` |
| `LINUX_WORKSTATION_PASSWORD` | SSH password (used when key not set) | Your login password | `mypassword` |
| `LINUX_WORKSTATION_KEY` | SSH private key path for linux workstation (optional if LINUX_WORKSTATION_PASSWORD set) | Check existing keys: `Get-ChildItem $HOME\.ssh\*.pub` in PowerShell. If `id_ed25519_qimsdk.pub` exists, use `C:/Users/<you>/.ssh/id_ed25519_qimsdk`. Otherwise generate: `ssh-keygen -t ed25519 -C "qimsdk-deploy" -f "$HOME\.ssh\id_ed25519_qimsdk"` | `C:/Users/<you>/.ssh/id_ed25519_qimsdk` |
| `LINUX_WORKSTATION_BUILD_DIR` | Working directory on linux workstation (local disk, not NFS) | Pick a path on local disk with ~10GB free | `/home/myusername/qimsdk-build` |
| `LINUX_WORKSTATION_PORT` | SSH port on the linux workstation | Standard Linux: use `22`. If you're not sure or using WSL, run on the machine: `grep -E '^Port' /etc/ssh/sshd_config 2>/dev/null \|\| echo "22"` | `22` |

> SDK install and repo clone are auto-provisioned on first deploy if not already present.
> If internet is blocked, place the SDK zip at `{LINUX_WORKSTATION_BUILD_DIR}/sdk.zip` (simplest) or use the arch-specific name: `x86-qli-2.0-standardsdk.zip` (x86_64) or `arm-qli-2.0-standardsdk.zip` (aarch64/WSL on ARM). The script checks for all three.

All Mode C keys above are required. Preflight exits immediately with `[FAIL]` if any are missing.

**Mode C — optional, bring-your-own SDK/repo:**
| Key | What it is | Example |
|-----|-----------|---------|
| `LINUX_WORKSTATION_SDK_PATH` | Absolute path **on the workstation** to an SDK installer — a `.zip` (unzipped automatically to find the installer inside) or a `.sh` (already extracted, run directly). Only consulted when the SDK is not yet installed; ignored otherwise. If not set, falls back to the existing zip-lookup + codelinaro.org download. | `/local/mnt/sdk/x86-qli-2.0-standardsdk.zip` or `/home/user/sdk-installer.sh` |
| `LINUX_WORKSTATION_IMSDK_PATH` | Absolute path **on the workstation** to an already-cloned `gst-plugins-imsdk` repo. When set, this path is used directly and the git clone step is skipped entirely. If the path is set but `CMakeLists.txt` is not found there, deploy fails with a clear message — it never silently falls back to cloning a fresh copy. If not set, falls back to cloning from GitHub into `{LINUX_WORKSTATION_BUILD_DIR}/gst-plugins-imsdk`. | `/local/mnt/repos/gst-plugins-imsdk` |

**Ask the user during the Mode C wizard, right after `LINUX_WORKSTATION_BUILD_DIR`:**
1. "Do you have the SDK installer already on the workstation (as a `.zip` or `.sh`)?"
   — if yes: "What is the path on the workstation?" → save as `LINUX_WORKSTATION_SDK_PATH`
   — if no: nothing to do; SDK downloads automatically on first deploy
2. "Do you have `gst-plugins-imsdk` already cloned on the workstation?"
   — if yes: "What is the path on the workstation?" → save as `LINUX_WORKSTATION_IMSDK_PATH`
   — if no: nothing to do; repo clones automatically on first deploy

**Mode D — adds (same LINUX_WORKSTATION_* keys as Mode C, no new required keys):**

Mode D reuses every `LINUX_WORKSTATION_HOST/USER/KEY/PASSWORD/PORT/BUILD_DIR` key above — it targets
the same class of Linux x86_64/WSL workstation, just with a different SDK (the Yocto standard SDK,
not gst-plugins-imsdk). Optional additions:

| Key | What it is | How to find it | Example |
|-----|-----------|---------------|---------|
| `LINUX_WORKSTATION_SDK_URL` | Source for the Yocto SDK zip (installer .sh inside) | A `file://` path to a network share, or an `http(s)://` URL | `file:///mnt/share/qcom-yocto-sdk-deploy.zip` |
| `LINUX_WORKSTATION_SDK_PATH` | Absolute path **on the workstation** to a Yocto SDK installer already present — a `.zip` (unzipped automatically to find the `.sh` installer inside) or a `.sh` (already extracted, run directly). Takes precedence over the build-dir zip lookup and `LINUX_WORKSTATION_SDK_URL`. Only consulted when the SDK is not yet installed; ignored otherwise. **Same key as Mode C** — set once, works for both modes. | `/local/mnt/sdk/qcom-yocto-sdk-deploy.zip` or `/local/mnt/sdk/installer.sh` |

> If a `qcom-yocto-sdk*.zip` (or `sdk.zip`) is already present in `LINUX_WORKSTATION_BUILD_DIR`,
> neither `LINUX_WORKSTATION_SDK_PATH` nor `LINUX_WORKSTATION_SDK_URL` is needed — deploy uses the
> local zip. The SDK is installed once into `{LINUX_WORKSTATION_BUILD_DIR}/qcom-sdk` and reused for
> every app. SDK source precedence: `LINUX_WORKSTATION_SDK_PATH` → build-dir zip → `LINUX_WORKSTATION_SDK_URL`
> → default Artifactory zip:
> `https://artifacts.codelinaro.org/artifactory/qli-ci/flashable-binaries/meta-qcom/qcom-armv8a/qcom-yocto-sdk-deploy-0807.zip`.

**Ask the user during the Mode D wizard, right after `LINUX_WORKSTATION_BUILD_DIR`:**
1. "Do you have the Yocto SDK installer already on the workstation (as a `.zip` or `.sh`)?"
   — if yes: "What is the path on the workstation?" → save as `LINUX_WORKSTATION_SDK_PATH`
   — if no: deploy will download the default `qcom-yocto-sdk-deploy-0807.zip`
     from the Artifactory URL on first deploy. If you need a different source,
     set `LINUX_WORKSTATION_SDK_URL` to a `file://` path or `http(s)://` URL.
   (Mode D has no repo-clone step, so there is no `IMSDK_PATH` question — unlike Mode C.)

**Mode P — same as Mode A (no extra keys needed):**

Mode P uses the same device credentials as Mode A. No additional keys required — just `DEVICE_IP`, `DEVICE_USER`, `DEVICE_KEY`/`DEVICE_PASSWORD`, `HOST_KEY`, `DEPLOY_OUTPUT_DIR`. Never ask for `SOURCE_ROOT` or `LINUX_WORKSTATION_*` for Mode P. Artifact entry point is `main.py` (legacy `app.py` also accepted).

### Full example — copy this and fill in your values

```bash
# configs/.env
# ─────────────────────────────────────────────────────────────────────────────
# Required for all modes
# ─────────────────────────────────────────────────────────────────────────────

DEVICE_IP=<device-ip>
# Why: The SSH target. Used by preflight and deploy to connect to the device.

DEVICE_USER=ubuntu
# Why: SSH login user. Usually 'ubuntu' on Ubuntu devices, 'root' on QLI 2.0.

DEVICE_PASSWORD=<password>
# Why: SSH password. Never put this on the command line or in a YAML config.
#      Set DEVICE_PASSWORD here; the scripts read it via os.environ.

HOST_KEY=SHA256:cKvXMnoKhO6g+fnn17WzmrpnfSSXW+MwzwFNiUu/gC4
# Why: Prevents connecting to the wrong device.
#      If you don't have it, run preflight without HOST_KEY first — the script
#      will show you the actual fingerprint and tell you to save it here.

DEPLOY_OUTPUT_DIR=outputs/deploy
# Why: Where deploy scripts save per-artifact logs and pulled output files.

# ─────────────────────────────────────────────────────────────────────────────
# Mode B only (Ubuntu on-device build)
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_ROOT=/home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-1.0.r1.06800

# ─────────────────────────────────────────────────────────────────────────────
# Mode C (host build on linux workstation)
# ─────────────────────────────────────────────────────────────────────────────

LINUX_WORKSTATION_HOST=<linux-workstation-hostname>
LINUX_WORKSTATION_USER=<username>
LINUX_WORKSTATION_KEY=C:/Users/<you>/.ssh/id_ed25519_qimsdk
LINUX_WORKSTATION_PORT=22
# Why: SSH port on the linux workstation. Default is 22 (standard Linux).
#      WSL (Windows Subsystem for Linux) commonly uses port 2222.
#      To find your port, run on the linux workstation:
#        grep -E '^Port' /etc/ssh/sshd_config 2>/dev/null || echo "22"

# ─────────────────────────────────────────────────────────────────────────────
# Mode D (host build on Linux/WSL workstation — cpp-app-builder C++ SDK app)
# Reuses ALL LINUX_WORKSTATION_* keys above — no new required keys.
# ─────────────────────────────────────────────────────────────────────────────

LINUX_WORKSTATION_SDK_URL=file:///path/to/qcom-yocto-sdk-deploy.zip
# Why: Source for the Yocto standard SDK (installer .sh inside). Accepts a
#      file:// path (a share/mount visible on the workstation) or an
#      http(s):// URL. Optional if a qcom-yocto-sdk*.zip is already present
#      in LINUX_WORKSTATION_BUILD_DIR — deploy looks there first.
```

---

## When invoked — mandatory startup sequence

Every invocation follows this exact sequence. Do not skip steps.

### Step -1 — Verify Python is available

Before anything else, run:

```bash
python --version || python3 --version || py --version
```

If all three fail, Python is not installed. Tell the user:
> "Python 3 is required to run deploy. Install it from https://www.python.org/downloads/ and ensure it is on PATH. On Windows, tick 'Add Python to PATH' during installation. Then restart your terminal and try again."
**Stop here until Python is available.**

### Step 0 — Ask what the user wants to do

Before reading any files or checking any config, ask the user to pick exactly one mode from
this list — present each mode as its own separate, distinct option, never abbreviated or
grouped (e.g. never phrase it as "P or D?"):

1. **Mode — pick one:**
   - **A** — gst-launch pipeline (`pipeline.sh`), no build
   - **B** — Ubuntu on-device build (gstreamer-app-builder C app, QIMSDK source tree already on device)
   - **C** — Host build (gstreamer-app-builder C app), device is QLI 2.0, needs a Linux workstation
   - **D** — Host build (cpp-app-builder C++ SDK app), needs a Linux workstation
   - **P** — Python qimsdk app (`main.py`/`app.py`), no build
2. **Artifact path:** path to the artifact folder

Do not proceed until you have both. Mode determines which config keys are required. The subcommand is always deploy — preflight runs automatically before every deploy.

**Mode selection guide (show this if the user is unsure) — each mode below is a separate,
distinct choice, not to be merged or abbreviated when relaying to the user:**

- **Mode A** — artifact is a `pipeline.sh` (gst-launch command). No build needed.
- **Mode B** — artifact has `main.c` + `set(GST_EXAMPLE_BIN ...)`, device is Ubuntu with QIMSDK source tree on-device.
- **Mode C** — artifact has `main.c` + `set(GST_EXAMPLE_BIN ...)`, device is QLI 2.0 / host build (no build tools). Needs a Linux workstation (x86_64 or aarch64 — arch is auto-detected; WSL on either Windows x86_64 or Windows ARM works).
- **Mode D** — artifact has `main.cc` + `set(TEST_TARGET ...)` using `qti::Pipeline` / `<qti/imsdk.h>` (cpp-app-builder). Needs a Linux x86_64/WSL workstation (no arch auto-detection — the Yocto SDK zip in use is x86_64-only).
- **Mode P** — artifact has `main.py` (or legacy `app.py`) using `qimsdk.Pipeline`. No build.

Mode C and Mode D both host-build on a workstation and push a binary to the device, but they target
different builder contracts (gstreamer-app-builder's C sample apps vs cpp-app-builder's standalone
C++ SDK apps) and use different toolchains (gst-plugins-imsdk source tree vs the Yocto standard SDK)
— check `main.c`+`GST_EXAMPLE_BIN` (Mode C) vs `main.cc`+`TEST_TARGET` (Mode D) in the artifact.

### Step 1 — Read `configs/.env` for the selected mode

Read `configs/.env` from the repo root. Check only the keys required for the selected mode:

| Mode | Required keys |
|------|--------------|
| A | `DEVICE_IP`, `DEVICE_USER`, `DEVICE_KEY` or `DEVICE_PASSWORD` (at least one), `DEPLOY_OUTPUT_DIR` |
| B | All Mode A keys + `SOURCE_ROOT` |
| C | All Mode A keys + `LINUX_WORKSTATION_HOST`, `LINUX_WORKSTATION_USER`, `LINUX_WORKSTATION_BUILD_DIR` |
| P | All Mode A keys (no extra) |
| D | All Mode A keys + `LINUX_WORKSTATION_HOST`, `LINUX_WORKSTATION_USER`, `LINUX_WORKSTATION_BUILD_DIR` (same as Mode C) + optional `LINUX_WORKSTATION_SDK_URL` |

- If the file does not exist: run the config wizard for the selected mode (see below)
- If the file exists but is missing required keys for the selected mode: run the wizard for only the missing keys
- If all required keys are present: proceed to Step 1a (Mode C/D) or Step 2 (all other modes)

### Step 1a — Mode C / Mode D only: ask about SDK and repo (REQUIRED, never skip)

After collecting `LINUX_WORKSTATION_BUILD_DIR`, always ask these questions before running preflight.
**Do not skip to preflight without completing this step.**

**Mode D — SDK question (ask exactly this):**
> "Do you have the Yocto SDK installer already on the workstation — as a `.zip` or a `.sh` file?"
- **Yes** → "What is the full path to it on the workstation?" → set `LINUX_WORKSTATION_SDK_PATH`
- **No** → leave unset; deploy will download the default Yocto SDK zip from Artifactory on first run

**Mode C — SDK question (ask exactly this):**
> "Do you have the SDK installer already on the workstation — as a `.zip` or a `.sh` file?"
- **Yes** → "What is the full path to it on the workstation?" → set `LINUX_WORKSTATION_SDK_PATH`
- **No** → leave unset; SDK downloads automatically from codelinaro.org on first deploy

**Mode C — repo question (ask exactly this):**
> "Do you have `gst-plugins-imsdk` already cloned on the workstation?"
- **Yes** → "What is the full path to it on the workstation?" → set `LINUX_WORKSTATION_IMSDK_PATH`
- **No** → leave unset; repo clones automatically from GitHub on first deploy

### Step 2 — Run preflight for the selected mode

Always run preflight before deploying — it takes ~5s and verifies the device is reachable and ready:

```bash
python <skill-references-dir>/preflight_check.py --mode <A|B|C|P|D>
```

**If preflight fails with a connection or auth error, work through the recovery flow below before asking the user to do anything manually.** Never proceed to Step 3 until preflight exits 0.

---

#### Preflight Recovery Flow

**Case 1 — Host key mismatch or `FATAL ERROR: Host key not in manually configured list`**

The device was likely re-imaged. Fetch the new fingerprint — run this in PowerShell (enter password when prompted):

```powershell
ssh <DEVICE_USER>@<DEVICE_IP> "for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf \$f 2>/dev/null; done"
```

This prints one `SHA256:...` line per key type the device has. Pick the `ed25519` line if present, otherwise use `ecdsa`. Update `HOST_KEY` in `configs/.env` with that value, then continue to Case 2 to re-copy the SSH key (a re-imaged device has lost all authorized_keys).

**Case 2 — `SSH key authentication failed`**

The SSH public key is not installed on the device (new or re-imaged device). Copy it now.

**NEVER ask the user for their password through the chat interface — not even "just this once".** Always give them the plink command to run themselves. Credentials stay out of the conversation.

First confirm the HOST_KEY in `configs/.env` is current (run Case 1 if unsure). Then copy the public key to the device — run these three commands one at a time (enter password at each prompt):

```powershell
ssh <DEVICE_USER>@<DEVICE_IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub" | ssh <DEVICE_USER>@<DEVICE_IP> "cat >> ~/.ssh/authorized_keys"
ssh <DEVICE_USER>@<DEVICE_IP> "chmod 600 ~/.ssh/authorized_keys"
```

Replace `<DEVICE_USER>` and `<DEVICE_IP>` from `configs/.env`. Do not chain these with `&&` — run each separately. You may be prompted for the password 2-3 times per command (PAM stacking — re-enter the same password each time). No output means success.

After running, re-run preflight. If it still fails with key auth, check that `DEVICE_KEY` in `configs/.env` points to the correct key file.

**Case 3 — `DEVICE_KEY` file not found**

The key file path in `configs/.env` is wrong or the file doesn't exist. Check:
```bash
ls -la <DEVICE_KEY>
```
If missing, generate a new key pair:
```powershell
ssh-keygen -t ed25519 -C "qimsdk-deploy" -f "$HOME\.ssh\id_ed25519_qimsdk"
```
Then update `DEVICE_KEY` in `configs/.env` and repeat Case 2.

**Case 4 — TCP reachable but all auth fails**

Try password auth as a fallback: uncomment `DEVICE_PASSWORD` in `configs/.env` and re-run preflight. Once preflight passes with password auth, run Case 2 to install the key, then switch back to key auth.

---

### Step 3 — State what you're about to do, then run

Tell the user: mode, artifact path, script that will run. Then run it. Do not improvise.

---

## Output review categories — sync vs async

Artifacts fall into two review categories based on how output is delivered:

| Category | Condition | Review timing |
|---|---|---|
| **Sync** | `waylandsink` present with no `filesink` (display-only), OR `qtirtspbin` as sink (RTSP-out) | **Must be reviewed live** — output cannot be recovered after the run. Pause before AND after every sync run. |
| **Async** | `filesink` present (output written to file) | File can be reviewed any time after the run. |

**For sync artifacts, when acting as orchestrator (not called from harness):**
- **Before running:** Tell the user the artifact name, what pipeline it is, what they should see on screen or stream, and ask them to confirm they are watching before you start.
- **After running:** Ask explicitly — (1) did you see the expected output? (2) did the inference results look correct? (bounding boxes on right objects, correct labels, etc.). Record their y/n answer as the G3 human score.
- **Never skip this for display or RTSP-out pipelines.** Running a display pipeline without the user watching is a wasted run with no scorable output.

## Non-interactive callers (`--no-confirm`)

When called from the eval skill or another automated context with `--no-confirm`:
- All config values come from `configs/.env`. If any required key is missing, stop and report which keys.
- Never ask interactive questions.
- If the artifact has a `waylandsink`, emit: "⚠️ Non-interactive call: pipeline writes to display — ensure Wayland compositor is running on device." Then proceed.

---

## Hard Rules

- **NEVER ask the user for a password through the chat interface** — not to install a key, not "just this once", not for any reason. If a password is needed to copy an SSH key to the device, give the user the plink command to run themselves. Credentials must not appear in the conversation.
- All SSH/SCP in deploy scripts uses **paramiko** (Python) — not plink/pscp. PuTTY is not required by the deploy scripts (it is optional for interactive manual use only).
- Always pass `HOST_KEY` fingerprint to every paramiko connection — never use AutoAddPolicy in the deploy scripts.
- Always use `timeout --signal=SIGINT --kill-after=15 30` for all gst-launch (Mode A) pipelines — both file-source and camera. 30 seconds allows for QNN/SNPE/TFLite model loading on first invocation (which can take 15-25s) plus time for PLAYING and initial inference. SIGINT (not SIGTERM) lets GStreamer flush the MP4 moov atom cleanly.
- **For Mode B/C/D C/C++ app file-source pipelines**: run to natural EOS — no timeout (5 min SSH cap). The binary exits on its own when the file ends. Using timeout before EOS produces corrupt MP4.
- **Camera pipeline retry logic (Mode A/B)**: the deploy script runs the camera pipeline first without restarting cam-server (the common case is fine). If attempt 1 fails (never reached PLAYING, or crashed, or no moov atom), it restarts cam-server, waits 3 seconds, and retries once. A second failure is a real problem — not retried again. Do not add unconditional pre-run restarts.
- **Pull output immediately after each camera run before starting the next** — camera pipelines (02, 03, c-app equivalents) share the same output filenames (`two_stream_obj_detect_out.mp4`, `three_stream_*`). The next run overwrites the previous output. Always: run → pull → run next.
- **Camera pipeline run duration: 30s for all modes** (Mode A/B/C/D). Use `timeout --signal=SIGINT --kill-after=15 30`. File-source runs to natural EOS.
- Always use `echo <PASSWORD> | sudo -S <cmd>` for any write to root-owned paths
- Never commit anything — leave git to the user
- Device config is always passed in as parameters — never hardcoded
- Never skip the upfront config stage when called standalone
- **Check port availability with `/proc/net/tcp` not `ss`** — `ss` is not available on QLI 2.0 devices. Use `cat /proc/net/tcp | grep <HEX_PORT>` where port 8900 = 22C4, port 8901 = 22C5 (4-digit hex uppercase).
- **RTSP input pipeline timing:** When you control both the RTSP source and the RTSP consumer pipeline, start source first, then start consumer exactly **3 seconds** later (not more). Longer gaps allow the source to start looping or stall before the consumer connects. Verify source port is open before starting consumer.
- **Wayland env is always set before every pipeline run** (all modes, all sink types) — `qtivoverlay` probes for a display even in file-output pipelines; setting it is harmless when no display is present. The socket must be searched across all of `/run`, not just `$(id -u)` runtime dir, because the compositor may run as a different user (e.g. `weston` uid 1000 at `/run/user/1000/wayland-1` while the pipeline runs as `root`). The deploy scripts handle this automatically; use this snippet if running manually:
  ```bash
  WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1)
  export XDG_RUNTIME_DIR=$(dirname "$WS")
  export WAYLAND_DISPLAY=$(basename "$WS")
  ```

---

## Structured Result

The deploy script always writes `result.json` to `<DEPLOY_OUTPUT_DIR>/<artifact-name>/result.json`,
even on failure. The schema and AI result card format are defined in:

**[`references/result-format.md`](references/result-format.md)**

Read that file before rendering any deploy result. Always show the result card first.

---

## Output Collection

```
<DEPLOY_OUTPUT_DIR>/<artifact-name>/
├── <artifact-name>_device.log
├── <artifact-name>_build.log     (Modes B/C/D only)
└── <artifact-name>_output.<ext>
```

When called standalone, pull to `DEPLOY_OUTPUT_DIR` (from `configs/.env`) and notify user.

---

## SSH/SCP Command Templates

### Windows host (paramiko — all deploy scripts)
The deploy scripts use paramiko internally. For interactive use from a terminal, use OpenSSH (included in Windows 10/11):
```bash
# SSH
ssh -i <KEY_FILE> <USER>@<IP> "<command>"

# SCP local → remote
scp -i <KEY_FILE> <local_file> <USER>@<IP>:<remote_path>

# SCP remote → local
scp -i <KEY_FILE> <USER>@<IP>:<remote_path> <local_path>
```

### Linux host
```bash
ssh -i <KEY_FILE> <USER>@<HOST> "<command>"
scp -i <KEY_FILE> <local_file> <USER>@<HOST>:<remote_path>
scp -i <KEY_FILE> <USER>@<HOST>:<remote_path> <local_path>
```

> **Deploy scripts always use paramiko** — see `deploy_mode_b.py`, `deploy_mode_a.py`, `deploy_mode_c.py` in `references/`. PuTTY (plink/pscp) is never needed.

---

## Mode A — gst-launch (No Build)

Use when: artifact is a `pipeline.sh` (generated by `qimsdk-gstreamer-dev`). No compilation needed. Works on any device with GStreamer and QIMSDK plugins installed.

**Script:** `references/deploy_mode_a.py`

---

### What the script does (step by step)

**A0 — Parse `pipeline.sh` and read `README.md` locally** before connecting to device:
- Read `README.md` **in full** if present — surface any prerequisites listed in "How to Run" (e.g. `wpctl set-default` for audio/AV pipelines) to the user before proceeding so they can complete them on the device
- Detect source type: `file-source` (filesrc), `camera` (qtiqmmfsrc/qticamsrc), or `rtsp` (rtspsrc)
- Extract output file path from `filesink location=<path>` (if present — display-only pipelines have none)
- Extract all input file paths from `filesrc location=<path>` (may contain `$HOME` shell variables)
- Detect if `waylandsink` is present (sets Wayland env before run regardless of sink type)
- Detect if `qtirtspbin` is present (RTSP serving **sink**, `rtsp_out=True`) — this is orthogonal to source type and does **not** make the pipeline an `rtsp` source
- **RTSP input pipelines** (`rtspsrc` — `source_type=rtsp`): cannot be run automatically — stop, print exact manual instructions, and ask the user to run and report back. Instructions to show:
  1. Push `pipeline.sh` to device: `pscp pipeline.sh ubuntu@<ip>:~/pipeline.sh`
  2. SSH in, set Wayland env, run: `bash ~/pipeline.sh`
  3. Verify: check for `Setting pipeline to PLAYING` in output; `ls -lh /home/ubuntu/Downloads/qimsdk_samples/media/output/` for any output file; pull it with `pscp`
  4. Ask user to paste the output and report what they observed
- **RTSP output pipelines** (`qtirtspbin` serving sink fed by camera/file): run automatically like any live sink — handled by the normal run path (same as `waylandsink`). Treated as a **sync** artifact (see review-timing table) — must be reviewed live.

**A1 — Verify input files exist on device** (file-source pipelines only, before pushing anything):
- For each `filesrc location=` path, expand shell variables on device (`eval echo $HOME/Downloads/qimsdk_samples/...`)
- `test -f <expanded_path>` — if any file is missing, FAIL with the exact path and stop
- **Why upfront**: avoids wasting time pushing the script only to fail 30 seconds into the run

**A2 — Push `pipeline.sh`** to `/home/<user>/pipeline.sh` via SFTP, normalizing line endings CRLF→LF. Windows editors produce `\r\n`; bash on Linux rejects `\r` as `command not found`. The deploy script always strips `\r` before pushing.

**A3 — `chmod +x`** and **`mkdir -p`** the output directory on device (if pipeline has a filesink)

**A3b — Kill stale GStreamer processes** before running:
```bash
echo <PASSWORD> | sudo -S pkill -9 -f 'gst-launch-1.0' 2>/dev/null
echo <PASSWORD> | sudo -S pkill -9 -f 'bash.*pipeline.sh' 2>/dev/null
sleep 2
# Verify — zombies (Z state) are fine, only live processes matter:
ps -eo stat,pid,cmd | grep 'gst-launch' | grep -v grep | grep -v '^Z' | wc -l
```
If count > 0 after SIGKILL: device needs reboot. Report and stop.

**Why SIGKILL directly, not SIGINT first:** Processes stuck waiting on v4l2/QNN hardware are in uninterruptible sleep — they ignore SIGINT entirely. Trying SIGINT first wastes time without helping. SIGKILL is safe here because stuck processes are in PREROLLING and have written nothing to corrupt.

**A4 — Set Wayland environment and run:**
```bash
WS=$(find /run -maxdepth 3 -name "wayland-*" ! -name "*.lock" 2>/dev/null | head -1)
export XDG_RUNTIME_DIR=$(dirname "$WS")
export WAYLAND_DISPLAY=$(basename "$WS")
timeout --signal=SIGINT --kill-after=15 30 bash /home/<user>/pipeline.sh 2>&1
```
See **Hard Rules → Wayland env** for why `/run` is searched broadly and why it is set regardless of sink type.
- **All gst-launch pipelines**: `timeout --signal=SIGINT --kill-after=15 30`. 30s allows for QNN/SNPE/TFLite model load on first invocation (can take 15-25s), PLAYING confirmation, and initial output write.
- **Camera**: same — `timeout --signal=SIGINT --kill-after=15 30`. Cam-server restart is automatic on retry if attempt 1 fails — do not restart unconditionally before every run.
- **Why SIGINT not SIGTERM**: SIGTERM kills immediately; the MP4 moov atom is never flushed. SIGINT triggers GStreamer EOS which writes the moov atom and closes the file correctly.

**A5 — Scan log** for results:
- `playing_reached = true` if any of these appear in log:
  - `Setting pipeline to PLAYING` (gst-launch format)
  - `Pipeline state changed from PAUSED to PLAYING` (C app format)
- Collect `ERROR:` lines into `error_lines`
- **Ignore (benign noise — never report as errors):**
  - `MapGbmBufInfoAddress: Mmap failed`
  - `Failed to initialize Wayland EGL display`
  - `Failed to initialize X11 EGL display`
  - `tiling.h WARNING`, `concat_opts WARNING`
  - `Internal data stream error` (normal EOS with mp4mux + `-e` flag)
  - `Got EOS from element`

**A6 — Check output file size** on device (`stat -c%s <path>`):
- Size > 0: pull to local output dir
- Size = 0: `0 bytes` — likely corrupt (pipeline didn't run long enough, or SIGTERM instead of SIGINT)
- File missing: pipeline may have written to a different path than expected
- **Fallback**: if the exact path has 0 bytes, search `find <output_dir> -name '*.mp4' -newer <sentinel>` for any MP4 written during the run
- **Display-only pipelines** (no filesink): `output_file_size = N/A` — nothing to pull

**A7 — Pull output file** via SFTP to `<DEPLOY_OUTPUT_DIR>/<artifact-name>/output.<ext>`

**A8 — Write `result.json`** to `<DEPLOY_OUTPUT_DIR>/<artifact-name>/result.json` — always written, even on failure, so failures are captured and reportable

---

## Mode P — Python qimsdk App

Use when: artifact is a Python app using `qimsdk.Pipeline` API (`main.py`, or legacy `app.py`, + `README.md`). No build step — push and run directly. Requires `qimsdk` Python package installed on device.

**Script:** `references/deploy_mode_p.py`

**Artifact structure:**
```
<artifact-name>/
├── main.py     — Python application (imports from qimsdk); app.py accepted as legacy fallback
└── README.md   — describes input files, output path
```

**What the script does:**

**P0 — Parse the app entry point + `README.md` locally** (no SSH):
- Entry point: `main.py` if present, else `app.py`; fails with a clear message if neither exists
- Source type: detect `qtiqmmfsrc`/`qticamsrc` in the app source → `camera`, else `file-source`
- Output path: only if the app source contains `filesink` — extracted from README `OUTPUT_FILE` row; `None` for display-only (waylanksink/RTSP)
- Input files: string literals matching device paths (`/home/ubuntu/Downloads/qimsdk_samples/...`, `/root/...`) in the app source + README

**P1 — SSH connect** using `configs/.env` credentials

**P2 — Push the app source** to `/tmp/deploy_p_<artifact-name>/<main.py|app.py>` on device (CRLF→LF normalized)

**P3 — Camera pre-run** (camera source only): restart cam-server (`sudo systemctl restart cam-server && sleep 3`) before every camera run — the encoder can be left wedged from a prior run.

**P4 — Ensure output dir** if app has filesink: `mkdir -p <output_dir>`

**P5 — Kill stale processes**: `pkill -9 -f '/tmp/deploy_p_<artifact-name>/<app-file>'`

**P6 — Run** (Wayland env set automatically — see Hard Rules):
```bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
timeout --signal=SIGKILL --kill-after=5 <N> python3 /tmp/deploy_p_.../<app-file>
```
- File-source: 45s timeout (model load ~15s + PLAYING confirmed + frames), run directly over the SSH channel.
- Camera: 30s timeout, launched **detached via `nohup`** writing to an on-device logfile
  (`/tmp/deploy_p_<artifact-name>/run.log`), which is read back in a fresh SSH command after the
  run window — a saturated camera pipeline can drop the SSH channel, and a dropped channel must
  never be misread as a run failure.

> **Stop behaviour:** `qimsdk.Pipeline.execute()` ignores SIGINT and SIGTERM in headless SSH sessions — only SIGKILL stops it reliably (GLib unix signal handler is installed but not triggered without a PTY). For file-output apps with natural EOS (file-source + filesink), the app exits on its own before timeout.

**P7 — Scan log**: PLAYING detected via `[IMSDK]...[STATE]...<name>] PLAYING`. On SIGSEGV/SIGABRT,
retry once after a 15s settle (device-state timing issue, e.g. GPU/display buffers not yet
released — not a code bug); only the retry attempt's own output is rescanned.

**P8 — moov-atom check + pull output**: for `.mp4` outputs, verify the file is a finalized, playable
MP4 via `gst-discoverer-1.0` before pulling (catches a file that has bytes but was SIGKILL'd before
the muxer trailer was written — unplayable, reported honestly, not counted as a pass). Otherwise
pull if `filesink` present; `N/A` for display-only.

**P9 — Cleanup**: `rm -rf /tmp/deploy_p_<artifact-name>/` from device

---

## Workspace Lifecycle — Mode B and Mode C (auto-provisioned)

`deploy_mode_b.py` and `deploy_mode_c.py` both call workspace setup helpers
(`workspace_setup_b.py` / `workspace_setup_c.py`) that detect the current
workspace state and run only the steps needed. **No manual workspace setup is
ever required.** The deploy script handles everything from a completely
fresh device or linux workstation.

### Mode B states (Ubuntu device — paths relative to `SOURCE_ROOT`)

| State | Indicator | Action taken |
|-------|-----------|--------------|
| 0 — nothing | `SOURCE_ROOT` dir absent | `apt-add-repository` → `apt build-dep` → `apt install` (base-dev + sample-apps + python-examples) → `apt source` |
| 1 — source downloaded | `build/Makefile` absent | cmake configure (full SDK flags) |
| 2 — cmake configured | `build/gst-sample-apps/{binary}/Makefile` absent | push source + wipe build + cmake reconfigure |
| 3 — app registered | `build/gst-sample-apps/{binary}/{binary}` absent | `make -C gst-sample-apps/{binary} -j$(nproc)` |
| 4 — binary built | binary present | push source (may have changed) + incremental make (~3s) |

`SOURCE_ROOT` is auto-discovered via glob (`/home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-*`)
if not set in `.env`. Set it to override or speed up detection.

### Mode C states (linux workstation — paths relative to `LINUX_WORKSTATION_BUILD_DIR`)

| State | Indicator | Action taken |
|-------|-----------|--------------|
| 0 — no SDK | `images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux` absent | unzip `x86-qli-2.0-standardsdk.zip` → run SDK installer (or download ~3.5 GB first) |
| 1 — SDK installed | `gst-plugins-imsdk/CMakeLists.txt` absent | `git clone https://github.com/qualcomm/gst-plugins-imsdk.git` |
| 2 — repo cloned | `gst-plugins-imsdk/build/Makefile` absent | cmake configure with host SDK toolchain |
| 3 — cmake configured | `build/gst-sample-apps/{binary}/{binary}` absent | push source + `cmake --build build --target {binary}` |
| 4 — binary built | binary present | push source + incremental cmake --build (~4s) |

**SDK zip lookup:** if `LINUX_WORKSTATION_SDK_PATH` is set, that path is used directly (a `.zip`
is unzipped to find the installer; a `.sh` is run as-is). Otherwise:
`{LINUX_WORKSTATION_BUILD_DIR}/x86-qli-2.0-standardsdk.zip` (x86_64 hosts)
or `arm-qli-2.0-standardsdk.zip` (aarch64 hosts). Downloaded from codelinaro.org if absent.
If download fails (network restricted), set `LINUX_WORKSTATION_SDK_PATH` or place the zip manually
at that path and re-run.

**Repo lookup:** if `LINUX_WORKSTATION_IMSDK_PATH` is set, that path is used directly as `imsdk_dir`
and the clone step is skipped — deploy fails with a clear message if `CMakeLists.txt` is not found
there rather than silently cloning a fresh copy. Otherwise cloned from GitHub into
`{LINUX_WORKSTATION_BUILD_DIR}/gst-plugins-imsdk`.

---

### Output folder layout

```
<DEPLOY_OUTPUT_DIR>/<artifact-name>/
├── device.log      ← full GStreamer stdout/stderr from run
├── result.json     ← structured result dict (always written)
└── output.<ext>    ← pulled output file (if pipeline wrote one)
```

`DEPLOY_OUTPUT_DIR` is set in `configs/.env`. Relative paths are resolved from the repo root.

---

### Structured result

See **[`references/result-format.md`](references/result-format.md)** for the full schema including `steps[]`.

---

## Mode B — Ubuntu On-Device Build

Use when: target device runs Ubuntu and has the QIMSDK source tree (`gst-plugins-qti-oss-*`) already present on device.

Device path constants (all derived from `SOURCE_ROOT` in `configs/.env`):
```
SOURCE_ROOT:     <SOURCE_ROOT>                          ← from configs/.env
SAMPLE_APPS_DIR: <SOURCE_ROOT>/gst-sample-apps
BUILD_DIR:       <SOURCE_ROOT>/build
INSTALL_BINDIR:  /usr/bin
OUTPUT_MEDIA_DIR: /home/ubuntu/Downloads/qimsdk_samples/media/output
```

### Step 1 — Derive app metadata
Read `CMakeLists.txt`: `set(GST_EXAMPLE_BIN ...)` → binary name
Read `README.md` **in full**: output file path, source type (camera/file), run duration, and any prerequisites listed in "How to Run" (e.g. `wpctl set-default` for audio/AV pipelines). Surface any prerequisites to the user before proceeding so they can complete them on the device.

### Step 2 — Clean and recreate app dir on device
The deploy script always deletes and recreates the app dir on every run — this ensures no stale source files or ownership issues from a prior run. The script owns the workspace; the artifact is never touched.
```bash
echo <PW> | sudo -S rm -rf <SOURCE_ROOT>/gst-sample-apps/<binary-name>
echo <PW> | sudo -S mkdir -p <SOURCE_ROOT>/gst-sample-apps/<binary-name>
echo <PW> | sudo -S chown ubuntu:ubuntu <SOURCE_ROOT>/gst-sample-apps/<binary-name>
```

### Step 3 — Push source files
Push `main.c`, `CMakeLists.txt`, `README.md` via SFTP to `<SOURCE_ROOT>/gst-sample-apps/<binary-name>/`.
Normalize CRLF→LF on all text files before push. Verify each file exists on device after push.

Before this step, apply [Additional App Files](#additional-app-files--detect-and-push-from-the-readme-mode-bc) — push any qualifying sidecar file (e.g. a runtime config named in the README) to the same directory, then to its runtime device path if the app reads it from elsewhere (e.g. `/etc/configs/<name>.json`).

### Step 4 — Register app in parent CMakeLists.txt

Per the official ubuntu-build docs, new apps must be explicitly added to `gst-sample-apps/CMakeLists.txt` before running cmake:

```bash
echo 'add_subdirectory(gst-<binary-name>)' >> <SOURCE_ROOT>/gst-sample-apps/CMakeLists.txt
```

The deploy script does this automatically (idempotent — skipped if already present). After registering, wipe the build dir and re-run cmake so it configures with the new entry.

> ⚠️ The source tree also has a `foreach` loop that globs subdirs, but this only picks up new dirs reliably when cmake re-runs with an existing build dir. For new apps, explicit `add_subdirectory` is the documented and reliable approach.

### Step 5 — Re-run cmake (from BUILD_DIR)
```bash
plink ... "cd /home/ubuntu/Downloads/qimsdk_samples/gst-plugins-qti-oss-1.0.r1.06800/build && echo <PW> | sudo -S cmake \
   -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=None -DCMAKE_INSTALL_SYSCONFDIR=/etc \
   -DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=ON -DCMAKE_SKIP_INSTALL_ALL_DEPENDENCY=ON \
   -GUnix\ Makefiles -DCMAKE_VERBOSE_MAKEFILE=ON \
   -DCMAKE_INSTALL_LIBDIR=lib/aarch64-linux-gnu \
   -DGST_VERSION_REQUIRED=1.20.1 -DSYSROOT_INCDIR=/usr/include -DSYSROOT_LIBDIR=/usr/lib \
   -DGST_PLUGINS_QTI_OSS_INSTALL_BINDIR=/usr/bin \
   -DGST_PLUGINS_QTI_OSS_INSTALL_LIBDIR=/usr/lib/aarch64-linux-gnu \
   -DGST_PLUGINS_QTI_OSS_INSTALL_INCDIR=/usr/include \
   -DGST_PLUGINS_QTI_OSS_INSTALL_CONFIG=/etc/configs \
   -DGST_PLUGINS_QTI_OSS_LICENSE=BSD -DGST_PLUGINS_QTI_OSS_VERSION=2.0.0 \
   -DGST_PLUGINS_QTI_OSS_PACKAGE=gstreamer1.0-plugins-qcom-oss \
   -DGST_PLUGINS_QTI_OSS_SUMMARY='Qualcomm open-source GStreamer Plug-ins' \
   -DGST_PLUGINS_QTI_OSS_ORIGIN=http://www.qualcomm.com \
   -DGST_IMAGE_MAX_WIDTH=5184 -DGST_IMAGE_MAX_HEIGHT=3880 \
   -DGST_VIDEO_MAX_WIDTH=5184 -DGST_VIDEO_MAX_HEIGHT=3880 \
   -DGST_VIDEO_MAX_FPS=120/1 -DCAMERA_METADATA_VERSION=1.0 \
   -DGST_VIDEO_TYPE_SUPPORT=TRUE -DEIS_MODES_ENABLE=TRUE \
   -DVHDR_MODES_ENABLE=TRUE -DFEATURE_OFFLINE_IFE_SUPPORT=TRUE \
   .. 2>&1"
```
Save to `<artifact>_build.log`. Stop if cmake exits non-zero.

### Step 6 — Build
```bash
plink ... "cd .../build && echo <PW> | sudo -S make -C gst-sample-apps/gst-<app-dir> -j\$(nproc) 2>&1"
```

### Step 7 — Install
```bash
plink ... "cd .../build && echo <PW> | sudo -S make -C gst-sample-apps/gst-<app-dir> install 2>&1"
```

### Steps 8–11 — Run, verify, pull
See [Common Run/Pull Steps](#common-runpull-steps) below.

---

## Additional App Files — Detect and Push From the README (Mode B/C)

An artifact is not always just `main.c` + `CMakeLists.txt` + `README.md`. A leveraged/copied app may ship extra runtime files it depends on — most commonly a JSON runtime config (e.g. `config-*.json`) read via a `--config-file` flag or a hardcoded default path in `main.c`. If such a file exists in the artifact folder but is never pushed anywhere, the binary silently falls back to whatever file already happens to sit at that path on the device (stale, from prior testing, or absent) — the build and run can both report success while actually running the wrong configuration.

**Do this for every Mode B/C deploy, right after deriving app metadata (Mode B Step 1 / Mode C step C3), before pushing any files:**

1. **Read the README's file table and "Placeholders to Fill" / "How to Run" sections in full.** Any file the README names as something the app reads at runtime — a config, a JSON/txt sidecar the app opens, or any other file that looks like it was generated as part of the app itself — is a candidate, as long as it is NOT a model/media/label asset path.
2. **Check whether that filename exists in the artifact folder** (sibling to `main.c`). If it does, it must be pushed — it is part of the app, not a placeholder for the user to source themselves.
3. **Exclude model/label/media assets.** Files the README lists purely as *device-side data the user must already have or supply* — model files (`.tflite`, `.dlc`), label files (`.json`/`.txt` under a labels path), and media (`.mp4`, images) — are never pushed by the deploy skill. Those live on the device already or are the user's responsibility; only push files that are literally part of the app's own source tree.
4. **Push every qualifying file** alongside `main.c`/`CMakeLists.txt` in the same SFTP/SCP step, into the same destination directory as `main.c` (Mode B: `<SOURCE_ROOT>/gst-sample-apps/<binary-name>/`; Mode C: the linux workstation app dir, then on to the device path the app actually reads from at runtime — for a `--config-file`-style app this is the invocation path shown in the README's "How to Run", e.g. `/etc/configs/<name>.json`). Normalize CRLF→LF the same as any other pushed text file.
5. **State what you're pushing and why** before doing it — e.g. "Also pushing `config-multistream-inference.json` (referenced in README as the app's runtime config) to `/etc/configs/` on the device." This keeps the extra step visible instead of silent.

This is a generic detection rule, not a fixed file list — it must generalize to whatever app-specific sidecar files a future leveraged artifact ships, not just today's config-file case.

---

## Mode C — Host Build

Use when: target is a QLI 2.0 or other host build-based device that cannot build on-device. Requires a Linux workstation (x86_64 or aarch64 — arch is auto-detected; WSL works on either Windows x86_64 or Windows ARM).

### Prerequisites Checklist (verify all before proceeding)

**1. Linux workstation SSH port**

Find the SSH port before connecting:
```bash
grep -E '^Port' /etc/ssh/sshd_config 2>/dev/null || echo "22"
```
- Standard Linux: no output or `Port 22` → use port `22` (set `LINUX_WORKSTATION_PORT=22`)
- WSL (Windows Subsystem for Linux): typically `Port 2222` → set `LINUX_WORKSTATION_PORT=2222`

Save the result as `LINUX_WORKSTATION_PORT` in `configs/.env`.

**2. Linux workstation SSH access**

Try connecting (use the port you found above):
```bash
ssh -p <LINUX_WORKSTATION_PORT> -i ~/.ssh/id_ed25519_workstation <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST> "echo connected"
```
- If succeeds → proceed
- If fails → generate key and guide user to add it:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_workstation -N ""
  cat ~/.ssh/id_ed25519_workstation.pub
  ```
  Ask user to run on linux workstation:
  ```bash
  echo "<pubkey>" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
  ```
  Retry connection before proceeding.

**2. Host machine architecture**

Check if running from Windows ARM (cannot run SDK locally):
- If Windows ARM → linux workstation is required (SDK is x86_64 only)
- If Linux x86_64 → can run SDK steps locally without linux workstation

**3. Storage on linux workstation**

Check available space — SDK install needs ~5GB, imsdk repo ~500MB, build artifacts ~2GB.
**Use local disk, not NFS home** — NFS mounts often have quota limits that cause silent failures.
```bash
ssh -p <LINUX_WORKSTATION_PORT> ... "df -h /local/mnt/workspace 2>/dev/null || df -h /tmp"
```
Use `/local/mnt/workspace/<LINUX_WORKSTATION_USER>/qimsdk-build/` if available, else `/tmp/qimsdk-build/`.
Store the chosen path as `LINUX_WORKSTATION_BUILD_DIR`.

---

### One-Time Setup (skip steps already done — check before running each)

**C1 — Verify host SDK on linux workstation**

The SDK env script is always at `{LINUX_WORKSTATION_BUILD_DIR}/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux`. Check it exists at exactly that path:
```bash
ssh ... "test -f <LINUX_WORKSTATION_BUILD_DIR>/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux && echo FOUND || echo NOT_FOUND"
```
- If `FOUND` → validate the compiler works:
  ```bash
  ssh ... "bash -c '. <LINUX_WORKSTATION_BUILD_DIR>/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux && aarch64-qcom-linux-gcc --version 2>&1 | head -1'"
  ```
  - If output includes `aarch64-qcom-linux-gcc` version string → SDK is functional. Set `LINUX_WORKSTATION_ENV_SETUP=<LINUX_WORKSTATION_BUILD_DIR>/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux`.
  - If compiler not found or errors → report to user: "SDK env script found but `aarch64-qcom-linux-gcc` is not working. The SDK installation may be incomplete. Please re-install the SDK at `<LINUX_WORKSTATION_BUILD_DIR>` and retry." **Stop.**
- If `NOT_FOUND` → report to user: "SDK env script not found at `<LINUX_WORKSTATION_BUILD_DIR>/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux`. Please check that the SDK installer has been run with `-d .` from the `images/qcom-armv8a/sdk/` directory inside `LINUX_WORKSTATION_BUILD_DIR` and retry." **Stop.**

> **Note:** The skill does not install the host SDK automatically. SDK setup is a one-time operation requiring ~5GB disk space; it must be done by the user before the first Mode C build.

**C2 — Verify gst-plugins-imsdk on linux workstation**

`gst-plugins-imsdk` is always expected at `{LINUX_WORKSTATION_BUILD_DIR}/gst-plugins-imsdk`. The deploy script checks this path and fails immediately if it is not found:
```bash
ssh ... "test -d <LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk/gst-sample-apps && echo EXISTS || echo NOT_EXISTS"
```
If `NOT_EXISTS` → clone it first:
```bash
ssh ... "mkdir -p <LINUX_WORKSTATION_BUILD_DIR> && cd <LINUX_WORKSTATION_BUILD_DIR> && git clone https://github.com/qualcomm/gst-plugins-imsdk.git"
```

---

### Per-App Build Steps

**C3 — Derive app metadata**
Read `CMakeLists.txt` for binary name (`GST_EXAMPLE_BIN`). Read `README.md` **in full** for output/source type and any prerequisites listed in "How to Run" (e.g. `wpctl set-default` for audio/AV pipelines). Surface any prerequisites to the user before proceeding so they can complete them on the device.

The binary name already has the `gst-qimsdk-` prefix (e.g. `gst-qimsdk-object-detection`) — enforced by the `qimsdk-gstreamer-dev` skill's CMake template. No renaming needed.

**C4 — Create app directory on linux workstation**

Use the binary name from CMakeLists.txt as the directory name directly.

> ⚠️ **Do NOT add `add_subdirectory()` to the parent CMakeLists.txt** — `gst-plugins-imsdk/gst-sample-apps/CMakeLists.txt` contains a `foreach(dir)` loop that automatically includes ALL subdirectories. Just creating the directory is sufficient. Adding an explicit entry causes a cmake conflict.

```bash
ssh ... "mkdir -p <LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk/gst-sample-apps/<BINARY_NAME>"
```

**C5 — Push source files to linux workstation**
```bash
scp ... main.c CMakeLists.txt README.md <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST>:<LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk/gst-sample-apps/<BINARY_NAME>/
```

Push CMakeLists.txt as-is — no modification needed since the binary name is already correct.

Before this step, apply [Additional App Files](#additional-app-files--detect-and-push-from-the-readme-mode-bc) — any qualifying sidecar file (e.g. a runtime config) gets pushed alongside these three.

**C6 — Source SDK env, configure, and build**

> ⚠️ **Must use `bash -c '...'` for multi-command SSH** — shell variables and `&&` chains inside double-quoted SSH strings cause "Ambiguous output redirect" errors on some shells.

> ⚠️ **Build only the specific target** — use `--target gst-qimsdk-<slug>` to avoid rebuilding all apps. `cmake --build build` alone rebuilds everything which takes much longer.

```bash
ssh ... "bash -c '. <LINUX_WORKSTATION_BUILD_DIR>/images/qcom-armv8a/sdk/environment-setup-armv8a-qcom-linux && cd <LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk && cmake -B build -S . -DCMAKE_INSTALL_PREFIX=/usr -DENABLE_GST_IMSDK_PLUGINS=1 -DENABLE_GST_PLUGIN_MLTFLITE=1 -DENABLE_GST_PYTHON_EXAMPLES=1 -DENABLE_GST_SAMPLE_APPS=1 -DENABLE_GST_SAMPLE_APPS_CAMERA=1 -DENABLE_GST_PLUGIN_TOOLS=1 -DENABLE_GST_CAMERA_PLUGINS=1 && cmake --build build --target <BINARY_NAME> -- -j\$(nproc)'" 2>&1 | tail -10
```
Save to `<artifact>_build.log`. Stop if build fails.
Success indicator: `Built target <BINARY_NAME>`

> ⚠️ **CC/CXX may be empty after sourcing the env script via plink batch mode.** This is normal and harmless — the host compiler (`aarch64-qcom-linux-gcc`) is on PATH after sourcing even when CC is empty. cmake will find it via PATH. If cmake reports compiler not found, add explicitly: `-DCMAKE_C_COMPILER=aarch64-qcom-linux-gcc -DCMAKE_CXX_COMPILER=aarch64-qcom-linux-g++`

**C7 — Pull binary from linux workstation to local machine**

The binary is at: `<build_dir>/gst-sample-apps/<BINARY_NAME>/<BINARY_NAME>` — note it's in a subdirectory named after the binary itself.

> ⚠️ **pscp does not support long paths on Windows.** If the full path exceeds ~230 chars, pscp silently fails. Always pull to `C:/tmp/<BINARY_NAME>` first (short path), then push from there to the QLI device.

```bash
# Pull to short intermediate path (avoid pscp long-path failure)
mkdir -p C:/tmp/qimsdk_compiled
plink ... "cat <LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk/build/gst-sample-apps/<BINARY_NAME>/<BINARY_NAME>" > C:/tmp/qimsdk_compiled/<BINARY_NAME>
# OR use pscp if path is short enough:
pscp ... <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST>:<LINUX_WORKSTATION_BUILD_DIR>/gst-plugins-imsdk/build/gst-sample-apps/<BINARY_NAME>/<BINARY_NAME> C:/tmp/qimsdk_compiled/<BINARY_NAME>
```
Verify it's an ARM64 binary: `file <binary>` should show `ELF 64-bit LSB ... ARM aarch64`

**C8 — Push binary to QLI device**
```bash
# pscp from short path to avoid long-path issues
pscp -pw <PASSWORD> -hostkey "<HOST_KEY>" C:/tmp/qimsdk_compiled/<BINARY_NAME> <DEVICE_USER>@<DEVICE_IP>:/usr/bin/<BINARY_NAME>
plink ... "chmod +x /usr/bin/<BINARY_NAME> && echo deployed"
```

> ⚠️ **C binary $HOME paths:** If main.c contains `#define INPUT_FILE "$HOME/media/..."` these paths are string literals in C — the shell variable $HOME is NOT expanded at runtime. Manually replace `$HOME` with the actual device home directory (e.g. `/root`) in main.c before building.

**C9 — Ensure output dir on device**
```bash
plink ... "mkdir -p /home/ubuntu/Downloads/qimsdk_samples/media/output"
```

**C9b — Push any sidecar file pushed in C5 to its runtime device path**, if not already placed there directly (e.g. `/etc/configs/<name>.json` for a `--config-file`-style app) — see [Additional App Files](#additional-app-files--detect-and-push-from-the-readme-mode-bc), step 4.

### Steps C10–C12 — Run, verify, pull
See [Common Run/Pull Steps](#common-runpull-steps) below.

---

## Mode D — C++ Standalone Host Build

Use when: artifact is from **qimsdk-cpp-app-builder** — `main.cc` + `CMakeLists.txt` with
`set(TEST_TARGET "...")`, using the `qti::Pipeline` / `<qti/imsdk.h>` C++ API and linking a single
`qtiimsdk` library. This is a **different builder contract than Mode C** (which targets
gstreamer-app-builder's `main.c` + `GST_EXAMPLE_BIN` C sample apps) — do not confuse the two by
CMake shape alone; check for `TEST_TARGET` (Mode D) vs `GST_EXAMPLE_BIN` (Mode C).

Requires a Linux x86_64 or WSL workstation, same as Mode C — Mode D reuses every
`LINUX_WORKSTATION_*` key. The key structural difference from Mode C: **Mode D never clones or
mutates a shared source tree.** Each app is cross-built **standalone, out of tree**, against the
**Yocto standard SDK** (not gst-plugins-imsdk) — `libqtiimsdk.so` ships inside the SDK's target
sysroot, so there is no "build the C++ IMSDK SDK from source" step.

**Script:** `references/deploy_mode_d.py` (build delegated to `references/workspace_setup_d.py`)

### One-time setup — Yocto SDK install (auto-provisioned)

The SDK is installed once into `{LINUX_WORKSTATION_BUILD_DIR}/qcom-sdk` and reused for every app.
If not already installed, deploy resolves the SDK source in this precedence order:
1. `LINUX_WORKSTATION_SDK_PATH`, if set — an installer already on the workstation: a `.sh` (already
   extracted, run directly, no unzip) or a `.zip` (unzipped to find the `.sh` inside). Same key as Mode C.
2. Else a `qcom-yocto-sdk*.zip` (or `sdk.zip`) already in `LINUX_WORKSTATION_BUILD_DIR`.
3. Else `LINUX_WORKSTATION_SDK_URL`, if set (`file://` path or `http(s)://` URL).
4. Else the default Yocto SDK zip is downloaded from:
   `https://artifacts.codelinaro.org/artifactory/qli-ci/flashable-binaries/meta-qcom/qcom-armv8a/qcom-yocto-sdk-deploy-0807.zip`.

Then it unzips (if needed) to find the `.sh` installer, runs it non-interactively
(`<installer>.sh -d <sdk_dir> -y`), and verifies an `environment-setup-*-qcom-linux` script exists
under `<sdk_dir>` — the script name is version-specific (e.g. `environment-setup-armv8-2a-qcom-linux`),
so it is discovered by glob, not hardcoded.

> **Note:** the Yocto SDK default is the Artifactory URL above. If the download is
> unavailable, place `qcom-yocto-sdk*.zip` in `LINUX_WORKSTATION_BUILD_DIR` and
> re-run, or set `LINUX_WORKSTATION_SDK_PATH` to a `.zip` or `.sh` installer already
> on the workstation.

### Per-app build steps

1. **Parse artifact** — extract the target name from `set(TEST_TARGET "<name>")` (fallback:
   `add_executable`/`project`), confirm `main.cc` exists.
2. **Push source to an isolated per-app dir** — `{LINUX_WORKSTATION_BUILD_DIR}/qimsdk-cpp-apps/<target>/`.
   The artifact's own `CMakeLists.txt` is **not** used for the build — it links a bare `qtiimsdk`
   target that only resolves inside the full IMSDK source tree. Deploy instead generates a
   **standalone wrapper CMakeLists.txt** (pushed in its place) that resolves the library via
   `find_library(QTIIMSDK_LIBRARY NAMES qtiimsdk PATHS "$ENV{SDKTARGETSYSROOT}/usr/lib" REQUIRED)` —
   the original is preserved alongside as `CMakeLists.artifact.txt` for reference.
3. **Configure + build** in the sourced SDK env:
   ```bash
   . <sdk_dir>/environment-setup-*-qcom-linux
   cmake -S . -B build
   cmake --build build
   ```
   Verifies the resulting binary is an aarch64 ELF (`file build/<target>` → `ELF 64-bit ... ARM aarch64`).
4. **Pull binary** from `<app_dir>/build/<target>` to `C:/tmp/qimsdk_compiled/<target>` (Windows
   long-path workaround, same as Mode C).
5. **Push to device** `/usr/bin/<target>` via `/tmp` + `mv` (`sudo mv` unless already root — same
   as Mode C), `chmod +x`.
6. **Run** — the qti C++ SDK has no built-in SIGINT handler (same as gst C apps): file-source runs
   to natural EOS with no timeout; camera uses `timeout --signal=SIGINT --kill-after=15 30` after a
   cam-server restart. YAML-config apps: replicate the README's "create target dir + copy YAML" step
   before running.
7. **Verify + pull output** — moov-atom check (`gst-discoverer-1.0`) for `.mp4` outputs, then pull;
   SIGSEGV auto-retry once after a 15s settle (Mode A's working retry pattern — not Mode C's).

> **Runtime dependency:** the built binary dynamically links `libqtiimsdk.so.1`. If preflight WARNs
> it's missing on the device, install the C++ IMSDK runtime library there before deploying — the
> binary will fail to start otherwise. This is a device-provisioning step, not something deploy
> fixes automatically (Fail Fast, Don't Fix).

---

## Common Run/Pull Steps

These steps apply to Mode B and Mode C (C app binaries). Mode A uses `deploy_mode_a.py` directly.

### Run Duration Defaults

- **Mode A** — 30s, all source types. 1 retry on camera encoder-busy.
- **Mode B** — camera: 30s with retries; file-source: natural EOS (5 min cap).
- **Mode C / D** — camera: 30s; file-source: natural EOS (5 min cap). 1 SIGSEGV retry.
- **Mode P** — SIGKILL only (no SIGINT support): camera 30s; file-source 45s.

Display/RTSP-out pipelines should be watched live on the device screen.

### Run (Mode B / Mode C)

Camera source (SIGINT for clean MP4 shutdown):
```bash
plink ... "timeout --signal=SIGINT --kill-after=15 30 <binary_name> 2>&1; exit 0"
```

File source (run to natural EOS — no timeout):
```bash
plink ... "<binary_name> 2>&1"
```

Save full output to `<artifact>_device.log`. Scan logs:
- `playing_reached=true` if `Pipeline state changed from PAUSED to PLAYING` found (C app format)
- Collect all `ERROR:` lines into `error_lines`
- **Ignore (benign):** `MapGbmBufInfoAddress: Mmap failed`, `Failed to initialize Wayland EGL display`, `Failed to initialize X11 EGL display`, `SetupXcbConnection: Failed to get xcb connection`, `tiling.h WARNING`, `concat_opts WARNING`, `Internal data stream error`, `Got EOS from element`, `Failed to set RPC polling time`, `Failed to set rpc polling`, `Failed to set powerConfig`

### Verify output file
```bash
plink ... "ls -lh <output_file_path>"
```
Missing or zero-byte → `output_file_size="missing"` or `"0 bytes"`.

### Pull output file
```bash
pscp ... <DEVICE_USER>@<DEVICE_IP>:<output_file_path> <device-output-dir>/<artifact>_output.<ext>
```

---

## Device State Failures — Recognize and Report Immediately

These are device-side issues, not pipeline bugs. Do not attempt to debug the pipeline. Report the exact symptom, save the log, and stop.

### Pipeline stuck in PREROLLING (never reaches PLAYING)

**Cause:** A previous gst-launch process is holding a hardware resource (v4l2 decoder, QNN HTP/DSP, camera encoder). New pipeline queues behind it and never gets the resource.

**How to identify:** Log shows `Pipeline is PREROLLING ...` but no `Setting pipeline to PLAYING`. Run exits after timeout with no output file.

**What the script does:** Before every run, `sudo pkill -9 -f gst-launch-1.0` + verify non-zombie count = 0. If count > 0 after SIGKILL, report and stop.

**Do NOT try SIGINT first.** Processes in uninterruptible sleep on hardware ignore SIGINT. Go straight to `sudo pkill -9`.

---

### Pipeline crashes with SIGSEGV during preroll

**Cause:** GPU/display driver buffer objects not fully released after a prior pipeline teardown — the next pipeline allocates display memory and receives null addresses. Most common after consecutive display-sink pipelines with short gaps between them.

**How to identify:** Log contains `Caught SIGSEGV` and/or `mmap: Invalid argument` / `bo cpu address failed` during preroll. The process enters a GDB spinning loop.

**What the script does:** `_scan_log` detects `Caught SIGSEGV` and returns it as `crash_reason`. The deploy script automatically waits 15 seconds and retries once before reporting failure — since the root cause is timing, a single retry with idle time covers most cases.

**Fix:** Wait for the device to fully idle between consecutive display-sink deploys. If the automatic retry also crashes, wait 30+ seconds manually before re-running. A reboot may help but is not a guaranteed fix — the driver state can persist across reboots depending on timing. The pipeline code is correct — do not modify it.

---

### Pipeline stuck — timeout reached, exit -1 from SSH channel

**Cause:** The SSH channel's wall-clock deadline was hit before the command produced an EOF. Usually means the pipeline is hung (see PREROLLING above) or the run was killed externally.

**How to identify:** Script reports `exit -1` and elapsed time equals the wall-clock limit exactly. Log is truncated with no EXIT: line.

**Fix:** Check for stale processes (PREROLLING case), reboot if crash state, then re-run.

---

## Error Handling

| Situation | Action |
|-----------|--------|
| `QMMF Recorder StartCamera Failed` in logs | Report it. Device cam-server was not running. User must restart cam-server and re-deploy. |
| `v4l2h264enc: Failed to process frame` + zero/corrupt MP4 | Report it. Hardware encoder was left busy from a prior run. User must restart cam-server and re-deploy. |
| cmake non-zero | Stop, set `build_passed=false`, save log, return result |
| make/build error | Stop, set `build_passed=false`, save log, return result |
| `ERROR:` in GStreamer logs | Collect into `error_lines`, continue unless pipeline crashes |
| Pipeline never reaches PLAYING | Set `playing_reached=false`, return result |
| Output file missing/zero | Set `output_file_size=missing`, return result |
| SSH connection failed | Stop immediately, report to caller |
| Linux workstation SSH failed (Mode C) | Report exact error and stop. User must fix SSH access. |
| SDK not found at LINUX_WORKSTATION_BUILD_DIR | Report exact path and stop. User must verify path. |

---

## Key Pitfalls

### Mode B (Ubuntu on-device) — Diagnosed and verified 2026-07-06

**1. `ENABLE_GST_SAMPLE_APPS` must be ON — default is OFF**

Symptom: cmake runs in 0.6s, `build/gst-sample-apps/` is empty, `make -C gst-sample-apps/<app>` fails with "No such file or directory".
Root cause: The root `CMakeLists.txt` has `option(ENABLE_GST_SAMPLE_APPS "..." OFF)` — sample apps are disabled by default. cmake configures the project but skips `gst-sample-apps/` entirely.
Fix: Always pass `-DENABLE_GST_SAMPLE_APPS=ON` to cmake. The deploy script does this. If you see a 0.6s cmake configure, this flag is missing.

**2. Build dir must be wiped for cmake to pick up a new app subdir**

Symptom: cmake runs but does not generate a build dir for the new app even though the source dir exists in `gst-sample-apps/`.
Root cause: cmake's `file(GLOB ...)` in the `foreach` auto-discovery loop caches results. A leftover build dir from a prior run has stale glob results that don't include the new app.
Fix: `rm -rf {build_dir} && mkdir -p {build_dir}` before every cmake run. The deploy script does this (B4 step).

**3. Build dir must be owned by ubuntu — not root**

Symptom: cmake exits 0 but writes nothing useful. Build log shows `CMake Error: Cannot open file for write: .../CMakeCache.txt.tmp... Permission denied`.
Root cause: The build dir was created by `sudo` (root-owned). cmake runs as ubuntu via NOPASSWD and cannot write its cache files.
Fix: `chown ubuntu:ubuntu {build_dir}` immediately after `mkdir -p`. The deploy script does this in the same command: `rm -rf ... && mkdir -p ... && chown ubuntu:ubuntu ...`.

**4. Binary installs to `/usr/<name>` instead of `/usr/bin/<name>`**

Symptom: build and install succeed (exit 0), but `find /usr/bin -name <binary>` returns nothing. Binary is at `/usr/<binary-name>`.
Root cause: The app's generated `cmake_install.cmake` has `DESTINATION "/usr"` hardcoded because `GST_PLUGINS_QTI_OSS_INSTALL_BINDIR` was `UNINITIALIZED` at cmake generate time. Even with `:PATH` typing in the cmake flags, the variable resolves empty in `cmake_install.cmake` for this source tree version.
Fix: The deploy script checks `/usr/<name>` as a fallback after checking `/usr/bin`. If found there, it runs the binary from that path directly (no move needed — the binary works fine from `/usr/`).

**5. Do NOT add `add_subdirectory()` to parent `gst-sample-apps/CMakeLists.txt`**

Symptom: cmake fails with "binary directory already used to build a source directory" for the new app.
Root cause: The parent `CMakeLists.txt` has a `foreach(file(GLOB ...))` loop that auto-discovers all subdirs. Adding an explicit `add_subdirectory(<app>)` line creates a duplicate entry.
Fix: Do not add `add_subdirectory`. The `foreach` glob handles it — as long as `ENABLE_GST_SAMPLE_APPS=ON` and the build dir is wiped (pitfalls 1 and 2 above).

**6. Other established pitfalls**
- Build dir is root-owned after `sudo mkdir` — always `chown ubuntu:ubuntu` immediately (see pitfall 3)
- `timeout` without `--signal=SIGINT` → SIGTERM → corrupts MP4 moov atom
- `MapGbmBufInfoAddress: Mmap failed` is benign — do not stop on it
- `gst-sample-apps-utils` is built automatically by cmake dependency tracking — do not build it manually
- `QMMF Recorder StartCamera Failed` or `v4l2h264enc Failed to process frame` — run `sudo systemctl restart cam-server` and retry. Camera server not started after reboot, or encoder left busy from prior run.


### Mode C (host build)
1. **Host SDK architecture must match the linux workstation** — `x86-qli-2.0-standardsdk.zip` for x86_64 hosts; `arm-qli-2.0-standardsdk.zip` for aarch64 hosts (e.g. WSL on ARM Windows). The skill detects host arch and picks the right zip automatically. Windows ARM with WSL2 Ubuntu (aarch64) works — the ARM SDK is available on codelinaro alongside the x86 zip.
2. **Use local disk, not NFS home** — NFS mounts often have user quotas that cause silent write failures. Use `/local/mnt/workspace/` or similar local path.
3. **Do NOT add `add_subdirectory()` to parent CMakeLists.txt** — the imsdk repo auto-discovers all subdirs via a `foreach` loop. Adding it explicitly causes "binary directory already used" cmake error.
4. **Generated apps use `gst-qimsdk-` prefix** — the `qimsdk-gstreamer-dev` skill generates binaries named `gst-qimsdk-<name>`. This avoids conflicts with the imsdk repo's own apps (`gst-ai-*`). Use the binary name from `CMakeLists.txt` (`set(GST_EXAMPLE_BIN ...)`) directly — this is BOTH the cmake target name AND the output binary filename.
5. **Binary location after build:** `<build>/gst-sample-apps/<BINARY_NAME>/<BINARY_NAME>` — the binary is in a subdirectory named after itself. Do not search in other locations.
6. **Use `bash -c '...'` for multi-command SSH** — chained commands in double-quoted SSH strings cause "Ambiguous output redirect" errors on some shells.
7. **Build only the target** — use `cmake --build build --target <name>` not `cmake --build build` which rebuilds all apps and takes much longer.
8. **Source the SDK env in every new shell** — the environment is not persistent; always `. .../environment-setup-armv8a-qcom-linux` at the start of build commands.
9. **Stale build directory** — if cmake previously failed, clean `build/` before retrying to avoid "binary directory already used" errors.
10. **CC/CXX may be empty** — `source environment-setup-armv8a-qcom-linux` may not set CC/CXX via plink batch mode. This is normal — the host compiler is on PATH. If cmake fails to find the compiler, pass `-DCMAKE_C_COMPILER=aarch64-qcom-linux-gcc -DCMAKE_CXX_COMPILER=aarch64-qcom-linux-g++` explicitly.
11. **pscp multi-file loops** — pscp does not support wildcards or multiple source args. Loop:
    ```bash
    for f in file1 file2 file3; do pscp -pw <PW> -hostkey "<HK>" "$f" user@host:dest/; done
    ```
12. **pscp long paths** — paths > ~230 chars fail silently on Windows. Always copy artifact files to `C:/tmp/` before pscp.
13. **$HOME in C source** — `#define PATH "$HOME/media/..."` is a string literal in C; $HOME is never expanded at runtime. Manually replace `$HOME` with the actual device home directory (e.g. `/root`) in main.c before building.
14. **Re-run cmake after adding new app subdirectory** — cmake discovers subdirs at configure time, not at build time. If a new app dir is added after cmake ran, re-run cmake before building.
