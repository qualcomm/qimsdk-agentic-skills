# Plugin Catalog

## Purpose

Canonical plugin, runtime, property, pad/caps, and postprocess module catalog.

## Load When

Load when selecting plugins, checking property names, choosing inference runtimes, or resolving qtimlpostprocess modules.

## This File Owns

- Allowed plugin names and plugin facts
- Key properties, enum nicks, pads, and caps
- Inference runtime element facts
- qtimlpostprocess module names and module selection hints
- Intrinsic plugin constraints

## This File Does Not Own

- Full pipeline topology; use ai-pipeline-patterns.md or multimedia-pipeline-patterns.md
- Queue/tee placement policy; use pipeline-utilities.md
- Source/sink chain selection; use source-sink-patterns.md

---

## Plugin Inventory

# Plugin Inventory (Do Not Hallucinate)

## Purpose

Use this file as the hard plugin allow-list for generating:

- `gst-launch-1.0` pipelines
- GStreamer C apps

This catalog is organized to reduce invention risk:
- **Primary entries**: plugin facts fully captured in this inventory
- **Extended entries**: allowed plugins with conservative property/caps detail

For each plugin include:
- plugin name
- one-line purpose
- key properties (documented/source-confirmed)
- pad/caps notes

If exact property/caps values are unclear, keep placeholders and do not invent values.

### Refreshed Source Scope And Intentional Exclusions

The refreshed `gstreamer/` source tree contains more factories than this app-builder
skill should generate directly. The catalog is intentionally scoped to elements used
by the supported gst-launch and native-C artifact patterns, not every factory built by
the source tree.

The following source factories are implementation-only, legacy, or specialized
sample-app components and are therefore not generation allow-list entries here unless
a future request adds a verified pattern for them:

- Camera/reprocessing and Codec2 internals: qticamimgreproc, qticamreproc,
  qtic2venc, qtic2vdec, qtic2aenc, qtic2adec
- Specialized CV/media utilities: qticvimgpyramid, qticvoptclflow, qtidfs,
  qtidngpacker, qtidrmdecryptor, qtiheifmux, qtihexagon, qtijpegpacker,
  qtimlaic
- Deprecated video postprocess wrappers: qtimlvclassification,
  qtimlvdetection, qtimlvpose, qtimlvsegmentation,
  qtimlvsuperresolution; use `qtimlpostprocess` for new pipelines
- Legacy overlay wrapper: qtioverlay; use `qtivoverlay` for supported overlay
  pipelines

This exclusion is based on the refreshed source implementation, source readmes, and
checked-in sample code. It is not based solely on the older documentation sandbox,
which may lag the source. Do not infer that an excluded factory is unavailable on a
target; it means the skill lacks a verified, supported generation pattern for it.

---

## A) Primary Plugin Catalog

These entries are the primary plugin facts to use for generation.

### AI / ML / Postprocess

| Plugin | One-line description | Key properties | Pads / caps notes |
|---|---|---|---|
| `qtimlaconverter` | Converts a raw audio stream into a neural-network tensor stream, applying a selectable preprocessing feature such as spectrogram, mel-filterbank, or MFCC extraction. | `feature` (enum `GstMLAudioFeatureMode`: `raw`/`spectrogram`/`mfe`/`lmfe`/`mfcc`, default `raw`), `sample-rate` (integer, range -2147483648 to 2147483647, default 16000), `params` (string, GstStructure-format preprocessor configuration, default `"params;"`, e.g. `params="params,nfft=512,nhop=5,nmels=80;"`), `qos` (boolean, default false), `name` (string, default `"mlaudioconverter0"`), `parent` (GstObject). | Sink (`sink`, always): `audio/x-raw` formats { S32LE, U32LE, S16LE, U16LE, S8, U8, F32LE }, rate [1, 2147483647], channels 1, layout interleaved. Src (`src`, always): `neural-network/tensors` type { FLOAT32 }. Output tensor dimensions depend on the selected `feature`: raw mode yields a single-dimensional tensor sized by `sample-rate`, while mfe/lmfe modes yield a multi-dimensional [batch, channels, windows, mels] shape derived from the `nhop`/`nmels`/`chunklen` fields in `params`. |
| `qtimldemux` | Demultiplexes batched neural-network tensor output into per-input tensor streams for independent downstream processing. | No element-specific properties; only standard GObject `name` (string, default `mldemux0`) and `parent` properties are exposed. | Sink (`sink`, always): `neural-network/tensors` with type { INT8, UINT8, INT32, UINT32, FLOAT16, FLOAT32 }. Src (`src_%u`, on request): same `neural-network/tensors` caps. Typically paired with `qtibatch`, which aggregates multiple input streams before batched inference, while `qtimldemux` splits the batched output tensors back into per-stream results downstream of the inference element (e.g. `qtimltflite`), restoring the mapping between each result and its originating stream before per-stream post-processing (e.g. `qtimlpostprocess`). |
| `qtimlmetaextractor` | Extracts ML inference metadata (object detection, pose/landmarks, and image classification) attached to video buffers and serializes it into a text stream of GstStructures. | No element properties (only standard `name`, `parent`, and `qos` base properties). | Sink (`sink`, always): `video/x-raw(ANY)`, accepting any raw video format carrying attached ROI/landmarks/classification meta. Src (`src`, always): `text/x-raw, format=(string)utf8`. Operates as a 1:1 GstBaseTransform filter that reads GstVideoRegionOfInterestMeta (object detection), GstVideoLandmarksMeta (pose estimation), and GstVideoClassificationMeta (image classification) from each input buffer, groups entries by parent-id, and appends a serialized GstValueList of structures (`ObjectDetection`, `PoseEstimation`, `ImageClassification`) as a UTF-8 text buffer on the src pad; if no supported meta is present it still emits an empty `ObjectDetection` structure. Advertises GstVideoMeta allocation support to allow DMA-buf backed upstream buffers without extra copies, and is GAP-aware. |
| `qtimlmetaparser` | qtimlmetaparser is a filter element that dynamically loads a parser module to serialize ML inference metadata attached to input buffers into UTF-8 text output. | `module` (enum `GstMLParserModules`: `none`/`json`, default `none`) selects the runtime parsing module, `module-params` (string, default `"params;"`) passes module-specific parameters as a GstStructure string (e.g. the JSON module's `attach-frame` boolean, default `false`, which Base64-encodes a JPEG input frame into the output when enabled), `qos` (boolean, default `false`). | Sink (`sink`, always): `image/jpeg`, `video/x-raw(ANY)`, `text/x-raw` format `utf8`. Src (`src`, always): `text/x-raw` format `utf8`. Accepts JPEG, raw video, or UTF-8 text input and always outputs UTF-8 text carrying serialized ML metadata (e.g. JSON produced by the `json` module). **`module=json` output schema (device-verified):** a UTF-8 JSON object keyed by task, e.g. `{"object_detection": [ {"label": "person", "confidence": 83.1, "color": 16711935, "rectangle": {"x": 0.28, "y": 0.34, "width": 0.06, "height": 0.41}}, ... ]}`. Top-level key is `object_detection` (detection task); each entry's class-name key is `label` (lowercase), with `confidence`, `color`, and a normalized `rectangle`. This is a DIFFERENT serialization from the GstStructure text form (`qtimlpostprocess` emits directly, structure names `ObjectDetection`/`bounding-boxes`, parsed via `gst_value_deserialize` — see the appsink examples in `c-app-development.md`) and from `qtimlmetaextractor`'s output; use the `object_detection`/`label` keys ONLY when consuming `qtimlmetaparser module=json`. |
| `qtimlonnx` | qtimlonnx executes ONNX models via the ONNX Runtime as the inference stage in a tensor-mode GStreamer pipeline, accepting preformatted input tensors and producing output tensors matching the model signature. | `model` (string, required, construct-only, path to `.onnx` file, default NULL), `execution-provider` (enum: `cpu`/`qnn`, default `cpu`), `backend-path` (string, construct-only, default NULL, absolute path to QNN backend library such as `libQnnHtp.so`, used only when `execution-provider=qnn`), `htp-performance-mode` (enum: `default`/`burst`/`balanced`/`low-balanced`/`high-performance`/`extreme-power`/`low-power`/`sustained-high-performance`, default `default`, applicable only when `execution-provider=qnn`), `optimization-level` (enum: `disable-all`/`enable-basic`/`enable-extended`/`enable-all`, default `enable-extended`), `threads` (unsigned integer, construct-only, default 1, range 1-16, intra-op threads mostly affecting CPU execution) | Sink (`sink`, always): `neural-network/tensors` format { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Src (`src`, always): same `neural-network/tensors` formats. Single sink pad accepts single- or multi-input tensor sets; single src pad emits single- or multi-output tensor sets. Operates purely on tensors with no preprocessing, reshaping, batching, or postprocessing; typically preceded by `qtimlvconverter`/`qtibatch` and followed by `qtimlpostprocess`. Auto-extracts scale/zero-point from `QuantizeLinear` nodes to dequantize quantized outputs to FLOAT32 when downstream negotiation requires it. Auto-detects NCHW/NHWC layout for 4-D outputs via `Transpose` node inspection and adds `layout=nchw` to src caps when applicable (defaults to NCHW if no Transpose found). GAP-aware: skips inference and forwards GAP-flagged buffers unchanged. |
| `qtimlpostprocess` | Parses raw inference output tensors using a loadable post-processing module and emits ML metadata as text, an image mask, or tensors. | `module` (enum `GstMLPostProcessModules`, default `0`/"none", mandatory; nicks include `ssd-mobilenet`, `mobilenet`, `mobilenet-softmax`, `srnet`, `east-textdt`, `easy-ocr-detector`, `qfd`, `hlandmark`, `mediapipe-pose-landmark`, `palmd`, `yolov8`, `yolov8-seg`, `ocr`, `ocr-recognizer`, `qpd`, `posenet`, `deeplab-argmax`, `yamnet`, `yolo-nas`, `deepbox-3d`, `deepbox-yolo`, `hrnet`, `tensor`, `mediapipe-pose`, `wave2vec`, `qfr`, `qfr-softmax`, `lite-3dmm`, `midas-v2`, `yolov5`, each entry documenting its supported tensor shapes/dtypes), `labels` (string, default null, path to label file in JSON or newline-separated format), `settings` (string, default null, JSON string or path to JSON file with module-specific config e.g. confidence threshold), `results` (unsigned integer, range 0-50, default 5, caps number of output results by discarding lowest-confidence entries), `bbox-stabilization` (boolean, default false, reduces bounding-box jitter across frames), `qos` (boolean, default false), `name` (string, default `mlpostprocess0`), `parent` (object of type `GstObject`). | Sink (`sink`, always): `neural-network/tensors`. Src (`src`, always): `video/x-raw` formats { BGRA, RGBA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, BGR16 } for image-mask output, `text/x-raw` format { utf8 } for serialized ML metadata text, or `neural-network/tensors` for tensor passthrough between chained models; only one src pad, so output format is fixed per element instance and selected via caps negotiation or an explicit capsfilter downstream. Post-processing module shared libraries must be deployed under /usr/lib/gstreamer-1.0/ml/modules and follow the naming convention libml-postprocess-<module-name>.so; the element auto-detects modules present at that path and the module enum list above reflects what is currently deployed on the device. Text output is typically muxed via qtimetamux, image-mask output is composited with qtivcomposer, and tensor output is used to bridge daisy-chained inference stages. |
| `qtimlqairt` | Executes QAIRT/SNPE model containers or cached context binaries as an inference stage, consuming and producing tensor buffers. Use only when the user explicitly requests QAIRT or provides QAIRT/SNPE model evidence; do not default to it for generic AI requests. | `model` (string, required, path to `.dlc` QAIRT/SNPE container or cached context `.bin`), `backend` (string, backend shared-library name — nicks `libQairtHtp.so`/`libQairtGpu.so`/`libQairtCpu.so`, default `libQairtCpu.so`, required for GPU/HTP; replaces the removed `delegate` enum), `priority` (enum nicks `normal`/`low`/`normal-high`/`high`/`critical`), `layers` (array of strings, output layer filter), `tensors` (array of strings, output tensor filter), `qos` (boolean). | Sink/src (`sink`/`src`, always): `neural-network/tensors` with standard tensor types { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Same pipeline position as the other inference elements: `qtimlvconverter -> qtimlqairt -> qtimlpostprocess`. |
| `qtimlqnn` | A GstBaseTransform inference element that executes neural network models on the Qualcomm AI Engine Direct (QNN) runtime, consuming and producing tensor buffers. | `backend` (string, default `/usr/lib/libQnnCpu.so`, selects QNN execution backend library e.g. libQnnHtp.so for NPU, libQnnGpu.so for GPU), `backend-device-id` (uint, range 0-1, default 0, selects hardware instance for multi-device DSP/HTP backends), `model` (string, default null, required path to a `.so` model or `.bin` cached binary file), `system` (string, default `/usr/lib/libQnnSystem.so`, path to QNN system library), `tensors` (GstValueArray of gchararray, default `< >`, list of output tensor names to emit; when empty all model outputs are emitted), `qos` (boolean, default false, handle Quality-of-Service events), `name` (string, default `mlqnn0`), `parent` (GstObject). | Sink (`sink`, always): `neural-network/tensors` with type in { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Src (`src`, always): same `neural-network/tensors` caps and type set. Single sink and single src pad; multi-input and multi-output models are supported by delivering/emitting all tensors together as a tensor set on the one pad. Input tensors must be fully prepared (layout, shape, dtype) by an upstream element such as qtimlvconverter before reaching this element; it performs no preprocessing, reshaping, batching, or postprocessing itself. Uses DMA-backed buffers via GstMLBufferPool for zero-copy transport. GAP-aware: buffers flagged GST_BUFFER_FLAG_GAP skip inference and are forwarded downstream unchanged, preserving timing for cascaded pipelines. Available only in the qcom-multimedia-proprietary-image build. |
| `qtimlsnpe` | Executes a neural network model packaged as an SNPE DLC file, running inference on CPU, DSP (Hexagon), GPU (Adreno), or AIP and emitting output tensors matching the model's declared signature. | `model` (string, default NULL, path to the SNPE DLC model file), `delegate` (enum `GstMLSnpeDelegate`: `none`/`dsp`/`gpu`/`aip`, default `none`), `performance-profile` (enum `GstMLSnpePerformanceProfile`: `default`/`balanced`/`high-performance`/`power-saver`/`system-settings`/`sustained-high-performance`/`burst`/`low-power-saver`/`high-power-saver`/`low-balanced`, default `default`), `priority` (enum `GstMLSnpeExecutionPriority`: `normal`/`high`/`low`, default `normal`), `profiling-level` (enum `GstMLSnpeProfilingLevel`: `off`/`basic`/`detailed`/`moderate`, default `off`), `layers` (array of strings, default empty, list of output layer names, mutually exclusive with `tensors`), `tensors` (array of strings, default empty, list of output tensor names in emission order, mutually exclusive with `layers`), `qos` (boolean, default false) | Sink (`sink`, always): `neural-network/tensors` with type list { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Src (`src`, always): same `neural-network/tensors` type list. Single sink pad accepts pre-formatted input tensors (typically produced by qtimlvconverter) and supports both single-input and batch-input models delivered in one buffer; the element does not reshape or reinterpret input tensors. Single src pad emits all output tensors together, including batch outputs; if the model's native output type is not FLOAT32, output caps include a type list [FLOAT32, native] to enable downstream dequantization negotiation. GAP-aware: buffers marked GST_BUFFER_FLAG_GAP skip inference and are forwarded downstream unchanged. Uses GstMLBufferPool/DMA buffers and SNPE user buffer mode to minimize copies. Set only one of `layers` or `tensors`; whichever is set last takes effect. |
| `qtimltflite` | Executes a TensorFlow Lite model as the inference stage of a pipeline, accepting preformatted input tensors and emitting output tensors according to the model's signature. | `model` (string, default null, required - path to .tflite model), `delegate` (enum `GstMLTFLiteDelegate`: `none`/`gpu`/`xnnpack`/`external`, default `none`), `external-delegate-path` (string, default null, used when `delegate=external`), `external-delegate-options` (GstStructure boxed pointer / delegate init options, used when `delegate=external`), `priority` (enum `GstMLTFLitePriority`: `min-latency`/`max-precision`, default `min-latency`, gpu delegate precision only), `threads` (uint, range 1-4, default 1), `qos` (boolean, default false) | Sink (`sink`, always): `neural-network/tensors` with type { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Src (`src`, always): same `neural-network/tensors` caps and type set. Single sink/src pad each support single- or multi-tensor (multi-input/multi-output) models delivered as a tensor set; does not preprocess, reshape, batch, or postprocess tensors itself. GAP-aware: buffers marked GST_BUFFER_FLAG_GAP skip inference and are forwarded downstream unchanged. Can dequantize quantized (UINT8/INT8) outputs to FLOAT32 using model quantization metadata when downstream negotiation requires FLOAT32. Typically placed downstream of qtimlvconverter/qtibatch and upstream of qtimlpostprocess. |
| `qtimlvconverter` | Converts video frame buffers into normalized neural-network tensors, handling cropping/ROI selection, aspect-ratio-aware resizing, format conversion, batching, and per-channel mean/sigma normalization for ML inference. | `engine` (enum `GstVideoConverterBackend`: `none`/`gles`, default `gles`), `image-disposition` (enum `GstMLVideoDisposition`: `top-left`/`centre`/`stretch`/`centre-crop`, default `top-left`), `mode` (enum `GstMLVideoConversionMode`: `image-batch-non-cumulative`/`image-batch-cumulative`/`roi-batch-non-cumulative`/`roi-batch-cumulative`, default `image-batch-non-cumulative`), `mean` (GstValueArray of gdouble, per-channel mean subtraction for FLOAT tensors, default empty), `sigma` (GstValueArray of gdouble, per-channel divisor for FLOAT tensors, default empty), `subpixel-layout` (enum `GstMLVideoPixelLayout`: `regular`/`reverse`, default `regular`), `qos` (boolean, default false), `name` (string, default `mlvideoconverter0`), `parent` (object of type `GstObject`). | Sink (`sink`, always): `video/x-raw` formats { RGBA, BGRA, ABGR, ARGB, RGBx, BGRx, xRGB, xBGR, BGR, RGB, GRAY8, NV12, NV21, YUY2, UYVY, NV12_Q08C }, width/height [1, 2147483647], framerate [0/1, 2147483647/1]. Src (`src`, always): `neural-network/tensors` with type { INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT16, FLOAT32 }. Consumes GstVideoRegionOfInterestMeta for ROI-based cropping/batching when `mode` is set to a roi-batch variant; supports multi-GstMemory batched buffers (e.g. from qtibatch) producing HWC/CHW/NHWC/NCHW/NDHWC/NDCHW tensor layouts; attaches GstProtectionMeta describing per-frame preprocessing placement/batch-sequence details consumed by downstream postprocess elements. When multiple blits are produced or custom `mean`/`sigma` preprocessing is requested, keep the engine path enabled (`engine=gles`) unless the user explicitly implements a custom process path. |
| `qtiobjtracker` | Tracks detected objects across consecutive frames, assigning persistent track IDs via a pluggable tracking backend selected at runtime. | `algo` (enum `GstObjTrackerBackend`: `bytetrack`, default `0`/"bytetrack"), `name` (string, default `"objtracker0"`), `parameters` (string, default `null`, GstStructure-format parameters for the chosen tracking algorithm; applicable only for some algorithms), `parent` (object of type `GstObject`), `qos` (boolean, default `false`). | Sink (`sink`, always): `video/x-raw(ANY)` and `text/x-raw` format `utf8`. Src (`src`, always): same caps as sink. Operates purely on detection metadata (structured `text/x-raw` payloads or `GstROIMeta` attached to video buffers), not on pixel data; must be placed downstream of elements that generate object detections (e.g. after `qtimetamux`/ML postprocess stage). Output format always matches input format (text stays text, ROI stays ROI); adds a single persistent track ID per detected object while passing through all other detection attributes unchanged. |

### Metadata / Messaging / Transport

| Plugin | One-line description | Key properties | Pads / caps notes |
|---|---|---|---|
| `qtibatch` | Aggregates buffers from multiple parallel streams or successive buffers from a single stream into a single batched output buffer for efficient downstream processing. | `moving-window-size` (uint, default 1, range 1-16, changeable only in NULL/READY) - number of buffers reused from the previous batch in sequential batching; 0 gives non-overlapping batches, greater than 0 gives overlapping/sliding-window batches. | Sink (`sink_%u`, on request): `video/x-raw` and `audio/x-raw`, format ANY. Src (`src`, always): `video/x-raw` and `audio/x-raw`, format ANY, carrying batched buffers. Supports multi-stream (parallel, time-aligned across sink pads with GAP insertion on missing input) and single-stream (sequential, count-based) batching modes; does not copy payloads, only references input buffers and attaches batch metadata; performs no pixel/sample modification, inference, or pre/post-processing. |
| `qtimetamux` | Synchronizes AI/CV post-processing results (bounding boxes, labels, segmentation masks, motion vectors, etc.) with the original audio/video buffer and attaches them as GstMeta. | `latency` (uint64, default 0, range 0-18446744073709551615, additional latency in nanoseconds allowing more time for upstream to produce metadata entries; changeable only in NULL or READY state), `mode` (enum `GstMetaMuxMode`: `async`/`sync`, default `async`; `async` holds a media buffer until metadata entries arrive on all data pads with no timestamp sync, `sync` holds a buffer up to 1/framerate (video) or 1/rate (audio) waiting for metadata with matching timestamps; changeable only in NULL or READY state), `queue-size` (uint, default 10, range 3-4294967295, size of internal input and output queues; changeable only in NULL or READY state), `name` (string, default "metamux0"), `parent` (GstObject) | Sink (`sink`, always): `image/jpeg(ANY)`, `video/x-raw(ANY)`, `audio/x-raw(ANY)` — the single main media pad. Sink (`data_%u`, on request): `text/x-raw` format utf8, `cv/x-optical-flow` — auxiliary pads for ML metadata or motion-vector/optical-flow data. Src (`src`, always): `image/jpeg(ANY)`, `video/x-raw(ANY)`, `audio/x-raw(ANY)`. Typically placed after a `tee` splitting the raw video from an inference branch (e.g. qtimlvconverter -> qtimltflite -> qtimlpostprocess) so the postprocess text/x-raw output feeds a `data_%u` pad while the raw frame feeds `sink`; output metadata can be consumed by qtivoverlay for rendering or by qtimlmetaparser for JSON serialization, and can be daisy-chained into further inference stages. |
| `qtimetatransform` | In-place transform element that filters/processes/converts metadata attached to video buffers using a selectable backend module. | `module` (enum `GstMetaTranformModules`, default `none` (0)): `none` (0, no module/invalid), `roi-palmd`, `roi-label-moving-average`, `roi-pose`, `roi-deepbox`, `roi-auto-framing`, `roi-person-merge`; `module-params` (string, default NULL) - module-specific parameters as a GstStructure string, can be a literal string or a file path to deserialize; `qos` (boolean, default false). | Sink (`sink`, always): `video/x-raw(ANY)`. Src (`src`, always): `video/x-raw(ANY)`. Operates in-place and is GAP-aware (passes through zero-size GAP buffers untouched); the `module` property must be explicitly set to a value other than `none` or negotiation fails with a "Module name not set" error. |
| `qtimsgpub` | A GstBaseSink-derived publisher element that transmits pipeline buffer data or a literal message string to an external MQTT or Kafka message broker on a configured topic. | `protocol` (string, default NULL, e.g. `mqtt`/`kafka`), `host` (string, default NULL), `port` (integer, range 0-2147483647, default 1883), `topic` (string, default NULL, changeable in NULL/READY/PAUSED/PLAYING), `config` (string, default NULL, absolute path to protocol config file), `message` (string, default NULL, changeable in NULL/READY/PAUSED/PLAYING), `json` (boolean, default false, converts buffer data to JSON before publishing), `async` (boolean, default true), `blocksize` (uint, range 0-4294967295, default 4096), `enable-last-sample` (boolean, default true), `last-sample` (boxed GstSample, readable), `max-bitrate` (uint64, default 0), `max-lateness` (int64, range -1 to 9223372036854775807, default -1), `processing-deadline` (uint64, default 20000000), `qos` (boolean, default false), `render-delay` (uint64, default 0), `stats` (boxed GstStructure, readable: average-rate/dropped/rendered), `sync` (boolean, default true), `throttle-time` (uint64, default 0), `ts-offset` (int64, default 0). Signal: `add-publish` (gboolean return; args: topic string, message string) for publishing an additional topic/message at runtime. | Sink (`sink`, always): caps `ANY` (format-agnostic; the element publishes raw buffer bytes or a literal message string rather than negotiating a specific media type). No src pad (pure sink element). Placement: typically terminates a pipeline after processing/postprocess elements, e.g. after `qtimlpostprocess` with `json=true` to publish AI metadata; requires a valid `protocol`/`host`/`port`/`topic`/`config` set to connect to an MQTT (libmosquitto) or Kafka (librdkafka) broker at runtime via dynamically loaded protocol adaptor libraries. |
| `qtimsgsub` | A source element that subscribes to a topic on an external message broker (MQTT or Kafka) and injects received messages into the pipeline as buffers. | `protocol` (string, default NULL, e.g. "mqtt" or "kafka"), `host` (string, default NULL, broker IP address), `port` (integer, range 0-2147483647, default 1883), `topic` (string, default NULL, changeable in NULL/READY/PAUSED/PLAYING states, topic to subscribe to), `config` (string, default NULL, absolute path to protocol config file), `automatic-eos` (boolean, default true), `blocksize` (unsigned integer, range 0-4294967295, default 4096), `do-timestamp` (boolean, default false), `num-buffers` (integer, range -1 to 2147483647, default -1), `typefind` (boolean, default false, deprecated/non-functional) | Src (`src`, always): `ANY` caps. No sink pad; this is a GstBaseSrc-derived source element with SOURCE element flag. Received broker messages are wrapped into GstBuffers and pushed downstream (e.g. to `filesink` or metadata-consuming elements); the `topic`, `host`, `port`, `protocol`, and `config` properties must all be set for the element to connect and subscribe. MQTT config files use key=value format (options such as `id`, `keepalive`, `qos`, `username`/`password`, `mqtt_version`); Kafka config files use INI-style sections (`[global-config]`, `[consumer-config]` with `group-id`, `proto-cfg`). |
| `qtiredissink` | qtiredissink is a GStreamer sink element that publishes text/x-raw buffers (e.g. serialized ML metadata) to a Redis server channel via Redis Pub/Sub. | `channel` (string, default NULL, changeable only in NULL or READY state), `host` (string, default "127.0.0.1", changeable only in NULL or READY state), `port` (uint, range 0-4294967295, default 6379, changeable only in NULL or READY state), `username` (string, default NULL, changeable only in NULL or READY state), `password` (string, default NULL, changeable only in NULL or READY state), `sync` (boolean, default true), `async` (boolean, default true), `qos` (boolean, default false), `blocksize` (uint, range 0-4294967295, default 4096), `max-bitrate` (uint64, default 0), `max-lateness` (int64, range -1 to 9223372036854775807, default -1), `processing-deadline` (uint64, default 20000000), `render-delay` (uint64, default 0), `throttle-time` (uint64, default 0), `ts-offset` (int64, default 0), `enable-last-sample` (boolean, default true), `last-sample` (GstSample, read-only), `stats` (GstStructure with average-rate/dropped/rendered, read-only). | Sink (`sink`, always): `text/x-raw`, no format field (format: NA). It is a pure sink element (SINK flag) with no source pad and terminates the pipeline branch. A valid `channel` must be set before publishing; messages are delivered only to currently connected subscribers via Redis PUBLISH and are not retained for later delivery. |
| `qtirtspbin` | A GstBin-derived sink element that acts as an RTSP server, packetizing and publishing video, audio, and custom metadata streams over the network for RTSP clients to consume. | `address` (string, default `"127.0.0.1"`, IP address of the server), `port` (string, default `"8900"`, port to listen on), `mpoint` (string, default `"/live"`, mounting point), `mode` (enum `GstRtspBinMode`: `async`/`sync`, default `async` - async returns buffers immediately if no client connected and enqueues all buffers regardless of client read speed, sync restricts the number of input buffers until the client starts reading; changeable only in NULL or READY state), `async-handling` (boolean, default `false`, bin handles asynchronous state changes), `message-forward` (boolean, default `false`, forwards all children messages). | Sink (`sink_%u`, on request): `video/x-h264`, `video/x-h265`, `audio/mpeg`, `text/x-raw`. No src pad templates (element has SINK flag; it is a network endpoint, not a passthrough element). Internally routes each incoming stream to the appropriate RTP payloader (`rtph264pay`/`rtph265pay` for video, `rtpmp4apay` for audio, `rtpgstpay` for metadata/custom serialized data such as ML detection JSON) and exposes the packetized stream via an internal RTSP server. Multiple instances with unique `port` values are required to serve multiple independent streams; clients connect via `rtsp://<address>:<port><mpoint>`. |
| `qtisocketsink` | qtisocketsink is a GstBaseSink-derived element that transmits FD-backed GstBuffers (e.g. DMA buffers) over a UNIX domain socket for zero-copy sharing with a receiving qtisocketsrc in another process or container. | `socket` (string, default NULL, changeable only in NULL or READY state, must be a valid `.sock` path), `async` (boolean, default true), `sync` (boolean, default true), `qos` (boolean, default false), `max-lateness` (int64, range -1 to 9223372036854775807, default -1, drops buffers exceeding this delay), `max-bitrate` (uint64, range 0-18446744073709551615, default 0/disabled), `throttle-time` (uint64, default 0), `render-delay` (uint64, default 0), `ts-offset` (int64, default 0), `processing-deadline` (uint64, default 20000000), `blocksize` (uint, default 4096), `enable-last-sample` (boolean, default true), `last-sample` (GstSample, read-only), `stats` (GstStructure, read-only: average-rate, dropped, rendered), `name`, `parent`. | Sink (`sink`, always): `neural-network/tensors`, `video/x-raw` (ANY), `text/x-raw`. No src pad (sink-only element). Buffers must be FD-backed (e.g. DMA memory) to be transferable over the socket; a paired qtisocketsrc on the receiving process reconstructs the buffers. Buffer ownership is tracked via reference counting until the receiver returns it, and async=false forces a synchronous PAUSED transition. |
| `qtisocketsrc` | A GstPushSrc-based source element that receives GstBuffer objects (including FD-backed, zero-copy DMA buffers) from another process over a UNIX domain socket, pairing with qtisocketsink for inter-process pipeline bridging. | `socket` (string, default NULL, changeable only in NULL or READY state), `timeout` (uint64, default 1000, range 0-18446744073709551615, changeable only in NULL or READY state), `blocksize` (uint, default 4096, range 0-4294967295), `num-buffers` (int, default -1 (unlimited), range -1-2147483647), `do-timestamp` (boolean, default false), `automatic-eos` (boolean, default true), `typefind` (boolean, default false, deprecated/non-functional), plus standard `name`/`parent` GObject properties. | Src (`src`, always): `neural-network/tensors`, `video/x-raw` (ANY format), `text/x-raw` — supports raw video frames, ML tensor data, and structured text/metadata annotations. No sink pad (source element, GstFdSocketSrc/GstPushSrc-derived). Listens on the UNIX socket path given by `socket`; must be paired with a qtisocketsink instance writing to the same socket file. Buffers are FD-backed for zero-copy transfer across process boundaries. |

### Video / Camera / Display / Codec

| Plugin | One-line description | Key properties | Pads / caps notes |
|---|---|---|---|
| `qtivtransform` | Hardware-accelerated video transform (resize, rotate, flip, crop, color-convert). | `background` (uint ARGB8888, default `0xFF808080`, mutable-playing), `crop` (GstValueArray `<X,Y,W,H>`, mutable-playing), `destination` (GstValueArray `<X,Y,W,H>`, mutable-playing), `engine` (enum: `none`/`gles`/`fcv`, default `gles`), `engine-param` (string), `flip-horizontal` (bool, mutable-playing), `flip-vertical` (bool, mutable-playing), `rotate` (enum, mutable-playing) | Sink (`sink`, always): `video/x-raw` formats `{ NV12, NV21, YUY2, P010_10LE, NV12_10LE32, RGBA, BGRA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, GRAY8, NV12_Q08C }`. Src (`src`, always): `video/x-raw` formats `{ NV12, NV21, YUY2, P010_10LE, RGBA, BGRA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, RGBP, BGRP, GRAY8, NV12_Q08C }`. Note: use for documented source conversion or requested resize/rotate/flip/crop/color-convert. `rotate` enum nicks: `none`, `90CW`, `90CCW`, `180` — use exact nicks, NOT integer degrees. Requires DMA-backed buffers for zero-copy; pipeline setup fails if incompatible allocation. Scale via downstream caps filter (qtivtransform handles scale + flip + rotate in one pass). Composer/overlay-to-encoder file output uses direct NV12 caps on the composer/overlay output path. |
| `qtivcomposer` | Composes multiple video streams into one output frame using GPU. | `background` (uint ARGB8888, default `0xFF808080`), `engine` (enum: `none`/`gles`/`fcv`, default `gles`), per-pad: `alpha` (double 0–1, default 1.0), `crop` (GstValueArray `<X,Y,W,H>`), `dimensions` (GstValueArray `<W,H>`), `flip-horizontal` (bool), `flip-vertical` (bool), `position` (GstValueArray `<X,Y>`), `rotate` (enum: `none`/`90CW`/`90CCW`/`180`), `zorder` (int, default -1) | Sink (`sink_%u`, on request): `video/x-raw` formats `{ NV12, NV21, UYVY, YUY2, P010_10LE, RGBA, BGRA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, GRAY8, NV12_Q08C }`. Src (`src`, always): same formats. Note: requires DMA-backed buffers from all inputs; file-output encode path can negotiate NV12 via direct downstream caps: `qtivcomposer ! video/x-raw,format=NV12 ! v4l2h264enc ...`. Declare at top of gst-launch command when pads are addressed before source elements. |
| `qtivoverlay` | Draws overlays (boxes, text, masks, images) on video frames. | `bboxes` (string GstStructure list, writable-playing), `images` (string GstStructure list, writable-playing), `masks` (string GstStructure list, writable-playing), `strings` (string GstStructure list, writable-playing), `timestamps` (string GstStructure list, writable-playing) | Sink (`sink`, always): `video/x-raw` formats `{ NV12, NV21, YUY2, RGBA, BGRA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, NV12_Q08 }`. Src (`src`, always): same formats. Note: static overlays configured via properties; AI metadata overlays (bounding boxes, landmarks, classifications) rendered automatically from upstream `GstVideoRegionOfInterestMeta`/`GstVideoLandmarksMeta`/`GstVideoClassificationMeta`. |
| `qtivsplit` | Produces multiple transformed outputs from an input frame or ROI regions. | `engine` (enum: `none`/`gles`/`fcv`, default `gles`), per-pad: `mode` (enum: `none`/`force-transform`/`single-roi-meta`/`batch-roi-meta`, default `none`, writable-null/ready) | Sink (`sink`, always): `video/x-raw` formats `{ NV12, NV21, UYVY, YUY2, RGBA, BGRA, ARGB, ABGR, RGBx, BGRx, xRGB, xBGR, RGB, BGR, GRAY8, NV12_Q08C }`. Src (`src_%u`, on request): same formats. Note: `mode=none` reuses input buffer if caps match; `force-transform` always allocates new buffer; `single-roi-meta` extracts one ROI per frame (emits GAP if no ROI); `batch-roi-meta` emits one buffer per ROI found. |
| `qticamsrc` / `qtiqmmfsrc` | ISP camera source — **QLI only** (`qcom-multimedia-proprietary-image` package required; not available on Ubuntu). `qticamsrc` is the canonical name (GST_RANK_PRIMARY) in newer SDK builds; `qtiqmmfsrc` is the backward-compatible alias (GST_RANK_NONE) and is what ships on current QLI devices — **use `qtiqmmfsrc` unless `gst-inspect-1.0 qticamsrc` confirms it is present on your target**. **Device-verified (full diff on QLI 10.73.195.44): properties, pad templates, caps, signals, and action signals are byte-for-byte identical between the two names.** Both register the same `GstQmmfSrc` type. **`vhdr` — Video HDR / SHDR:** enum `GstSHDRMode`, default `0`=`off`, **mutable-playing YES**; values: `0`=`off`, `1`=`shdr-raw` (raw line-interleaved, better perf), `2`=`shdr-yuv` (YUV virtual-channel, better quality, may reduce framerate), `3`=`raw-shdr-switch` (auto lux-based linear↔raw-SHDR), `4`=`yuv-shdr-switch` (auto lux-based linear↔YUV-SHDR), `5`=`qbc-hdr-video` (in-sensor HDR video, sensor-specific), `6`=`qbc-hdr-snapshot` (in-sensor HDR snapshot, scene-decided). In gst-launch use integer: `vhdr=1`. On devices without VHDR HAL, property name is `shdr` (bool) instead. **`eis` — Electronic Image Stabilization:** on QLI devices (`qticamsrc`/`qtiqmmfsrc`): **enum** `GstEisMode`, default `0`=`eis-off`, **not mutable-playing** (no `changeable in PLAYING` flag — set before PAUSED); values: `0`=`eis-off`, `1`=`eis-on-single-stream` (EIS on first stream; max 1 preview + 1 video + 1 snapshot pad), `2`=`eis-on-dual-stream` (EIS on both preview and video streams). In gst-launch use integer: `eis=1`. On Ubuntu/non-QLI builds: **bool**, default `false`. **`ldc`:** bool, default false, mutable-playing YES (device-verified). **`lcac`:** bool, default false. **`adrc`:** bool, default false, mutable-playing YES. **`ife-direct-stream`:** bool, default false — skips IPE/other ISP modules. **`sw-tnr`:** bool, default false — software TNR. **`infrared-mode`:** enum, default `0`=`off`; `0`=`off`, `1`=`on`, `2`=`auto`, `3`=`cut-filter-only`, `4`=`cut-filter-disable`; mutable-playing YES. **`antibanding`:** enum, default `3`=`auto`; `0`=`off`, `1`=`50hz`, `2`=`60hz`, `3`=`auto`; mutable-playing YES. **`exposure-mode`:** enum, default `1`=`auto`; `0`=`off` (use `manual-exposure-time`), `1`=`auto`; mutable-playing YES. **`manual-exposure-time`:** int64 ns, default 33333333, mutable-playing YES. **`exposure-compensation`:** int -12–12, default 0, mutable-playing YES. **`exposure-lock`:** bool, default false, mutable-playing YES. **`exposure-metering`:** enum, default `0`=`average`; `0`=`average`, `1`=`center-weighted`, `2`=`spot`, `6`=`custom`; mutable-playing YES. **`focus-mode`:** enum, default `0`=`off`; `0`=`off`, `1`=`auto`, `2`=`macro`, `3`=`continuous`, `4`=`edof`; mutable-playing YES. **`white-balance-mode`:** enum, default `3`=`auto`; `0`=`off`, `1`=`manual-cc-temp`, `2`=`manual-rgb-gains`, `3`=`auto`, `4`=`shade`, `5`=`incandescent`, `6`=`fluorescent`, `7`=`warm-fluorescent`, `8`=`daylight`, `9`=`cloudy-daylight`, `10`=`twilight`; mutable-playing YES. **`manual-wb-settings`:** GstStructure string, mutable-playing YES — used with `white-balance-mode=manual-cc-temp/manual-rgb-gains`. **`white-balance-lock`:** bool, default false, mutable-playing YES. **`iso-mode`:** enum, default `0`=`auto`; `0`=`auto`, `1`=`deblur`, `2`=`100`, `3`=`200`, `4`=`400`, `5`=`800`, `6`=`1600`, `7`=`3200`, `8`=`manual`; mutable-playing YES. **`manual-iso-value`:** int 100–3200, default 800, mutable-playing YES. **`control-mode`:** enum, default `1`=`auto`; `0`=`off`, `1`=`auto`, `2`=`use-scene-mode`, `3`=`off-keep-state`; mutable-playing YES. **`noise-reduction`:** enum, default `1`=`fast`; `0`=`off`, `1`=`fast` (TNR fast), `2`=`hq` (TNR HQ); mutable-playing YES. **`frc-mode`:** enum, default `0`=`frame-skip`; `0`=`frame-skip`, `1`=`capture-request`; **not mutable-playing**. **`effect`:** enum, default `0`=`off`; `0`=`off`, `1`=`mono`, `2`=`negative`, `3`=`solarize`, `4`=`sepia`, `5`=`posterize`, `6`=`whiteboard`, `7`=`blackboard`, `8`=`aqua`; mutable-playing YES. **`scene`:** enum, default `1`=`face-priority`; `0`=`disabled`…`16`=`hdr` (full 17-value enum — see gst-inspect for all nicks); mutable-playing YES. **`contrast`:** int 1–10, default 5, mutable-playing YES. **`saturation`:** int 0–10, default 5, mutable-playing YES. **`sharpness`:** int 0–6, default 2, mutable-playing YES. **`op-mode`:** flags `GstFrameSelection`, default `0x1`=`none`; `0x1`=`none`, `0x2`=`frameselection`, `0x4`=`fastswitch`; **not mutable-playing**. **`video-pads-activation-mode`:** enum, default `0`=`normal`; `0`=`normal`, `1`=`signal`; mutable-playing YES. **`sensor-mode`:** int -1–15, default -1 (auto), not mutable-playing. **`camera`:** uint 0–32, default 0, not mutable-playing. **`slave`:** bool, default false, not mutable-playing. **`zoom`:** GstValueArray `<X,Y,W,H>` sensor pixel array coords, mutable-playing YES. **`input-roi-enable`:** bool, not mutable-playing. **`input-roi-info`:** GstValueArray, mutable-playing YES. **ISP tuning strings (mutable-playing YES):** `custom-exposure-table`, `defog-table`, `ltm-data`, `noise-reduction-tuning` — GstStructure strings. **Read-only/advanced:** `active-sensor-size` (GstValueArray), `static-metadata`, `static-metas` (GHashTable), `image-metadata`, `video-metadata` (pointer, r/w), `session-metadata` (write-only pointer). **Signals:** `result-metadata`, `urgent-metadata`, `device-status-change`. **Action signals (gst-pipeline-app only):** `capture-image`, `dynamic-capture-image`, `cancel-capture`, `video-pads-activation`. Per-pad (`video_%u`): `attach-cam-meta` (bool), `crop` (GstValueArray, mutable-playing), `extra-buffers` (uint, NULL/READY only), `framerate` (double 0–30, mutable-playing), `reprocess-enable` (bool), `rotate` (enum: `none`/`90CCW`/`180CCW`/`270CCW`), `source-index` (int, NULL/READY only), `super-buffer-mode` (bool, NULL/READY only), `type` (enum: `video`/`preview`, mutable-playing), `logical-stream-type` (enum 0–19, NULL/READY/PAUSED only). **EIS + SHDR concurrent use:** both on same element; EIS before PAUSED (not mutable-playing), `vhdr` mutable while PLAYING. Example: `qtiqmmfsrc vhdr=1 eis=1  (or qticamsrc vhdr=1 eis=1 on QLI)`. | Video src (`video_%u`, on request): `video/x-raw` formats `{ NV12, NV16, NV12_Q08C, RGB, YUY2, P010_10LE, NV12_Q10LE32C }`, `video/x-bayer` formats `{ bggr, rggb, gbrg, grbg, mono }`, `image/jpeg`. Width 176–4000, height 144–3000, framerate 0–240. Image src (`image_%u`, on request): `image/jpeg`, `video/x-raw` `{ NV21, NV12, P010_10LE, NV12_Q10LE32C }`, `video/x-bayer`. **Image pad rules (critical):** (1) Image pads are **on-demand** — triggered via `capture-image` signal (`gst-pipeline-app`, not `gst-launch-1.0`). (2) **`image_N::type=jpeg` MUST be declared** on the element. (3) **Pad index is sequential across ALL pad types** — 2 video pads → image pad is `image_2`. (4) Video pads stream continuously; image pads do not. |
| `qtijpegenc` | Hardware-accelerated JPEG encoder. | `camera-id` (uint, default 0): selects ISP JPEG engine, `orientation` (enum: `0`/`90`/`180`/`270`, default `0`): EXIF metadata only — no pixel rotation, `quality` (int 0–100, default 85, mutable-playing): adjustable while PLAYING | Sink (`sink`, always): `video/x-raw` formats `{ NV12, NV21 }`. Src (`src`, always): `image/jpeg`. Note: requires FD-backed DMA buffers on input — upstream must be `qticamsrc` or `qtivtransform`; fails if upstream delivers non-DMA buffers. For live JPEG snapshots from camera, prefer `qticamsrc.image_N` pad (delivers JPEG directly from ISP without needing `qtijpegenc`). |
| `v4l2h264dec` | Hardware H.264 decoder. | `capture-io-mode` (enum, see IO modes below), `output-io-mode` (enum, see IO modes below), `extra-controls`, `max-errors`, `discard-corrupted-frames`, `qos`, `automatic-request-sync-points`, `automatic-request-sync-point-flags`, `min-force-key-unit-interval` | Sink (`sink`, always): `video/x-h264` stream-format `byte-stream`, alignment `au`, profiles `{ baseline, main, high, high-10, high-4:2:2, ... }`, levels `1–6.2`. Src (`src`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. **IO mode values (same for all v4l2 codec elements):** `auto`=0, `rw`=1, `mmap`=2, `userptr`=3, `dmabuf`=4, `dmabuf-import`=5. **Standard zero-copy setting for decoders and file/RTSP-source pipelines: `capture-io-mode=4 output-io-mode=4`** — the v4l2 driver manages buffers on both sides. In gst-launch commands use integer values; in C apps use `gst_element_set_enum_property()` with string nick (e.g., `"dmabuf"` for 4, `"dmabuf-import"` for 5). |
| `v4l2h264enc` | Hardware H.264 encoder. | `capture-io-mode` (enum, see IO modes in v4l2h264dec entry), `output-io-mode` (enum), `extra-controls` (GstStructure string for V4L2 controls) | Sink (`sink`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. Src (`src`, always): `video/x-h264` stream-format `byte-stream`, alignment `au`, profiles `{ baseline, main, high, high-10, high-4:2:2, ... }`, levels `1–6.2`. **IO modes for v4l2h264enc — select from what actually allocates the buffer arriving at the encoder's input, not from the pipeline's original source type in isolation:** if the immediate upstream element hands the encoder a driver-managed/transform-produced NV12 buffer (a decoder, a `qtivcomposer`/`qtivoverlay` output, or any other non-camera producer), use `capture-io-mode=4 output-io-mode=4` — the driver manages both sides. If the immediate upstream element hands the encoder the camera's natively-allocated DMA buffer FDs directly (no intervening compositor/overlay/transform stage that produces its own buffer), use `capture-io-mode=4 output-io-mode=5` (dmabuf-import) — the driver manages capture-side output allocation (`capture-io-mode=4`) while importing the camera's FDs on input (`output-io-mode=5`). The source-type shortcut below is usually right but can mislead when a camera-source pipeline routes through a compositor/overlay before the encoder — the encoder's immediate input there is the compositor's output, not the camera, so it takes `4/4` like a file/RTSP path even though the pipeline's ultimate source is a camera. Quick lookup for the common cases: (1) **Camera source feeding the encoder directly** (`qtiqmmfsrc`/`qticamsrc` with no compositor/overlay in between): `capture-io-mode=4 output-io-mode=5`. (2) **File or RTSP source decoded through `v4l2h264dec`/`v4l2h265dec`, or any composited/overlaid output regardless of original source**: `capture-io-mode=4 output-io-mode=4`. (3) **AV record** (encoder output feeds `mp4mux` alongside an audio stream): use `capture-io-mode=4 output-io-mode=5` regardless of source — the dual-input mux requires dmabuf-import on the encoder output. This behavior changed in v4l2 driver release 1.4; prior to 1.4 both sides used `5/5`. **`extra-controls` syntax:** `"controls,<key>=<value>,<key>=<value>;"` — e.g., `extra-controls="controls,video_bitrate=1000000,video_gop_size=29;"`. Key properties: `video_bitrate` (bps), `video_gop_size` (frames between keyframes), `video_h264_i_frame_qp`, `video_h264_p_frame_qp`. |
| `v4l2h265dec` | Hardware H.265 decoder. | `capture-io-mode` (enum, see IO modes in v4l2h264dec entry), `output-io-mode` (enum), `extra-controls`, `max-errors`, `discard-corrupted-frames`, `qos`, `automatic-request-sync-points`, `automatic-request-sync-point-flags`, `min-force-key-unit-interval` | Sink (`sink`, always): `video/x-h265` stream-format `byte-stream`, alignment `au`, profiles `{ main, main-still-picture, main-10 }`, levels `1–6.2`. Src (`src`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. **Standard zero-copy: `capture-io-mode=4 output-io-mode=4`** (same as v4l2h264dec). |
| `v4l2h265enc` | Hardware H.265 encoder. | `capture-io-mode` (enum, see IO modes in v4l2h264dec entry), `output-io-mode` (enum), `extra-controls` (same format as v4l2h264enc; use `video_hevc_i_frame_qp`/`video_hevc_p_frame_qp` for HEVC QP) | Sink (`sink`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. Src (`src`, always): `video/x-h265` stream-format `byte-stream`, alignment `au`, profiles `{ main, main-still-picture, main-10 }`, levels `1–6.2`. **Standard zero-copy: `capture-io-mode=4 output-io-mode=4`** (same as v4l2h264enc). |
| `qtismartvencbin` | Smart adaptive bitrate/bandwidth encoder bin — best choice when bandwidth savings or storage reduction is the goal. Dynamically reduces bitrate in static/background regions and allocates more bits to high-motion or AI-detected ROI regions. Can operate without AI metadata (camera-only smart encode) or with AI detection metadata for content-aware encoding. **Device-verified (QLI gst-inspect):** `encoder` (enum `GstBinEncoderType`, default `0`=`c2enc`; **set at NULL/READY only**; values: `0`=`c2enc` (Codec2 — not available on all devices), `1`=`omxenc`, `2`=`v4l2h264enc` (H.264, **use this on QLI/Ubuntu**), `3`=`v4l2h265enc` (H.265, best bandwidth savings)); `max-bitrate` (uint, default 6000000 bps, NULL/READY only); `default-gop` (uint, default 30, NULL/READY only); `max-gop` (uint, default 600, NULL/READY only); `smart-framerate` (bool, default true, NULL/READY only — drop frames in static scenes); `smart-gop` (bool, default true, NULL/READY only — extend GOP in static scenes); `min-buffers` (uint, default 30, NULL/READY only; **set to 1 for file-source pipelines** — default 30 causes a 30-frame startup stall on file inputs); `levels-override` (string, default null — override bitrate/FR levels e.g. `"LevelsOverride,bitrate_static=160000,bitrate_low=358000,bitrate_medium=700000,bitrate_high=1400000,fr_static=15,fr_low=3,fr_medium=1,fr_high=0;"`); `roi-quality-cfg` (string, default null — per-class ROI quality e.g. `"ROIQPs,car=2,person=1,tree=-2;"`). **CRITICAL — encoder must be set at construction time:** in gst-launch this means setting it as a property on the element declaration (e.g. `qtismartvencbin encoder=2`) — gst-launch sets properties before NULL→READY transition so this works correctly. In C apps use `gst_element_factory_make_with_properties`. **c2enc (encoder=0) is not available on QLI/standard Ubuntu — always use encoder=2 or encoder=3.** For H.265 bandwidth savings use `encoder=3`. **gst-launch wiring (3 sink pads, all ALWAYS):** `sink` = main NV12 video, `sink_ctrl` = low-res NV12 control stream (e.g. 640×480 downscale), `sink_ml` = `text/x-raw` AI detection metadata. Src `src` outputs `video/x-h264` or `video/x-h265` depending on encoder. **Minimal camera-only example (no AI, H.265, bandwidth savings):** `qtismartvencbin name=svenc encoder=3 max-bitrate=2000000 min-buffers=1` with `tee` feeding `svenc.sink` (main) and `svenc.sink_ctrl` (downscaled via `qtivtransform`); `fakesink` on `svenc.sink_ml` when no AI metadata. **With AI detection:** add `qtimlpostprocess ! text/x-raw ! svenc.sink_ml`. | Three ALWAYS sink pads: `sink` (NV12/NV12_Q08C, any resolution), `sink_ctrl` (NV12/NV12_Q08C, any resolution — typically downscaled), `sink_ml` (`text/x-raw,format=utf8`). One ALWAYS src pad: `src` (`video/x-h264` or `video/x-h265`). **After src:** add `h264parse`/`h265parse` then sink (filesink, mp4mux, qtirtspbin etc). |
| `v4l2vp9dec` | Hardware VP9 decoder. | `capture-io-mode`, `output-io-mode`, `extra-controls`, `max-errors`, `discard-corrupted-frames`, `qos`, `automatic-request-sync-points`, `automatic-request-sync-point-flags`, `min-force-key-unit-interval` | Sink (`sink`, always): `video/x-vp9`. Src (`src`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. **Standard zero-copy: `capture-io-mode=4 output-io-mode=4`** (same as v4l2h264dec). **No parse step needed** — connect `matroskademux ! queue ! v4l2vp9dec` directly; do not add `vp9parse`. |
| `v4l2av1dec` | Hardware AV1 decoder. | `capture-io-mode`, `output-io-mode`, `extra-controls`, `max-errors`, `discard-corrupted-frames`, `qos`, `automatic-request-sync-points`, `automatic-request-sync-point-flags`, `min-force-key-unit-interval` | Sink (`sink`, always): `video/x-av1`. Src (`src`, always): `video/x-raw` formats `{ NV12_Q08C, NV12_Q10LE32C, NV12, NV21 }`. Note: use `av1parse` upstream to ensure correct OBU-aligned framing before `v4l2av1dec`. |
| `waylandsink` | Wayland display sink. | `display`, `drm-device`, `force-aspect-ratio`, `fullscreen` (bool, default false), `fullscreen-output`, `rotate-method`, `sync` (bool, default true): sync to pipeline clock — use `sync=false` when display runs alongside encode sinks to avoid clock stalls, or for more than 8 independent processing-heavy/batched multistream streams where HTP/batch preroll makes frames arrive late (see `ai-pipeline-patterns.md` Template 17), or when the user explicitly requests lower latency; omit `sync` entirely for audio-classification display, `async` (bool, default true): enable async state changes — use `async=false` only for snapshot/still sinks, `enable-last-sample` (bool, default true): maintain last-sample buffer — set `enable-last-sample=false` in multi-sink camera pipelines to reduce memory usage | Sink (`sink`, always): `video/x-raw` formats `{ BGR10A2_LE, RGB10A2_LE, AYUV, RGBA, ARGB, BGRA, ABGR, BGR10x2_LE, RGB10x2_LE, P010_10LE, NV12_10LE40, Y444, v308, RGBx, xRGB, BGRx, xBGR, RGB, BGR, Y42B, NV16, NV61, YUY2, YVYU, UYVY, I420, YV12, NV12, NV21, Y41B, YUV9, YVU9, BGR16, RGB16, NV12_Q08C }`, `video/x-raw(memory:DMABuf)` format `DMA_DRM`. No src pad. **Skill default: `fullscreen=true sync=true` for every source type, including live camera** — override with `sync=false fullscreen=true enable-last-sample=false` when display is one of multiple sinks in the same pipeline (e.g., camera encode + display running in parallel), not based on source type alone. A normal 4-stream AI wall or a batch group of 8 or fewer streams stays `sync=true`. |

### Primary Catalog Notes

- `qtimetamux` text metadata structures now include `Segmentation` and `Depth` in addition to object detection, pose, image classification, generic text, and optical-flow/motion data. It can attach segmentation/depth GstMeta when those structures are present, but current known-good segmentation/depth templates still use Topology B (`qtimlpostprocess -> video/x-raw -> qtivcomposer`) until a current sample or device run verifies a text-metadata segmentation/depth path.
- Current camera source code registers `qticamsrcdeviceprovider`, so `gst-device-monitor-1.0` can enumerate camera devices on builds where the provider is installed. This does not change generation defaults: keep using `qtiqmmfsrc` unless `qticamsrc` is requested or confirmed on target.
- `qtivcomposer` output caps are demand-driven. Use direct `qtivcomposer ! video/x-raw,format=NV12 ! v4l2h264enc ...` for file/encoder paths and use explicit canvas caps when a specific multistream template requires them, but do not add a blanket `qtivcomposer ! video/x-raw,format=NV12 ! tee` to every composer pipeline. Display-only composer paths can usually negotiate directly unless a task-specific template calls for fixed caps.

---

## B) Extended Plugin Catalog

These entries are allowed with conservative property/caps detail where needed.

| Plugin | One-line description | Key properties | Pads / caps notes |
|---|---|---|---|
| `qtiqmmfsrc` | Alias for `qticamsrc` — GST_RANK_NONE on QLI. **Full diff confirmed on device: properties, pad templates, caps, signals, and action signals are byte-for-byte identical to `qticamsrc`.** Only difference is rank and plugin filename. Use `qtiqmmfsrc` on Ubuntu (only name available); use `qticamsrc` on QLI (PRIMARY rank) or `qtiqmmfsrc` interchangeably — both work. See the combined `qticamsrc` / `qtiqmmfsrc` row above for all details. | Identical — see above. | Identical — see above. |
| `qtimlaconverter` | Audio preprocessing converter for AI feature extraction. | `sample-rate`, `params`, `feature` (confirmed in source `g_object_class_install_property`). | Source confirms sink/src pad templates; use in audio AI pipelines only. |
| `qtimetatransform` | In-place `GstBaseTransform` that dlopen-loads a `.so` module implementing `gst_meta_module_open/close/process()` to rewrite ROI-derived metadata between pipeline stages. | `module` (dynamic enum populated from installed transform modules — set with `gst_element_set_enum_property()`, not `g_object_set()`), `module-params` (string, GstStructure format, passed through to the module). **Documented modules:** `roi-palmd` (derives a hand-landmark ROI from a parent palm-detection ROI — gesture recognition Stage 1→2 handoff), `roi-label-moving-average` (smooths/averages ROI label confidence over time), `roi-auto-framing` (auto-framing ROI transform), `roi-person-merge` (person ROI merge transform). Usage: `qtimetatransform module=roi-palmd` placed immediately after `qtimetamux`, before the next `tee`/consumer, e.g. `qtimetamux → qtimetatransform(module=roi-palmd) → tee`. | Sink/src pad templates accept and produce metadata-carrying buffers (in-place transform — same caps in/out). |
| `qtimlmetaextractor` | Converts binary buffer-attached ML metas (`GstVideoRegionOfInterestMeta`/classification/landmarks metas) into serialized `text/x-raw` `GstStructure`-list output (structure names `ObjectDetection`, `PoseEstimation`, `ImageClassification`) — the inverse of `qtimetamux` (which deserializes this same text/x-raw GstStructure format back into binary metas), and complementary to `qtimlmetaparser` (which converts to JSON text for external systems). | No properties (no `g_object_class_install_property` calls in source). | Sink/src pad templates registered for binary-meta-bearing `video/x-raw` in, `text/x-raw` metadata out. **Placement:** after `qtimetamux` (or any element producing binary `GstBuffer`-attached ML metas), before elements expecting the `text/x-raw` metadata convention (`qtiobjtracker`, `qtimetatransform`, `qtimlmetaparser`) or networking/file/logging sinks (`qtiredissink`, `qtirtspbin`, `multifilesink`). Most standard AI pipelines never need it — `qtimlpostprocess` already emits `text/x-raw` directly. Treat direct use as an advanced/specialized route. |
| `qtimlvideotflitebin` | ML bin wrapper for TFLite video inference + postprocess. | Use ML-bin property family: `inference-*` and `postprocess-*` (skill convention for ML-bin generation). | Bin exposes sink/src templates via `mlbin` implementation. |
| `qtimlaclassification` | Audio classification plugin. | Source shows `module`, `labels`, `num-results`, `threshold`. | Source confirms sink/src templates registered. |
| `qtirestrictedzonedbg` | Restricted-zone filter/debug-style plugin. | `zone-config` property confirmed in source. | Source confirms sink/src templates registered. |
| `qtisync` | Stream synchronization helper element. | Plugin metadata is available; detailed property table is limited. | Treat caps/properties conservatively. |
| `qtiuridecodebin` | URI decode bin wrapper/plugin. | `uri`, `iterations` | Use conservative placeholders for unresolved details. |
| `qtivideotemplate` | Template plugin for custom video processing library integration. | Source shows `custom-lib-name`, `custom-params`. | Source confirms sink/src pad templates registered. |

Rule for extended-catalog plugins:
- If exact property spelling/caps are not explicitly documented in this skill references, prefer placeholders and mention verification requirement via `gst-inspect-1.0` on target.

---

## C) Upstream GStreamer Elements Allowed

### Core pipeline elements

- `filesrc` — reads file from filesystem; key prop: `location` (string)
- `filesink` — writes data to file; key prop: `location` (string)
- **`gst-pipeline-app`** — interactive pipeline runner (separate utility, NOT `gst-launch-1.0`). Required for `qticamsrc` image capture workflows. Supports `capture-image` signal trigger via interactive menu. Available from QIM SDK 2.0 RC3 (version 0.1.4). Invoked as: `gst-pipeline-app -e <pipeline>`. After PLAYING, presents interactive menu to send signals to named elements.
- `qtdemux` — demultiplexes MP4/QuickTime containers; exposes **dynamic pads** via `pad-added` signal — never statically link from qtdemux; use dynamic pad callback. For AV files: `pad-added` fires twice (video + audio); inspect caps in callback to route each pad to correct downstream chain.
- `matroskademux` — demultiplexes WebM/MKV containers; exposes **dynamic pads** via `pad-added` signal (same pattern as `qtdemux`). Use for VP9, VP8, AV1, and Opus streams packaged in `.webm` or `.mkv` files.
- `h264parse` — parses H.264 bitstream; key prop: `config-interval` (int, default 0): `-1` = insert SPS/PPS before every IDR frame (required for RTSP streaming); `0` = only at stream start; `N` = every N seconds
- `h265parse` — parses H.265 bitstream; same `config-interval` semantics as `h264parse`
- `v4l2h264dec`, `v4l2h265dec`, `v4l2h264enc`, `v4l2h265enc` — see Section A entries for full IO mode details
- `v4l2src` — V4L2 video capture source (USB cameras); key prop: `device` (string, default `/dev/video0`): V4L2 device node; outputs `video/x-raw,format=YUY2` for most USB cameras — must be followed by `qtivtransform ! video/x-raw,format=NV12`
- `rtspsrc` — RTSP client source; key props: `location` (string, RTSP URL), `latency` (uint ms, default 200): jitter buffer — increase for unstable networks; exposes dynamic pads via `pad-added` (same pattern as qtdemux)
- `rtph264depay` — depayloads H.264 RTP; no key props for basic use
- `rtph265depay` — depayloads H.265 RTP; no key props for basic use
- `rtph264pay` — payloads H.264 into RTP; key props: `pt` (int, default 96): RTP payload type — must match receiving server's `encoding-name=H264,payload=96` caps; `config-interval` (int): `-1` = SPS/PPS in every RTP packet (required for RTSP)
- `queue` — decouples pipeline stages; key props: `max-size-buffers`, `max-size-bytes`, `max-size-time`. **Always add before encoders (`v4l2h264enc`, `v4l2h265enc`) for DMA writability. Always add on each tee branch. Add after `qtdemux` or `matroskademux` in playback pipelines. Always add immediately after any hardware decoder (`v4l2h264dec`, `v4l2h265dec`, `v4l2vp9dec`, `v4l2av1dec`), regardless of what follows (see pipeline-utilities.md → Queue Usage).**
- `tee` — duplicates buffers to multiple branches; use `name=t` and address branches via `t. !`; each branch MUST have queue immediately after tee pad
- `capsfilter` — enforces caps negotiation; set `caps` property. Use `video/x-raw,format=NV12` to normalize. Use `video/x-raw,width=<W>,height=<H>` to trigger resize.
- `appsink` — exposes buffers to application; key props: `sync=false emit-signals=true`
- `appsrc` — injects application buffers into pipeline
- `mp4mux` — muxes streams into MP4; for AV mux: name it (`mp4mux name=muxer`), audio auto-routes to first free sink, route video explicitly with `! muxer.`
- `multifilesink` — writes sequentially numbered files; key props: `location` (string with `%d` printf counter, e.g., `frame%d.jpg`), `sync` (bool, default true), `async` (bool)
- `videoscale` — software video resizer; use with downstream caps filter to set output resolution; sink/src: `video/x-raw`
- `udpsink` — UDP network sink for RTP streaming; key props: `host` (default `127.0.0.1` — use `0.0.0.0` for external clients), `port`; sink: `application/x-rtp`; pair with `rtph264pay` upstream and `gst-rtsp-server` for RTSP serving pattern
- `fpsdisplaysink` — wraps a real video sink and overlays live frame-rate stats; key props: `video-sink` (object of type `GstElement` — **must be a real element instance**, e.g. a `waylandsink` created with `gst_element_factory_make` and passed via `g_object_set(fpsdisplaysink, "video-sink", waylandsink, NULL)`; a factory-name string is not a valid value for this property and fails at property-set time), `text-overlay` (bool, default true — draws FPS as text on the video), `signal-fps-measurements` (bool, default false), `sync` (bool, default true — forwarded to the wrapped sink if it supports it), `fps-update-interval` (int ms, default 500). Use only when the user explicitly asks for an FPS counter/overlay; otherwise use the wrapped sink (e.g. `waylandsink`) directly. See `c-app-development.md` "fpsdisplaysink — Properties" for the full C construction pattern.
- `webrtcbin`, `waylandsink`

### Audio elements

- `flacparse`, `flacdec`
- `audioconvert` - converts between audio formats/rates; include whenever the source audio format may differ from the sink's expected format. **Rule: always include `audioconvert` after `pulsesrc` and after `wavparse` before encode/sink. Omit only when upstream decoder output is guaranteed compatible - `mpg123audiodec` outputs compatible PCM for `pulsesink` directly, so `audioconvert` is not needed in MP3 playback or AV file playback audio branch.**
- `audioresample` - resamples audio between different sample rates; include when source and sink sample rates may differ. **Rule: omit in simple PCM and MP3 capture/playback pipelines where `pulsesrc`/`pulsesink` negotiate a compatible rate. Include when bridging between different sample-rate domains (e.g., AI audio pipelines that require a specific rate).**
- `audiobuffersplit`
- `aacparse`, `avdec_aac`, `mpegaudioparse`, `mpg123audiodec`
- `pulsesrc` - PulseAudio audio capture source; key props: `volume` (double, 0-10), `do-timestamp` (bool, default false - set `TRUE` for A/V sync in AV record pipelines), `provide-clock` (bool, default true - set `FALSE` for A/V sync in AV record pipelines); prerequisite: `wpctl set-default <node_no.>` before pipeline run
- `pulsesink` - PulseAudio audio output sink; key prop: `volume` (double, 0-10); sink: `audio/x-raw`; prerequisite: `wpctl set-default <node_no.>` before pipeline run
- `lamemp3enc` - software MP3 encoder; sink: `audio/x-raw`, src: `audio/mpeg, mpegversion=1, layer=3`; no special enum properties - standard `g_object_set` in C apps
- `wavenc` - WAV container encoder; sink: `audio/x-raw`, src: `audio/x-wav`
- `wavparse` - WAV container parser/demuxer; sink: `audio/x-wav`, src: `audio/x-raw`
- `tsdemux`, `multifilesrc`, `multifilesink`, `videotestsrc`

---

## Rules

- Never output a QTI plugin name that is not in this file.
- Never "correct" to a guessed plugin name.
- Use `qtiredissink` (with double `s`) as the plugin name. If user writes `qtiredisink`, treat it as this same plugin.
- If user asks for a plugin outside this list, keep request text in README notes and use placeholder values in pipeline/code.
- `qtimlpostprocess` module values are not plugin names; choose modules from `plugin-catalog.md`.
- For extended-catalog plugins with partial detail, prefer conservative generation and explicit placeholders over guessed properties/caps.
- The four ML video bin wrappers are named `qtimlvideotflitebin`, `qtimlvideoqnnbin`, `qtimlvideosnpebin`, `qtimlvideoonnxbin` (pattern: `qtimlvideo<runtime>bin`) - do not use `qtimlvtflitebin`, `qtimlvqnnbin`, `qtimlvsnpebin`, or `qtimlvonnxbin` as element names.

---

## Postprocess Modules

# qtimlpostprocess Modules

## Purpose

Use this reference to select `qtimlpostprocess module=...` values without inventing module names.

- If the user explicitly provides a module name, use it exactly as given - do not cross-check against this table.
- If the user does not specify a module, infer from this table based on the AI task described.
- If the module cannot be inferred confidently, use a placeholder like `<POSTPROC_MODULE_STAGE1>`.
- Never invent a module name when the user has not provided one and the task does not clearly map to a known module.
- This table reflects currently deployed modules; additional modules may be added over time. If the user provides one not listed here, trust it.

## Supported Module Table

| Enum ID | Module Name |
|---:|---|
| 0 | `none` |
| 1 | `deepbox-3d` |
| 2 | `mobilenet` |
| 3 | `srnet` |
| 4 | `east-textdt` |
| 5 | `qfd` |
| 6 | `hlandmark` |
| 7 | `mediapipe-pose-landmark` |
| 8 | `palmd` |
| 9 | `yolov8` |
| 10 | `ocr` |
| 11 | `qpd` |
| 12 | `posenet` |
| 13 | `deeplab-argmax` |
| 14 | `yamnet` |
| 15 | `yolo-nas` |
| 16 | `ssd-mobilenet` |
| 17 | `midas-v2` |
| 18 | `yolov5` |
| 19 | `deepbox-yolo` |
| 20 | `qfr-softmax` |
| 21 | `easy-ocr-detector` |
| 22 | `yolov8-seg` |
| 23 | `ocr-recognizer` |
| 24 | `mobilenet-softmax` |
| 25 | `hrnet` |
| 26 | `tensor` |
| 27 | `mediapipe-pose` |
| 28 | `wave2vec` |
| 29 | `qfr` |
| 30 | `lite-3dmm` |

## Module Selection Hints (Use Only When Explicitly Supported)

| Request signal in prompt/docs | Use module |
|---|---|
| User explicitly says `module=<name>` | Use that exact module if present in table |
| YOLOv8 detection | `yolov8` |
| PPE objects detection | `yolov8` |
| YOLOX detection | `yolov8` (documented compatibility note) |
| YOLOv5 detection | `yolov5` |
| YOLO-NAS detection | `yolo-nas` |
| Classification with MobileNet-style output | `mobilenet` or `mobilenet-softmax` (only if prompt/reference specifies which) |
| Pose with HRNet | `hrnet` |
| Face detection | `qfd` |
| Facial landmark / 3DMM pose (face recognition stage 2) | `lite-3dmm` - see `model-catalog.md` Face Detection/Recognition category and `ai-pipeline-patterns.md` Template 17 for full 3-stage order |
| Face recognition / identity classification (face recognition stage 3, on facemap-aligned crop) | `qfr` - do not use at stage 2 directly on the raw detection ROI; must run after the `lite-3dmm` landmark stage |
| PPE / person-foot model using QPD in examples | `qpd` |
| Segmentation with Deeplab Argmax | `deeplab-argmax` |
| 3D bounding box detection | `deepbox-3d` |
| YOLO-based 3D/extended bounding box detection | `deepbox-yolo` |
| Palm detection (gesture stage 1) | `palmd` - also add `bbox-stabilization=true` property on this postprocess element for gesture pipelines |
| Hand landmark detection (gesture stage 2) | `hlandmark` - used as stage_02_1_postproc in gesture pipeline (one of two outputs from t_split_4) |
| Tensor forwarding / intermediate stage in gesture pipeline | `tensor` - used as stage_02_2_postproc; its output feeds directly into stage_03_1_inference (gesture embedder) |
| Gesture classifier output (gesture stage 3 final) | `mobilenet` - stage_03_postproc in gesture recognition pipeline; outputs gesture labels |
| OCR detector requests | `easy-ocr-detector` |
| OCR recognizer requests | `ocr-recognizer` |
| MediaPipe pose requests | `mediapipe-pose` or `mediapipe-pose-landmark` (use exact user-requested stage) |
| Speech/audio embedding requests that explicitly mention Wave2Vec | `wave2vec` |

If multiple modules could apply and prompt does not disambiguate, use placeholder(s) instead of guessing.

| `qtimlpostprocess` property | Value | When to use |
|---|---|---|
| `bbox-stabilization` (bool, default false) | `bbox-stabilization=true` | Object detection (yolov8, yolov5, yolo-nas), face detection (qfd), and palm detection (palmd) pipelines - reduces bounding box jitter across frames |

## Module Output Types — Format Support by Category

`qtimlpostprocess` declares `text/x-raw`, `video/x-raw` (image-mask), and `neural-network/tensors` on its src pad for every module — which format is actually used is decided by caps negotiation downstream, not fixed per module name. Whether a given module can negotiate to `text/x-raw` depends on its **task category**, not its individual name:

> **Device-verified — never pin `width=`/`height=` on the render-overlay capsfilter right after `qtimlpostprocess` when a downstream `qtivcomposer` sizes the tile.** On the target QLI build, `gst-inspect-1.0 qtimlpostprocess` reports the src-pad video caps as `video/x-raw, format={ RGBA, RGBx }` — `format=BGRA` fails to link (`could not link ... can't handle caps video/x-raw, format=(string)BGRA`). But relabeling the capsfilter to `format=RGBA` while still pinning `width=`/`height=` does **not** fully fix it either — it fails differently, with `Fixated width in filter caps is not supported with current post-process type!` (device-verified). The pinned dimensions are the actual problem, independent of format. The correct fix is to drop `width=`/`height=` from the capsfilter on this branch entirely (a bare `format=RGBA` with no dimensions is fine, or omit the capsfilter altogether) and let the size negotiate; size the branch via the downstream `qtivcomposer` sink-pad `dimensions` instead. Verified by running the AI-wall / segmentation / audio-classification gst-launch references on-device: they FAIL with a pinned `format=BGRA,width=,height=` capsfilter, FAIL differently with `format=RGBA` while still pinning `width=`/`height=`, and only reach PLAYING once the dimensions are dropped. Older docs/examples that pin fixed dimensions (with either `BGRA` or `RGBA`) on this branch are stale — drop the dimensions, don't just relabel the format.
>
> **Do NOT pin `width`/`height` on a `video/x-raw` capsfilter right after `qtimlpostprocess` when a downstream `qtivcomposer` sizes the tile** — pinning dimensions here fails post-process caps fixation regardless of the format used. Leave the size unset (a bare `format=RGBA` capsfilter, or no capsfilter at all, is fine) and let the composer sink-pad `dimensions` scale it. Pinning dimensions is only appropriate when nothing downstream sizes the branch.

| Category | Modules | `text/x-raw` | `video/x-raw` (image mask) |
|---|---|---|---|
| `object-detection` | `yolov8`, `yolov5`, `yolo-nas`, `qfd`, `qpd`, `ssd-mobilenet`, `palmd`, `easy-ocr-detector`, `east-textdt`, `mediapipe-pose` | ✅ | ✅ |
| `image-classification` | `mobilenet`, `mobilenet-softmax`, `qfr`, `qfr-softmax`, `ocr`, `ocr-recognizer` | ✅ | ✅ |
| `pose-estimation` | `hrnet`, `posenet`, `hlandmark`, `mediapipe-pose-landmark`, `lite-3dmm` | ✅ | ✅ |
| `audio-classification` | `yamnet`, `wave2vec` | ✅ | ✅ |
| `image-segmentation` | `deeplab-argmax`, `yolov8-seg` | ❌ | ✅ (only option) |
| `depth-estimation` | `midas-v2` | ❌ | ✅ (only option) |
| `super-resolution` | `srnet` | ❌ | ✅ (only option) |
| `tensor` | `tensor` | — | — (emits `neural-network/tensors` only) |

**Only `image-segmentation`, `depth-estimation`, and `super-resolution` are format-restricted** — the plugin's caps-negotiation logic excludes `text/x-raw` for modules that write pixels directly rather than producing a list of predictions. Every other category (detection, classification, pose, audio-classification) supports **both** formats identically — a module's presence in this table under detection/classification/pose/audio does not mean it is text-only; it means the *category* permits either, and the pipeline author selects the format via topology (see below), not the module choice.

**Typical/example dimensions** for modules that negotiate to `video/x-raw` (illustrative, not fixed — dimensions come from the model's own tensor shape; no capsfilter should be inserted on this branch per the device-verified note above, and the size should be left entirely to negotiation when a composer sizes the tile):

| Module | Example `video/x-raw` output |
|---|---|
| `yolov8`, `yolov5`, `yolo-nas`, `qfd`, `hrnet`, `qpd` | native size; no capsfilter when composed |
| `mobilenet`, `mobilenet-softmax`, `qfr`, `qfr-softmax` | size derived from font size × label count; no capsfilter when composed |
| `deeplab-argmax` | native mask size; no capsfilter when composed |
| `midas-v2` | native depth-map size; no capsfilter when composed |
| `srnet` | upscaled size; no capsfilter when composed |
| `yamnet` | label-panel size; no capsfilter when composed |

**Which format to pick — Topology A vs Topology B:**
- **Topology A** (`text/x-raw → qtimetamux → qtivoverlay`): draws at native video resolution, directly on the real frame, via Cairo — crisp regardless of tile size, and keeps a metadata trail usable by `qtiobjtracker`/RTSP-metadata/logging. Preferred default for single-stream pipelines and whenever the metadata itself is needed downstream.
- **Topology B** (`video/x-raw → qtivcomposer` directly, no capsfilter, no `qtimetamux`/`qtivoverlay`): the mask is rendered once at its module's native negotiated resolution and GPU-blitted by the compositor — fewer elements, one less CPU-side Cairo pass, but the mask is stretched to fill whatever tile size the composer assigns, and its placement must track the composer's `position`/`dimensions` independently of the passthrough tile.
- Only `image-segmentation`, `depth-estimation`, and `super-resolution` modules are *forced* into Topology B. Current `qtimetamux` source can attach `Segmentation` and `Depth` text structures, but do not switch known-good segmentation/depth templates from Topology B until a current sample or device run verifies that full path. Every detection/classification/pose/audio module supports both — the choice is a topology decision, not a module restriction.

### Preference: reuse an existing `qtivcomposer` directly, skip `qtimetamux`/`qtivoverlay`

**Critical: the `video/x-raw` output of a detection/classification/pose/audio module is a transparent mask containing ONLY the drawn boxes/labels/text — it is NOT the video frame.** (Confirmed by `qtimlpostprocess.mdx`: *"This is a transparent frame that contains only ML results."*) Feeding that mask alone into a `qtivcomposer` sink pad produces a tile with no video in it — only the box outlines over whatever the composer's `background` fill is. This is the segmentation/SR pattern too: `deeplab-argmax`/`srnet` always pair their mask with the raw passthrough frame on a **second** sink pad (see Segmentation Topology / Super Resolution Topology sections) — the mask is never the sole content of a tile.

So the direct-to-composer optimization does **not** eliminate the passthrough tee — it only removes `qtimetamux`+`qtivoverlay`. Each optimized stream still needs **two** `qtivcomposer` sink pads at the same `position`/`dimensions`:
```text
<decode> ! queue ! tee name=tN
tN. ! queue ! comp.sink_A                                                       (raw video passthrough — required)
tN. ! queue ! qtimlvconverter ! ... ! qtimlpostprocess ... ! queue ! comp.sink_B   (mask, no capsfilter, alpha=1.0, same position/dimensions as sink_A)
```
`comp.sink_A` (raw) and `comp.sink_B` (mask) must share identical `position`/`dimensions` so the mask lands exactly over its own video tile, and `sink_B::alpha` should be `1.0` (or omitted, default 1.0) unless partial transparency is explicitly wanted. `zorder` defaults to creation order, so declaring `sink_A` before `sink_B` is sufficient to layer the mask on top without setting `zorder` explicitly.

**What `position`/`dimensions` must actually be set to — this is the single most common mistake in Topology B, device-verified:** `position`/`dimensions` default to `<  >` (empty), and an empty `dimensions` means "same as input dimensions" per `gst-inspect-1.0 qtivcomposer` — i.e. full-frame, automatically, with no property set at all. For a single full-screen stream (the common case — one video source, no side-by-side/PiP/AI-wall layout), either leave `position`/`dimensions` unset on both `sink_A` and `sink_B` (each pad's own input caps determine its size, which is correct — the raw pad's real decoded size, the mask pad's model-negotiated size, both rendered at full destination since dimensions default to input size), or explicitly set both to the **decoded source's real width/height** (e.g. `1920x1080`) if you need to force a specific output size. Do NOT set `dimensions` to a small "typical model output" tile size (e.g. `640x360`, borrowed from a model-dimensions table) — that value describes the *model's inference input size*, not the *intended display tile size*, and forcing it here produces a real, observed symptom: video renders correctly composited but confined to a small rectangle in one corner of the screen, with the rest of the display showing flat `background` fill color. This looks like a display/EGL/driver bug but is actually a `dimensions` override that shouldn't have been applied. Only override `dimensions` to a smaller, non-full-frame value when the request is explicitly multistream/AI-wall/side-by-side/PiP, where each stream legitimately owns a sub-tile of the display and every stream's pair of sink pads gets its own tile-sized `position`/`dimensions`.

When a pipeline **already requires `qtivcomposer`** for another reason (multistream/AI-wall, side-by-side, PiP — i.e. the composer is present regardless of AI overlay), and one of the AI branches feeding it uses a `qtimlpostprocess` module whose category supports `video/x-raw` (per the table above), **prefer wiring that branch's `qtimlpostprocess` mask into a second `qtivcomposer` sink pad (paired with the existing raw-passthrough pad as shown above)** instead of adding a separate `qtimetamux` + `qtivoverlay` stage for that stream. This avoids an extra CPU-side draw pass and buffer round-trip when the compositor is going to blit that stream's tile anyway — the composer can do the same box/label rendering as part of the single hardware blit it already performs for that stream's tile.

This is a preference for the already-composing case, not a mandate:
- Do not introduce `qtivcomposer` into a pipeline that otherwise has no compositing requirement just to gain this optimization — Topology A (`qtimetamux`+`qtivoverlay`) remains the default for plain single-stream detection/classification requests.
- Both topologies are permissible for any detection/classification/pose/audio module; when the user's request implies they want the metadata trail preserved (tracking, RTSP metadata channel, external logging) alongside the composited display, keep Topology A for that stream even inside a composer pipeline.

- For `qtimlpostprocess`, use property key `settings` for module configuration.
- `settings` is optional; include it only when the user explicitly asks for postprocess config/settings or threshold-style tuning.
- If user writes "postprocess config", map it to `settings`.
- Never replace `settings` with a `config` property for `qtimlpostprocess`.
- **`settings` value MUST use JSON object format with escaped quotes:** `settings="{\"confidence\": 51.0}"` - NOT semicolon-delimited format like `settings="confidence=51.0;"`. The canonical documented format is always `settings="{\"key\": value}"`.

## Property Name: Always Use `module=`, Never `postprocessing=`

The correct `qtimlpostprocess` property for selecting the postprocessing algorithm is `module=`. The property `postprocessing=` does NOT exist - using it is an error:

```bash
# CORRECT
qtimlpostprocess module=yolov8 labels=...

# WRONG - postprocessing= is not a valid property
qtimlpostprocess postprocessing=object-detection labels=...
```

The canonical `settings` property format for `qtimlpostprocess` is JSON with escaped inner quotes:

```bash
qtimlpostprocess module=yolov8 labels=<LABELS> settings="{\"confidence\": 51.0}"
```

Do NOT use semicolon-delimited format (`settings="confidence=51.0;"`) - this is incorrect and will fail at runtime.

## Runtime Verification (When User Asks)

To verify modules available on device:

```bash
gst-inspect-1.0 qtimlpostprocess
```

Use the module names reported by the target device as source of truth.

---

## Inference Runtimes

# Inference Runtimes

## Use This Reference For

- Selecting the correct inference element for a requested runtime
- Getting exact property names, enum nicks, and default values for each runtime
- Understanding model format requirements and backend library paths
- Swapping runtimes in an existing pipeline

## The Five Inference Elements

All five elements occupy the same position in the pipeline:

```
qtimlvconverter -> <inference element> -> qtimlpostprocess
```

All five accept `neural-network/tensors` on their sink pad and produce `neural-network/tensors` on
their source pad. **Postprocess module selection (`module=yolov8`, `module=qpd`, etc.) is
runtime-agnostic** - the same module names work regardless of which inference element produced the
tensors. `qtimlpostprocess` reads tensor metadata, not the identity of the upstream inference element.

## Runtime Comparison

| Runtime | Element | Model format | Primary backend | Availability |
|---------|---------|-------------|----------------|-------------|
| TFLite / LiteRT | `qtimltflite` | `.tflite` | GPU, XNNPACK, HTP (via external delegate) | Available |
| SNPE | `qtimlsnpe` | `.dlc` | DSP (Hexagon), GPU, CPU, AIP | Available |
| QNN | `qtimlqnn` | `.so` or `.bin` | HTP/NPU, GPU, CPU | Available (qcom-multimedia-proprietary-image build) |
| QAIRT | `qtimlqairt` | `.dlc` or cached context `.bin` | HTP, GPU, CPU | Available in refreshed source; verify target package/plugin availability |
| ONNX Runtime | `qtimlonnx` | `.onnx` | CPU, QNN (HTP/NPU) | Source present; verify target package/plugin availability |

## When to Use Each Runtime

- **qtimltflite** - user has a `.tflite` model and wants TFLite execution; or wants HTP via QNN TFLite external delegate, GPU via the `gpu` delegate, or CPU via `xnnpack`
- **qtimlsnpe** - user has a `.dlc` model (converted via Qualcomm SNPE SDK); DSP delegate gives best latency for many models
- **qtimlqnn** - user has a QNN-compiled `.so` model or cached `.bin`; gives direct access to HTP/NPU without TFLite layer
- **qtimlqairt** - user explicitly requests QAIRT or provides QAIRT/SNPE model evidence and wants a `cpu`/`gpu`/`htp` backend through the QAIRT element (select the backend with the `backend` library-name property, e.g. `backend=libQairtHtp.so`)
- **qtimlonnx** - user has a `.onnx` model; verify `gst-inspect-1.0 qtimlonnx` on the target before claiming device support

---

## qtimltflite - Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | null | Path to `.tflite` model file |
| `delegate` | enum | `none` | Execution backend. Nicks: `none` (0, CPU), `gpu` (5, Adreno GPU), `xnnpack` (6, XNNPACK CPU runtime), `external` (7, external delegate via `external-delegate-path`/`external-delegate-options`) |
| `external-delegate-path` | string | null | Path to external delegate shared library. Required when `delegate=external`. Example: `libQnnTFLiteDelegate.so` |
| `external-delegate-options` | GstStructure | - | Options for external delegate (backend type, backend library path). For QNN HTP: `"QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` |
| `priority` | enum | `min-latency` | Inference priority, GPU delegate precision only. Nicks: `min-latency` (0), `max-precision` (1) |
| `threads` | uint | 1 | Number of CPU threads. Range: 1-4 |
| `qos` | boolean | false | Handle Quality-of-Service events |

### TFLite HTP pattern (external delegate to QNN HTP)

```
qtimltflite model=<model.tflite> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
```

### TFLite GPU pattern

```
qtimltflite model=<model.tflite> delegate=gpu
```

### TFLite XNNPACK (CPU) pattern

```
qtimltflite model=<model.tflite> delegate=xnnpack
```

---

## qtimlsnpe - Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | null | Path to `.dlc` model file |
| `delegate` | enum | `none` | Execution backend |
| `performance-profile` | enum | `default` | Performance/power tradeoff |
| `profiling-level` | enum | `off` | Runtime diagnostics verbosity |
| `priority` | enum | `normal` | Execution priority |
| `layers` | GstValueArray of strings | empty | Optional output layer filter, order-preserving. Mutually exclusive with `tensors`. |
| `tensors` | GstValueArray of strings | empty | Optional output tensor filter, order-preserving. Mutually exclusive with `layers`. |
| `qos` | boolean | false | Handle Quality-of-Service events |

**`delegate` enum nicks:**

| Nick | Target |
|------|--------|
| `none` | CPU |
| `dsp` | Hexagon DSP (recommended for latency on QCS6490/IQ devices) |
| `gpu` | Adreno GPU |
| `aip` | Snapdragon AIX + HVX |

**`performance-profile` enum nicks:**

| Nick | Description |
|------|-------------|
| `default` | Standard mode |
| `balanced` | Balance between performance and power |
| `high-performance` | Maximum performance |
| `power-saver` | Low power mode |
| `system-settings` | Use system settings |
| `sustained-high-performance` | High performance maintained over time |
| `burst` | Maximum burst performance |
| `low-power-saver` | Lower clock than power-saver |
| `high-power-saver` | Higher clock and better performance than power-saver |
| `low-balanced` | Lower balanced mode |

**`profiling-level` enum nicks:** `off`, `basic`, `detailed` (per-layer statistics), `moderate`

**`priority` enum nicks:** `normal`, `high`, `low`

**`layers` vs `tensors`:**
- Both are optional filters — default is empty, meaning "emit every model output tensor, in the model's native order." Setting one is only needed when there's a known reason (see "Tensor Filter — Decision Rule" below) — do not set either by default.
- When one is needed: they filter which outputs are emitted and in what order, and are mutually exclusive — setting one clears the other.
- Use `layers` if your model identifies outputs by layer name; use `tensors` if it identifies outputs by tensor name.
- Never invent a placeholder name — get the real name from the model's conversion docs or `gst-inspect-1.0 qtimlpostprocess` (see decision rule).

**In gst-launch, GstValueArray is written as a comma-separated list in angle brackets:**
```
tensors="<output_tensor_name>"
layers="<layer_name_0>,<layer_name_1>"
```

The canonical patterns below omit `tensors=`/`layers=` since the unfiltered
default is correct absent a known reason to filter — see the note after these
patterns for when to add it.

### SNPE DSP pattern

```
qtimlsnpe model=<model.dlc> delegate=dsp
```

### SNPE DSP with performance profile

```
qtimlsnpe model=<model.dlc> delegate=dsp performance-profile=sustained-high-performance
```

### SNPE GPU pattern

```
qtimlsnpe model=<model.dlc> delegate=gpu
```

### SNPE CPU pattern

```
qtimlsnpe model=<model.dlc> delegate=none
```

If the model's native output tensors don't match what the downstream `module=`
expects (extra/debug tensors, non-default order), add the filter with real names:
```
qtimlsnpe model=<model.dlc> delegate=dsp tensors="<real_tensor_name_from_model_docs>"
```

---

## qtimlqnn - Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | null | Path to `.so` model shared library or `.bin` cached binary. Required. |
| `backend` | string | `/usr/lib/libQnnCpu.so` | Path to QNN backend shared library |
| `system` | string | `/usr/lib/libQnnSystem.so` | Path to QNN system shared library |
| `backend-device-id` | uint | `0` | Backend device index (for multi-CDSP devices). Range: 0-1 |
| `tensors` | GstValueArray of strings | empty | Optional output tensor filter, order-preserving. "When set, only the specified output tensor names are emitted on the source pad. When empty, all model outputs are emitted." (official element docs) |
| `qos` | boolean | false | Handle Quality-of-Service events |

**Backend library paths by hardware target:**

| Target | `backend` value |
|--------|----------------|
| NPU / HTP (recommended for inference) | `/usr/lib/libQnnHtp.so` |
| Adreno GPU | `/usr/lib/libQnnGpu.so` |
| CPU | `/usr/lib/libQnnCpu.so` (default) |

`tensors=` is optional here too — default (empty) emits every model output tensor.
Omit it unless your model/module pairing has a known reason to need it (see
"Tensor Filter — Decision Rule" below).

### QNN HTP/NPU pattern (recommended)

```
qtimlqnn model=<model.so> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so
```

### QNN GPU pattern

```
qtimlqnn model=<model.so> backend=/usr/lib/libQnnGpu.so system=/usr/lib/libQnnSystem.so
```

### QNN CPU pattern

```
qtimlqnn model=<model.so> backend=/usr/lib/libQnnCpu.so system=/usr/lib/libQnnSystem.so
```

### QNN cached binary pattern

```
qtimlqnn model=<model.bin> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so
```

If the model's native output tensors don't match what the downstream `module=`
expects, add the filter with real names:
```
qtimlqnn model=<model.so> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so tensors="<real_tensor_name_from_model_docs>"
```

---

## qtimlqairt - Full Property Reference

Use `qtimlqairt` only when the user explicitly asks for QAIRT or when the model/runtime evidence clearly points to QAIRT/SNPE containers or cached context binaries. Do not silently substitute it for TFLite, QNN, or SNPE requests.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | null | Path to a `.dlc` QAIRT/SNPE container or cached context `.bin` |
| `backend` | string | `libQairtCpu.so` | QAIRT backend shared-library name. Use `libQairtHtp.so` for NPU/HTP, `libQairtGpu.so` for GPU, `libQairtCpu.so` for CPU. Required (non-null) for GPU/HTP; the engine fails to create if no backend library is set. Replaces the removed `delegate` enum — there is no delegate-nick-to-library mapping anymore, pass the `.so` name explicitly. |
| `priority` | enum | `normal` | Execution priority hint. Nicks: `normal`, `low`, `normal-high`, `high`, `critical`. Some backends may reject priority configuration; the plugin can retry context creation without the priority hint. |
| `layers` | array of strings | empty | Optional output layer list. Alternative name for the same output-selection list as `tensors`; whichever property is set last replaces the output list. |
| `tensors` | array of strings | empty | Optional output tensor list. Alternative name for the same output-selection list as `layers`; the outputs are generated in the order defined by this list. |
| `qos` | boolean | false | Handle Quality-of-Service events |

### QAIRT HTP pattern

```
qtimlqairt model=<model.dlc> backend=libQairtHtp.so
```

### QAIRT cached-context pattern

```
qtimlqairt model=<context.bin> backend=libQairtHtp.so
```

As with SNPE/QNN output filters, omit `layers`/`tensors` unless the model documentation or postprocess module pairing requires a known output subset/order. `qtimlqairt` consumes and produces `neural-network/tensors`; its source caps advertise common integer and float tensor types, while the QAIRT engine converts/dequantizes native outputs to FLOAT32 blocks for downstream elements.

---

## qtimlonnx - Full Property Reference

> **Build status:** `qtimlonnx` source is present in the refreshed source tree, but target package/plugin availability still must be verified with `gst-inspect-1.0 qtimlonnx`. Always include a README note when generating ONNX pipelines unless the target has already been verified.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | null | Path to `.onnx` model file. Required, construct-only. |
| `execution-provider` | enum | `cpu` | Execution backend |
| `backend-path` | string | null | Path to QNN backend lib. Required when `execution-provider=qnn`. Construct-only. Example: `/usr/lib/libQnnHtp.so` |
| `htp-performance-mode` | enum | `default` | HTP performance mode. Only applies when `execution-provider=qnn` |
| `optimization-level` | enum | `enable-extended` | ONNX Runtime graph optimization level |
| `threads` | uint | 1 | Intra-op threads, mostly affects CPU execution. Range: 1-16. Construct-only. |

**`execution-provider` enum nicks:**

| Nick | Target |
|------|--------|
| `cpu` | CPU execution |
| `qnn` | Qualcomm AI accelerator / HTP/NPU via QNN |

**`htp-performance-mode` enum nicks (when execution-provider=qnn):**

| Nick | Description |
|------|-------------|
| `default` | Default mode |
| `burst` | Maximum performance |
| `balanced` | Balance performance/power |
| `low-balanced` | Lower balanced mode |
| `high-performance` | High performance |
| `extreme-power` | Extreme power mode |
| `low-power` | Low power mode |
| `sustained-high-performance` | Sustained high performance |

**`optimization-level` enum nicks:** `disable-all` (0), `enable-basic` (1), `enable-extended` (2, default), `enable-all` (3)

**Layout detection:** `qtimlonnx` auto-detects NCHW/NHWC layout for 4-D outputs via `Transpose` node inspection
and adds `layout=nchw` to src caps when applicable (defaults to NCHW if no Transpose node is found).

### ONNX CPU pattern

```
qtimlonnx model=<model.onnx> execution-provider=cpu
```

### ONNX QNN/HTP pattern

```
qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so
```

### ONNX QNN/HTP with performance mode

```
qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so \
  htp-performance-mode=sustained-high-performance
```

---

## Runtime Swap Pattern

To swap runtimes in the same pipeline, replace only the inference element and its properties.
Everything else - source, preprocess (`qtimlvconverter`), postprocess (`qtimlpostprocess`), overlay,
sink - stays identical.

**Example: single-stream detection, file source -> display**

TFLite HTP:
```
qtimlvconverter ! \
qtimltflite model=<model.tflite> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

SNPE DSP (same pipeline, swap inference stage only):
```
qtimlvconverter ! \
qtimlsnpe model=<model.dlc> delegate=dsp ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

QNN HTP:
```
qtimlvconverter ! \
qtimlqnn model=<model.so> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

QAIRT HTP:
```
qtimlvconverter ! \
qtimlqairt model=<model.dlc> backend=libQairtHtp.so ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

ONNX QNN (verify target plugin availability):
```
qtimlvconverter ! \
qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

`tensors=` is omitted above because `module=yolov8` is a known module and none of
these runtimes has a documented reason to filter/reorder its output for this
model — see "Tensor Filter — Decision Rule" below before adding it back for a
different model.

---

## Clarification Rule

If the user does not specify a runtime, ask before generating:

> "Which inference runtime should the pipeline use? Options: TFLite (`.tflite`), SNPE (`.dlc`), QNN (`.so`/`.bin`), QAIRT (`.dlc`/cached `.bin`), or ONNX (`.onnx`, target plugin availability must be verified)."

Backend/delegate selection changes the element, properties, and model format - generating without
knowing the runtime produces structurally wrong code.

---

## Known Constraints

- `qtimlqairt` is available in the refreshed source tree. Use it only when QAIRT is requested or strongly implied; do not default to it.
- `qtimlonnx` source is present in the refreshed source tree. Verify target plugin availability before claiming device support; otherwise add a README note when generating ONNX pipelines.
- `qtimlsnpe` `layers` and `tensors` are mutually exclusive — setting one clears the other; both default to empty (unfiltered). **Property names are exact and fail silently with wrong names:** use `tensors` (NOT `output-tensors`, NOT `tensor-names`, NOT `output-layers`); delegate nicks are lowercase (`dsp`, `gpu`, `none`, `aip`). See "Tensor Filter — Decision Rule" below for when to actually set one.
- `qtimlqnn` `backend` and `system` default to CPU. For NPU inference, `backend=/usr/lib/libQnnHtp.so` must be set explicitly. Always set both. **Property names are exact:** use `tensors` (NOT `output-tensors`, NOT `tensor-names`); `backend` (NOT `backend-path`); `system` (NOT `system-lib`, NOT `backend-extra`). `tensors` defaults to empty (unfiltered) — see decision rule below for when to set it. `qtimlqnn` requires the qcom-multimedia-proprietary-image build.
- `qtimltflite` supports three CPU/GPU delegate paths directly (`none`=CPU, `xnnpack`=XNNPACK CPU runtime, `gpu`=Adreno GPU) plus `external` for QNN HTP via `external-delegate-path`/`external-delegate-options`.
- All five inference elements occupy the same pipeline position and have compatible tensor caps — they are runtime swaps only when the model format and target plugin availability match.
- **Camera source element:** Use the documented default ISP camera source for the target SDK (currently `qtiqmmfsrc`). If the user explicitly requests a different element, or if newer SDK documentation shows a different default (e.g., `qticamsrc`), use the user's choice or the newest documented recommendation. Both elements occupy the same pipeline position.

---

## Tensor Filter — Decision Rule

`qtimlqnn.tensors` and `qtimlsnpe.tensors`/`layers` are an **optional output
filter + orderer**, not a required mapping step. Default (empty) means "emit
every model output tensor, in the model's native order" — the correct choice most
of the time. `qtimlpostprocess module=<name>` (e.g. `yolov8`, `qfd`, `qpd`,
`hrnet`, ...) is a separate shared library with its own fixed expectation of input
tensor count/shape/order; the filter exists only to reconcile the model's native
output with that expectation when they don't already match.

**Default: omit `tensors=`/`layers=` entirely.** Only add one when there's
positive evidence of a mismatch:
- the model is known (from its own conversion docs or the model's own metadata)
  to emit extra/debug tensors beyond what `module=` expects, or
- the model documentation states a non-default output order that the target
  module doesn't already assume, or
- **`model-catalog.md` lists a `tensors=` value for this model.** SNPE models
  that bake Non-Maximum Suppression (NMS) into the graph — the step that
  collapses hundreds of overlapping candidate boxes down to one box per
  object — commonly export the NMS result as several separate named tensors
  (e.g. `boxes`, `scores`, `class_idx`) instead of one combined tensor.
  `qtimlsnpe` without `tensors=` forwards only the first native tensor, so the
  rest are silently dropped and `qtimlpostprocess` fails to negotiate caps.
  Confirmed examples: SNPE DSP object detection (`tensors="<boxes,scores,class_idx>"`,
  Template 13) and SNPE GPU classification (`tensors="<class_logits>"`,
  Template 15) — check the catalog before assuming the plain default is safe.
  **Only use a name/order confirmed in `model-catalog.md` or a template for
  that specific model family — never guess or reuse a different model's names.
  This skill generates offline and has no device access to verify a model's
  real output tensors (e.g. via `snpe-net-run`), so if the exact model isn't
  one of the confirmed cases, omit `tensors=`/`layers=` per the default above
  rather than assuming the tensor names transfer.**

(`gst-inspect-1.0 qtimlpostprocess` would show a module's expected input tensor
shapes, but this skill has no device access — that check is not available to the
agent; rely on the model's own documentation/metadata instead.)

**Elevated uncertainty for undocumented modules:** if `module=` is not one of
the documented values (see the Postprocess Modules / Supported Module Table),
treat "no evidence of mismatch" as **unverified**, not as **confirmed safe** —
a custom/proprietary module has no corpus precedent, so the ordinary default
carries more risk of silently being wrong than it does for a well-known module
like `yolov8`. Still default to omitting `tensors=`/`layers=` (the "never invent
a placeholder" rule below doesn't change) — but state in the README's
"Assumptions" section (see `artifact-contract.md`) that `module=<name>` is
undocumented in this skill and that tensor filtering was omitted unverified, on
the assumption the model's native output already matches what that module
expects.

**Never invent a placeholder tensor name.** If the exact name isn't known and
there's no positive evidence of a mismatch, omit the property — do not write
`tensors="<OUTPUT_TENSOR>"` or any other unfillable placeholder; that produces an
unresolved artifact, not a working one. If a mismatch is suspected but the name
truly can't be determined, say so in the README and ask the user for the model's
conversion documentation.

**`qtimltflite` has no filter property at all** — it always emits every output
tensor in native order. If a TFLite model's native output doesn't match the target
module, that's a hard mismatch to fix by re-exporting the model correctly, not
something a pipeline property can patch around.

**SNPE practical note:** `.dlc` compiles more often retain extra/debug output
nodes than QNN/TFLite exports do, so it's reasonable to check SNPE model outputs
against the module's expectation even without an explicit prompt to — but this is
a probability-based habit, not a hard requirement, and the same decision procedure
above still applies.

**Known source-pattern quirk — do not imitate:** one batched multistream QNN
branch sets `tensors=` alongside a known `module=yolov8` match, almost
certainly copy-paste residue from the SNPE branch directly above it in that file.
This is not evidence that QNN needs the filter when a module is known.

---

## Performance Profile Recommendations (SNPE)

Use these as defaults when the user mentions a deployment goal:

| Use case | Recommended profile |
|----------|-------------------|
| Continuous real-time streaming | `sustained-high-performance` |
| Latency-critical single-shot | `burst` |
| General real-time | `high-performance` |
| Battery / power constrained | `power-saver` or `low-power-saver` |
| No specific requirement | `default` (omit property) |

---

## SNPE vs QNN - When to Use Each

| Situation | Use |
|-----------|-----|
| User has an existing `.dlc` model file | SNPE (`qtimlsnpe`) |
| User has a `.so` or `.bin` QNN model | QNN (`qtimlqnn`) |
| New development, no existing model | QNN is the forward path |
| User explicitly asks for SNPE SDK | SNPE |

**Key difference:** SNPE uses DLC format (converted via Qualcomm SNPE SDK). QNN uses `.so`/`.bin` compiled via QAIRT SDK. Both target Hexagon HTP/NPU. QNN is the recommended path for new development.

**SNPE delegate is self-contained** - the `delegate` enum selects the hardware target directly. No backend library path is needed (unlike QNN's `backend` property).

---

## File Sink Output Pattern

When the user does not have a display (no waylandsink), encode the annotated output to MP4:

```
qtivoverlay ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! \
mp4mux ! filesink location=<OUTPUT>.mp4
```

This replaces `waylandsink fullscreen=true sync=true` at the end of the pipeline. The full path is:
```
qtimetamux -> qtivoverlay -> v4l2h264enc capture-io-mode=4 output-io-mode=4 -> h264parse -> mp4mux -> filesink location=/home/ubuntu/Downloads/qimsdk_samples/media/output/<filename>.mp4
```

**For SNPE GPU classification (Topology B - qtivcomposer):**
The SNPE GPU classification example uses `qtivcomposer` instead of `qtimetamux/qtivoverlay`. For file output:
```
qtivcomposer ! video/x-raw,format=NV12 ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! \
mp4mux ! filesink location=<OUTPUT>.mp4
```
