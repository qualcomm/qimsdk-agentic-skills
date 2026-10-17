# ML Preprocess and Postprocess

## Custom Python Preprocess

Use custom Python preprocess when the user asks for:

- custom preprocessing
- external preprocess
- placeholder preprocess logic
- Python callback image-to-tensor conversion

Use `MLVConverter` for discrete pipelines:

```python
preprocess = (
    MLVConverter("preprocessing")
    .set(engine="none")
    .set_handler(preprocess_callback)
)
```

Use ML-bin wrapper preprocess delegation for ML-bin pipelines:

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set("preprocess-engine", "none")
mlbin.set_preprocess_handler(preprocess_callback)
```

Supported callback signature:

```python
def preprocess_callback(blits, outmlframe) -> bool:
    """Placeholder for custom preprocessing."""
    # TODO: inspect blit.info, blit.destination, and blit.planes().
    # TODO: map pixels into the model input tensor layout.
    # TODO: apply resize/letterbox, channel order, normalization, and quantization.
    # TODO: write the prepared tensor through outmlframe.
    # Keep return False until a valid tensor is written.
    # After writing outmlframe.get_tensor(...), return True so output is emitted.
    return False
```

Rules:

- For discrete `MLVConverter`, always emit explicit `.set(engine="none")` before `.set_handler(...)`. The wrapper also sets this internally, but the reference examples make the external-preprocess takeover explicit.
- For ML-bin wrappers, always set `"preprocess-engine", "none"` before `.set_preprocess_handler(...)`.
- Return `False` in a placeholder that does not write a valid tensor.
- Generated placeholders must include a nearby comment explaining that after the TODO tensor-write logic is implemented successfully, the callback must return `True`.
- Use `MLVideoBlit` facts from `api-surface.md`; do not import from `qimsdk._*`.
- Do not tell generated code to call `blit.unmap()` manually; the qimsdk wrapper unmaps all `MLVideoBlit` entries in a `finally` block after the callback returns. Tell users not to keep plane memoryviews after callback return.
- Do not invent image-to-tensor conversion math, tensor shape, quantization zero-point, normalization constants, or letterbox policy.
- Mention custom preprocess TODOs in README. If the callback is a placeholder that returns `False`, README must state that the artifact is not functionally runnable for inference until real tensor-write logic is implemented.

## Built-In Postprocess

Use built-in `qtimlpostprocess` when the request maps to a documented module:

```python
post = Element("qtimlpostprocess", "postprocess")
post.set("module", "yolov8")
post.set("labels", "<LABELS_PATH>")
```

Use `settings` only when the user asks for config/settings/threshold tuning or provides a settings path.

For confidence threshold tuning, use the canonical JSON key:

```python
post.set("settings", '{"confidence": 50.0}')
```

Do not use `confidence_threshold`, `confidence-threshold`, or semicolon-delimited settings such as `confidence=50;`.

For live camera or RTSP object/face/palm detection pipelines, set `bbox-stabilization` on the postprocess element:

```python
post.set("bbox-stabilization", True)
```

Apply this to `yolov8`, `yolov5`, `yolo-nas`, `qfd`, and `palmd` live-source pipelines. Omit it for file-source pipelines unless the user explicitly requests stabilization.

## Custom Python Postprocess

Use custom Python postprocess when the user asks for:

- custom postprocessing
- external postprocess
- placeholder postprocess logic
- Python callback tensor decoding

Custom postprocess handler registration depends on the `GstQtiML-1.0.typelib` runtime package. Generated custom-postprocess apps must make this dependency explicit before importing qimsdk postprocess wrappers:

```python
import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML
```

If `GstQtiML-1.0.typelib` is missing on the target device, functional custom postprocess apps fail early at import time, and placeholder apps that use `MLPostprocess.set_handler(...)` fail later when qimsdk tries to connect the corresponding `process-*` signal. Mention this dependency in README `Assumptions` or `Steps to Run`.

Use `MLPostprocess`:

```python
post = MLPostprocess("postprocess").set_handler(callback)
```

or ML-bin wrapper handler delegation when using an ML-bin:

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set_postprocess_handler(callback)
```

## `mlframe`/`mlparams` Runtime Objects

Every custom postprocess callback receives `mlframe` and `mlparams` as GObject-introspected SDK runtime objects (`GstMLFrame`/`GstMLParam` from `GstQtiML-1.0`), not plain Python types. Use only the documented accessors below and model-specific keys provided by references or the user — do not invent additional members.

### Reading raw tensor data from `mlframe`

`mlframe.get_tensor(index)` is the documented way to read a model output tensor as a NumPy array. It wraps `gst_ml_frame_get_tensor()` and hands back an array already mapped, shaped, and dtyped per the tensor's real dimensions and `GstMLType` — every bundled reference app (`classification_resnet101`, `detection_yolov5`, `detection_yolov8`, `pose_estimation_hrnet`, `pose_estimation_posenet`, `depth_estimation_midasv2`, `segmentation_deeplab`, `gesture_recognition`, `mlbin_yolov8`) reads its tensors this way, with no manual pointer/size handling:

```python
tensor = mlframe.get_tensor(batch_idx)                              # single-tensor output
tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)      # multi-tensor output
```

This is a real callback-argument method, not something to leave as a `# TODO: read tensor buffer` placeholder — a generated callback that needs raw tensor bytes must call it directly.

Other real `mlframe` accessors seen throughout the bundled reference apps:

- `mlframe.info.n_tensors` — number of tensors in the frame (`GstMLInfo.n_tensors`). Validate this against the model's expected tensor count before indexing, for example `if mlframe.info.n_tensors != EXPECTED_TENSORS: raise KeyError(...)`.
- `mlframe.info.type` — tensor type info, used for logging (`f"tensors={mlframe.info.type}"`).
- `mlframe.buffer` — the underlying `GstBuffer`, needed only to look up per-tensor quantization metadata (below). Do not map/unmap it directly; `get_tensor()` already handles mapping.

Multi-tensor, multi-batch outputs are indexed as `n_tensors * batch_idx + tensor_idx`, matching the per-tensor memory index (`GstMLTensorMeta.id`) inside the buffer.

### Quantization metadata (`GstMLTensorMeta`)

`GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)` returns a `GstMLTensorMeta` (or `None` if the buffer carries no such metadata) with these real fields, from `GstQtiML-1.0.gir` / `gstmlmeta.h`:

| Field | Meaning |
|---|---|
| `id` | memory index inside the `GstBuffer` this meta describes |
| `name` | tensor name (`GQuark`) |
| `type` | `GstMLType` enum (`int8`/`uint8`/`int16`/`uint16`/`int32`/`uint32`/`int64`/`uint64`/`float16`/`float32`) |
| `n_dimensions`, `dimensions` | tensor shape, mirroring `mlframe.info` |
| `qscale` | dequantization scale (`gfloat`) |
| `qoffset` | dequantization offset (`gfloat`) |

Dequantize only when `get_tensor()` returns an integer dtype — the common TFLite/QNN flow already delivers dequantized `float32`:

```python
def dequantize_if_int(arr: np.ndarray, mlframe, index: int) -> np.ndarray:
    if arr.dtype in (np.int8, np.uint8, np.int16, np.uint16, np.int32):
        meta = GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)
        qscale = meta.qscale if meta is not None and hasattr(meta, "qscale") else 1.0
        qoffset = meta.qoffset if meta is not None and hasattr(meta, "qoffset") else 0.0
        return (arr.astype(np.float32) - float(qoffset)) * float(qscale)
    return arr.astype(np.float32)
```

Never hardcode a `qscale`/`qoffset` constant; always read it from the buffer's own metadata for that tensor index — it is per-export.

### `mlparams.get_*()`

`mlparams.get_*()` accessors return `(ok, value)`. Always check `ok` before using `value`:

```python
ok_w, tensor_w = mlparams.get_uint("input-tensor-width")
ok_h, tensor_h = mlparams.get_uint("input-tensor-height")
if not (ok_w and ok_h):
    raise KeyError("Missing input tensor width/height in mlparams")
```

### Writing `Tensors`-kind output

For `Tensors`-kind callbacks, write output through the typed `tensors` argument, not through `mlframe`. When the output tensor is writable and shaped like the decoded array, use `numpy.copyto(...)`:

```python
np.copyto(tensors.get_tensor(0), landmarks_2d)
```

Do not invent `mlframe` / `mlparams` members, tensor metadata keys, or accessors beyond `get_tensor()`, `info.n_tensors`, `info.type`, `buffer`, and `buffer_get_ml_tensor_meta_id()` above. Tensor shapes, box/keypoint/mask layout, and other decode math still must come from the user's model contract or this skill's references — reading the tensor is documented, decoding its contents is model-specific.

## Supported Callback Kinds

| Kind | Signal | Marker annotation |
|---|---|---|
| `image-classification` | `process-image-classification` | `ImageClassifications` |
| `audio-classification` | `process-audio-classification` | `AudioClassifications` |
| `object-detection` | `process-object-detection` | `ObjectDetections` |
| `pose-estimation` | `process-pose-estimation` | `Poses` |
| `depth-estimation` | `process-depth-estimation` | `DepthMaps` |
| `image-segmentation` | `process-segmentation` | `Segmentations` |
| `tensors` | `process-tensors` | `Tensors` |

## Functional Output Construction

Reference apps use the public marker annotation for dispatch, but construct concrete output objects with `GstQtiML`:

| Marker annotation | Concrete output objects / write target |
|---|---|
| `ObjectDetections` | create `GstQtiML.Detection()`, set `left`, `top`, `right`, `bottom`, `confidence`, `name`, `color`, optional `landmarks`, append to `detections` |
| `Poses` | create `GstQtiML.Pose()`, `GstQtiML.Keypoint()`, and optional `GstQtiML.KeypointLink()`, set pose confidence/name/color, keypoints, links, append to `poses` |
| `Segmentations` | create `GstQtiML.Segmentation()`, set `n_rows`, `n_columns`, `labels`, `colors`, append to `segmentations`; segmentation models may also create `GstQtiML.Detection()` objects internally for mask/object association |
| `DepthMaps` | create `GstQtiML.DepthMap()`, set `n_rows`, `n_columns`, `values`, `colors`, append to `depthmaps` |
| `ImageClassifications` | create `GstQtiML.Classification()`, set `confidence`, `name`, `color`, append to `classifications` |
| `AudioClassifications` | no documented Python SDK field mapping in this skill; keep as a TODO placeholder unless a future source refresh documents the concrete audio classification fields |
| `Tensors` | do not create a `GstQtiML.*` object; write into output tensors returned by `tensors.get_tensor(index)` |

Functional callbacks use `mlframe.get_tensor(index)` to read tensors and `GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)` to read tensor quantization metadata — both documented above. Do not invent tensor indexing beyond the documented `n_tensors * batch_idx + tensor_idx` formula, tensor shapes, NMS, keypoint layout, mask layout, label mapping, or tensor-output copy logic when the user did not provide those details.

All successful custom postprocess callbacks return `True`. Use `False` only for a deliberate validation/error branch. Do not return the typed output container object.

## Placeholder Callback Templates

Object detection:

```python
def detection_callback(mlframe, mlparams, detections: ObjectDetections):
    """Placeholder for custom object detection postprocessing."""
    # TODO: read tensors with mlframe.get_tensor(index); dequantize if int (see Quantization metadata above).
    # TODO: decode boxes, class ids, and scores for the selected model.
    # TODO: apply confidence thresholding and NMS.
    # TODO: append GstQtiML.Detection() objects to detections.
    # Empty metadata is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

Pose:

```python
def pose_callback(mlframe, mlparams, poses: Poses):
    """Placeholder for custom pose postprocessing."""
    # TODO: read tensors with mlframe.get_tensor(index); dequantize if int (see Quantization metadata above).
    # TODO: decode keypoints using the selected model's output layout.
    # TODO: scale keypoints back to frame or ROI coordinates.
    # TODO: append GstQtiML.Pose() objects with Keypoint/KeypointLink entries to poses.
    # Empty metadata is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

Segmentation:

```python
def segmentation_callback(mlframe, mlparams, segmentations: Segmentations):
    """Placeholder for custom segmentation postprocessing."""
    # TODO: read tensors with mlframe.get_tensor(index); dequantize if int (see Quantization metadata above).
    # TODO: decode mask tensors and class ids for the selected model.
    # TODO: apply resizing/color mapping if required by the output target.
    # TODO: append GstQtiML.Segmentation() objects to segmentations.
    # Empty metadata is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

Depth estimation:

```python
def depthmap_callback(mlframe, mlparams, depthmaps: DepthMaps):
    """Placeholder for custom depth-map postprocessing."""
    # TODO: read tensors with mlframe.get_tensor(index); dequantize if int (see Quantization metadata above).
    # TODO: decode depth tensor values and color mapping for the selected model.
    # TODO: append GstQtiML.DepthMap() objects to depthmaps.
    # Empty metadata is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

Image classification:

```python
def classification_callback(mlframe, mlparams, classifications: ImageClassifications):
    """Placeholder for custom image classification postprocessing."""
    # TODO: read tensors with mlframe.get_tensor(index); dequantize if int (see Quantization metadata above).
    # TODO: decode logits/probabilities, top-k, labels, confidence, and colors.
    # TODO: append GstQtiML.Classification() objects to classifications.
    # Empty metadata is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

Tensor pass-through:

```python
def tensor_callback(mlframe, mlparams, tensors: Tensors):
    """Placeholder for tensor inspection or dump logic."""
    # TODO: inspect tensors with mlframe.get_tensor(index) / mlframe.info.n_tensors (see Reading raw tensor data above).
    # TODO: write output data into tensors.get_tensor(index) only if the app requires it.
    # Empty/no-op output is valid for a placeholder; return True so downstream flow continues.
    # Use False only for real validation/error branches.
    return True
```

## Rules

- Prefer annotation-based kind inference.
- Pass explicit `kind` only when annotation is not possible.
- Include the `gi.require_version(..., "GstQtiML", "1.0")` import block for generated apps that use `MLPostprocess.set_handler(...)` or ML-bin `.set_postprocess_handler(...)`.
- Do not invent tensor shapes or output decoding.
- Mention custom postprocess TODOs in README.
- Use comments inside placeholders to show exactly what the user must implement.
- Placeholder callbacks must not use bare `return`; return `True` with the typed output object left empty. Use `False` only for a real validation/error branch.
