---
id: qimsdk.cpp.app_builder
version: 1.0
status: draft
tech_area: [qimsdk, cpp, application_generation]
skill_category: app_generation
confidence_level: advisory
output_type: procedure
depends_on: []
related_skills:
  - qimsdk.gstreamer.app_builder
  - qimsdk.python.app_builder
  - qimsdk.deploy
applicable_products:
  - Qualcomm Linux platforms with the QIM SDK C++ API
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
  - id: qimsdk-agentic-skills/qimsdk-cpp-app-builder/SKILL.md
    revision: 1
    title: QIM SDK C++ App Builder Skill
    section: Full skill router
    relationship: informational
  - id: qimsdk-agentic-skills/qimsdk-cpp-app-builder/references
    revision: 1
    title: QIM SDK C++ App Builder References
    section: API surface, SDK architecture, generation rules, plugin catalog, source/sink patterns, pipeline construction, artifact contract
    relationship: informational
known_gaps:
  - Does not generate gst-launch commands or GStreamer C sample apps unless the user explicitly switches skills.
  - Does not generate Python qimsdk applications.
  - Does not deploy or run artifacts on device by itself.
  - Does not invent missing model paths, labels, settings, plugins, SDK methods, callbacks, or undocumented properties.
---

# QIM SDK C++ App Builder Metadata

## Purpose & Scope

This skill generates, debugs, validates, and packages QIM SDK C++ applications that use the C++ SDK API and GStreamer-backed QIM SDK elements.

**In scope:**
- C++ app artifacts using `qti::Pipeline`, `qti::Element`, `qti::AppSrc`, `qti::AppSink`, `qti::CamSrc`, `qti::MLVConverter`, `qti::MLPostprocess`, and `qti::MLVideo*Bin`.
- Explicit and fluent pipeline construction patterns.
- AI, multimedia, ML-bin, AppSrc/AppSink, YAML config mode, custom preprocess, custom postprocess, multistream, composer, and metadata overlay patterns documented by the skill.
- Artifact folders with `main.cc`, `CMakeLists.txt`, and `README.md`.

**Out of scope:**
- `gst-launch-1.0` command generation.
- GStreamer C sample app generation.
- Python qimsdk app generation.
- Device deployment or execution.
- Invention of SDK methods, callback signatures, plugins, properties, model paths, labels, settings, or undocumented behavior.

## When to Use This Skill

**Invoke this skill when:**
- The user asks for a QIM SDK C++ app.
- The request mentions C++ SDK APIs, `qti::Pipeline`, `qti::Element`, AppSrc/AppSink, ML wrappers, or YAML config constructor mode.
- The user wants a runnable C++ artifact rather than a GStreamer C sample app or Python app.

**Example queries:**
- "Create a QIM SDK C++ app for camera YOLOv8 detection with overlay."
- "Generate a C++ app using AppSrc/AppSink to bridge a qimsdk pipeline."
- "Build a C++ ML-bin PPE detection app using the external HTP delegate."

**Do not invoke this skill when:**
- The requested output is gst-launch or GStreamer C.
- The requested output is Python.
- The user only wants to deploy or run an existing artifact.

## Required Inputs

Before applying this skill, the agent must have confirmed:

| Input | Description | Example |
|---|---|---|
| application_intent | The user-visible app behavior and topology. | camera detection overlay, MP4 decode to encode, event-triggered recording |
| input_source | Source type and path/device. | MP4 path, RTSP URL, `qticamsrc`, AppSrc |
| output_target | Display, file, AppSink, metadata, network, or mixed output. | Wayland display, MP4 file, AppSink callback |
| construction_style | Explicit object style by default, or fluent/YAML when requested or preserving existing code. | explicit `Element` objects, fluent `.add(...)`, YAML config |
| runtime_values | Models, labels, settings, delegate, paths, dimensions, framerate, and custom callback expectations. | YOLOv8 model path, `libQnnTFLiteDelegate.so`, `qpd` |

If any required input affects topology or SDK API selection and is missing, the agent should ask a targeted clarification before proceeding.

## Procedure / Decision Logic

### Classify the App

Classify the request as basic media, camera, AI, ML-bin, AppSrc/AppSink, YAML, external preprocess, external postprocess, validation, or edit.

### Load Required References

For generation or edits, load `references/generation-rules.md`, `references/plugin-catalog.md`, `references/sdk-architecture.md`, and `references/artifact-contract.md`. Load task-specific references from the routing table and `references/model-catalog.md` when a known model name or filename appears.

### Ground in Examples

For app generation, load `references/example-retrieval.md` and run `rank_examples.py`. Use retrieved examples as grounding while still validating every API, property, element, path, and topology rule against the references.

### Generate and Validate the Artifact

Create `<artifact>/main.cc`, `<artifact>/CMakeLists.txt`, and `<artifact>/README.md`. Use explicit construction by default, keep stream filters named, validate API use, and keep README `Pipeline Flow` synchronized with actual code.

## Source References

| Document ID | Revision | Title | Section | Relationship |
|---|---|---|---|---|
| `qimsdk-agentic-skills/qimsdk-cpp-app-builder/SKILL.md` | 1 | QIM SDK C++ App Builder Skill | Full skill router | Informational |
| `qimsdk-agentic-skills/qimsdk-cpp-app-builder/references` | 1 | QIM SDK C++ App Builder References | API surface, SDK architecture, generation rules, plugin catalog, source/sink patterns, pipeline construction, artifact contract | Informational |

**Related skill files:**
- `qimsdk.gstreamer.app_builder` - Generates gst-launch and GStreamer C artifacts for comparable use cases.
- `qimsdk.python.app_builder` - Generates Python qimsdk apps for comparable use cases.
- `qimsdk.deploy` - Deploys and runs generated artifacts.

## Known Exceptions / Edge Cases

| Situation | Recommended Handling |
|---|---|
| User asks for missing custom preprocess/postprocess tensor logic | Generate honest TODO placeholders unless exact tensor layout and decode logic are supplied. |
| Request names unknown model/runtime or ambiguous stage count | Ask a targeted clarification when the answer changes topology or delegate selection. |
| User gives concrete paths or module names not listed in catalogs | Preserve user values unless they conflict with documented syntax. |
| Existing app uses a different construction style | Preserve the existing style unless the user asks for conversion. |

---
