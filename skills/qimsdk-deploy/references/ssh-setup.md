# SSH Key Setup Guide

For use with `qimsdk-deploy` and `qimsdk-eval` when connecting from **Windows** to remote Linux machines.

Two connections may be needed depending on mode:
- **Device** (Ubuntu or QLI) — required for every mode (A, B, C, D, P, M)
- **Linux workstation** (Linux x86_64) — required for Modes C and D only

Mode M is the exception to the "from Windows" framing: it cross-compiles on the Linux/WSL
host that runs the skill, so it needs no workstation connection and no Windows host — only
the device connection, with the extra requirements in [Step M](#step-m--mode-m-post-process-module-device-access) below.

> The deploy scripts use **paramiko** (Python SSH library) — no PuTTY required at any step.
> Follow these steps once. After that, set the key paths in `configs/.env` and run preflight.

**Prerequisite:** Python with paramiko installed (`pip install paramiko` — already handled by `requirements.txt`).

---

## Step A — Generate a key pair on Windows

Open **PowerShell** or any terminal:

```powershell
ssh-keygen -t ed25519 -C "qimsdk-deploy" -f "$HOME\.ssh\id_ed25519_qimsdk"
```

When prompted for a passphrase: **press Enter twice** (leave empty — deploy scripts run non-interactively and cannot type a passphrase).

This creates:
- `C:\Users\<you>\.ssh\id_ed25519_qimsdk` — private key (never share)
- `C:\Users\<you>\.ssh\id_ed25519_qimsdk.pub` — public key (copy this to remote machines)

---

## Step B — Get the HOST_KEY fingerprint

**Option 1 — SSH into the device (works on Windows, no extra tools needed):**

```powershell
ssh <DEVICE_USER>@<DEVICE_IP> "for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf \$f 2>/dev/null; done"
```

Enter the device password when prompted. This prints one `SHA256:...` line per key type. Pick the `ed25519` line if present, otherwise use `ecdsa`. That value is your `HOST_KEY`.

**Option 2 — Skip for now, get it during preflight:**

Leave `HOST_KEY` blank in `configs/.env`. When you run preflight it will print the actual fingerprint:
```
[WARN] HOST_KEY not configured. Device presented: SHA256:abc123...
       Add to configs/.env:  HOST_KEY=SHA256:abc123...
```

---

## Step C — Set values in `configs/.env`

```
DEVICE_IP=<device-ip>
DEVICE_USER=ubuntu
DEVICE_KEY=C:/Users/<you>/.ssh/id_ed25519_qimsdk
DEVICE_PASSWORD=<password>        # keep as fallback while setting up key auth
HOST_KEY=<SHA256:...>             # from Step B above
```

**Use forward slashes** in the path — paramiko handles them correctly on Windows.

The deploy scripts try key first, then password. Keep both in `.env` during transition; remove the password once key auth is confirmed working.

---

## Step D — Copy public key to Ubuntu device

**There is no `ssh-copy-id` on Windows** — it's a Linux/macOS shell script not shipped with Windows OpenSSH. Two options below; try native OpenSSH first (Option 1) since it needs no Python and works with password prompts typed directly at the terminal (nothing embedded in a command line).

### Option 1 — Native OpenSSH (recommended, no Python)

Run these **three separate commands**, one at a time, each on its own line (do not combine with `&&` — see gotchas below):

```powershell
ssh <DEVICE_USER>@<DEVICE_IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub" | ssh <DEVICE_USER>@<DEVICE_IP> "cat >> ~/.ssh/authorized_keys"
ssh <DEVICE_USER>@<DEVICE_IP> "chmod 600 ~/.ssh/authorized_keys"
```

You'll be prompted for the password on each command (some hosts prompt 2-3× per command due to PAM stacking — this is normal, just re-enter the same password each time).

**Gotchas that will break this if you combine steps or wrap lines:**
- **Don't chain all three into one `&&`-joined command.** If the terminal line-wraps mid-paste, the shell can see a dangling `&&` or an unterminated quote and fail with `Invalid null command` (if the remote login shell is csh/tcsh) or `Unmatched '''` (broken quote nesting). Three short, unbroken commands avoid both failure modes entirely.
- **Don't nest single quotes inside double quotes** (e.g. `"sh -c '...'"`) to force a specific remote shell — if the line wraps, the quotes can split across lines and break. The three-command form above needs no shell-forcing and no nested quotes.
- On Linux/macOS, replace `Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub"` with `cat ~/.ssh/id_ed25519_qimsdk.pub` — same three-command structure otherwise.

### Option 2 — Python/paramiko snippet

Run this Python snippet (uses paramiko, no PuTTY needed):

```bash
python3 -c "
import paramiko, pathlib
pub = pathlib.Path.home() / '.ssh' / 'id_ed25519_qimsdk.pub'
key_line = pub.read_text().strip()
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<DEVICE_IP>', username='<DEVICE_USER>', password='<DEVICE_PASSWORD>', timeout=15)
client.exec_command(
    f'mkdir -p ~/.ssh && echo {repr(key_line)} >> ~/.ssh/authorized_keys '
    f'&& chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh'
)
client.close()
print('Done')
"
```

Replace `<DEVICE_IP>`, `<DEVICE_USER>`, `<DEVICE_PASSWORD>` with your values. Produces no output on success beyond "Done". Note the password is embedded in the command text itself (not typed at an interactive prompt) — prefer Option 1 if that matters for your setup.

> **Note:** `AutoAddPolicy()` is used here only for the one-time key copy. The deploy scripts use a strict fingerprint policy (HOST_KEY) for all subsequent connections.

---

## Step E — Copy public key to linux workstation (Mode C / Mode D only)

Same as Step D — use Option 1 (native OpenSSH, three separate commands) or Option 2 (paramiko snippet), substituting linux workstation credentials:

```powershell
ssh <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST> "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub" | ssh <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST> "cat >> ~/.ssh/authorized_keys"
ssh <LINUX_WORKSTATION_USER>@<LINUX_WORKSTATION_HOST> "chmod 600 ~/.ssh/authorized_keys"
```

Or the paramiko snippet:

```bash
python3 -c "
import paramiko, pathlib
pub = pathlib.Path.home() / '.ssh' / 'id_ed25519_qimsdk.pub'
key_line = pub.read_text().strip()
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('<LINUX_WORKSTATION_HOST>', username='<LINUX_WORKSTATION_USER>', password='<LINUX_WORKSTATION_PASSWORD>', timeout=15)
client.exec_command(
    f'mkdir -p ~/.ssh && echo {repr(key_line)} >> ~/.ssh/authorized_keys '
    f'&& chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh'
)
client.close()
print('Done')
"
```

---

## Step F — Run preflight to confirm

```bash
python .claude/skills/qimsdk-deploy/references/preflight_check.py --mode A
```

Expected when key auth is working:
```
[ OK ]  Key auth configured (id_ed25519_qimsdk)
[ OK ]  SSH login OK as ubuntu@10.73.x.x (key (id_ed25519_qimsdk))
```

If you see `[WARN] password auth` — `DEVICE_KEY` path is wrong or file not found. Check the path.

---

## Step M — Mode M (post-process module) device access

Mode M uses the **same `configs/.env` device keys** as every other mode — Steps A–D above
apply unchanged. What differs is *where the skill runs from* and *which account it needs*.

**Mode M runs on the Linux/WSL host itself.** There is no separate Linux workstation and no
Windows-host requirement: the aarch64 cross-compile happens locally with
`g++-aarch64-linux-gnu`, and only the install step opens an SSH connection. So Steps E and
the WSL section below are irrelevant to Mode M — the WSL box *is* the build host, not an SSH
target. If you are already on Linux/WSL, key setup is the ordinary
`ssh-copy-id <DEVICE_USER>@<DEVICE_IP>` rather than the PowerShell forms in Step D.

**`DEVICE_USER` must be `root`.** QLI images ship without `sudo`, so no other account can
write the `qtimlpostprocess` module directory (`/usr/lib/imsdk/qtimlpostprocess/modules` and
siblings) or the model/label directories. This is checked in preflight and again before the
first byte is written — there is no `sudo` fallback to fall back to.

**Two optional Mode M keys** control where the model and labels land. Both default to
values that match how `qimsdk-model-onboarding` artifacts are wired, so leave them unset
unless your device layout differs:

```
DEVICE_MODELS_DIR=/root/models     # optional, default /root/models
DEVICE_LABELS_DIR=/root/labels     # optional, default /root/labels
```

**`HOST_KEY` on a device you reflash.** Mode M never reads or writes `~/.ssh/known_hosts`
(it passes `allow_agent=False` / `look_for_keys=False` and installs its own policy), so
reflashing the device can never produce the `REMOTE HOST IDENTIFICATION HAS CHANGED` wall of
text and there is no stale fingerprint to clear. Setting `HOST_KEY` opts into strict
pinning: a mismatch then aborts the connect. Leave it unset on a device you reflash often —
the fingerprint changes every time. `DEVICE_HOST_KEY` is accepted as an alias, so an older
Mode M config keeps working; `HOST_KEY` wins when both are set.

**Password versus key, honestly.** Either works, and a key lets you drop `DEVICE_PASSWORD`
entirely. But note what a key does *not* buy you here: reflashing the device wipes the
installed public key, so you re-run `ssh-copy-id` every time — and the password it replaces
is typically `oelinux123`, a published factory default, so treating it as a secret is
theatre. Password auth is the pragmatic default for a lab device; keys make more sense for a
device you provisioned deliberately and do not reflash.

**Redaction.** Every occurrence of `DEVICE_PASSWORD` is replaced with `***` in console
output, in `device.log`, and in `result.json`. A password in a log file is as leaked as one
in shell history — `device.log` is the first thing anyone pastes into a bug report.

Verify the whole path with:

```bash
python .claude/skills/qimsdk-deploy/references/preflight_check.py --mode M \
    --artifact-path <path/to/imsdk-postproc-module-dir>
```

That checks the host cross-toolchain and module headers *and* the device side (root,
`qtimlpostprocess` present, model/label dirs writable) in one run.

---

## Switching back to password auth

If key auth fails and you need to fall back:
1. Comment out `DEVICE_KEY` in `configs/.env`
2. Ensure `DEVICE_PASSWORD` is set
3. Re-run preflight

---

## WSL (Windows Subsystem for Linux) as a Mode C / Mode D Linux Workstation

WSL Ubuntu can be used instead of a remote Linux workstation for Mode C and Mode D host
builds. The skill connects to WSL via SSH to `localhost` the same way it connects to a remote
machine. Run these steps once before running Mode C/D preflight.

> Not needed for Mode M: Mode M builds directly on the Linux/WSL host with no SSH connection
> to it at all, so sshd, passwordless sudo and `LINUX_WORKSTATION_*` are all irrelevant there.

**Prerequisite:** WSL installed with Ubuntu. If not installed, run in PowerShell as admin:
```powershell
wsl --install
```
Then launch the Ubuntu app from the Start menu and complete the first-run user setup (username + password prompt).

**Step WSL-1 — Start SSH server in WSL**

WSL Ubuntu does not start sshd automatically. Run inside WSL:
```bash
sudo service ssh start
sudo service ssh status
```
It should say `active (running)`. Keep the WSL terminal open (minimize it — don't exit, or WSL will shut down).

Check what port sshd is configured on:
```bash
grep -E '^Port' /etc/ssh/sshd_config 2>/dev/null || echo "22 (no explicit Port line)"
```
Note the port — you'll need it for all subsequent commands. Port 2222 is common when Windows OpenSSH is already on port 22.

> You need to run `sudo service ssh start` again after every Windows reboot (or configure WSL to auto-start it).

**Step WSL-2 — Configure passwordless sudo**

The skill auto-installs `unzip` and `cmake` if missing via `apt-get`. This requires passwordless sudo — a password prompt in a non-interactive SSH session will hang forever.

Run inside WSL (replace `<username>` with your WSL username):
```bash
echo "<username> ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/<username>
```
Verify it worked: `sudo whoami` should print `root` without a password prompt.

**Step WSL-3 — Copy SSH public key to WSL**

First verify you can reach WSL at all (enter WSL password when prompted):
```powershell
ssh -p <wsl-port> <wsl-username>@localhost "echo connected"
```

Then copy the public key:
```powershell
Get-Content "$HOME\.ssh\id_ed25519_qimsdk.pub" | ssh -p <wsl-port> <wsl-username>@localhost "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"
```

Verify key auth works (should print `connected` with no password):
```powershell
ssh -p <wsl-port> -i "$HOME\.ssh\id_ed25519_qimsdk" <wsl-username>@localhost "echo connected"
```

**Step WSL-4 — Set configs/.env for WSL**

```
LINUX_WORKSTATION_HOST=localhost
LINUX_WORKSTATION_USER=<wsl-username>
LINUX_WORKSTATION_PORT=<wsl-port>
LINUX_WORKSTATION_KEY=C:/Users/<you>/.ssh/id_ed25519_qimsdk
LINUX_WORKSTATION_BUILD_DIR=/home/<wsl-username>/qimsdk-build
```

After this, run preflight for Mode C — it will verify the SSH connection and report workspace state.

**What the skill handles automatically (no user action needed):**
- `unzip` not installed → auto-installs via `sudo apt-get install -y unzip`
- `cmake` not installed → auto-installs via `sudo apt-get install -y cmake`
- Detects WSL is `aarch64` → downloads `arm-qli-2.1.1-standardsdk.zip` (not the x86_64 zip)

**What the skill does NOT handle (WSL user setup — do before running preflight):**
- WSL installation
- sshd running (`sudo service ssh start`)
- Passwordless sudo (`/etc/sudoers.d/<username>`)

---

## What the deploy scripts do NOT do

- Generate keys
- Copy keys to remote machines
- Fix SSH configuration on remote machines
- Troubleshoot network connectivity

All of the above are one-time setup tasks you perform manually using this guide.
