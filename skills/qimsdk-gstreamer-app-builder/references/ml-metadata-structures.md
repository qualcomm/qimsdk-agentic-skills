# ML Metadata Attached to GstBuffer

## Use This Reference For

- Understanding what metadata structures QIM SDK ML elements actually attach to a `GstBuffer` as it flows through preprocess → inference → postprocess → mux → overlay/tracker stages.
- Understanding why `qtimlmetaextractor`, `qtimetatransform`, and `qtiobjtracker` are placed where they are in a pipeline.

Do not invent additional meta types or fields beyond what is listed here — this file is source-grounded against `gst-plugin-base/gst/ml/` and related plugin sources in `gst-plugins-imsdk-oss`.

## Tensor Metadata — `GstMLTensorMeta`

Attached by inference elements (`qtimltflite`/`qtimlqnn`/`qtimlsnpe`/`qtimlonnx`) to buffers on the `neural-network/tensors` src pad — one instance per output tensor/memory:

```c
struct _GstMLTensorMeta {
  GstMeta   meta;
  guint     id;              /* memory index inside the GstBuffer */
  GQuark    name;             /* tensor name */
  GstMLType type;             /* tensor type */
  guint     n_dimensions;
  guint     dimensions[GST_ML_TENSOR_MAX_DIMS];  /* GST_ML_TENSOR_MAX_DIMS = 8 */
  gfloat    qscale;           /* dequantization scale */
  gfloat    qoffset;          /* dequantization offset */
};
```

- `GstMLType` enum: `GST_ML_TYPE_UNKNOWN`, `_INT8`, `_UINT8`, `_INT16`, `_UINT16`, `_INT32`, `_UINT32`, `_INT64`, `_UINT64`, `_FLOAT16`, `_FLOAT32`. Helpers: `gst_ml_type_get_size()`, `gst_ml_type_from_string()`, `gst_ml_type_to_string()`.
- `GST_ML_MAX_TENSORS = 8` (per buffer), `GST_ML_TENSOR_MAX_DIMS = 8`.
- API: `gst_buffer_add_ml_tensor_meta()`, `gst_buffer_get_ml_tensor_meta()` (returns the lowest-id meta), `gst_buffer_get_ml_tensor_meta_id()` (returns the meta with a specific `id`) — a buffer normally carries multiple `GstMLTensorMeta` entries, one per output tensor. Use `gst_buffer_get_ml_tensor_meta_id()` to read `qscale`/`qoffset` for dequantization in a custom postprocess element.

## Region/Result Metadata (Binary Path)

These are the standard GStreamer video metas that QIM SDK ML postprocess/overlay/composer/tracker elements read and write when operating on binary (non-text) metadata:

- `GstVideoRegionOfInterestMeta` — standard GStreamer ROI meta (region rect + `roi_type` GQuark + a `GstStructure` param list), extended in QIM SDK usage with a `parent_id` linkage field so derived ROIs (e.g. a landmark ROI cropped from a detection ROI) can reference their parent. `parent_id == -1` marks a top-level ROI.
- `GstVideoClassificationMeta` — per-ROI classification labels/confidences.
- `GstVideoLandmarksMeta` — per-ROI keypoint/landmark data (pose, palm, face landmarks).
- `GstVideoOriginMeta` — pre-transform width/height and the crop rectangle applied; attached only by `qtivtransform` on non-passthrough output, used downstream to map coordinates back to the original frame.
- `GstClassLabel` and the `GST_BUFFER_ITERATE_ROI_METAS` macro (`gst/utils/common-utils.h`) — iteration helper for walking all ROI metas on a buffer.

`qtivoverlay` renders bounding boxes/landmarks/classifications automatically from these metas when they are present on the buffer it receives — no separate configuration is needed for AI-metadata overlays (only static overlays go through `qtivoverlay`'s `bboxes`/`strings`/`masks`/`images` properties).

## Text Metadata (Structured Path)

Most QIM SDK sample pipelines carry ML results as serialized `text/x-raw` `GstStructure`-list buffers rather than the binary metas above, muxed onto the main video buffer by `qtimetamux`. Structure names used across the pipeline: `"ObjectDetection"`, `"PoseEstimation"`, `"ImageClassification"`. This is what `qtimlpostprocess` emits on its `text/x-raw`-negotiated src pad, what `qtimetamux` synchronizes onto the video buffer's `data_%u` sink pads, and what `qtiobjtracker`/`qtimetatransform` read and rewrite in place.

- **`qtimlmetaextractor`** converts the *binary* metas (previous section) into this *text* `GstStructure`-list form — the inverse of `qtimetamux` (which deserializes this same text/x-raw GstStructure format back into binary metas), and complementary to `qtimlmetaparser` (which converts the same binary metas to JSON text for external systems rather than reversing them back into binary). Use it when an element upstream only produces binary buffer-attached metas but a downstream stage (`qtimetamux`, `qtiobjtracker`, `qtimetatransform`) expects the text/x-raw convention. **Placement:** after `qtimetamux` (or the upstream element producing binary metas), before networking/file/logging consumers (`qtiredissink`, `qtirtspbin`, `multifilesink`, `qtimlmetaparser`).
- **`qtimetatransform`** dlopen-loads a module (`roi-palmd`, `roi-label-moving-average`) that rewrites this text metadata in place — e.g. deriving a new ROI from a parent ROI, or smoothing label confidence across frames.
- **`qtiobjtracker`** reads the detection/ROI entries out of this text metadata, runs its tracking algorithm (`bytetrack`), and writes updated per-object tracking IDs back into the same metadata stream.