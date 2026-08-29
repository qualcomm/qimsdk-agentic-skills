---
name: qimsdk-cpp-app-builder
description: QIM SDK C++ app development using only APIs and patterns defined by this skill bundle. Use when building or updating C++ applications with qti::Pipeline, qti::Element, qti::AppSrc/AppSink, qti::CamSrc, qti::MLVConverter, qti::MLPostprocess, qti::MLVideo*Bin, stream filters, YAML pipeline config mode, and QIM SDK logging.
---

# QIM SDK C++ App Builder Skill

Use this skill when the user asks for C++ application development with the QIM SDK C++ API described by this skill bundle.

## Hard Rules

- Use only APIs, examples, and docs defined in this skill folder and its `references/` files.
- Always read the minimal matching references before generating output.
- Always load `references/plugin-catalog.md` before generating pipeline/app code.
- Always load `references/generation-rules.md` before generating, editing, validating, or reviewing app artifacts.
- Treat `references/plugin-catalog.md` as the authoritative structured plugin catalog:
  - plugin name
  - one-line description
  - key properties
  - pad/caps notes
- Use `references/plugin-catalog.md` for `qtimlpostprocess` module selection.
- If the request names a known model display name or `.tflite` filename, load `references/model-catalog.md` before selecting postprocess module, delegate, settings, labels, or mandatory results. Catalog model facts override generic heuristics; user-provided paths and values still win.
- Always load `references/sdk-architecture.md` before generating app structure.
- Always load `references/artifact-contract.md` before generating deliverable files.
- Do not rely on or cite other repos/sources.
- Do not switch to `gst-launch-1.0` output unless user explicitly asks.
- Do not invent model paths, label paths, settings values, backend configurations, plugin names, or undocumented properties. Use user-provided values as-is; when missing, use explicit placeholders.
- Match requested scope exactly. Do not add daisy-chaining, multistream composition, YAML/config mode, callback-heavy scaffolding, or manual lifecycle control unless the user explicitly asks.
- If the user asks for a simple single-stream app, return a simple single-stream app and nothing more complex.
- For AI pipelines, follow stage order: source -> preprocess -> inference -> postprocess -> metadata/use.
- For overlay pipelines, keep metadata aligned with the main video path using `tee`/`queue` and `qtimetamux` before `qtivoverlay`.
- Prefer strongly-typed IMSDK wrappers (`qti::Pipeline`, `qti::Element`, `qti::AppSrc`, `qti::AppSink`, `qti::CamSrc`, `qti::MLVConverter`, `qti::MLPostprocess`, `qti::MLVideo*Bin`) when applicable.
- Keep generated C++ syntax aligned with the SDK patterns documented in this skill bundle:
  - default to explicit object style: `Element elem("factory", "name"); elem.set(...); pipeline.add(elem);`
  - use fluent factory style (`Pipeline(...).add("factory", "name", ...).link(...).execute()`) only when user explicitly asks for fluent/implicit style or when preserving an existing fluent app
  - do not mix fluent-factory and wrapper-object construction styles in the same generated app unless user explicitly requests mixed style
- Use exact API names from the SDK headers; do not invent methods or callback signatures.
- If a plugin property/value is not confirmed in SDK headers/apps/docs, keep it as an explicit placeholder.
- Do not hardcode element instance names; use user-provided names when available, otherwise use neutral placeholders and keep names consistent across `add(...)`, `get(...)`, and `link(...)`.
- For element retrieval, use `pipeline.get("name")` for generic `qti::Element`; use `get<T>` only for wrappers that support typed retrieval in this SDK.
- Use `pipeline.execute()` as the default run pattern for generated apps; use `start()/wait()/stop()` only when the user explicitly asks for staged lifecycle control.
- If the user provides concrete values (for example module names, file paths, labels, settings, thresholds), carry them into generated code directly.
- Use placeholders only for fields the user did not provide and that cannot be confirmed from SDK references.
- For `qtimlpostprocess`, use SDK property keys directly (`module`, `labels`, `settings`). `settings` is optional by default; include it when user provides postprocess config/settings or threshold-style tuning. Keep placeholders only for unresolved values.
- Treat user terms `postprocess config`, `postprocess settings`, and `settings` as the same intent and map to `qtimlpostprocess` property `settings`.
- For `qtimlpostprocess module`, infer from `references/plugin-catalog.md`; if unresolved, use a placeholder and never invent module names.
- For person-foot requests, choose module `qpd` when request/model/labels/config paths indicate person-foot intent (for example `person_foot` or `foot_track_net`) unless user explicitly overrides module.
- For PPE equipment/object detection and `gear_guard_net` paths, choose module `yolov8` unless user explicitly overrides module.
- For YOLOX detection, choose module `yolov8` unless user explicitly overrides module.
- For threshold-style tuning (for example confidence), encode it as a JSON string in `settings` with the canonical `confidence` key (for example `"{\"confidence\": 50.0}"`). Do not use `confidence_threshold` or semicolon-delimited settings.
- For live camera or RTSP object/face/palm detection postprocess (`yolov8`, `yolov5`, `yolo-nas`, `qfd`, `palmd`), set `bbox-stabilization=true`; omit it for file-source pipelines unless user requests stabilization.
- When user asks for backend-specific delegate setup, apply the SDK-documented delegate configuration for the requested flow.
- For `qtimltflite`, valid delegate options are `gpu` and `external`; when using `external` for HTP/NPU, set `external-delegate-path="libQnnTFLiteDelegate.so"` and `external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` unless the user provides exact delegate options.
- For `qtimlvideotflitebin`, valid `inference-delegate` options are `gpu` and `external`; when using `external` for HTP/NPU, set `inference-external-delegate-path="libQnnTFLiteDelegate.so"` and `inference-external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` unless the user provides exact delegate options.
- For secondary ROI inference stages where the user asks for high-performance HTP/NPU daisy-chain behavior, or for high-concurrency parallel HTP/NPU workloads, use `QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;`. For multi-batch HTP/NPU walls, use the named topology section and round-robin batch groups with `htp_device_id` when multiple HTP devices are present.
- Never emit `QNNExecurorBackend:HTP`, `QNNExecutorBackend:HTP`, `QNNExternalDelegateBackend:HTP`, or colon-separated QNN delegate option strings.
- For ML-bin style (`qtimlvideotflitebin` / `qti::MLVideo*Bin`) requests, follow the documented ML-bin property syntax:
  - inference keys: `inference-delegate`, `inference-external-delegate-path`, `inference-external-delegate-options`, `inference-model`
  - postprocess keys: `postprocess-module`, `postprocess-labels` (and other `postprocess-*` keys when requested)
- For ML-bin overlay, keep ML-bin stages directly in the media path: `source/decode -> VideoFilter(NV12) -> MLVideo*Bin -> qtivoverlay -> sink`, or direct cascades such as `mlbin1 -> mlbin2 -> qtivoverlay -> sink`.
- For PPE or other multi-stage ML-bin requests, use ML-bin for the documented direct fused topology (`source/decode -> mlbin1 -> mlbin2 -> overlay/display`). Do not adapt ML-bin into an external metadata branch such as `tee -> queue -> mlbin -> TextFilter -> qtimetamux`; if the request needs original-video branch preservation or tee/metadata fan-in overlay, use discrete `qtimlvconverter -> qtimltflite -> qtimlpostprocess` stages on the AI branch.
- For custom C++ preprocess, use `qti::MLVConverter::set_handler(...)` for discrete pipelines or `qti::MLVideo*Bin::set_preprocess_handler(...)` for ML-bin pipelines. Set `engine="none"` on discrete `MLVConverter` and `"preprocess-engine", "none"` on ML-bin before registering the handler. Do not use postprocess callback APIs for preprocessing.
- For custom C++ postprocess placeholders, callback bodies must return a deliberate `bool`: `return true;` for a valid empty/populated result or `return false;` only for a real error path. Do not generate placeholder callbacks that fall through without returning.
- Default CMake pattern should link `qimsdk-app-builder` directly (SDK app style) and should not require `pkg-config` unless user explicitly asks.
- Use the CMake template from `references/artifact-contract.md` (`set(TEST_TARGET ...)`, `add_executable`, `target_link_libraries(... qimsdk-app-builder)`, and `install(...)`) unless the user requests a different build style.
- For ROI-based secondary inference and postprocess tuning, follow SDK-documented configuration and keep unresolved details as placeholders.
- For two-stage daisy-chain requests, preserve stage-wise structure (stage-1 preprocess/infer/postprocess + stage-2 ROI preprocess/infer/postprocess + metadata merge path).
- For two-stage daisy-chain requests, set stage-1 `qtimlvconverter` mode to `image-batch-non-cumulative` and stage-2 `qtimlvconverter` mode to `roi-batch-cumulative`.
- For gesture-recognition chains (palm detection -> hand landmark -> gesture embedding -> gesture classification), do not use a generic N-stage mux cascade. Use the documented gesture topology: exactly two `qtimetamux` stages, `qtimetatransform module="roi-palmd"` between the first mux and the second split, stage-2 inference output split into an `hlandmark` metadata branch and a `tensor -> embedder inference -> classifier inference -> mobilenet` classification branch, then merge both into the second mux before `qtivoverlay`.
- For tee + `qtimetamux` fan-in, prefer SDK-style `pipeline.link(...)` by element names; do not hardcode `sink_0`/`sink_1` pad names unless explicitly confirmed.
- For tee + `qtimetamux` fan-in links, terminate each branch at the mux, then link `metamux -> overlay/display` separately. Do not fold downstream `qtivoverlay` or sink elements into the AI branch link call.
- For MP4 file-input flows, default to `filesrc -> qtdemux -> h264parse/h265parse -> v4l2h264dec/v4l2h265dec -> queue`; add a queue after `qtdemux` only when decoupling/robustness is explicitly needed.
- **C++ string literals never expand shell variables.** A literal `"$HOME/..."` string passed to any filesystem-bearing property (`filesrc.location`, `qtimltflite.model`, `qtimlpostprocess.labels`/`settings`, `filesink.location`) is passed byte-for-byte to the OS `open()` call — the app fails at runtime with `Could not open '$HOME/...'` / `Failed to load model file`, not at compile time. Always resolve `HOME`-relative paths in C++ itself before assigning them, with an explicit unset/empty check (do not silently fall back to `""` and construct an invalid relative path):
  ```cpp
  std::string expand_home(const std::string& suffix) {
    const char* home = std::getenv("HOME");
    if (home == nullptr || *home == '\0') {
      throw std::runtime_error("HOME is not set; cannot resolve filesystem path: " + suffix);
    }
    return std::string(home) + suffix;
  }
  ```
  This is a language/API-boundary rule, not an app-specific exception — apply it to every filesystem property in every generated app, regardless of topology.
- **Each `tee` branch gets exactly one complete `pipeline.link(...)` chain to its consumer.** Do not also link the tee directly to a downstream element (e.g. a mux) when a separate queued chain already terminates at that same element — GStreamer request pads are allocated per `link()` call, so the direct link and the queued link each claim their own pad, leaving the queued branch's pad `NOT_LINKED` while the direct one silently "works." This produces confusing downstream symptoms (encoder `Invalid argument`, `Device or resource busy`) that look unrelated to linking. Before adding a link, check whether that consumer already has a queued chain from the same tee.
- When a source's demuxed branches run concurrently (e.g. an MP4 with both an H.264 video track and a FLAC/AAC audio track feeding independent downstream paths), give each dynamic `qtdemux` pad — and any intermediate decode boundary within a branch (e.g. after `flacdec`, before `audioconvert`) — its own `queue` immediately after the pad. One branch's preroll/scheduling can otherwise block the other's, and the pipeline hangs before `PLAYING` with no obvious error. Reserve the minimal direct `qtdemux -> parser` hop for genuinely single-stream video paths.
- For `qticamsrc`/`qtiqmmfsrc` camera inputs, set `camera=0` by default unless the user explicitly requests another camera ID.
- For `qticamsrc`/`qtiqmmfsrc` camera inputs, if the user does not provide resolution or framerate, constrain the camera stream with `VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)` before display, branching, or AI preprocessing, and call out `1920x1080 @ 30fps` as an assumed camera default in the artifact README.
- For H.264 decode stages, set `v4l2h264dec` IO modes to `capture-io-mode=4` and `output-io-mode=4` unless the user explicitly requests a different documented mode.
- Select encoder IO modes (`v4l2h264enc`) from what actually allocates the buffer arriving at the encoder's input, not from the pipeline's original source type in isolation: use `capture-io-mode=4 output-io-mode=4` when the encoder's input is driver-managed/transform-produced NV12 (file/RTSP source decoded through the hardware decoder, or any AppSrc-fed/copied buffer), and reserve `output-io-mode=5` (dmabuf-import) for camera-native DMA import or a documented AV-record dual-input-mux branch. Getting this backwards on a file-source or AppSrc branch produces device-side encoder errors (`Invalid argument`, `Device or resource busy`) that look unrelated to IO mode. See `references/source-sink-patterns.md` "File Recording".
- Place a `queue` immediately after every hardware decoder before any filter, tee, AI, display, or mux stage.
- Normalize source/decode output to `video/x-raw,format=NV12` before branching (`tee`) or AI preprocessing unless the user explicitly requests a different documented source format.
- Add `VideoFilter`, `TextFilter`, `TensorFilter`, `ImageFilter`, `H264Filter`, `AudioFilter`, and other `StreamFilter` objects with `pipeline.add_stream_filter("<name>", filter_obj)`, never with `pipeline.add(...)`.
- Every stream filter name used in `pipeline.link(...)` must exactly match the string passed to `pipeline.add_stream_filter(...)`; C++ SDK `add_stream_filter` requires a unique name plus filter instance.
- For metadata overlay with discrete AI elements, keep a main video branch with `tee`, route postprocess metadata through `TextFilter()`, and merge video + metadata with `qtimetamux` before `qtivoverlay`.
- For segmentation, rendered-mask, alpha-blend, side-by-side, or other video-output postprocess branches, use direct-to-composer topology: passthrough branch plus AI/render branch into `qtivcomposer`. Do not force rendered video through `TextFilter`/`qtimetamux`.
- The render `VideoFilter` after `qtimlpostprocess` MUST be `.format("RGBA")` (never `BGRA`/`RGB` — device src caps are `video/x-raw,{RGBA,RGBx}`; anything else fails to link). Do NOT pin `.resolution()` on it when a downstream `qtivcomposer` sizes the tile — device-verified, it fails caps fixation with `Fixated width in filter caps is not supported with current post-process type!` regardless of format; let the composer sink-pad `dimensions` size it. See `references/plugin-catalog.md` "Module Output Types".
- For super-resolution, audio AI, AI wall, batched multi-stream inference, face recognition, or AI metadata parsing requests, use the named topology sections in `references/pipeline-construction.md`; do not reduce these to the generic single-stream or two-stage templates.
- For `qtimetamux` writability, do not assume camera buffers are inherently non-writable; add `qtivtransform` before `qtimetamux` when the same source/tee also feeds `qtivcomposer` (always required — it holds buffers for stream sync), when a sibling branch has `filesink` or `qtimlmetaparser` (required under load — these are fast but can still hold the buffer), when a documented source/format path requires conversion/copy, or when runtime logs show the metadata attach warning.
- A leaf `appsink` (frame/metadata tap) sharing a tee with a `qtimlvconverter` branch poisons the DMA pool (`Buffer does not have FD memory` → 0 inference); insert `qtivtransform` before that `appsink` to isolate its allocation. Same buffer-ownership family as the `qtimetamux` writability rule. See `references/source-sink-patterns.md`.
- Buffer writability is a shared-tee invariant, not just a `qtimetamux`/composer special case: whenever more than one buffer-mutating consumer (in-place overlay, composer, appsink tap) reads off the same `tee` pad, only one of them can draw/consume in place — the rest silently do nothing (no error, no crash). In a parallel-branch wall where N sibling AI branches each overlay their own result off one shared `tee`, give every branch's passthrough leg its own `qtivtransform ! VideoFilter(NV12)` copy before its `qtimetamux`, or only one branch's overlay will render. See `references/pipeline-construction.md` "Mixed AI Wall".
- Resolve unresolved model/labels/settings paths against the closest known-good reference (working Python cache app, or the original GStreamer C sample) before falling back to a bare placeholder — an app that reaches `PLAYING` with a literal placeholder string (e.g. `<MODEL_PATH_STAGE1_QPD>`) is not runnable and will fail with `Failed to load model file` the moment it is executed; prefer a concrete, catalog/reference-backed value whenever one is resolvable.
- A `qtivcomposer`'s configured `input(N)` pad geometry must exactly match the number of branches actually linked into it at the time of generation/edit — this is an invariant to re-derive after every topology change, not a fixed count to carry forward. A stale `input(N)` call for a pad no longer linked throws `Port: cannot resolve target pad` before `PLAYING`. This commonly happens when a branch that used to feed the composer as two raw pads (passthrough + rendered mask) is refactored to pre-compose those two into one finished tile on a local `qtivcomposer` first — the top-level composer then needs one fewer configured pad. See `references/pipeline-construction.md` "Mixed AI Wall".
- HRNet and other documented top-down pose models can run either directly on full frames (simpler, one stage) or on a detector-cropped ROI (an extra stage, sharper keypoints when the subject is small/distant). Build the topology the request actually describes — do not silently drop or silently add the stage-1 detector. If the request is ambiguous about whether ROI cropping is wanted, prefer the full-frame single-stage form as the default (fewer moving parts) and ask only if accuracy on small/distant subjects is explicitly a concern. When building the two-stage cascade, use stage-1 person/foot detection with `image-batch-non-cumulative`, merge its metadata, then stage-2 `roi-batch-cumulative` HRNet with a second metadata merge before overlay; settings and results are mandatory for both stages in that case. `lite-3dmm` (face-recognition stage 2) needs `/etc/data/{blendShape,meanFace,shapeBasis}.bin` on the device — note that prerequisite in the README. See `references/model-catalog.md`.
- For two-stage discrete daisy-chain AI with overlay of both stage outputs, do not generate a single linear `postprocess -> TextFilter -> qtimetamux -> qtivoverlay` chain. Use two tee/mux stages: split before stage 1, merge stage-1 metadata with `qtimetamux`, split the merged stream before stage 2, merge stage-2 metadata with a second `qtimetamux`, then overlay.
- For display, default `waylandsink fullscreen=true sync=true` for every source type, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`) — camera source type alone is not a reason to use `sync=false`. Use `sync=false` only for: (1) more than 8 independent concurrently active input streams AND a processing-heavy topology (shared/batched inference, or a large composer grid where HTP/batch preroll makes frames arrive well behind their PTS) — a 4-stream AI wall, a simple multi-stream playback grid, or a batch group of 8 or fewer streams stays `sync=true`; (2) display sharing a tee/composer source with a parallel encode/file/metadata sink that can stall the display clock (stream-count-independent, pair with `enable-last-sample=false` when it is also a multi-sink camera pipeline); (3) the user explicitly requests lower latency over A/V sync. Audio-classification display pipelines omit the `sync` property entirely. See `references/pipeline-construction.md` "Display Sink Sync Policy" for the full canonical statement.
- For any app that uses `pulsesrc` or `pulsesink`, include the PulseAudio prerequisite in the response and in README `Steps to Run on QLI`: `wpctl status`, then `wpctl set-default <node_no.>`. Do not invent a default node number; `<node_no.>` is device-specific.
- For single-input pipelines, use minimal queue placement; avoid inserting queue between every stage unless needed for tee branches or explicit decoupling.
- Reject redundant queues (for example adjacent queue stages with no clear reason).
- Default runtime pattern is `pipeline.execute()` unless the user explicitly asks for manual lifecycle.
- Artifact folder names generated by this skill must start with `qimsdk-cpp-`.
- Generated C++ app names must use `qimsdk-cpp-<appname>` prefix (for example pipeline name and CMake target/binary name unless user explicitly overrides).
- Folder name and app/binary name may be the same value.
- Generated examples should include concise comments for major pipeline sections, placeholders, custom preprocess/postprocess TODOs, and non-obvious SDK requirements. Do not comment every obvious line.
- For C++ AppSrc/AppSink callbacks, use `set_buffer_producer(...)`, `set_buffer_consumer(...)`, `set_preroll_handler(...)`, `set_eos_handler(...)`, and `set_enough_handler(...)`; do not use generic `set_handler(...)` or old camelCase names. For raw element GObject signals not covered by a typed wrapper, use `qti::Element::connect_signal(...)` / `disconnect_signal(...)`; do not use them to replace the AppSrc/AppSink handler callbacks.
- For event-triggered recording (a resident recorder pipeline gated by a metadata condition on another pipeline), never call `Pipeline::start()`/`stop()`/`eos()` from inside the gating AppSink's callback — synchronous state transitions there can deadlock the producer/display pipeline. Keep the recorder resident and `PLAYING` from before the gate can fire; have the callback only flip an in-memory boolean. Forward the gated AppSink's buffer to the recorder's AppSrc with `push_buffer(std::move(buffer))` — never allocate a fresh `qti::Buffer` and `memcpy` into it, which breaks the hardware encoder's expected DMA-backed buffer contract and can crash it (`SIGSEGV`) on the first gated frame. See `references/pipeline-construction.md` "Event-triggered recording variant".

## Custom Preprocess Guardrails

- Use custom C++ preprocess only when requested or when the user explicitly asks for placeholder preprocess logic.
- For discrete pipelines, use `qti::MLVConverter preprocessing("preprocessing"); preprocessing.set("engine", "none"); preprocessing.set_handler(callback);`.
- For ML-bin wrappers, set `"preprocess-engine", "none"` before `.set_preprocess_handler(callback)` on `qti::MLVideoTFLiteBin`, `qti::MLVideoQNNBin`, `qti::MLVideoSNPEBin`, or `qti::MLVideoONNXBin`.
- Callback type is compatible with `qti::TensorsPreprocessCallback`: `bool(const qti::MLVideoBlits& blits, qti::MLFrame& output)`.
- Generate an honest TODO placeholder when the user does not provide tensor layout, scale/letterbox policy, quantization, channel order, and normalization details. Do not invent image-to-tensor conversion math.
- Placeholder callbacks return `false` until a valid tensor is written. Generated comments must state that after implementing tensor conversion and writing output tensors, the callback must return `true`.

## Clarification vs Placeholder Rules

**Ask the user upfront** when missing info changes topology or element selection:
- input source type is missing (file vs camera vs RTSP)
- output target is missing (display vs file vs appsink vs mixed)
- ML stage count is unclear (single-stage vs daisy-chain)
- backend family is unclear (`qtimltflite` vs ML-bin wrappers)
- custom preprocess tensor contract is unclear and the user expects working conversion logic rather than a placeholder

**Use placeholders** when only runtime values are missing:
- `<INPUT_FILE>`, `<MODEL_PATH>`, `<LABELS_PATH>`, `<SETTINGS_PATH>`, `<OUTPUT_FILE>`
- `<POSTPROC_MODULE>` when module cannot be inferred from references

After generation, list all placeholders in README so none are missed.

## Generation Workflow

1. Classify request: basic media app, camera app, AI app, external preprocess app, external postprocess app, or YAML-config app.
2. Before generating anything, load `references/artifact-contract.md` and `references/plugin-catalog.md`, then only matching references from this skill.
   Also load `references/generation-rules.md` for shared generation, placeholder, YAML, comments, and pipeline rules.
   If the request names a known model display name or `.tflite` filename, also load `references/model-catalog.md`.
3. For any C++ app generation request, load `references/example-retrieval.md` and run `rank_examples.py` to ground the draft in known-good examples — this adds context alongside the rest of this workflow, it does not replace step 2's reference loading or steps 4-5. **Finding a retrieval match does not skip or shorten any subsequent step — all of steps 4-5 run unconditionally regardless of what retrieval returns or whether a file was leveraged.** Skip this step for plugin-only lookups.
4. Generate filesystem artifacts (folder + source + README).
5. Include `Pipeline Flow` with `Text Summary` and `Mermaid Diagram` subsections, plus `Steps to Run on QLI`, in README from actual code wiring.
6. Validate API usage against `references/api-surface.md` and `references/sdk-architecture.md`.

## Request Matching

- **General C++ IMSDK app** → `references/api-surface.md` + `references/sdk-architecture.md` + `references/pipeline-construction.md` + `references/source-sink-patterns.md`
- **C++ qimsdk SDK app request** (user asks for a C++ application using the qimsdk C++ API, `qti::Pipeline`/`qti::Element`/`qti::CamSrc` classes) → load `references/api-surface.md` + `references/pipeline-construction.md` + `references/source-sink-patterns.md` plus the pipeline-topology references that match the request type. Do not load `references/c-app-*.md` for this output type.
- **AppSrc/AppSink bridging** → also load `references/pipeline-construction.md`
- **Camera capture/control** → also load `references/pipeline-construction.md` + `references/source-sink-patterns.md`
- **File/RTSP source or file/RTSP output request** → also load `references/source-sink-patterns.md`
- **AI inferencing (TFLite/QNN/SNPE/ONNX bin)** → also load `references/ml-and-postprocess.md`
- **Known model filename/display name** → also load `references/model-catalog.md`
- **Delegate/backend-specific inferencing request** → also load `references/inference-runtimes.md`
- **External C++ preprocess callback** → also load `references/ml-and-postprocess.md`
- **External C++ postprocess callback** → also load `references/ml-and-postprocess.md`
- **Custom/external postprocess handler request (C++ `MLPostprocess::set_handler`, app-authored tensor decoding)** → load `references/ml-and-postprocess.md`
- **YAML config constructor mode** → also load `references/pipeline-construction.md`
- **Build/run deliverable request** → also load `references/artifact-contract.md`
- **Any C++ app generation request** (before drafting, after classification) → also load `references/example-retrieval.md` and run `rank_examples.py` to ground the draft in known-good examples

## Output Contract

- Generate artifacts by default, not copy-paste-only responses.
- Never return only inline snippets when runnable artifacts are requested.
- Use `references/plugin-catalog.md` as the single plugin allow-list and plugin-facts source; do not maintain separate inline plugin lists in this file.
- Whenever generated code is changed (`main.cc`, `main.cpp`, `*.cc`, `*.cpp`, or `CMakeLists.txt`), update the corresponding artifact `README.md` in the same folder in the same pass so flow, placeholders, and run/build steps remain accurate.
- Artifact folder name must use prefix `qimsdk-cpp-` (prepend it if user-provided name does not include it).
- App name in generated code/build outputs must use `qimsdk-cpp-<appname>` prefix by default.
- Folder name and app/binary name can be set to the same prefixed string.
- Required folder layout for every request:
  - `<artifact-name>/main.cc`
  - `<artifact-name>/CMakeLists.txt`
  - `<artifact-name>/README.md`
- YAML config constructor requests also include a YAML config file unless the user explicitly says the YAML is already provided externally and should not be generated.
- README must include:
  - purpose and assumptions
  - `Pipeline Flow` mapped from actual code, with `Text Summary` and `Mermaid Diagram` subsections
  - required placeholders to fill
  - `Steps to Compile`: exactly the line `Yocto: https://imsdkdocs.qualcomm.com/advanced/yocto-build#steps-to-build-custom-application` — no other compile instructions
  - `Steps to Run on QLI`

## Completion Checklist

1. Uses only IMSDK C++ APIs present in `include/qti/*.h`.
2. Pipeline lifecycle is coherent (`start/wait/stop` or `execute`).
3. Element names in `.add(...)` and `.link(...)` are consistent.
4. Any callback signatures match SDK typedefs.
5. README `Text Summary` and `Mermaid Diagram` match actual code paths.
6. README includes `Steps to Compile` (the Yocto build link only) and clear `Steps to Run on QLI`.
7. No references to sources outside this skill folder.
8. User-specified values are preserved in code instead of being replaced with placeholders.
9. Display output defaults to `waylandsink fullscreen=true sync=true` for every source type including live camera sources; `sync=false` is used only for the three documented exceptions (>8-stream processing-heavy topology, multi-sink clock-stall, or explicit user low-latency request), and audio-classification pipelines omit `sync` entirely.
10. Queue usage is minimal and justified for single-input flows.
11. No invented plugin names or invented `qtimlpostprocess` module names.
12. If C++ source/build files were changed, `README.md` in the same artifact folder was updated to match.
13. Custom preprocess placeholders are explicit about missing image-to-tensor conversion logic.
32. Every generated app calls `qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog)` and `qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug)` at the top of `main()`, before constructing any `Pipeline`, unless user explicitly requests a different logging mode or verbosity; wrap pipeline construction/execution in `try { ... } catch (const std::exception&)`
33. Includes only `<qti/qimsdk.h>` — never the individual `qimsdk-*.h` headers directly, and never mixes in raw `gst_element_factory_make`/`gst_sample_apps_utils.h` C-app scaffolding
34. `CMakeLists.txt` links only against the `qimsdk-app-builder` target (`target_link_libraries(<target> PRIVATE qimsdk-app-builder)`) — does not additionally link GStreamer or QTI ML/video-base libraries directly
35. `qti::` classes/methods/property-prefixes used match `references/api-surface.md` exactly (e.g. `set_postprocess_handler` on ML bins vs. `set_handler` on discrete `MLPostprocess`) — do not invent methods or mix bin-style and discrete-style property names on the same element
36. No SIGINT/Ctrl+C handling is assumed from the SDK — the SDK provides none; only hand-write it if the user explicitly asks for it. CLI arg-parsing is also not emitted by default; add an opt-in `--input-config` `getopt_long` parser (in `main()`, README-documented) only when the user asks for runtime-configurable input

## Reference Files

- `references/plugin-catalog.md` — plugin allow-list and `qtimlpostprocess` module-selection guidance
- `references/model-catalog.md` — known model-to-module/delegate/settings/results lookup table
- `references/generation-rules.md` — shared C++ generation defaults, placeholders, comments, YAML mode, and pipeline rules
- `references/api-surface.md` — API inventory and valid method names
- `references/sdk-architecture.md` — `include/` and `src/` structure and generation implications
- `references/pipeline-construction.md` — fluent C++ construction patterns from SDK apps
- `references/ml-and-postprocess.md` — ML bin usage and external preprocess/postprocess callbacks
- `references/inference-runtimes.md` — inference runtime elements, delegate/backend property mapping, and C++ SDK equivalents
- `references/source-sink-patterns.md` — source and sink selection patterns
- `references/multimedia-pipeline-patterns.md` — multimedia pipeline patterns
- `references/artifact-contract.md` — artifact folder/readme output contract for this skill
- `references/verify-cpp-app.sh` — generated C++ artifact verifier
- `references/example-retrieval.md` — retrieval-grounding: how to run `rank_examples.py`, screen ranked candidates for a genuine fit, and leverage a real matching file as the starting point (falling back to building fresh from the rules only when none fit).
