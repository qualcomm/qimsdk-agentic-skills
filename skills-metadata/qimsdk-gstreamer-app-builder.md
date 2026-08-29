---
id: qimsdk.gstreamer.app_builder
version: 1.0
status: draft
tech_area: [qimsdk, gstreamer, application_generation]
skill_category: app_generation
confidence_level: advisory
output_type: procedure
depends_on: []
related_skills:
  - qimsdk.cpp.app_builder
  - qimsdk.python.app_builder
  - qimsdk.deploy
applicable_products:
  - Qualcomm Linux platforms with QIM SDK GStreamer plugins
applicable_releases:
  - "1"
region: global
required_inputs:
  - requested_artifact_type
  - pipeline_intent
  - input_source
  - output_target
  - runtime_values
document_references:
  - id: qimsdk-agentic-skills/qimsdk-gstreamer-app-builder/SKILL.md
    revision: 1
    title: QIM SDK GStreamer App Builder Skill
    section: Full skill router
    relationship: informational
  - id: qimsdk-agentic-skills/qimsdk-gstreamer-app-builder/references
    revision: 1
    title: QIM SDK GStreamer App Builder References
    section: generation rules, plugin catalog, source/sink patterns, AI and multimedia patterns, C app development, artifact contract
    relationship: informational
known_gaps:
  - Does not generate Python qimsdk applications.
  - Does not generate C++ qimsdk SDK applications.
  - Does not deploy or run artifacts on device by itself.
  - Does not invent missing model paths, labels, settings, plugins, pads, caps, runtimes, or modules.
---

# QIM SDK GStreamer App Builder Metadata

## Purpose & Scope

This skill generates, debugs, validates, and packages QIM SDK GStreamer artifacts for Qualcomm Linux platforms.

**In scope:**
- `gst-launch-1.0` pipeline artifacts.
- GStreamer C sample app artifacts.
- Multimedia, AI, LiteRT/TFLite, QNN, SNPE, ONNX, ML-bin, daisy-chain, batching, multistream, composition, metadata, zero-copy, and runtime-management patterns covered by this skill bundle.
- Artifact folders with `pipeline.sh` or `main.c`/`CMakeLists.txt` plus `README.md`.

**Out of scope:**
- Python `qimsdk` app generation.
- C++ `qti::Pipeline` SDK app generation.
- Device deployment or execution.
- Invention of undocumented plugin names, properties, pads, caps, runtimes, modules, or model paths.

## When to Use This Skill

**Invoke this skill when:**
- The user asks for a QIM SDK GStreamer pipeline, `gst-launch-1.0` command, or GStreamer C sample app.
- The task is about GStreamer plugin/property lookup, pipeline wiring, caps, queues, mux/demux, display, encode/decode, AI inference, postprocess modules, or C app generation.
- The user wants generated GStreamer artifacts that can be built or run later.

**Example queries:**
- "Create a QIM SDK gst-launch pipeline for YOLOv8 object detection from MP4 to Wayland display."
- "Generate a GStreamer C app for multistream inference with qtivcomposer."
- "What properties should I use for qtimltflite and qtimlpostprocess in this pipeline?"

**Do not invoke this skill when:**
- The requested artifact is a Python `qimsdk` app.
- The requested artifact is a C++ SDK app.
- The task is only to deploy an existing artifact.

## Required Inputs

Before applying this skill, the agent must have confirmed:

| Input | Description | Example |
|---|---|---|
| requested_artifact_type | Whether the user wants a gst-launch artifact, GStreamer C app, plugin lookup, validation, or edit. | `gst-launch`, `C app`, `plugin lookup` |
| pipeline_intent | The functional goal and major topology requirements. | object detection overlay, AI wall, AV record |
| input_source | Source type and path/device when relevant. | MP4 file, `qtiqmmfsrc camera=0`, RTSP URL |
| output_target | Display, file, metadata sink, network sink, or mixed output. | `waylandsink`, MP4 file, Redis |
| runtime_values | User-provided models, labels, settings, delegate, dimensions, framerate, and output path. | `/root/models/yolov8_det_w8a8.tflite`, `external`, `1920x1080@30` |

If any required input affects topology or element selection and is missing, the agent should ask a targeted clarification before generating.

## Procedure / Decision Logic

### Classify the Request

Decide whether the task is plugin lookup, gst-launch generation, C app generation, validation/debug, AI, multimedia, or mixed. For generation/edit/review tasks, load `references/generation-rules.md` first.

### Load Relevant References

Use `SKILL.md` routing to load only matching references. Always use `references/plugin-catalog.md` as the plugin/property/module source of truth. Load `references/model-catalog.md` when a model display name or `.tflite` filename is present.

### Ground Generation

For gst-launch and C app generation, load `references/example-retrieval.md` and run `rank_examples.py` to ground the draft in known-good examples. Retrieval is context, not a shortcut; every element still needs verification against the skill references.

### Generate and Validate Artifacts

Create the required files for the selected artifact type, follow `references/artifact-contract.md`, run the relevant verification script, and report assumptions, placeholders, and user-filled values.

## Source References

| Document ID | Revision | Title | Section | Relationship |
|---|---|---|---|---|
| `qimsdk-agentic-skills/qimsdk-gstreamer-app-builder/SKILL.md` | 1 | QIM SDK GStreamer App Builder Skill | Full skill router | Informational |
| `qimsdk-agentic-skills/qimsdk-gstreamer-app-builder/references` | 1 | QIM SDK GStreamer App Builder References | generation rules, plugin catalog, source/sink patterns, AI/multimedia patterns, C app development, artifact contract | Informational |

**Related skill files:**
- `qimsdk.cpp.app_builder` - Generates C++ SDK apps for comparable use cases.
- `qimsdk.python.app_builder` - Generates Python qimsdk apps for comparable use cases.
- `qimsdk.deploy` - Deploys and runs generated artifacts.

## Known Exceptions / Edge Cases

| Situation | Recommended Handling |
|---|---|
| Missing input source, output target, inference runtime, or topology-changing detail | Ask a targeted clarification before generating. |
| User provides runtime paths or model/settings values not present in catalogs | Preserve user-provided values exactly unless they conflict with documented syntax. |
| User asks for Python or C++ SDK code | Use the corresponding app-builder skill. |
| Device/runtime failure after generation | Use deploy output, logs, and relevant references; do not guess a fix from generation context alone. |

---
