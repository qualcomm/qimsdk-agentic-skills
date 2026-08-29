---
id: qimsdk.python.app_builder
version: 1.0
status: draft
tech_area: [qimsdk, python, application_generation]
skill_category: app_generation
confidence_level: advisory
output_type: procedure
depends_on: []
related_skills:
  - qimsdk.gstreamer.app_builder
  - qimsdk.cpp.app_builder
  - qimsdk.deploy
applicable_products:
  - Qualcomm Linux platforms with the Python qimsdk package
applicable_releases:
  - "1"
region: global
required_inputs:
  - application_intent
  - input_source
  - output_target
  - construction_style
  - runtime_values
document_references:
  - id: qimsdk-agentic-skills/qimsdk-python-app-builder/SKILL.md
    revision: 1
    title: QIM SDK Python App Builder Skill
    section: Full skill router
    relationship: informational
  - id: qimsdk-agentic-skills/qimsdk-python-app-builder/references
    revision: 1
    title: QIM SDK Python App Builder References
    section: API surface, SDK architecture, generation rules, plugin catalog, source/sink patterns, AI and multimedia patterns, artifact contract
    relationship: informational
known_gaps:
  - Does not generate gst-launch commands or GStreamer C sample apps.
  - Does not generate C++ qimsdk SDK applications.
  - Does not deploy or run artifacts on device by itself.
  - Does not invent missing model paths, labels, settings, plugins, qimsdk APIs, callbacks, or undocumented properties.
---

# QIM SDK Python App Builder Metadata

## Purpose & Scope

This skill generates, debugs, validates, and packages QIM SDK Python applications that use the public `qimsdk` package.

**In scope:**
- Python app artifacts using `Pipeline`, `Element`, `AppSrc`, `AppSink`, `CamSrc`, `MLVConverter`, `MLPostprocess`, `MLVideo*Bin`, and stream filters.
- Explicit and implicit construction patterns.
- AI, multimedia, ML-bin, AppSrc/AppSink, YAML mode, custom preprocess, custom postprocess, multistream, composer, metadata, and deployment-ready README guidance.
- Artifact folders with `main.py` and `README.md`, plus YAML when YAML mode is requested.

**Out of scope:**
- `gst-launch-1.0` command generation.
- GStreamer C sample app generation.
- C++ qimsdk SDK app generation.
- Device deployment or execution.
- Invention of qimsdk APIs, callback signatures, plugin properties, model paths, labels, settings, or undocumented behavior.

## When to Use This Skill

**Invoke this skill when:**
- The user asks for a QIM SDK Python app.
- The request mentions Python `qimsdk`, `Pipeline`, `Element`, AppSrc/AppSink, ML wrappers, YAML loading, or Python custom callbacks.
- The output should be a runnable Python artifact rather than a C++ or gst-launch artifact.

**Example queries:**
- "Create a QIM SDK Python app for camera YOLOv8 detection using MLVideoTFLiteBin."
- "Generate a Python qimsdk app with a custom postprocess TODO callback."
- "Build a Python app that decodes MP4, runs pose estimation, and displays overlay."

**Do not invoke this skill when:**
- The requested output is gst-launch or GStreamer C.
- The requested output is C++.
- The user only wants to deploy or run an existing artifact.

## Required Inputs

Before applying this skill, the agent must have confirmed:

| Input | Description | Example |
|---|---|---|
| application_intent | The user-visible Python app behavior and topology. | camera detection overlay, tensor dump placeholder, RTSP display |
| input_source | Source type and path/device. | MP4 path, camera source, RTSP URL, AppSrc |
| output_target | Display, file, AppSink, metadata, network, or mixed output. | `waylandsink`, MP4 file, JSON metadata AppSink |
| construction_style | Explicit style by default, or implicit/YAML when requested or preserving existing code. | explicit `Element` objects, chained `.add(...)`, YAML |
| runtime_values | Models, labels, settings, delegate, paths, dimensions, framerate, and custom callback expectations. | `$HOME/models/model.tflite`, `external`, `labels.json` |

If any required input affects topology, runtime selection, or callback contract and is missing, the agent should ask a targeted clarification before proceeding.

## Procedure / Decision Logic

### Classify the Python App

Classify the request as basic app, multimedia app, AI app, ML-bin app, YAML app, AppSrc/AppSink bridge, custom preprocess, custom postprocess, validation, or edit.

### Load Required References

Load `references/generation-rules.md` first for generation/edit/validation/review tasks. Use `references/plugin-catalog.md` for plugin and module facts. Load task-specific references from the routing table and `references/model-catalog.md` when a model name or filename appears.

### Ground in Examples

For Python app generation, load `references/example-retrieval.md` and run `rank_examples.py`. Use retrieved examples as grounding while still applying every relevant reference rule.

### Generate and Validate the Artifact

Create `<artifact>/main.py` and `<artifact>/README.md`; include a YAML config only for YAML-mode requests unless the user says it is external. Keep executable setup inside `main()`/`create_and_execute_pipeline(...)`, run `references/verify-python-app.sh` when applicable, and keep README `Pipeline Flow` synchronized with actual code.

## Source References

| Document ID | Revision | Title | Section | Relationship |
|---|---|---|---|---|
| `qimsdk-agentic-skills/qimsdk-python-app-builder/SKILL.md` | 1 | QIM SDK Python App Builder Skill | Full skill router | Informational |
| `qimsdk-agentic-skills/qimsdk-python-app-builder/references` | 1 | QIM SDK Python App Builder References | API surface, SDK architecture, generation rules, plugin catalog, source/sink patterns, AI/multimedia patterns, artifact contract | Informational |

**Related skill files:**
- `qimsdk.gstreamer.app_builder` - Generates gst-launch and GStreamer C artifacts for comparable use cases.
- `qimsdk.cpp.app_builder` - Generates C++ SDK apps for comparable use cases.
- `qimsdk.deploy` - Deploys and runs generated artifacts.

## Known Exceptions / Edge Cases

| Situation | Recommended Handling |
|---|---|
| User asks for custom preprocess/postprocess without tensor details | Generate honest TODO placeholders and document that functional tensor logic is not implemented. |
| `$HOME` paths are used in element properties | Expand paths in Python before assigning element properties; do not pass raw shell variables to GStreamer properties. |
| Request is ambiguous about topology-changing details | Ask a targeted clarification before generating. |
| Existing app uses implicit style | Preserve the existing construction style unless the user asks for conversion. |

---
