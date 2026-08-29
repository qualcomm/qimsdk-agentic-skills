---
id: qimsdk.deploy
version: 1.0
status: draft
tech_area: [qimsdk, deployment, device_execution]
skill_category: deployment
confidence_level: advisory
output_type: procedure
depends_on: []
related_skills:
  - qimsdk.gstreamer.app_builder
  - qimsdk.cpp.app_builder
  - qimsdk.python.app_builder
applicable_products:
  - Qualcomm Linux devices and Linux or WSL host workstations used for QIM SDK app deployment
applicable_releases:
  - "1"
region: global
required_inputs:
  - deploy_mode
  - artifact_path
  - environment_config
document_references:
  - id: qimsdk-agentic-skills/qimsdk-deploy/SKILL.md
    revision: 1
    title: QIM SDK Deploy Skill
    section: Full skill router and deploy flow
    relationship: informational
  - id: qimsdk-agentic-skills/qimsdk-deploy/references
    revision: 1
    title: QIM SDK Deploy Scripts and Tests
    section: preflight, deploy modes A/B/C/D/P, workspace setup, tests
    relationship: informational
known_gaps:
  - Does not generate application artifacts.
  - Does not fix device, host, SDK, package, or credential problems during deploy.
  - Does not run deploy without mode, artifact path, configs/.env, and passing preflight.
  - Does not commit generated outputs or deployment changes.
---

# QIM SDK Deploy Metadata

## Purpose & Scope

This skill deploys, builds, runs, and retrieves outputs for existing QIM SDK artifacts on Qualcomm Linux devices and configured Linux or WSL workstations.

**In scope:**
- Mode A: deploy and run gst-launch artifacts directly on device.
- Mode B: build GStreamer C sample apps on Ubuntu device.
- Mode C: host-build GStreamer C sample apps on a Linux workstation and deploy to QLI device.
- Mode P: deploy and run Python qimsdk apps.
- Mode D: host-build C++ SDK apps against the Yocto SDK and deploy to QLI device.
- Preflight checks, environment validation, artifact deployment, run output collection, and structured eval-style results.

**Out of scope:**
- Generating application artifacts.
- Fixing device, SDK, host, dependency, package, or credential issues during deploy.
- Running without explicit mode and artifact path.
- Committing files or modifying generated app source as part of deployment.

## When to Use This Skill

**Invoke this skill when:**
- The user asks to deploy, build, run, or evaluate an existing QIM SDK artifact on a target device.
- The user names Mode A, B, C, P, or D, or asks for device execution of a generated artifact.
- The task requires preflight, `.env` configuration, SSH/device validation, or output retrieval.

**Example queries:**
- "Deploy this gst-launch artifact to the device and run it."
- "Run Mode P for this Python qimsdk app."
- "Build this C++ app with Mode D and retrieve the output."

**Do not invoke this skill when:**
- The user asks to generate an app from a prompt.
- The user asks to fix a failed generated artifact before deployment.
- The user asks to bypass preflight, credentials, or config validation.

## Required Inputs

Before applying this skill, the agent must have confirmed:

| Input | Description | Example |
|---|---|---|
| deploy_mode | Which deployment mode to use. | `A`, `B`, `C`, `P`, `D` |
| artifact_path | Path to the generated artifact folder or file. | `outputs/qimsdk-python-yolov8` |
| environment_config | Required values in `configs/.env` for the selected mode. | device IP/user/password, workstation host/user, build dir |

If any required input is missing, the agent must ask before running preflight or deploy.

## Procedure / Decision Logic

### Confirm Mode and Artifact

Ask for deploy mode and artifact path before doing anything else. Do not read `.env`, run preflight, or inspect the environment until both are known.

### Load Configuration

Read `configs/.env` fresh for the selected mode. If required keys are missing, run the documented config wizard and collect only the keys needed for that mode.

### Run Preflight

Run `preflight_check.py --mode <A|B|C|P|D> [--artifact-path <path>]` from the skill's own `references/` directory. Stop on any failure and report the exact error.

### Deploy

Run the mode-specific deploy script exactly as documented:
- Mode A: `deploy_mode_a.py`
- Mode B: `deploy_mode_b.py`
- Mode C: `deploy_mode_c.py`
- Mode P: `deploy_mode_p.py`
- Mode D: `deploy_mode_d.py`

Do not improvise commands or try alternate paths unless explicitly documented by the skill.

### Report Results

Return the deploy/run status, command output summary, retrieved artifacts if any, and exact failure output when deployment stops.

## Source References

| Document ID | Revision | Title | Section | Relationship |
|---|---|---|---|---|
| `qimsdk-agentic-skills/qimsdk-deploy/SKILL.md` | 1 | QIM SDK Deploy Skill | Full skill router and deploy flow | Informational |
| `qimsdk-agentic-skills/qimsdk-deploy/references` | 1 | QIM SDK Deploy Scripts and Tests | preflight, deploy modes A/B/C/D/P, workspace setup, tests | Informational |

**Related skill files:**
- `qimsdk.gstreamer.app_builder` - Generates gst-launch and GStreamer C artifacts that deploy handles in Modes A/B/C.
- `qimsdk.cpp.app_builder` - Generates C++ SDK artifacts that deploy handles in Mode D.
- `qimsdk.python.app_builder` - Generates Python qimsdk artifacts that deploy handles in Mode P.

## Known Exceptions / Edge Cases

| Situation | Recommended Handling |
|---|---|
| Missing or incomplete `configs/.env` | Run the documented config wizard for the selected mode, then run preflight. |
| Preflight failure | Stop immediately and report the exact failing check and error output. |
| Device, host, SDK, package, or runtime failure | Do not attempt repair during deploy; report exact output and stop. |
| Two deploys could compete for exclusive hardware resources | Run sequentially, especially for camera pipelines. |

---
