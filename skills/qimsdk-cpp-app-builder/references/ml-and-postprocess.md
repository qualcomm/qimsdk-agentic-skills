# ML Preprocess and Postprocess Patterns

Use this file for C++ AI app generation with IMSDK wrappers and direct plugin elements.

## Direct Plugin AI Path

Common direct path from SDK apps:

- `qtimlvconverter`
- `qtimltflite` or `qtimlqnn`
- `qtimlpostprocess`
- `qtimetamux`
- `qtivoverlay`

For `qtimlpostprocess`, use:

- `"module", "<MODULE>"`
- `"labels", "<LABELS_PATH>"`
- `"settings", "<SETTINGS_JSON_OR_PATH>"`
- `"bbox-stabilization", true` for live camera/RTSP object, face, or palm detection

Rules:

- If prompt says postprocess `config`, map to `settings`.
- Do not invent a `config` property on `qtimlpostprocess`.
- Keep placeholders only for unresolved values.
- For threshold tuning (for example confidence), encode in `settings` JSON using the canonical `confidence` key. Do not use `confidence_threshold`, `confidence-threshold`, or semicolon-delimited strings.
- For live camera or RTSP object/face/palm detection (`yolov8`, `yolov5`, `yolo-nas`, `qfd`, `palmd`), set `"bbox-stabilization", true`. Omit it for file-source pipelines unless the user requests stabilization.

Example:

```cpp
.add("qtimlpostprocess", "post",
     "module", "yolov8",
     "labels", "/etc/labels/yolov8.json",
     "settings", "{\"confidence\": 50.0}")
```

## Delegate and Backend

For TFLite external delegate with HTP backend (direct element flow):

```cpp
.add("qtimltflite", "infer",
     "delegate", "external",
     "external-delegate-path", "libQnnTFLiteDelegate.so",
     "external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
     "model", "<MODEL_PATH>")
```

## ML Bin Wrappers

`MLVideoTFLiteBin`, `MLVideoONNXBin`, `MLVideoQNNBin`, `MLVideoSNPEBin` all derive from `MLPreprocessBase` and `MLPostprocessBase` and share an identical shape, differing only by the wrapped factory (`qtimlvideotflitebin`, `qtimlvideoonnxbin`, `qtimlvideoqnnbin`, `qtimlvideosnpebin`):

```cpp
explicit MLVideoTFLiteBin(const std::string& name = {});
template <typename Value, typename... Rest>
MLVideoTFLiteBin& set(const char* prop, Value&&, Rest&&...);
template <typename Callback>
MLVideoTFLiteBin& set_preprocess_handler(Callback&& handler);
template <typename Callback>
MLVideoTFLiteBin& set_postprocess_handler(Callback&& handler);
```

There are no distinct C++ delegate-enum types per runtime — delegate/backend selection is via plain string property values, using the `inference-*`/`postprocess-*` prefix convention (different from the discrete multi-element `qtimltflite`/`qtimlqnn`/`qtimlsnpe`/`qtimlonnx` + `qtimlpostprocess` chain, which uses bare `delegate`/`model`/`module`/`labels`):

```cpp
qti::MLVideoTFLiteBin mlbin("mlbin");
mlbin.set("inference-model", "<MODEL_PATH>")
     .set("inference-delegate", "external")
     .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
     .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
```

Link ML-bin overlay paths directly:

`source/decode -> VideoFilter(NV12) -> MLVideoTFLiteBin -> qtivoverlay -> display/file`

Direct ML-bin cascades are also valid, for example `source/decode -> VideoFilter(NV12) -> mlbin1 -> mlbin2 -> qtivoverlay -> display`.

Do not adapt ML-bin into `tee -> queue -> MLVideoTFLiteBin -> TextFilter -> qtimetamux`. Use that fan-in overlay topology only with discrete `qtimlvconverter -> qtimltflite -> qtimlpostprocess` AI branches.

Available wrappers in this SDK:

- `qti::MLVideoTFLiteBin`
- `qti::MLVideoQNNBin`
- `qti::MLVideoSNPEBin`
- `qti::MLVideoONNXBin`

### Multi-stage pipelines: prefer daisy-chained mlbins

For a multi-stage/multi-model pipeline, prefer chaining two (or more) fused ML bins directly over the discrete `qtimlvconverter`/`qtimltflite`/`qtimlpostprocess` per-stage form when the topology can stay linear. Set `"preprocess-mode", "roi-batch-cumulative"` on every bin after the first.

Use discrete elements instead of ML-bin when the requested behavior needs branch-level metadata merging: original-video branch preservation, `tee -> queue -> AI branch -> TextFilter -> qtimetamux`, tee-based composer/metamux overlay, custom postprocess callback between stages, or a non-ROI-batch preprocessing mode. In plain terms, "ML-bin style where appropriate" means ML-bin is appropriate for the direct linear PPE chain, but not as the AI branch feeding `TextFilter`/`qtimetamux`.

## External Preprocess Callbacks

Use custom C++ preprocess only when requested. It is application-owned image-to-tensor conversion logic; do not generate fake conversion math.

### Option 1: `MLVConverter` wrapper

- Create `qti::MLVConverter preprocessing("<name>");`
- Set `engine="none"` when using external preprocess.
- Attach callback via `preprocessing.set_handler(callback);`

```cpp
qti::TensorsPreprocessCallback preprocess_callback =
    [](const qti::MLVideoBlits& blits, qti::MLFrame& output) {
      // TODO: inspect blits.entries and output.tensors.
      // TODO: map source image planes into the model input tensor.
      // TODO: apply resize/letterbox, channel order, normalization, and quantization.
      // Keep return false until a valid tensor is written.
      // After writing output tensors successfully, return true so output is emitted.
      return false;
    };

qti::MLVConverter preprocessing("preprocessing");
preprocessing.set("engine", "none");
preprocessing.set_handler(preprocess_callback);
```

### Option 2: ML-bin preprocess callback

- Use `mlbin.set_preprocess_handler(callback);`
- Set `"preprocess-engine", "none"` on the ML-bin when overriding built-in preprocessing.

```cpp
qti::MLVideoTFLiteBin mlbin("mlbin");
mlbin.set("preprocess-engine", "none");
mlbin.set_preprocess_handler(preprocess_callback);
```

Rules:

- Callback shape is `bool(const qti::MLVideoBlits& blits, qti::MLFrame& output)`.
- Return `false` in placeholders that do not write a valid output tensor.
- Generated placeholders must include a nearby comment explaining that after the TODO tensor-write logic is implemented successfully, the callback must return `true`.
- Do not invent tensor dimensions, color conversion, scale policy, quantization, or normalization.
- Mention custom preprocess TODOs in README.

## External Postprocess Callbacks

### Option 1: `MLPostprocess` wrapper

- Create `qti::MLPostprocess post("<name>");`
- Attach callback via `post.set_handler(callback);`

### Option 2: ML-bin callback

- Attach callback directly with `mlbin.set_postprocess_handler(callback);`

Note: `set_handler` is the method on the discrete `MLPostprocess` wrapper; `set_postprocess_handler` is the method on ML bins. These are different methods on different classes — do not mix them.

## `qti::MLPostprocess` / `MLPostprocessBase` — Full Callback API

`MLPostprocessBase` (`virtual public Element`) defines six typed callback aliases, dispatched at compile time by `set_handler`'s overload resolution:

```cpp
using ClassificationPostprocessCallback  = std::function<bool(const MLFrame&, const MLParam&, MLClassifications&)>;
using ObjectDetectionPostprocessCallback = std::function<bool(const MLFrame&, const MLParam&, MLDetections&)>;
using PoseEstimationPostprocessCallback  = std::function<bool(const MLFrame&, const MLParam&, MLPoses&)>;
using DepthEstimationPostprocessCallback = std::function<bool(const MLFrame&, const MLParam&, MLDepthMaps&)>;
using SegmentationPostprocessCallback    = std::function<bool(const MLFrame&, const MLParam&, MLSegmentations&)>;
using TensorsPostprocessCallback         = std::function<bool(const MLFrame&, const MLParam&, MLFrame&)>;
```

## ML Types (`qimsdk-ml-types.h`)

### Input types

```cpp
enum class MLTensorType { Unknown, Int8, UInt8, Int16, UInt16, Int32, UInt32, Int64, UInt64, Float16, Float32 };
struct MLTensor { MLTensorType type; std::vector<uint32_t> dimensions; void* data; size_t size; };
struct MLFrame  { std::vector<MLTensor> tensors; };
struct MLParam  { template <typename T> bool get(const std::string& key, T& out) const; };
```

`MLParam::get<T>` supports `std::string`, `const char*`, `bool`, floating-point, and integral `T`, plus a `Region` specialization that composes `<key>-x`/`<key>-y`/`<key>-width`/`<key>-height` fields into one rect (the key `"input-tensor-region"` is remapped internally to the `"input-region"` prefix). `MLParam::get<T>(key, out)` reads model input-tensor dimensions and ROI region fields for daisy-chained/ROI pipelines.

### Output types

Every result struct carries a common `MLExtraParam extra` field for module-specific extension data:

```cpp
struct MLClassification { std::string name; float confidence; uint32_t color; MLExtraParam extra; };
struct MLDetection      { std::string name; float confidence; uint32_t color;
                           float top, left, bottom, right;
                           std::vector<MLKeypoint> landmarks; MLExtraParam extra; };
struct MLPose           { std::string name; float confidence;
                           std::vector<MLKeypoint> keypoints;
                           std::vector<MLKeypointLink> links; MLExtraParam extra; };
struct MLDepthMap       { std::vector<double> values; std::vector<uint32_t> colors;
                           uint32_t n_rows, n_columns; MLExtraParam extra; };
struct MLSegmentation   { std::vector<std::string> labels; std::vector<uint32_t> colors;
                           uint32_t n_rows, n_columns; MLExtraParam extra; };
using MLClassifications = std::vector<MLClassification>;
using MLDetections      = std::vector<MLDetection>;
using MLPoses           = std::vector<MLPose>;
using MLDepthMaps       = std::vector<MLDepthMap>;
using MLSegmentations   = std::vector<MLSegmentation>;
```

`MLKeypoint`, `MLKeypointLink`, and `MLExtraParam` are opaque support types (per-point coordinate/label data, point-pair link data, and free-form extension key/values respectively) — populate only the top-level fields shown above unless the target model family's postprocess module documents a specific extra-param key to set.

## Callback Signature Rules

Use typedef-compatible callbacks from `qimsdk-postprocess-base.h`:

- `ClassificationPostprocessCallback`
- `ObjectDetectionPostprocessCallback`
- `PoseEstimationPostprocessCallback`
- `DepthEstimationPostprocessCallback`
- `SegmentationPostprocessCallback`
- `TensorsPostprocessCallback`

Example (custom postprocessing — use `MLPostprocess::set_handler` only when the user explicitly asks for custom/external postprocess logic; the tensor decoding logic itself is application code the SDK does not provide):

```cpp
ObjectDetectionPostprocessCallback detection_callback =
    [](const MLFrame& frame, const MLParam& params, MLDetections& detections) {
        // application-authored tensor decoding
        detections.push_back(MLDetection{"person", 0.9f, 0x00FF00FF, 0.1f, 0.1f, 0.5f, 0.5f});
        return true;
    };

MLPostprocess postprocessing("postprocessing");
postprocessing.set_handler(detection_callback);
pipeline.add(postprocessing);
```

Placeholder callback rule:

- If tensor decode math is not provided, generate TODO comments and return `true;` with an empty typed output vector to keep the pipeline moving.
- Use `return false;` only for a real error path or validation branch, such as missing required tensor data.
- Do not generate a callback body that falls through without returning a `bool`.

**Built-in module postprocessing** (default — same `module`/`labels`/`settings` properties as `gst-launch-1.0`; module names per `plugin-catalog.md`):

```cpp
pipeline.add("qtimlpostprocess", "postprocessing",
    "results", 5, "module", "yolov8",
    "labels", "<LABELS_PATH>",
    "settings", "{\"confidence\": 70.0}");
```

## Daisy-Chain Rules

For two-stage direct-element daisy-chain:

- stage-1 converter mode: `image-batch-non-cumulative`
- stage-2 converter mode: `roi-batch-cumulative`
- stage-1 metadata branch should pass through `TextFilter()` before mux
- if request requires original video + both stage outputs, use two mux stages (`metamux_1`, `metamux_2`) with intermediate tee

## Module Selection

- Use module values from `references/plugin-catalog.md`.
- If module cannot be inferred from request and references, keep placeholder (for example `<POSTPROC_MODULE>`).
- Never invent module names.
