# Generation Rules

## Scope

Use these rules for any `qimsdk-cpp-app-builder` generation, edit, validation, or review task.

Keep C++ API names, callback signatures, and build details grounded in `api-surface.md`, `pipeline-construction.md`, `ml-and-postprocess.md`, and `artifact-contract.md`.

## Clarification vs Placeholder

Ask the user before generating when missing information changes structure:

- input source type is unclear: file, camera, AppSrc, RTSP, test source
- output target is unclear: display, file, AppSink, metadata-only, RTSP
- inference runtime is unclear: discrete `qtimltflite`, ML-bin, QNN, SNPE, ONNX
- AI stage count is unclear: single-stage, parallel, daisy-chain
- custom preprocess tensor contract is unclear: input planes, tensor shape, scale/letterbox, color order, normalization, quantization
- custom postprocess output type is unclear: detections, poses, tensors, segmentation, depth, classification

Use placeholders when only runtime values are missing:

- `<INPUT_FILE>`
- `<OUTPUT_FILE>`
- `<MODEL_PATH>`
- `<LABELS_PATH>`
- `<SETTINGS_PATH>`
- `<POSTPROCESS_MODULE>`

Do not leave a delegate-options placeholder for standard TFLite HTP/NPU requests. Use `QNNExternalDelegate,backend_type=htp,log_level=(string)1;` unless the user provides exact delegate options.

For confidence threshold tuning, encode `qtimlpostprocess settings` as JSON with the canonical `confidence` key, for example `"{\"confidence\": 50.0}"`. Do not use `confidence_threshold`, `confidence-threshold`, or semicolon-delimited strings.

List every placeholder in `README.md`.

## Known Model Catalog

When the request names a known model display name or `.tflite` filename, load `model-catalog.md` and use the matching row for model-specific facts: postprocess module, delegate, labels/settings expectation, and mandatory `results`.

Preserve user-provided model, labels, settings, and delegate values exactly. If the catalog provides only a filename convention and the user did not provide a path, use the normal placeholder (`<MODEL_PATH>`, `<LABELS_PATH>`, `<SETTINGS_PATH>`) instead of inventing a device path.

For classification models, do not add `qtimlpostprocess settings` or confidence thresholds by default. Use the catalog row to choose `mobilenet` versus `mobilenet-softmax`; if precision or softmax expectation is unclear, ask instead of guessing.

For HRNet/person-pose cascades, settings and `results` are mandatory when the catalog marks them mandatory. Use user-provided values, exact catalog defaults for matched known models, or explicit placeholders; do not omit them as optional tuning.

For face recognition, the safe stage order is `qfd -> lite-3dmm -> qfr`. Do not generate `qfr` directly after face detection. If a face-recognition request is missing required stage inputs, ask for them or use explicit placeholders rather than inventing a standalone `qfr` pipeline.

## Construction Style

Default new apps to explicit object construction:

```cpp
qti::Element source("filesrc", "source");
source.set("location", "<INPUT_FILE>");

qti::Pipeline pipeline("example");
pipeline.add(source);
```

Use fluent factory construction only when the user requests fluent/implicit style or when preserving an existing fluent app.

Do not mix explicit object and fluent factory construction in one generated app unless requested.

## YAML Mode

For requests using YAML pipeline construction, classify the YAML source before generating:

- If the user asks for a YAML-driven app and gives a YAML path, but does not explicitly say the YAML already exists or is externally provided, generate the YAML config file in the artifact.
- If the user explicitly says the YAML already exists, is provided, or should not be generated, generate only the loader app, `CMakeLists.txt`, and README, and include the exact README phrase `External YAML provided by user`.
- When generating YAML, use the SDK schema from `pipeline-construction.md`: top-level `pipeline:`, then `elements:` and `links:`.
- In `elements:`, use `type:` for both normal element factories and stream filters. Do not emit `factory:` anywhere in generated YAML.
- Put element properties directly on the element mapping. Do not emit a nested `properties:` mapping.

## Comments in Generated Examples

Include concise comments for:

- source/decode, preprocessing, inference, postprocess, metadata merge, overlay, and output sections
- non-obvious utility elements such as queue, tee, demux, mux, stream filters, or request-pad-sensitive stages
- placeholders and user-fillable values
- custom preprocess/postprocess TODOs

Avoid comments that restate obvious C++ syntax or every property assignment.

## Defaults

- Include only `<qti/qimsdk.h>` for SDK app generation.
- Set IMSDK logging before constructing `qti::Pipeline`:
  - `qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog)`
  - `qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug)`
- Wrap pipeline construction/execution in `try { ... } catch (const std::exception&)`.
- Use `pipeline.execute()` for default run lifecycle.
- Enable EOS/finalization behavior for mux/file-output flows when supported by the SDK pattern.
- Do not add manual SIGINT handling by default. Do not add CLI parsing unless the user asks for runtime-configurable input. When the user does want the input source (camera number, device path, or file path) to be runtime-configurable, add an opt-in `--input-config` option parsed with `getopt_long` in `main()` (support `-i/--input-config` and `-h/--help`, reject unexpected positional args, and print usage on error), then apply the parsed value to the source element (e.g. `source.set("camera", std::stoi(input_config))` for a camera, or as the `filesrc`/device path). Preserve user-provided fixed values as hardcoded constants instead. Document the option, its default, and validation in the generated README.
- Preserve user-provided paths and property values exactly.
- Prefer placeholders over invented device-specific values when the user omits runtime paths.
- Resolve shell-style path placeholders before assigning them to C++ element properties. C++ string literals do not perform shell expansion: never pass a literal `$HOME/...` value to `filesrc`, inference `model`, postprocess `labels`, settings, or any other filesystem property. Use a small runtime helper around `std::getenv("HOME")` (with an explicit unset/empty check) when the requested path is HOME-relative, and keep the resulting path in the artifact README assumptions. This is a language/API boundary rule, not a filename-specific exception.

## Pipeline Rules

- Keep single-input linear pipelines minimal.
- Add queues for branch decoupling, dynamic-pad demux boundaries, blocking AI branches, mux/encode scheduling, and immediately after every hardware decoder.
- Do not add adjacent queues without a concrete reason.
  The queue immediately after hardware decode is required and is not considered redundant.
- Normalize decoded/camera video to `VideoFilter().format("NV12")` before AI or branching.
- For rendered-video output from `qtimlpostprocess` (segmentation, depth, super-resolution, or another image-output module), negotiate a format the target postprocess src caps actually support. On the device-verified QIM build, use `VideoFilter().format("RGBA")` for the render branch (`qtimlpostprocess` advertises `{RGBA, RGBx}` there); do not copy an older RGB/BGRA caps choice without verifying it against the target. When that branch feeds `qtivcomposer` and the composer pad sets `dimensions`, leave render-filter resolution unfixed so the composer performs the pane sizing.
- For `qticamsrc`/`qtiqmmfsrc` camera input, if the user omits resolution or framerate, use `VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)` and document the `1920x1080 @ 30fps` camera default in README assumptions.
- Add stream filters with `pipeline.add_stream_filter("<name>", filter_obj)`, never with `pipeline.add(...)`.
- Every stream filter name used in `pipeline.link(...)` must match the name passed to `pipeline.add_stream_filter(...)`.
- For metadata overlay with discrete AI elements, keep a main video branch with `tee`, route postprocess metadata through `TextFilter()`, and merge video + metadata with `qtimetamux` before `qtivoverlay`.
- For tee + `qtimetamux` fan-in, each branch link must terminate at `qtimetamux`; link `qtimetamux -> qtivoverlay -> sink` separately after all branch links. For camera + tee pipelines, explicitly link the main source/filter path into the tee before branch links, for example `pipeline.link("source", "cam_vf", "split")`.
- For live camera or RTSP object/face/palm detection postprocess, set `bbox-stabilization=true`; do not add it to file-source pipelines unless requested.
- Insert `qtivtransform` before `qtimetamux` when the same source/tee also feeds `qtivcomposer` (always required), when a sibling branch has `filesink` or `qtimlmetaparser` (required under load), for documented conversion/copy requirements, or for observed `qtimetamux` writable-buffer warnings.
- Each tee branch must have exactly one complete `pipeline.link(...)` chain from the tee to its intended consumer — no more, no fewer. Two failure modes, both device-verified: (a) UNDER-linking — a `tee` exposes one request src pad per branch, and a single chained `pipeline.link("split", "a", "b")` walks only ONE path off the tee, so a second branch needs its OWN `pipeline.link("split", "other_queue")` call; if you forget it, that branch's queue sink stays `NOT_LINKED`, its consumer (e.g. a `qtivcomposer` tile) gets no data, preroll never completes, and the pipeline hangs (or crashes nondeterministically) instead of erroring cleanly. (b) OVER-linking — do not also link the tee directly to a consumer when a queued branch already terminates there; duplicate request-pad links can leave the queue branch `NOT_LINKED` and make downstream failures misleading. Rule of thumb: count the tee's intended branches, emit exactly that many `pipeline.link(...)` calls from the tee name, each terminating at one distinct consumer.
- Every element instance name must be unique across the ENTIRE pipeline, not just within the function/loop that creates it. When two element families are named by numeric index (e.g. per-stream `qtdemux` as `demux_0..demux_(N-1)` and per-group `qtimldemux` as `demux_0..demux_(G-1)`), their index ranges can overlap and collide; `pipeline.add()` → `gst_bin_add()` then fails on the duplicate name and aborts with `Exception: Failed to add external element in the pipeline` before PLAYING. Give each family a distinct prefix (e.g. `demux_` for `qtdemux`, `mldemux_` for `qtimldemux`).
- Select encoder IO modes from the allocation at the encoder input boundary, not merely from the original source type: use `capture/output-io-mode=4/4` for transform-produced or file-style NV12 buffers, and reserve `4/5` for camera-native DMA import or documented AV-record branches.
- Keep README `Text Summary` and `Mermaid Diagram` aligned with the generated code.

## AppSrc/AppSink Callback Rules

- For AppSrc producer callbacks, use `qti::AppSrc::set_buffer_producer(...)`.
- For AppSink consumer callbacks, use `qti::AppSink::set_buffer_consumer(...)`.
- Use `set_preroll_handler(...)`, `set_eos_handler(...)`, and `set_enough_handler(...)` only when those events are requested.
- Do not generate `appsrc.set_handler(...)` or `appsink.set_handler(...)`.
- Use snake_case SDK method names: `push_buffer(...)`, `end_of_stream()`, `set_buffer_producer(...)`, and `set_buffer_consumer(...)`.

## Custom Preprocess and Postprocess Rules

When custom preprocess is requested:

- wire the callback with `qti::MLVConverter::set_handler(...)` or ML-bin `.set_preprocess_handler(...)`
- set `engine="none"` on discrete `qti::MLVConverter` before `.set_handler(...)`
- set `"preprocess-engine", "none"` on ML-bin before `.set_preprocess_handler(...)`
- add TODO comments for source planes, tensor shape, resize/letterbox, channel order, normalization, quantization, and tensor write
- return `false` in placeholders that do not write a valid tensor
- add an inline comment near `return false` explaining that once real tensor conversion writes output tensors, the callback must return `true` so the converter emits output downstream
- do not fabricate image-to-tensor conversion logic

When custom postprocess is requested:

- use the callback signatures in `ml-and-postprocess.md`
- wire discrete callbacks with `qti::MLPostprocess::set_handler(...)`
- wire ML-bin callbacks with `.set_postprocess_handler(...)`
- add TODO comments for model-specific decode logic
- return `true` for a valid empty/populated placeholder result or `false` only for a real error path
- do not fabricate tensor decoding, NMS, label mapping, keypoint layout, ROI mapping, or metadata population
