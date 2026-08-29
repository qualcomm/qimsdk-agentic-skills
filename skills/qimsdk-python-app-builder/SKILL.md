---
name: qimsdk-python-app-builder
description: "QIM SDK Python application development using the qimsdk package. Use when building, editing, fixing, reviewing, or packaging Python apps that import qimsdk, construct Pipeline objects, use Element/AppSrc/AppSink/CamSrc/MLVConverter/MLPostprocess/MLVideo*Bin wrappers, generate multimedia or AI pipelines, wire custom Python preprocess or postprocess callbacks, or load qimsdk pipelines from YAML."
---

# QIM SDK Python App Builder Skill

## Purpose

Use this skill to generate, debug, validate, and package QIM SDK Python applications. This skill is for Python `qimsdk` app artifacts only; it is not for C++, C sample apps, or `gst-launch-1.0` command generation.

## Operating Model

`SKILL.md` is the router. Detailed API facts, plugin facts, construction patterns, artifact rules, and verifier checks live in the purpose-named references below. Load only the references needed for the user's task so generation stays fast, low-token, and accurate.

## Minimal Workflow

1. Classify the request: API lookup, basic app, multimedia app, AI app, custom preprocess app, custom postprocess app, ML-bin app, AppSrc/AppSink bridge, YAML mode, edit/fix, or validation.
2. Load `references/generation-rules.md` for any generation, edit, validation, or review task.
3. For any Python app generation request, load `references/example-retrieval.md` and run `rank_examples.py` to ground the draft in known-good examples — this adds context alongside the rest of this workflow, it does not replace steps 4-8. **Finding a retrieval match does not skip or shorten any subsequent step — all of steps 4-8 run unconditionally regardless of what retrieval returns or whether a file was leveraged.** Skip this step for plugin-only lookups.
4. Load only the task-specific references from the routing table.
5. Use `references/plugin-catalog.md` for plugin/property/module/runtime facts; do not invent or duplicate plugin facts.
6. If the request names a known model display name or `.tflite` filename, load `references/model-catalog.md` before filling module, delegate, labels/settings expectations, or mandatory results.
7. If missing information changes topology, element selection, runtime selection, custom preprocess tensor contract, or custom postprocess output type, ask a targeted clarification before generating.
8. For generated artifacts, load `references/artifact-contract.md`, create every required file, and run `references/verify-python-app.sh` before declaring the artifact complete.

## Non-Negotiable Invariants

- Use only instructions and facts from this skill folder (`SKILL.md` + `references/*.md`).
- Do not invent plugin names, properties, qimsdk APIs, postprocess modules, model stages, callback signatures, or Qualcomm-specific behavior.
- Keep plugin facts in `references/plugin-catalog.md`; other references may show usage but must not become competing catalogs.
- Known model facts live in `references/model-catalog.md`; use it when the prompt names a known model or `.tflite` filename. Catalog model facts override generic heuristics; user-provided paths and values still win.
- Match the requested scope exactly and prefer the simplest documented topology that works.
- Ask when ambiguity changes topology; use placeholders only for runtime values that do not change structure.
- Generate artifact folders with the `qimsdk-python-` prefix.
- Artifact requests produce `main.py` and `README.md`; YAML-mode requests also produce a YAML config file unless the user explicitly says the YAML already exists and should not be generated.
- If generated `main.py` changes, update the artifact `README.md` in the same pass.
- README must include `Pipeline Flow` with `Text Summary` and `Mermaid Diagram` subsections, plus `Steps to Run on QLI`; flow content must match the actual code.
- For any app that uses `pulsesrc` or `pulsesink`, include the PulseAudio prerequisite in the response and in README `Steps to Run on QLI`: `wpctl status`, then `wpctl set-default <node_no.>`. Do not invent a default node number; `<node_no.>` is device-specific.
- README `Steps to Run on QLI` must first state that all models, labels, media, and other referenced files are present on the device, then show `scp main.py root@<device-ip>:/root/`, then `ssh root@<device-ip>` and `python3 /root/main.py`. Do not add `chmod +x` — Python apps run via `python3 <path>`, not direct execution, so the executable bit is irrelevant; this is the one place this skill diverges from the GStreamer and C++ builder skills, which both chmod/execute a compiled or script artifact.
- Full generated apps must define `create_and_execute_pipeline(...)`, define `main() -> None`, and end with `if __name__ == "__main__": main()`.
- Generated examples should include useful comments for pipeline sections, placeholders, custom preprocess/postprocess TODOs, and non-obvious SDK requirements. Do not comment every obvious line.

## Construction Style

The SDK supports two construction styles.

### Explicit Style - Default

Use explicit construction for new generated apps unless the user asks otherwise:

- create `Element`, wrapper, and filter objects first
- configure properties with `.set(...)`
- add objects with `pipeline.add(element)`
- use named variables for important stages
- link by element names with `pipeline.link(...)`, or rely on insertion-order auto-linking only for simple linear chains

### Implicit Style - Supported

Implicit construction uses chained factory calls such as `.add("factory", "name", "prop", value, ...)`.

Use implicit style when:

- the user explicitly asks for implicit/fluent/chained style
- editing an existing implicit app and the user did not ask for a style conversion
- preserving a small existing code pattern is lower risk than rewriting it

Do not mix explicit and implicit construction styles in one generated app unless the user explicitly asks for mixed style.

## Python App Guardrails

Keep these rules router-visible because missing them commonly creates broken apps:

- Import from public `qimsdk` exports. Do not import from `qimsdk._*` internals unless the user explicitly asks.
- Put executable pipeline construction and `pipeline.execute()` inside `create_and_execute_pipeline(...)`; keep `main() -> None` for logging setup, argument parsing, and calling `create_and_execute_pipeline(...)`. Do not leave executable pipeline setup at module top level.
- Use exact logging exports: `ImsdkLogLevel`, `ImsdkGstLogMode`, `SetImsdkLogLevel`, `SetImsdkGstLogMode`.
- Call logging setters before constructing the pipeline when generating a full app.
- Default generated apps to `SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)` and `SetImsdkLogLevel(ImsdkLogLevel.Debug)` unless user explicitly requests a different logging mode or verbosity.
- Use `VideoFilter().resolution(width, height)` and `.framerate(fps)`; do not invent `.width()` or `.height()`.
- **Expand `$HOME` in Python before passing any path to an element property.** Element properties do not do shell expansion — a raw `"$HOME/..."` string fails to open. Use `f"{os.environ['HOME']}/..."` for every path constant (input/model/labels/output); do not use `os.path.expandvars("$HOME/...")` — it silently leaves the string unexpanded if `HOME` is unset instead of raising, reproducing the same unresolved-path failure. See `references/generation-rules.md`.
- Add `VideoFilter`, `TextFilter`, `TensorFilter`, `ImageFilter`, `H264Filter`, and other stream filter objects with `pipeline.add_stream_filter(...)`, never with `pipeline.add(...)`.
- In generated apps, always use the named `pipeline.add_stream_filter("<name>", filter_obj)` form. Do not use the one-argument form, because repeated filter types such as two `TextFilter()` instances collide on default element names like `textfilter`.
- Every stream filter name used in `pipeline.link(...)` must exactly match the name passed to `pipeline.add_stream_filter(...)`, for example `pipeline.add_stream_filter("vf", vf)`, `pipeline.add_stream_filter("mlf1", mlf1)`, and `pipeline.add_stream_filter("mlf2", mlf2)`.
- For AppSrc/AppSink callbacks, use `set_buffer_producer(...)`, `set_buffer_consumer(...)`, `set_preroll_handler(...)`, `set_eos_handler(...)`, and `set_enough_handler(...)`; for raw element signals (e.g. AppSrc `need-data`) use `connect_signal("need-data", ...)` (preferred over `get_raw().connect(...)`) only when explicitly requested. `connect_signal`/`disconnect_signal` are for generic GObject element signals not covered by a typed wrapper — do not use them to replace the AppSrc/AppSink handler callbacks, and do not generate generic `set_handler(...)` on AppSrc or AppSink.
- For H.264 decode, default `v4l2h264dec capture-io-mode=4 output-io-mode=4` unless user overrides with documented values, and place a `queue` immediately after hardware decode before any filter, tee, AI, display, or mux stage.
- Normalize decoded/camera video to `VideoFilter().format("NV12")` before branching or AI preprocessing unless user requests another documented format.
- For `qticamsrc`/`qtiqmmfsrc`, default `camera=0` unless user asks for another camera.
- For `qticamsrc`/`qtiqmmfsrc` camera inputs, if the user does not provide resolution or framerate, constrain the camera stream with `VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)` before display, branching, or AI preprocessing, and call out `1920x1080 @ 30fps` as an assumed camera default in the artifact README.
- For USB/V4L2 camera inputs, use `v4l2src device=<DEVICE_NODE> -> VideoFilter().format("YUY2") -> qtivtransform -> VideoFilter().format("NV12")` before display, branching, or AI preprocessing; do not apply ISP-camera resolution/framerate defaults to USB unless the user requests them.
- For display, default `waylandsink fullscreen=True sync=True` for EVERY source type, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`) — camera source type alone is not a reason to use `sync=False`. Use `sync=False` only for three exceptions: (1) more than 8 independent concurrently active input streams AND a processing-heavy topology (shared/batched inference, or a large composer grid where HTP/batch preroll makes frames arrive well behind their PTS, causing `sync=True` to drop late frames and freeze/blacken the display) — a 4-stream AI wall, a simple multi-stream playback grid, or a batch group of 8 or fewer streams stays `sync=True`; (2) display shares a tee/composer source with a parallel encode/file/metadata sink that can stall the display clock (stream-count-independent, can apply even with 1 stream) — pair with `enable-last-sample=False` when it is also a multi-sink camera pipeline; (3) the user explicitly requests lower latency over A/V sync. Audio-classification display pipelines must OMIT the `sync` property entirely.
- For metadata overlay with discrete AI elements, keep a main video branch with `tee`, route postprocess metadata through `TextFilter()`, and merge video + metadata with `qtimetamux` before `qtivoverlay`.
- For segmentation, rendered-mask, alpha-blend, side-by-side, or other video-output postprocess branches, use direct-to-composer topology: passthrough branch plus AI/render branch into `qtivcomposer`. Do not force rendered video through `TextFilter()`/`qtimetamux`.
- The render `VideoFilter` after `qtimlpostprocess` MUST be `.format("RGBA")` (never `BGRA`/`RGB` — src caps are `video/x-raw,{RGBA,RGBx}`; anything else fails to link). Resolution: when the branch feeds a `qtivcomposer` whose sink pad sets `dimensions` (segmentation/depth/super-res/detection tiles), do NOT also pin `.resolution()` on this filter — device-verified, it fails caps fixation with `Fixated width in filter caps is not supported with current post-process type!` regardless of format; let the composer size it. Only pin `.resolution(w, h)` when there is NO composer pad geometry sizing the branch (e.g. a small overlay panel composited without per-pad dimensions). See `references/plugin-catalog.md` "Module Output Types".
- Set `qtivcomposer` pad geometry via `composer.input(N).set("position", [x, y])` / `.set("dimensions", [w, h])` using Python LISTS. Never use gst-array strings like `"<x, y>"`, never element-level `sink_N::position` properties, never scalar `x`/`y`/`width`/`height`. See `references/api-surface.md`.
- When a parallel/overlay branch shares a tee with a `qtivcomposer` (**always** a trigger — composer holds buffers for stream sync → the in-place `qtimetamux`/`qtivoverlay` draws nothing), a sibling branch has `filesink` or `qtimlmetaparser` (**required under load** — these are fast but can still hold the buffer), a leaf video `AppSink` shares a tee with `qtimlvconverter` (poisons the DMA pool → 0 inference, "Buffer does not have FD memory"), or N sibling AI branches each overlay their own result off one shared tee with no composer at all (only one buffer-mutating consumer per shared tee pad can draw in place — the rest render nothing), insert `qtivtransform ! VideoFilter(NV12)` on each affected branch to force a private buffer copy. See `references/source-sink-patterns.md` "Buffer Writability Under Shared Tees".
- For super-resolution, audio AI, AI wall, batched multi-stream inference, face recognition, AI metadata parsing, mixed-wall, or zero-copy requests, use the named topology sections in `references/ai-pipeline-patterns.md`; do not reduce these to the generic single-stream or two-stage templates.
- For RTSP serving, multimedia audio/AV, multi-stream grid/fan-out, transform, or camera multi-pad/snapshot requests, load the expanded sections in `references/multimedia-pipeline-patterns.md` and `references/source-sink-patterns.md`.
- For fused ML-bin overlay (`MLVideo*Bin` / `qtimlvideo*bin`), keep ML-bin stages directly in the media path: `source/decode -> VideoFilter(NV12) -> MLVideo*Bin -> qtivoverlay -> sink`, or direct cascades such as `mlbin1 -> mlbin2 -> qtivoverlay -> sink`. Do not wrap ML-bin output in external `tee -> TextFilter() -> qtimetamux` metadata fan-in; use `qtimetamux`/`TextFilter()` only for discrete AI branches that emit metadata separately.
- For `qtimetamux` writability, do not assume camera buffers are inherently non-writable; add `qtivtransform` before `qtimetamux` when the same source/tee also feeds `qtivcomposer` (always required — it holds buffers for stream sync), when a sibling branch has `filesink` or `qtimlmetaparser` (required under load — these are fast but can still hold the buffer), when a documented source/format path requires conversion/copy, or when runtime logs show the metadata attach warning.
- For two-stage daisy-chain AI, use stage-1 `qtimlvconverter mode="image-batch-non-cumulative"` and stage-2 `mode="roi-batch-cumulative"`.
- HRNet (and other top-down pose models) can run either directly on full frames or on a cropped ROI from a preceding detector — both are legitimate topologies with a real accuracy/complexity tradeoff, not a fixed requirement. Full-frame is simpler (one stage, no detector needed) and works well when the subject fills most of the frame; a detection→ROI cascade adds a stage but improves keypoint accuracy on smaller/distant subjects by giving HRNet a tighter crop to work from. Build whichever the request actually describes; don't silently add or remove the detection stage. See `references/generation-rules.md` "Known Model Catalog".
- `lite-3dmm` (facemap 3DMM, stage 2 of face recognition) requires `/etc/data/{blendShape,meanFace,shapeBasis}.bin` on the device (hard-coded in `facemap_3dmm_settings.json`); without them stage 2 fails `Failed to open /etc/data/meanFace.bin`. Note this device prerequisite in the README. See `references/model-catalog.md`.
- For two-stage discrete daisy-chain AI with overlay of both stage outputs, do not generate a single linear `postprocess -> TextFilter -> qtimetamux -> qtivoverlay` chain. Use two tee/mux stages: split before stage 1, merge stage-1 metadata with `qtimetamux`, split the merged stream before stage 2, merge stage-2 metadata with a second `qtimetamux`, then overlay.
- For gesture-recognition chains (palm detection -> hand landmark -> gesture embedding -> gesture classification), do not use a generic N-stage mux cascade. Use the documented gesture topology: exactly two `qtimetamux` stages, `qtimetatransform module="roi-palmd"` between the first mux and the second split, stage-2 inference output split into an `hlandmark` metadata branch and a `tensor -> embedder inference -> classifier inference -> mobilenet` classification branch, then merge both into the second mux before `qtivoverlay`.
- For `qtimlpostprocess`, use `module`, `labels`, and optional `settings`; include `settings` only when user asks for config/settings/threshold tuning or provides a settings path.
- For confidence threshold tuning, encode `settings` as JSON with the canonical `confidence` key, for example `{"confidence": 50.0}`. Do not use `confidence_threshold` or semicolon-delimited settings.
- For live camera or RTSP object/face/palm detection postprocess (`yolov8`, `yolov5`, `yolo-nas`, `qfd`, `palmd`), set `bbox-stabilization=True`; omit it for file-source pipelines unless user requests stabilization.
- Person-foot requests default to `module="qpd"` when model/labels/settings paths indicate person-foot intent, unless user overrides.
- PPE equipment/object detection and `gear_guard_net` paths default to `module="yolov8"` unless user overrides.
- YOLOX detection defaults to `module="yolov8"` unless user overrides.
- For TFLite external delegate targeting HTP/NPU, always set `external-delegate-path="libQnnTFLiteDelegate.so"` and `external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` unless the user provides exact delegate options.
- For secondary ROI inference stages where the user asks for high-performance HTP/NPU daisy-chain behavior, or for high-concurrency parallel HTP/NPU workloads, use `QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;`. For multi-batch HTP/NPU walls, use the named topology section and round-robin batch groups with `htp_device_id` when multiple HTP devices are present.
- Never emit `QNNExecurorBackend:HTP`, `QNNExecutorBackend:HTP`, `QNNExternalDelegateBackend:HTP`, or colon-separated QNN delegate option strings.
- For ML-bin wrappers, use `inference-*` and `postprocess-*` property families; do not mix discrete-element property names with bin property names.
- For custom Python preprocess, use `MLVConverter("name").set(engine="none").set_handler(callback)` for discrete pipelines or `MLVideo*Bin.set("preprocess-engine", "none").set_preprocess_handler(callback)` for ML-bin pipelines.
- Do not add manual `signal.signal(SIGINT, ...)` handling unless the user asks for custom shutdown behavior.

## Custom Preprocess Guardrails

- Use custom Python preprocess only when requested or when the user explicitly asks for placeholder preprocess logic.
- For discrete pipelines, use `MLVConverter("preprocessing").set(engine="none").set_handler(callback)`.
- For ML-bin wrappers, use `.set_preprocess_handler(callback)` on `MLVideoTFLiteBin`, `MLVideoQNNBin`, `MLVideoSNPEBin`, or `MLVideoONNXBin`.
- Callback signature is `(blits, outmlframe) -> bool`.
- For ML-bin wrappers with custom preprocess, set `"preprocess-engine", "none"` before `.set_preprocess_handler(...)`.
- `blits` contains `MLVideoBlit` entries; use `blit.info`, `blit.destination`, and `blit.planes()` only as placeholders unless the request provides exact tensor conversion logic. Do not instruct generated callbacks to call `blit.unmap()` manually; qimsdk unmaps blits after callback return.
- Generate an honest TODO placeholder when the user does not provide tensor layout, scale/letterbox policy, quantization, channel order, and normalization details. Do not invent image-to-tensor conversion math.
- Placeholder preprocess callbacks return `False` because no valid tensor was written. Generated comments must state that after implementing tensor conversion and writing `outmlframe.get_tensor(...)`, the callback must return `True`. README must state that placeholder custom-preprocess artifacts are not functionally runnable for inference until real tensor-write logic is implemented.

## Custom Postprocess Guardrails

- Use custom Python postprocess only when requested or when the user explicitly asks for placeholder postprocess logic.
- Use `MLPostprocess("name").set_handler(callback)` for discrete postprocess stages.
- ML-bin wrappers expose `.set_postprocess_handler(callback, kind=None)` after the bin is constructed and contains an internal `qtimlpostprocess`.
- Callback signatures must be either `(mlframe, mlparams, results)` or `(mlpostprocess, mlframe, mlparams, results)`.
- Annotate the output parameter with a public marker type such as `ObjectDetections`, `Poses`, `Segmentations`, `DepthMaps`, `Tensors`, `ImageClassifications`, or `AudioClassifications`.
- Generated custom postprocess apps must include the explicit `gi.require_version("GstQtiML", "1.0")` import block from `references/ml-postprocess.md` and mention the `GstQtiML-1.0.typelib` runtime dependency in README.
- Functional callbacks must populate marker outputs with concrete `GstQtiML.*` objects (`Detection`, `Pose`, `Keypoint`, `KeypointLink`, `Segmentation`, `DepthMap`, `Classification`) or write `Tensors` outputs as documented in `references/ml-postprocess.md`.
- If the user does not provide tensor decode math, generate an honest placeholder callback with TODO comments. Do not invent decoding, scaling, NMS, keypoint layout, label mapping, or metadata population logic.
- Placeholder postprocess callbacks must not use a bare `return`. They must leave the typed output object valid and return `True`, even if it is empty, so downstream `TextFilter`/`qtimetamux` can receive a buffer and the pipeline can advance past PAUSED. Use `False` only for real validation/error branches. Add a TODO comment beside the stub explaining that decode/population logic is intentionally omitted.

## Reference Routing

| Task | Load these references |
|---|---|
| Any generation, edit, validation, or review | `references/generation-rules.md` |
| API/method/import/style lookup | `references/api-surface.md`, `references/sdk-architecture.md`, `references/pipeline-construction.md` |
| Plugin/property/module/runtime lookup | `references/plugin-catalog.md` |
| Known model filename/display name lookup | `references/model-catalog.md` |
| Source, sink, decode, encode, display, AppSrc/AppSink, queue/tee/filter utility rules | `references/source-sink-patterns.md` |
| Multimedia-only camera/file/encode/display/playback patterns | `references/multimedia-pipeline-patterns.md` |
| AI, ML-bin, daisy-chain, overlay, custom preprocess/postprocess | `references/ai-pipeline-patterns.md`, `references/ml-postprocess.md`, `references/inference-runtimes.md` |
| YAML/config-driven app | `references/pipeline-construction.md`, `references/api-surface.md` |
| Artifact folder/files, README, verification | `references/artifact-contract.md` |
| Grounding a Python app generation in known-good examples | `references/example-retrieval.md` |

## Common Load Sets

- Basic Python artifact: `generation-rules.md`, `example-retrieval.md`, `api-surface.md`, `pipeline-construction.md`, `source-sink-patterns.md`, `plugin-catalog.md`, `artifact-contract.md`.
- Multimedia artifact: `generation-rules.md`, `example-retrieval.md`, `multimedia-pipeline-patterns.md`, `source-sink-patterns.md`, `plugin-catalog.md`, `artifact-contract.md`.
- AI artifact: `generation-rules.md`, `example-retrieval.md`, `ai-pipeline-patterns.md`, `ml-postprocess.md`, `inference-runtimes.md`, `plugin-catalog.md`, `artifact-contract.md`.
- Custom preprocess or postprocess artifact: AI load set plus `api-surface.md` for callback forms and public wrapper/marker types.
- Plugin/API question only: `plugin-catalog.md` or `api-surface.md` as appropriate.

## Verification Script

- `references/verify-python-app.sh` verifies generated Python artifacts.

Run it through the workflow in `references/artifact-contract.md`; do not treat script execution alone as a full contextual review.

## Reference Files

- `references/generation-rules.md` - common generation, clarification, style, placeholder, and comment rules
- `references/api-surface.md` - public qimsdk imports, methods, wrappers, filters, lifecycle APIs
- `references/sdk-architecture.md` - qimsdk source layout and generation implications
- `references/pipeline-construction.md` - explicit and implicit construction patterns
- `references/plugin-catalog.md` - canonical plugin/property/module/runtime catalog
- `references/model-catalog.md` - known model-to-module/delegate/settings/results lookup table
- `references/source-sink-patterns.md` - source/decode/sink patterns plus queue, tee, filters, mux/demux, links, and path utility rules
- `references/multimedia-pipeline-patterns.md` - multimedia-only templates and constraints
- `references/ai-pipeline-patterns.md` - AI/ML-bin/daisy-chain/overlay templates
- `references/ml-postprocess.md` - built-in and custom Python postprocess guidance
- `references/inference-runtimes.md` - inference runtime and delegate property rules
- `references/artifact-contract.md` - artifact layout, README contract, and verification workflow
- `references/example-retrieval.md` - retrieval-grounding: how to run `rank_examples.py`, screen ranked candidates for a genuine fit, and leverage a real matching file as the starting point (falling back to building fresh from the rules only when none fit).
