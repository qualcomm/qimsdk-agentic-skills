# Generation Rules

## Scope

Use these rules for any `qimsdk-python-app-builder` generation, edit, validation, or review task.

## Clarification vs Placeholder

Ask the user before generating when missing information changes structure:

- input source type is unclear: file, camera, AppSrc, RTSP, test source
- output target is unclear: display, file, AppSink, metadata-only
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

For confidence threshold tuning, encode `qtimlpostprocess settings` as JSON with the canonical `confidence` key, for example `{"confidence": 50.0}`. Do not use `confidence_threshold`, `confidence-threshold`, or semicolon-delimited strings.

List every placeholder in `README.md`.

## Known Model Catalog

When the request names a known model display name or `.tflite` filename, load `model-catalog.md` and use the matching row for model-specific facts: postprocess module, delegate, labels/settings expectation, and mandatory `results`.

Preserve user-provided model, labels, settings, and delegate values exactly. If the catalog provides only a filename convention and the user did not provide a path, use the normal placeholder (`<MODEL_PATH>`, `<LABELS_PATH>`, `<SETTINGS_PATH>`) instead of inventing a device path.

For classification models, do not add `qtimlpostprocess settings` or confidence thresholds by default. Use the catalog row to choose `mobilenet` versus `mobilenet-softmax`; if precision or softmax expectation is unclear, ask instead of guessing.

For HRNet/person-pose cascades, settings and `results` are mandatory when the catalog marks them mandatory. Use user-provided values, exact catalog defaults for matched known models, or explicit placeholders; do not omit them as optional tuning.

HRNet (and any top-down pose model) is ROI-based: it estimates keypoints on a detected **person ROI**, not a full frame. **Always generate it as the two-stage cascade** — stage 1 person/foot detection (`qpd`, `qtimlvconverter mode="image-batch-non-cumulative"`) → `qtimetamux` → stage 2 `hrnet` (`qtimlvconverter mode="roi-batch-cumulative"`). **A request for "single-stage pose" / "pose directly on full frames" (no detection pre-stage) is INVALID for HRNet** — run single-stage it reaches PLAYING and infers but produces no keypoints and nothing overlays. Override such a request and build the cascade anyway (note the correction in the README); do not emit a single-stage HRNet pipeline. (Movenet/single-shot pose models that are genuinely full-frame are the exception — only override for top-down models like HRNet.)

For face recognition, the safe stage order is `qfd -> lite-3dmm -> qfr`. Do not generate `qfr` directly after face detection. If a face-recognition request is missing required stage inputs, ask for them or use explicit placeholders rather than inventing a standalone `qfr` pipeline.

## Construction Style

Default new apps to explicit construction:

```python
source = Element("filesrc", "source")
source.set("location", "<INPUT_FILE>")

pipeline = Pipeline("example")
pipeline.add(source)
```

Use implicit construction only when the user requests it or when preserving an existing implicit app.

Do not mix explicit and implicit construction in one generated app unless requested.

## YAML Mode

For requests using `Pipeline.from_yaml(...)`, classify the YAML source before generating:

- If the user asks for a YAML-driven app and gives a YAML path, but does not explicitly say the YAML already exists or is externally provided, generate the YAML config file in the artifact.
- If the user explicitly says the YAML already exists, is provided, or should not be generated, generate only the loader app and README, and include the exact README phrase `External YAML provided by user`.
- When generating YAML, use the SDK schema from `pipeline-construction.md`: top-level `pipeline:`, then `elements:` and `links:`.
- In `elements:`, use `type:` for both normal element factories and stream filters. Do not emit `factory:` anywhere in generated YAML.
- Put element properties directly on the element mapping. Do not emit a nested `properties:` mapping.

## Comments in Generated Examples

Include concise comments for:

- source/decode, preprocessing, inference, postprocess, metadata merge, overlay, and output sections
- non-obvious utility elements such as queue, tee, demux, mux, `TextFilter()`, or request-pad-sensitive stages
- placeholders and user-fillable values
- custom preprocess/postprocess TODOs

Avoid comments that restate obvious Python syntax or every property assignment.

## Defaults

- Import only public `qimsdk` names.
- Define `main() -> None` for every full generated app.
- Put pipeline construction, element setup, linking, and `pipeline.execute()` inside `main()`.
- End every full generated app with `if __name__ == "__main__": main()`.
- Set qimsdk logging before constructing `Pipeline`.
- Use `pipeline.execute()` for default run lifecycle.
- Use `pipeline.eos(True)` for mux/file-output flows that must finalize containers.
- Use `try/except RuntimeError` only when the user asks for app-level error handling.
- Do not add manual SIGINT handling unless requested.
- Do not add CLI argument parsing by default. When the user wants the input source (camera number, device path, or file path) to be runtime-configurable, add an opt-in `--input-config` option parsed with `argparse` inside `main()` — use an `argparse.ArgumentParser` subclass that prints usage on error (`HelpOnErrorArgumentParser`), keep the parser call in `main()` (never at module top level), then pass the parsed value into `create_and_execute_pipeline(...)` and apply it to the source (e.g. `source.set("camera", int(input_config))` for a camera, or as the `filesrc`/device path). Preserve user-provided fixed values as direct constants instead. Document the option, its default, and validation in the generated README.
- Preserve user-provided paths and property values exactly.
- Prefer `$HOME`-relative paths only when the user did not provide explicit absolute paths — and **always expand `$HOME` in Python before passing the value to an element property.** GStreamer element properties do NOT perform shell expansion: a literal `"$HOME/..."` string reaches `filesrc location`/model/labels verbatim and the file open fails (`Could not open '$HOME/...'`). Use `f"{os.environ['HOME']}/..."`, not `os.path.expandvars("$HOME/...")` — `expandvars` silently returns the string unexpanded (no exception) when `HOME` is unset, which reintroduces the same unresolved-`$HOME` bug at runtime instead of failing loudly; `os.environ['HOME']` raises `KeyError` immediately if `HOME` is unset. (This is the single most common generation bug — every path constant fed to an element must be expanded.)

## Pipeline Rules

- Keep single-input linear pipelines minimal.
- Add queues for branch decoupling, dynamic-pad demux boundaries, blocking AI branches, and immediately after every hardware decoder.
- Do not add adjacent queues without a concrete reason.
  The queue immediately after hardware decode is required and is not considered redundant.
- Normalize decoded/camera video to `VideoFilter().format("NV12")` before AI or branching.
- For `qticamsrc`/`qtiqmmfsrc` camera input, if the user omits resolution or framerate, use `VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)` and document the `1920x1080 @ 30fps` camera default in README assumptions.
- Use `TextFilter()` before `qtimetamux` for Python metadata text branches.
- For live camera or RTSP object/face/palm detection postprocess, set `bbox-stabilization=True`; do not add it to file-source pipelines unless requested.
- Insert `qtivtransform` before `qtimetamux` when the same source/tee also feeds `qtivcomposer` (always required), when a sibling branch has `filesink` or `qtimlmetaparser` (required under load), for documented conversion/copy requirements, or for observed `qtimetamux` writable-buffer warnings.
- Keep README `Text Summary` and `Mermaid Diagram` aligned with the generated code.

## AppSrc/AppSink Callback Rules

- For AppSrc producer callbacks, use `AppSrc.set_buffer_producer(...)` by default.
- If the request explicitly asks for raw `need-data` signal handling, use `appsrc.get_raw().connect("need-data", handler)`.
- For AppSink consumer callbacks, use `AppSink.set_buffer_consumer(...)`.
- Use `set_preroll_handler(...)`, `set_eos_handler(...)`, and `set_enough_handler(...)` only when those events are requested.
- Do not generate `appsrc.set_handler(...)` or `appsink.set_handler(...)`.

## Custom Preprocess and Postprocess Rules

When custom preprocess is requested:

- wire the callback with `MLVConverter.set(engine="none").set_handler(...)` or ML-bin `.set("preprocess-engine", "none").set_preprocess_handler(...)`
- add TODO comments for source planes, tensor shape, resize/letterbox, channel order, normalization, quantization, and tensor write
- return `False` in placeholders that do not write a valid tensor
- add an inline comment near `return False` explaining that once real tensor conversion writes `outmlframe.get_tensor(...)`, the callback must return `True` so the converter emits output downstream
- do not fabricate image-to-tensor conversion logic
- do not tell users to call `blit.unmap()` manually; qimsdk unmaps blits after callback return
- document in README that placeholder custom-preprocess artifacts are not functionally runnable for inference until real tensor-write logic is implemented

When custom postprocess is requested:

- generate a callback with a valid marker annotation
- wire it with `MLPostprocess.set_handler(callback)` for discrete pipelines or ML-bin `.set_postprocess_handler(callback)`
- include the explicit `gi.require_version("GstQtiML", "1.0")` import block from `ml-postprocess.md`
- document the `GstQtiML-1.0.typelib` target-device dependency in README
- add TODO comments for model-specific decode logic
- do not fabricate tensor decoding, NMS, label mapping, keypoint layout, ROI mapping, or metadata population
- document the TODOs in `README.md`
- return `True` from successful placeholder callbacks with empty outputs; add an inline comment saying empty metadata is valid and keeps `TextFilter`/`qtimetamux` moving. Use `False` only for real validation/error branches
