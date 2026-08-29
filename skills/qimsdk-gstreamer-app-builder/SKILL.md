---
name: qimsdk-gstreamer-app-builder
description: QIM SDK GStreamer development for gst-launch commands and GStreamer C sample apps on Qualcomm platforms. Use when building multimedia pipelines, AI pipelines, LiteRT/QNN/SNPE inference flows, daisy-chained models, batching, multi-stream composition, runtime source management, zero-copy cross-process pipelines, or robust bus/error handling.
---

# QIM SDK GStreamer App Builder Skill

## Purpose

Use this skill to generate, debug, validate, and package QIM SDK GStreamer `gst-launch-1.0` pipelines and GStreamer C sample apps. This skill is for GStreamer command artifacts and C sample apps only; it is not for Python or C++ SDK app generation.

## Operating Model

`SKILL.md` is the router. Detailed rules live in the purpose-named references below. Load only the references needed for the user's task so generation stays fast, low-token, and accurate.

## Minimal Workflow

1. Classify the request: plugin lookup, gst-launch pipeline, artifact package, C app, verification/debug, AI, multimedia, or mixed.
2. Load `references/generation-rules.md` for any generation, edit, validation, or review task.
3. For gst-launch or C app generation requests, load `references/example-retrieval.md` and run `rank_examples.py` to ground the draft in known-good examples — this adds context alongside the rest of this workflow, it does not replace steps 4-9. **Finding a retrieval match does not skip or shorten any subsequent step — all of steps 4-9 run unconditionally regardless of what retrieval returns or whether a file was leveraged.** Skip this step for plugin-only lookups.
4. Load only the task-specific references from the routing table.
5. **If the request identifies a specific model** (by display name or `.tflite` filename), load `references/model-catalog.md` and look up the model row now — before filling in any pipeline property. Catalog values for module, labels file, settings format, delegate, and precision availability are pre-resolved and take priority over all generic rules. If the model is not in the catalog, continue without it.
6. **MANDATORY — read before writing a single line of code:** For every element in the pipeline — whether copied from a reference file or written from scratch — open `references/plugin-catalog.md` and the relevant pattern file and read the actual documented properties, pads, caps, and constraints for that element. Do not assume an element works the same way in a new topology. If you swapped a source type, changed a delivery mechanism, added a branch, or combined elements from different reference files, the downstream element choices, pad formats, and timing assumptions may all be invalidated. **There is no such thing as plug-and-play in this pipeline.** Every element must be verified against the reference docs for the specific combination being built — not assumed to be correct because it worked in a different pipeline.
7. Use `references/plugin-catalog.md` for plugin/runtime/property/module facts; do not invent or duplicate plugin facts.
8. If missing information changes topology or element selection, ask a targeted clarification before generating.
9. For generated artifacts, load `references/artifact-contract.md`, create every required file, and run the relevant verification script before declaring the artifact complete.


## Non-Negotiable Invariants

- Use only instructions and facts from this skill folder (`SKILL.md` + `references/*.md`).
- Do not invent plugin names, properties, pads, caps, runtimes, postprocess modules, model stages, or Qualcomm-specific behavior.
- Keep plugin facts in `references/plugin-catalog.md`; other references may show usage but must not become competing catalogs.
- Match the requested scope exactly and prefer the simplest documented topology that works.
- Ask when ambiguity changes topology; use placeholders only for runtime values that do not change structure.
- When a pipeline requires `qtivcomposer` (multistream, PiP, side-by-side, AI wall) and any stream's postprocess module is not `image-segmentation`/`super-resolution`, **prefer** wiring `qtimlpostprocess`'s rendered output directly into an additional `comp.sink_N` pad instead of `qtimetamux`+`qtivoverlay`. The only exception is when `qtiobjtracker` or metadata-downstream consumption is explicitly required. Always pair the mask sink pad with a raw-passthrough video sink pad at the same `position`/`dimensions`. *(see `plugin-catalog.md` → "Preference: reuse an existing qtivcomposer")*
- For generated artifact folders must start with `qimsdk-gstreamer-`.
- In generated `CMakeLists.txt`, both the `project()` name and `GST_EXAMPLE_BIN` must use the `gst-qimsdk-` prefix (e.g. `gst-qimsdk-event-encoder`). The cmake build system only discovers targets whose source directory starts with `gst-`; a binary named without this prefix will silently fail to build.
- For generated gst-launch commands, pipeline.sh files, and README command examples, prefer shell-friendly paths using `$HOME` (for example `$HOME/media`, `$HOME/models`, `$HOME/labels`, and `$HOME/output`) whenever the user has not provided explicit paths, because shell expansion works correctly in command-line contexts and improves portability across QLI and Ubuntu systems; use platform-specific absolute paths such as `/root/media/...` on QLI or `/home/ubuntu/media/...` on Ubuntu only when an absolute path is explicitly required, while continuing to prohibit `$HOME` or other shell-variable expansion inside C/C++ `#define` path constants because string literals do not perform shell expansion at runtime.
- For `qticamsrc`/`qtiqmmfsrc` camera inputs, default `camera=0` unless the user asks for another camera. If the user does not provide resolution or framerate, constrain the camera stream with `video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1` before display, branching, AI preprocessing, or encode, and call out `1920x1080 @ 30fps` as an assumed camera default in the artifact README.
- For audio AI pipelines (`qtimlaconverter`/YAMNet): use `feature=lmfe params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"` for YAMNet; set `output-buffer-size=31200` (15600 samples × 2 bytes). When the user does not specify model details, apply these YAMNet defaults and **explicitly state each assumed value** (rate, channels, buffer size, feature mode, params, confidence, results count) in the response so the user can correct them before running.
- For any pipeline or C app that uses `pulsesrc` or `pulsesink`, **always include the PulseAudio prerequisite** in the response and in the generated README "Steps to Run on QLI" section — verbatim from `references/multimedia-pipeline-patterns.md` "Audio Pipeline Prerequisites": `wpctl status` to list nodes, then `wpctl set-default <node_no.>` to set the default source/sink. Do not omit this for audio capture, audio playback, AV record, or AV playback pipelines. No default node number exists — `<node_no.>` is always device-specific.
- **NEVER use `$HOME` or shell variable expansion in C `#define` path constants.** C does not expand environment variables in string literals at runtime. `#define INPUT_FILE "$HOME/Downloads/qimsdk_samples/media/..."` will literally pass the string `$HOME/Downloads/qimsdk_samples/media/...` to GStreamer, which will fail to open the file. Use absolute paths (e.g. `/root/Downloads/qimsdk_samples/media/...` for QLI, `/home/ubuntu/Downloads/qimsdk_samples/media/...` for Ubuntu). Mark these as placeholders in the README "Placeholders to Fill" section so the user knows to substitute the correct path for their device before building.

## Buffer writability — `qtimetamux`, `qtivoverlay` and `qtivcomposer`

`qtimetamux`, `qtivoverlay`, and `qtivcomposer` all write in-place and require sole ownership of the buffer (refcount == 1). After any `tee`, if another branch hasn't released the buffer by the time the writing element runs, the write is silently skipped — no error, no crash, just missing boxes or compositing. This applies both when branches share a `tee` directly and when they share a buffer through a downstream `tee`.

- **Always required** when a parallel branch contains `qtivcomposer` — it holds buffers for stream sync so refcount is guaranteed > 1 when the overlay chain runs. Fix: insert `qtivtransform ! video/x-raw,format=NV12` immediately before the writing element (`qtimetamux` for overlay chains) on the affected branch — it forces a buffer copy, giving the writer sole ownership. Example: `t. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! obj_mux.`
- **Required under load** when a parallel branch contains `filesink` or `qtimlmetaparser` — these are fast but can hold the buffer; add `qtivtransform` to be safe.
- **Omit** when no other branch could be holding the buffer (e.g. plain single-stream `tee → qtimetamux` + AI branch → `qtivoverlay`, no composer/filesink/qtimlmetaparser sibling).
- **Same family — leaf `appsink` off a shared tee:** a leaf `appsink` (frame/metadata tap) on a `tee` that also feeds `qtimlvconverter` poisons the DMA buffer pool — `qtimlvconverter` then fails `Buffer does not have FD memory!` and produces **0 inference** (pipeline plays but no ML output). Insert `qtivtransform ! video/x-raw,format=NV12` immediately before that `appsink` to isolate its allocation. See `references/source-sink-patterns.md`.
- **Same family — N sibling overlay branches off one shared tee (no composer involved):** the underlying constraint is buffer writability under a shared tee, not specifically "composer + overlay" — whenever more than one buffer-mutating consumer (in-place overlay, composer, appsink tap) reads off the same tee pad, only one can draw/consume in place; the rest silently produce no output (no error, no crash). In a parallel-AI-wall pipeline where N independent AI branches each overlay their own result off one shared `tee` (each with its own `qtimetamux`/`qtivoverlay`, no top-level composer), give **every** branch's passthrough leg its own `qtivtransform ! video/x-raw,format=NV12` copy immediately before that branch's `qtimetamux`, not just one of them — otherwise only one branch's overlay renders and the rest draw nothing.

## Render-overlay caps: never pin `width`/`height` when a composer sizes the tile

When `qtimlpostprocess`'s render/overlay output feeds directly into a `qtivcomposer`/`mixer` sink pad, **do not pin `width=`/`height=` on the capsfilter between them** (a bare `format=RGBA` capsfilter with no dimensions is fine, and omitting the capsfilter entirely is also fine). Device-verified: pinning `width=`/`height=` on this branch — regardless of format — fails with `Fixated width in filter caps is not supported with current post-process type!`; this is not a format problem, so switching `BGRA`→`RGBA` while keeping pinned dimensions does **not** fix it. Let the size negotiate freely and size the branch via the composer sink-pad `dimensions` instead. Older docs/examples that pin fixed dimensions (with either `BGRA` or `RGBA`) on this specific branch are stale — drop the dimensions, don't just relabel the format. See `references/plugin-catalog.md` "Module Output Types". (Note: `sink_N::position`/`sink_N::dimensions` gst-array strings and `$HOME` in shell/gst-launch are CORRECT here — do not change those.)

## Model prerequisites

- HRNet (and other top-down pose models) can run either directly on full frames or on a cropped ROI from a preceding detector — both are legitimate topologies with a real accuracy/complexity tradeoff, not a fixed requirement. Full-frame is simpler (one stage, no detector needed) and works well when the subject fills most of the frame; a detection→ROI cascade adds a stage but improves keypoint accuracy on smaller/distant subjects by giving HRNet a tighter crop to work from. Build whichever the request actually describes; don't add or remove the detection stage on your own. See `references/model-catalog.md`.
- `lite-3dmm` (face-recognition stage 2) requires `/etc/data/{blendShape,meanFace,shapeBasis}.bin` on the device; without them stage 2 fails `Failed to open /etc/data/meanFace.bin`. Note this device prerequisite in the README. See `references/model-catalog.md`.

## C App Guardrails

Keep these C app rules router-visible because missing them creates code that may compile poorly or fail at runtime:

- Include `#include <gst/sampleapps/gst_sample_apps_utils.h>` and use the sample-app utilities from that header.
- Set QTI enum properties with `gst_element_set_enum_property()` using string nicks; do not use hardcoded enum integers.
- Set TFLite delegates with `GST_ML_TFLITE_DELEGATE_*` constants; do not define numeric delegate constants.
- Set `qtimlpostprocess module` with `get_enum_value(element, "module", "<nick>")`; never hardcode module integers.
- Do not redefine `GstAppContext`, sample bus callbacks, `handle_interrupt_signal`, `get_enum_value`, or `gst_element_set_enum_property`.
- Start generated sample apps with `GST_STATE_PAUSED`; the sample `state_changed_cb` handles transition to PLAYING.
- For Topology A metadata overlay, keep `qtimlpostprocess -> text/x-raw -> qtimetamux`; do not invent `qtimetamux` pad names such as `sink_0` or `sink_1` in C.

## TFLite Delegate Selection Rules
These rules apply only to `qtimltflite`.

### Delegate Selection Priority
Use the following priority order:
1. Explicit delegate provided by the user
2. Delegate inferred from a known model mapping
3. Delegate inferred from a known postprocess module mapping
4. Ask the user

Never select a delegate randomly.

---

### Known Model Mapping
When a model name or filename is identifiable in the request, consult `references/model-catalog.md` (Step 5 above). The catalog is the authoritative source for per-model delegate, module, labels file, settings format, and precision availability.

If the model is not in the catalog, fall back to these family-level heuristics:
- quantized MobileNet variants → external
- quantized YOLO variants → external
- quantized EfficientDet variants → external
- floating-point MobileNet variants → gpu
- floating-point YOLO variants → gpu

Only use heuristics for families explicitly listed above. Do not extend them to unlisted architectures (e.g. DETR, RTMDet, ViT) — those may deviate and are covered by the catalog or by asking the user.

---

### Postprocess-Based Inference
If the model filename does not reveal the model type but the selected
`qtimlpostprocess` module uniquely identifies a documented model family,
delegate may be selected from that family mapping.

Example:
module=qfd
and references document qfd pipelines using a specific delegate.
Use that delegate.

---

### Unknown Models
If the model name is unknown and no documented mapping exists:
Ask the user:
"Which delegate should be used: gpu or external?"
Do not guess.

---

### Filename Heuristics
Filename heuristics may be used only when they clearly identify a known model family.
Examples:
mobilenet_v1_quant.tflite
mobilenet_v2_quantized.tflite
yolov8n_int8.tflite
detection-w8a8.tflite
→ quantized family

mobilenet_v1_float.tflite
yolov8_fp32.tflite
→ floating-point family

Generic filenames are not sufficient:
model.tflite
network.tflite
detect.tflite
For such names, do not infer a delegate.

---

### Safety Rule
When delegate selection cannot be derived from:
- explicit user input
- documented model mapping
- documented postprocess-module mapping
ask the user.

Incorrect delegate selection is worse than asking.

## Reference Routing

| Task | Load these references |
|---|---|
| Any generation, edit, validation, or review | `references/generation-rules.md` |
| Specific model identified by name or filename (any task) | `references/model-catalog.md` — load alongside other task references; if model not found in catalog, proceed without it |
| Plugin/property/runtime/module lookup | `references/plugin-catalog.md` |
| Source, sink, decode, encode, display, RTSP, socket, appsink/appsrc selection | `references/source-sink-patterns.md` |
| Queues, tee, capsfilter, mux/demux, parser, batching, fan-in, zero-copy utility behavior | `references/pipeline-utilities.md` |
| AI inference pipeline, audio AI, daisy-chain, multistream AI, AI wall, segmentation, SR, metadata overlay | `references/ai-pipeline-patterns.md`. For multistream/AI-wall tasks: before copying a template's `qtimetamux`+`qtivoverlay` wiring for a given stream, check whether that stream's module supports `video/x-raw` directly (`plugin-catalog.md` category table) and decide per stream — templates show one valid form, not the only one. If using the direct form, the mask output is transparent boxes/labels only (no video) and must feed an *additional* composer sink pad paired with that stream's existing raw-passthrough pad at the same `position`/`dimensions`, never a sink pad alone. |
| Multimedia-only capture, display, record, playback, transform, streaming, audio, AV, composition | `references/multimedia-pipeline-patterns.md` |
| Artifact folder/files, README Pipeline Flow, validation scripts, completion checklist | `references/artifact-contract.md` |
| GStreamer C sample app, C callbacks, C API usage, CMake/build, JSON config | `references/c-app-development.md` |
| Object tracking request (persistent IDs across frames, multi-object tracking, `qtiobjtracker`) | `references/ai-pipeline-patterns.md` (Object Tracking section) and `references/ml-metadata-structures.md` |
| Grounding a gst-launch or C app generation in known-good examples | `references/example-retrieval.md` |

## Common Load Sets

- AI gst-launch artifact: `generation-rules.md`, `example-retrieval.md`, `ai-pipeline-patterns.md`, `plugin-catalog.md`, `source-sink-patterns.md`, `pipeline-utilities.md`, `artifact-contract.md`. Add `model-catalog.md` when a specific model name or filename is present in the request.
- Multimedia gst-launch artifact: `generation-rules.md`, `example-retrieval.md`, `multimedia-pipeline-patterns.md`, `source-sink-patterns.md`, `pipeline-utilities.md`, `plugin-catalog.md` when properties must be checked, `artifact-contract.md`.
- C app artifact: `generation-rules.md`, `example-retrieval.md`, `c-app-development.md`, the relevant AI or multimedia pattern file, `plugin-catalog.md`, `source-sink-patterns.md`, `pipeline-utilities.md`, `artifact-contract.md`. Add `model-catalog.md` when a specific model name or filename is present in the request.
- Plugin/property question only: `plugin-catalog.md`.

## Verification Scripts

- `references/verify-gst-launch.sh` verifies generated `pipeline.sh` artifacts.
- `references/verify-c-app.sh` verifies generated `main.c` and `CMakeLists.txt` artifacts.

Run these through the workflow in `references/artifact-contract.md`; do not treat script execution alone as a full contextual review.

## Reference Files

- `references/ml-metadata-structures.md` — GstBuffer-attached ML metadata structures (`GstMLTensorMeta`, ROI/classification/landmarks metas, text/x-raw structured metadata) and how postprocess/mux/tracker/transform elements read and write them
- `references/model-catalog.md` — per-model lookup table for named AI Hub models: module, labels file, settings format, delegate, and precision availability; load at Step 5 when a model name or filename is present
- `references/example-retrieval.md` — retrieval-grounding: how to run `rank_examples.py`, screen ranked candidates for a genuine fit, and leverage a real matching file as the starting point (falling back to building fresh from the rules only when none fit).
