# AI Pipeline Patterns

## Standard Discrete AI Flow

Use:

`source -> decode/camera -> VideoFilter(NV12) -> qtimlvconverter -> qtimltflite -> qtimlpostprocess -> metadata/use`

For overlay:

`source -> decode/camera -> VideoFilter(NV12) -> tee`

`tee video branch -> qtimetamux -> qtivoverlay -> display/file`

`tee AI branch -> qtimlvconverter -> qtimltflite -> qtimlpostprocess -> TextFilter() -> qtimetamux`

Metadata branch should pass through `TextFilter()` before `qtimetamux`. Do not generate a single linear overlay chain that sends only `qtimlpostprocess -> TextFilter() -> qtimetamux -> qtivoverlay`; preserve a video branch and merge metadata back into it.

## Direct-To-Composer AI Overlay

Use this topology when the selected postprocess path produces rendered video frames or when the user explicitly asks for composer-based overlay/alpha-blending/side-by-side output.

```text
source/decode/camera -> VideoFilter(NV12) -> tee
tee passthrough branch -> queue -> qtivcomposer
tee AI/render branch -> queue -> qtimlvconverter -> queue -> qtimltflite
                 -> queue -> qtimlpostprocess or MLPostprocess callback
                 -> VideoFilter(RGBA or requested rendered format) -> qtivcomposer
qtivcomposer -> display/file
```

Minimal Python link shape:

```python
pipeline.add_stream_filter("vf", VideoFilter().format("NV12"))
pipeline.add(split)
pipeline.add(q_video)
pipeline.add(q_ai)
pipeline.add(pre)
pipeline.add(q_infer)
pipeline.add(infer)
pipeline.add(q_post)
pipeline.add(post)
pipeline.add_stream_filter("render_filter", VideoFilter().format("RGBA"))
pipeline.add(composer)
pipeline.add(display)

pipeline.link("source", "vf", "split")
pipeline.link("split", "q_video", "composer")
pipeline.link("split", "q_ai", "pre", "q_infer", "infer", "q_post", "post", "render_filter", "composer")
pipeline.link("composer", "display")

pipeline.get("composer").input(1).set("alpha", 0.5)
```

Rules:

- Use `qtimetamux + qtivoverlay` for text metadata overlays such as boxes/keypoints/class labels when no composer is otherwise needed.
- Use `qtivcomposer` when the postprocess branch emits or renders video frames (`video/x-raw` RGBA masks, segmentation overlays, SR comparison panes, side-by-side output).
- **The render `VideoFilter` after `qtimlpostprocess` must be `.format("RGBA")`** (never `BGRA`/`RGB` — src caps are `video/x-raw,{RGBA,RGBx}`; anything else fails to link with `could not link ... can't handle caps`). **Resolution is conditional:** when this branch feeds a `qtivcomposer` whose sink pad sets `dimensions`, do NOT also pin `.resolution()` here — device-verified, it fails caps fixation with `Fixated width in filter caps is not supported with current post-process type!` regardless of format; let the composer's sink-pad `dimensions` size it. Pin `.resolution(w, h)` ONLY when nothing else sizes the branch (a small overlay panel composited with no per-pad `dimensions`). See `plugin-catalog.md` "Module Output Types".
- Do not send a rendered-video branch through `TextFilter()` or `qtimetamux`.
- Do not add `qtivtransform` after `qtivcomposer` before encode; constrain composer output with an NV12 `VideoFilter` before the encoder if file output requires NV12.
- Set composer input alpha/geometry only when the requested layout is clear. If geometry affects correctness and is not specified, ask.

## Segmentation / Video-Output Postprocess

Use the direct-to-composer shape for segmentation and other postprocess outputs that produce video-overlay frames rather than text metadata. The SDK examples use a passthrough branch and an AI branch that returns RGBA rendered output to `qtivcomposer`.

```text
source/decode -> VideoFilter(NV12) -> tee
tee passthrough branch -> queue -> qtivcomposer
tee AI branch -> queue -> qtimlvconverter -> queue -> qtimltflite
          -> queue -> qtimlpostprocess/MLPostprocess(image-segmentation)
          -> VideoFilter(RGBA) -> qtivcomposer
qtivcomposer -> display/file
```

Rules:

- Built-in segmentation modules and custom `Segmentations` callbacks use this composer topology.
- For monodepth/depth-map postprocess (`midas-v2`), the render filter is `VideoFilter().format("RGBA")` with NO resolution — pinning the size (e.g. `.resolution(256, 144)`) makes postprocess caps fixation fail. `qtivcomposer` scales the RGBA depth branch into the requested pane through its sink-pad `dimensions`.
- Do not force segmentation into `TextFilter() -> qtimetamux -> qtivoverlay`; that is a text metadata topology.
- For custom segmentation callbacks, follow `references/ml-postprocess.md` for the `Segmentations` marker type and `GstQtiML-1.0.typelib` runtime dependency.

## Super Resolution

Use this for QuickSRNet / super-resolution / upscaling requests. This is a pure composer topology, not a metadata overlay topology.

```text
file/decode -> VideoFilter(NV12) -> tee
tee passthrough branch -> queue -> qtivcomposer sink_0
tee SR branch -> qtimlvconverter -> queue -> qtimltflite(external HTP)
          -> queue -> qtimlpostprocess(module=srnet)
          -> VideoFilter(RGBA) -> queue -> qtivcomposer sink_1
qtivcomposer -> display/file
```

Rules:

- Do not use `qtimetamux` or `qtivoverlay`.
- Use `qtimlpostprocess module="srnet"`.
- Use TFLite external delegate options for HTP unless the user provides exact options.
- For side-by-side comparison, use `qtivcomposer` geometry: passthrough left and SR output right. Ask if layout dimensions are not specified.
- For MP4 output, place `VideoFilter().format("NV12")` directly after `qtivcomposer` before `v4l2h264enc`; do not insert `qtivtransform` after the composer.

## Audio AI Classification

Use this for YAMNet or audio-classification-over-video requests. Audio AI is not a `TextFilter()`/`qtimetamux` overlay. `qtdemux` naturally splits the video and audio pads.

```text
filesrc -> qtdemux
demux video pad -> queue -> h264parse -> v4l2h264dec -> VideoFilter(NV12) -> queue -> qtivcomposer
demux audio pad -> queue -> flacparse -> flacdec -> queue -> audioconvert -> audioresample
              -> audiobuffersplit(output-buffer-size=31200) -> queue
              -> qtimlaconverter(sample-rate=16000)
              -> queue -> qtimltflite -> qtimlpostprocess(module=yamnet, settings={"confidence": 10.0}, results=3)
              -> VideoFilter(RGBA) -> queue -> qtivcomposer
qtivcomposer -> display/file
```

Rules:

- Use `module="yamnet"` and `results=3` unless user overrides.
- Default confidence for YAMNet is `{"confidence": 10.0}` unless user provides another threshold.
- Do not set a delegate in the canonical CPU path unless the user requests a backend.
- The audio overlay branch outputs an RGBA label panel and feeds `qtivcomposer`; do not route it through `TextFilter()` or `qtimetamux`. Use `VideoFilter().format("RGBA")` with no pinned resolution; the composer sink-pad `dimensions` size the panel (e.g. `[368, 64]`).
- The composer panel geometry uses list values: `pipeline.get("composer").input(1).set("position", [50, 50])` / `.set("dimensions", [368, 64])` — never gst-array strings like `"<50, 50>"`, never element-level `sink_1::position`.
- For audio-classification display pipelines specifically, omit the `sync` property on `waylandsink` entirely — do not set it to `True` or `False`. This exception is unique to audio-classification; it does not apply to other pipeline types, which default to `sync=True`.
- For MP4 output, use an NV12 filter immediately after `qtivcomposer` before the encoder.

## Face Recognition Daisy-Chain

Use this for face recognition / identity / verification requests, or when the user names `face_det_lite`, `facemap_3dmm`, or `face_attrib_net` as a recognition chain. Do not confuse this with plain face detection.

Mandatory order:

1. Stage 1 face detection: `qtimlvconverter mode="image-batch-non-cumulative"` -> `qtimltflite` -> `qtimlpostprocess module="qfd"` with detection labels, `results=6`, and optional confidence settings.
2. Stage 2 face landmark / 3DMM: `qtimlvconverter mode="roi-batch-cumulative"` with `image-disposition="centre"` -> `qtimltflite` -> `qtimlpostprocess module="lite-3dmm"` with `results=6` and mandatory `settings=<FACEMAP_3DMM_SETTINGS_PATH>`. Do not set `labels` on Stage 2.
3. Stage 3 face recognition: `qtimlvconverter mode="roi-batch-cumulative"` with `image-disposition="centre"` -> `qtimltflite` -> `qtimlpostprocess module="qfr"` with recognition labels, `results=6`, and mandatory `settings=<FACE_RECOGNITION_SETTINGS_PATH>` database config.

Topology:

```text
source/decode -> VideoFilter(NV12) -> split1
split1 passthrough -> metamux_1 -> split2
split1 stage1 -> qfd text -> metamux_1
split2 passthrough -> metamux_2 -> split3
split2 stage2 -> lite-3dmm text -> metamux_2
split3 passthrough -> metamux_3 -> qtivoverlay -> display/file
split3 stage3 -> qfr text -> metamux_3
```

Rules:

- Use exactly three `qtimetamux` stages: one per recognition stage.
- Never run `qfr` directly after face detection. It must consume the landmark-aligned Stage 2 output.
- Do not reuse one labels/settings pair across stages. If the user supplies ambiguous files, ask which file belongs to which stage.
- Stage 2 settings must point to the facemap 3DMM settings file and the runtime must have `blendShape.bin`, `meanFace.bin`, and `shapeBasis.bin` available under `/etc/data/` unless the user/reference provides another documented data location.
- Stage 3 settings must point to the registered face database entries; recognition can run without matches but will not identify anyone without valid database entries.
- Use TFLite external delegate HTP options for all three stages unless the user provides another documented backend. Stage 2 and Stage 3 must use `QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;` unless the user provides exact delegate options.

## AI Metadata Parser / Extractor

Use this when the user asks to parse inference metadata, extract bounding boxes programmatically, count objects, or send metadata to an app boundary. This is not a plain overlay-only request.

Topology:

```text
source/decode/camera -> VideoFilter(NV12) -> tee
tee video branch -> qtimetamux -> qtivoverlay -> display/file
tee AI branch -> qtimlvconverter -> qtimltflite -> qtimlpostprocess -> tee/detection split
detection split display branch -> TextFilter() -> qtimetamux
detection split app branch -> qtimlmetaparser(module=json) -> AppSink   (JSON text, or)
detection split app branch -> qtimlmetaextractor -> AppSink              (GstStructure)
```

Rules:

- Keep the display overlay branch and metadata-parser branch separate after `qtimlpostprocess`.
- Use `qtimlmetaextractor` only when the request is for serialized/raw metadata extraction and the plugin is available on the target.
- Use `AppSink` with `set_buffer_consumer(...)` for Python-side consumption; read the payload with `Buffer.data()` (see `api-surface.md`). For `qtimlmetaparser(module=json)` the JSON schema IS documented (`plugin-catalog.md` — top-level `object_detection`, per-entry `label`/`confidence`/`rectangle`), so parsing it is allowed. For any other serializer whose exact structure the references do NOT document, leave explicit TODO comments rather than inventing field names.
- If the video branch is composed by a `qtivcomposer` anywhere in the pipeline, the in-place `qtivoverlay` cannot get a writable buffer off the shared tee — insert `qtivtransform ! VideoFilter(NV12)` on the overlay branch before `qtimetamux`. A leaf video `AppSink` off the same tee also poisons the buffer pool; isolate it with a `qtivtransform` before the AppSink (see `source-sink-patterns.md` "Buffer Writability Under Shared Tees").

## Batched Multi-Stream AI

Use this when many streams run the same detector/classifier and the request calls for batching or a high stream count where one inference instance per stream is inappropriate (roughly 8+ streams sharing one detector/classifier). The naive approach — one `qtimltflite` per stream — instantiates N independent graphs on the HTP and exhausts its memory (`error 6020`). Batching lets B streams share one graph.

### How the batch pattern works

Reason from what each element actually does to the buffers flowing through it; the topology and rules below follow directly from this.

- **`qtibatch`** stacks one buffer from each of its linked `sink_%u` request pads into a single batched buffer. There is no `batch-size` property — the batch depth is however many streams you `pipeline.link(...)` into it, and that depth travels downstream as caps (`qtimlvconverter` writes `views=<depth>`; the model must be compiled for that same depth, e.g. a `..._batch_4.tflite` for a 4-stream group — a batch-1 model fed a 4-frame buffer forces the delegate to instantiate the graph 4 times on the HTP and blows up memory). `qtibatch` also tags each stacked frame with a `stream-id` equal to that pad's position in link order — this tag is what makes correct demultiplexing possible later. It only emits once every linked pad has produced a buffer, so every requested sink pad must actually receive a stream or the whole group stalls.
- **`qtimldemux`** reverses `qtibatch`: it has request src pads (`add_stream_filter`/`pipeline.link` targets you create per stream) and, for each channel in the batched tensor, routes it to the output whose position matches the `stream-id` tag — not by inspecting content. Concretely: **the k-th stream you linked into `qtibatch` comes out of the k-th output you link out of `qtimldemux`.** This is an index match, not a convention you have to remember to preserve — but you must link demux outputs in the exact same order you linked batch inputs, or stream k's detection results land on stream j's tile.
- **`qtimlpostprocess`**'s rendered output (RGBA) is a transparent mask containing only drawn boxes/labels/text — it is never the video frame (see `plugin-catalog.md` "Preference: reuse an existing `qtivcomposer` directly"). A batched pipeline that feeds only this mask into the composer, with no matching passthrough pad, displays a colored detection canvas instead of video — the original decoded frame was never wired in. The passthrough branch per stream is therefore not optional scaffolding; it is required by what the mask actually contains.
- **`qtivcomposer`** blends sink pads by request-pad creation order when `zorder` is not set explicitly (earlier-created pads paint underneath, later ones on top). Two pads per stream at identical `position`/`dimensions` — passthrough underneath, detection mask on top — is what makes the video show through with boxes overlaid; call `composer.input(passthrough_pad)...` before `composer.input(detection_pad)...` for each stream to get this z-order without setting it explicitly.

Topology (per-stream interleaved composer pads — this is required, not a stylistic choice, because each stream needs its own passthrough+mask pair at one tile position):

```text
N decode/camera streams -> each stream tee
  passthrough leg  -> qtivcomposer input(2*i)       (raw decoded video, stream i)
  AI leg           -> groups of B streams -> qtibatch -> qtimlvconverter
                       -> shared qtimltflite(batch-B model) -> qtimldemux
                       -> per-stream qtimlpostprocess -> VideoFilter(RGBA, no resolution)
                       -> qtivcomposer input(2*i + 1)  (detection/render mask, stream i, same position/dimensions as 2*i)
qtivcomposer -> display/file
```

Do not use block-contiguous pad ranges (`sink_0..sink_N-1` then `sink_N..sink_2N-1`); assign each stream's own pair of adjacent pad indices (`2*i`, `2*i+1`) and set identical `position`/`dimensions` on both. Link the passthrough pad before the detection pad for a given stream so default creation-order z-order layers the mask on top without an explicit `zorder`.

Rules:

- `qtibatch` batch size is determined by linked sink pads and caps, not by a `batch-size` property; use a model compiled for that batch depth (a batch-4 model for a 4-stream group).
- Every stream fed into a `qtibatch` group must reach it — a group stalls entirely if one of its linked pads never receives a buffer.
- Link `qtimldemux` outputs in the exact same order the corresponding streams were linked into `qtibatch`; this is how per-stream identity survives the round trip (see "How the batch pattern works" above). Swapped order silently swaps detections between tiles — it does not error.
- Every stream that feeds a batch group also needs its own passthrough branch straight from that stream's `tee` into the composer — never wire only the rendered mask. See `plugin-catalog.md` "Preference: reuse an existing `qtivcomposer` directly" for why the mask alone is insufficient.
- Rendered postprocess caps must be `VideoFilter().format("RGBA")` with NO pinned resolution (`BGRA` fails to link; pinning w/h breaks caps fixation — see `plugin-catalog.md` "Module Output Types"). Composer pad `position`/`dimensions` scale that rendered output into the tile.
- Configure each `qtivcomposer` sink pad through `composer.input(<pad-index>).set("position", [x, y])` and `.set("dimensions", [w, h])` — Python list values only. Do not use scalar pad names such as `x`, `y`, `width`, or `height`, gst-array strings like `"<x, y>"`, or element-level `sink_N::position`.
- **If any parallel branch contains a `qtivcomposer` (or the passthrough tile is composed while a `qtimetamux`/`qtivoverlay` overlay chain runs on another branch off the same tee), the composer holds tee buffers for stream sync (refcount > 1) and the in-place overlay silently draws nothing.** Insert `qtivtransform ! VideoFilter(NV12)` on each overlay branch's passthrough leg (before `qtimetamux`) to force sole buffer ownership. See `source-sink-patterns.md` "Buffer Writability Under Shared Tees".
- Do not force overlay format to NV12; use the rendered output format/dimensions documented for the selected module unless a reference requires otherwise.
- Use `waylandsink sync=False` only when this batched AI wall has more than 8 independent concurrently active input streams, where the processing-heavy shared/batched inference and long HTP graph preparation make frames arrive well behind their PTS, causing `sync=True` to drop late frames and freeze/blacken the display. A normal AI wall (for example a 4-stream wall, or any batch group of 8 or fewer streams) is not processing-heavy enough to trip this and stays `sync=True` unless the user overrides.
- For multiple HTP/NPU batch groups, round-robin inference instances across
  available HTP devices when practical. Detect dual HTP with
  `os.path.exists("/dev/fastrpc-cdsp1")`; use
  `htp_device_id=(string)<group_index % htp_count>` and
  `htp_performance_mode=(string)2` in each batch group's
  `external-delegate-options` unless the user provides exact delegate options.
- For high stream counts with many independent file/RTSP decode branches, raise
  the process file descriptor limit before constructing/executing the pipeline.
  Use `10000` for 24-32 stream artifacts; warn in the README that users can run
  `ulimit -n 10000` first if the program cannot raise the limit.

## GStreamer AI Topology Parity

The Python SDK must preserve GStreamer-supported application topologies even
when the refreshed Python examples do not include a named counterpart. Use
explicit `Element` construction for plugin-level routes and keep SDK wrappers
only where the Python source exposes them.

### Mixed AI Wall

For a mixed wall, keep one independent source/decode branch per stream, run the
requested AI stage on each branch, and feed the rendered result to a named
`qtivcomposer` sink pad. Do not collapse different model stages into one shared
inference path unless the request explicitly asks for batching.

```text
stream_N -> NV12 -> AI preprocess/inference/postprocess -> overlay or composer sink_N
all source passthrough/AI outputs -> qtivcomposer -> waylandsink or encoder
```

Use one queue per branch where branch scheduling is required. Preserve explicit
composer geometry and use direct composer input for video-output postprocess
modules; use `TextFilter -> qtimetamux -> qtivoverlay` for metadata-output
postprocess modules.

**Buffer ownership under a shared tee (critical for parallel walls):** when a
branch uses `qtimetamux`/`qtivoverlay` (in-place metadata draw) while another
branch on the same decode teed source uses a `qtivcomposer`, the composer holds
the tee's buffers for stream sync so the overlay cannot get a writable buffer and
draws nothing (models still infer — only the overlay is blank). Insert
`qtivtransform` + an `NV12` `VideoFilter` on each overlay branch's passthrough leg,
before `qtimetamux`, to force a private buffer copy. See
`source-sink-patterns.md` "Buffer Writability Under Shared Tees".

This is a general shared-tee writability constraint, not specific to the composer
case above: whenever more than one buffer-mutating consumer (in-place overlay,
composer, appsink tap) reads off the same `tee` pad, only one can draw/consume in
place — the rest silently produce nothing (no error, no crash). A parallel AI
wall with **no** top-level composer at all — N independent AI branches, each
overlaying its own result off one shared `tee` with its own `qtimetamux`/
`qtivoverlay` — hits the same constraint among the sibling branches themselves.
Give **every** branch's passthrough leg its own `qtivtransform ! NV12 VideoFilter`
copy before its own `qtimetamux`, not just the branches that happen to also share
the tee with a composer; otherwise only one sibling branch's overlay renders.

> **Device-plugin caveat:** the integrated render plugins the C reference AI-wall
> uses (`qtimlvdetection`, `qtimlvclassification`, `qtimlvpose`,
> `qtimlvsegmentation`) may be absent on the target build (only `qtimlpostprocess`
> + `qtivoverlay` present). With `qtivoverlay`, bounding boxes drawn in-place then
> rescaled by a downstream `qtivcomposer` tile can distort. Text/keypoint overlays
> survive rescale; detection boxes may not. Prefer sizing the branch to its final
> tile size before the overlay when box fidelity matters.

For parallel inferencing where multiple rendered AI branches feed one
`qtivcomposer`, use the reference parallel-inference pattern captured here and
keep caps and tile geometry separate:

- Each postprocess `VideoFilter` is `.format("RGBA")` with NO pinned resolution
  (`BGRA` fails to link; pinning w/h breaks caps fixation). The module's native
  rendered size flows through; do not reuse the composer tile size for the caps.
- Composer sink-pad `position`/`dimensions` define the display tile and may be
  larger or smaller than the rendered RGBA output.
- Configure `qtivcomposer` layout with pad array (Python list) properties only:
  `composer.input(i).set("position", [x, y])` and
  `composer.input(i).set("dimensions", [w, h])`. Do not use scalar pad names
  such as `x`, `y`, `width`, or `height`, gst-array strings `"<x, y>"`, or
  element-level `sink_N::position`.
- For the HRNet pose branch, include `settings` only when the user, model
  catalog, or loaded reference provides a concrete settings path such as
  `hrnet_settings.json`; otherwise omit `settings` rather than inventing a file.
- For high-concurrency HTP/NPU parallel inferencing, use external delegate
  options with `htp_performance_mode=(string)2` on each branch unless the user
  provides exact delegate options.
- For multi-batch HTP/NPU walls, also include
  `htp_device_id=(string)<group_index % htp_count>` per batch group when
  multiple HTP devices are present.
- For high stream counts with many independent file/RTSP decode branches,
  include the same file descriptor limit guard before `pipeline.execute()`:

```python
import resource

try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (10000, 10000))
except (OSError, ValueError) as exc:
    print(f"Warning: failed to raise fd limit; run 'ulimit -n 10000' first: {exc}")
```

- Use `waylandsink sync=False` for a multistream/file-source composer grid only when it has more than 8 independent concurrently active input streams AND the topology is processing-heavy (shared/batched inference, or HTP/batch preroll makes frames arrive well behind their PTS) so that `sync=True` would drop late frames and freeze/blacken the display. A simple multi-stream playback grid or a composer grid of 8 or fewer streams has no such late-frame risk and stays on the ordinary `sync=True` default.

### Cross-Process Zero-Copy

Use the catalogued socket elements when the request explicitly spans processes
or containers:

```text
producer pipeline -> qtisocketsink(socket=<SOCKET_PATH>)
receiver pipeline: qtisocketsrc(socket=<SOCKET_PATH>) -> downstream processing
```

Construct both elements with `Element`, preserve the user socket path exactly,
and keep buffers FD-backed. Do not add a socket boundary to an ordinary
single-process pipeline.

### API-Validated Feature Routes

Face enrollment, smart-codec control, event-triggered recording, and camera
snapshot/stream-activation actions remain valid use-case routes, but their exact
Python signal/callback wiring must be confirmed against the target SDK. Do not
invent methods or callbacks. When the user requests one of these routes, keep
the plugin topology and explicit unresolved control hook in the artifact README
until the public Python API is confirmed.

#### Event-Triggered Recording (device-verified pattern)

This route now has a working, verified shape — use it instead of leaving a
TODO. "Record to a file only while a person (or other class) is detected":

- **Main pipeline**: `decode -> tee`; one branch overlays for display; the AI
  branch runs detection and its metadata is teed to `qtimlmetaparser(module=json)
  -> AppSink` (the person-presence gate) and the overlaid video is teed to a
  second video `AppSink` (the frame source for recording). Isolate that video
  `AppSink` with a `qtivtransform` (see `source-sink-patterns.md` "Buffer
  Writability Under Shared Tees") or the AI branch gets 0 inference frames.
- **Recording pipeline (separate, resident, kept PLAYING the whole time)**:
  `AppSrc(format=TIME, is-live=True, do-timestamp=True, fixed NV12 caps) -> queue
  -> v4l2h264enc -> h264parse -> mp4mux -> filesink`, with `pipeline.eos(True)`.
  Do NOT toggle it PAUSED/PLAYING to gate (an appsrc pipeline can't finish PAUSED
  preroll without data). Do NOT add `qtivtransform`/`videoconvert` before the
  encoder (causes endless caps renegotiation).
- **Gate logic** (in the detection `AppSink` consumer): parse the JSON
  (`object_detection`/`label`, per `plugin-catalog.md`); on first detection set a
  `recording=True` flag; after N consecutive detection-free frames send
  `record_src.end_of_stream()` and stop. The video `AppSink` consumer pushes a
  **copied** buffer into the recorder only while `recording` is True:
  `record_src.push_buffer(Buffer(gst_buffer=buf.take_gst_buffer().copy()))`.
- Read buffers with `Buffer.data()` (`api-surface.md`). This is a dual-pipeline
  app — the README uses `flowchart TD` with a `subgraph` per pipeline
  (`artifact-contract.md`).

## TFLite External Delegate

For discrete `qtimltflite`:

```python
infer = Element("qtimltflite", "infer")
infer.set("model", "<MODEL_PATH>")
infer.set("delegate", "external")
infer.set("external-delegate-path", "libQnnTFLiteDelegate.so")
infer.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
```

For HTP/NPU, use `QNNExternalDelegate,backend_type=htp,log_level=(string)1;` unless the user provides exact delegate options. Do not use `QNNExecurorBackend:HTP`, `QNNExecutorBackend:HTP`, `QNNExternalDelegateBackend:HTP`, or colon-separated QNN delegate option strings.

## Built-In Postprocess

```python
post = Element("qtimlpostprocess", "postprocess")
post.set("module", "yolov8")
post.set("labels", "<LABELS_PATH>")
```

Use `settings` only when requested or provided.

## Custom Preprocess

Use custom preprocess only when the user asks for external/custom preprocessing or placeholder preprocess logic. Do not replace normal `qtimlvconverter` preprocessing by default.

Discrete AI overlay shape:

`source -> decode/camera -> VideoFilter(NV12) -> tee`

`tee video branch -> qtimetamux -> qtivoverlay -> output`

`tee custom-preprocess branch -> queue -> MLVConverter.set(engine="none").set_handler(...) -> queue -> inference -> queue -> postprocess -> TextFilter() -> qtimetamux`

Skeleton:

```python
def preprocess_callback(blits, outmlframe) -> bool:
    """Placeholder for model-specific preprocessing.

    TODO:
    - inspect blit.info, blit.destination, and blit.planes()
    - do not retain plane memoryviews after callback return; qimsdk unmaps blits after the callback
    - map source pixels into the model input tensor layout
    - apply required resize/letterbox, color order, quantization, and normalization
    - write the tensor through outmlframe
    """
    # Keep return False until a valid tensor is written.
    # After writing outmlframe.get_tensor(...), return True so output is emitted.
    return False


preprocess = (
    MLVConverter("preprocessing")
    .set(engine="none")
    .set_handler(preprocess_callback)
)
```

Rules:

- Always emit explicit `.set(engine="none")` before `.set_handler(...)` on discrete `MLVConverter`.
- Return `False` in placeholders that do not perform a real tensor write.
- Do not invent NV12/RGB conversion, scale policy, tensor dimensions, zero-points, or normalization.
- If the user provides exact conversion logic, preserve it and wire it through the callback.
- If the callback is a placeholder returning `False`, state in README that the artifact is not functionally runnable for inference until real tensor-write logic is implemented.
- For reference-style discrete custom preprocess pipelines, place queues after the tee, between preprocess and inference, between inference and postprocess, and after `qtimetamux` before overlay/display when generating a full runnable topology.
- For metadata overlay, keep the original video branch and merge the postprocess text branch with `qtimetamux`.

## ML-Bin Flow

Use ML-bin wrappers when the user asks for fused/bin-style inference or examples show the requested pattern:

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set("inference-model", "<MODEL_PATH>")
mlbin.set("inference-delegate", "external")
mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
mlbin.set("postprocess-module", "yolov8")
mlbin.set("postprocess-labels", "<LABELS_PATH>")
```

Use `inference-*` and `postprocess-*` keys for ML-bin. Do not use discrete names like `model` or `module` on ML-bin elements.

Link ML-bin overlay paths directly:

`source/decode -> VideoFilter(NV12) -> MLVideoTFLiteBin -> qtivoverlay -> display/file`

Direct ML-bin cascades are also valid, for example `source/decode -> VideoFilter(NV12) -> mlbin1 -> mlbin2 -> qtivoverlay -> display`.

Do not adapt ML-bin into a discrete metadata fan-in topology such as `tee -> queue -> MLVideoTFLiteBin -> TextFilter() -> qtimetamux`. The `tee`/`TextFilter()`/`qtimetamux` overlay pattern is for discrete `qtimlvconverter -> qtimltflite -> qtimlpostprocess` AI branches that produce metadata separately.

For custom preprocess on ML-bin wrappers:

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set("inference-model", "<MODEL_PATH>")
mlbin.set("inference-delegate", "external")
mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
mlbin.set("postprocess-module", "yolov8")
mlbin.set("postprocess-labels", "<LABELS_PATH>")
mlbin.set("preprocess-engine", "none")
mlbin.set_preprocess_handler(preprocess_callback)
```

Use `.set_preprocess_handler(...)`, not `.set_handler(...)`, for ML-bin custom preprocess. `.set_postprocess_handler(...)` is for ML-bin custom postprocess.
For ML-bin custom preprocess, set `"preprocess-engine", "none"` before `.set_preprocess_handler(...)` to mirror the reference intent that the callback owns tensor production.

## Custom Postprocess

When requested, replace built-in module output with a Python handler:

```python
def detection_callback(mlframe, mlparams, detections: ObjectDetections):
    """Placeholder for model-specific detection decoding.

    TODO:
    - read tensors from mlframe/mlparams using the documented SDK structure
    - decode boxes, classes, and scores for the selected model
    - apply thresholding and NMS
    - populate detections using SDK-supported output fields
    """
    return


post = MLPostprocess("postprocess").set_handler(detection_callback)
```

The placeholder must not pretend to implement model-specific decoding.

## Daisy-Chain

For two-stage ROI pipelines:

- stage 1 converter mode: `image-batch-non-cumulative`
- stage 2 converter mode: `roi-batch-cumulative`
- preserve metadata alignment through `qtimetamux`
- ask if stage count or ROI source is unclear

### Two-Stage Discrete Overlay Topology

Use this for stage-1 full-frame detection plus stage-2 ROI detection/pose/PPE requests where the final output overlays both stages on the original video. Each ML stage has a metadata-passthrough branch and an ML metadata branch, and each stage merges with its own `qtimetamux`.

```text
source -> decode -> queue -> VideoFilter(NV12) -> split1
split1 passthrough branch -> metamux_1
split1 stage-1 branch -> queue -> qtimlvconverter(image-batch-non-cumulative)
                      -> qtimltflite -> qtimlpostprocess -> TextFilter() -> metamux_1
metamux_1 -> split2
split2 passthrough branch -> metamux_2
split2 stage-2 branch -> queue -> qtimlvconverter(roi-batch-cumulative)
                      -> qtimltflite -> qtimlpostprocess -> TextFilter() -> metamux_2
metamux_2 -> qtivoverlay -> display/file
```

Minimal explicit Python shape:

```python
q_dec = Element("queue", "q_dec")
vf = VideoFilter().format("NV12")

split1 = Element("tee", "split1")
q_stage1 = Element("queue", "q_stage1")
pre1 = Element("qtimlvconverter", "stage_01_preproc")
pre1.set("mode", "image-batch-non-cumulative")
infer1 = Element("qtimltflite", "stage_01_inference")
post1 = Element("qtimlpostprocess", "stage_01_postproc")
mlf1 = TextFilter()
metamux1 = Element("qtimetamux", "metamux_1")

split2 = Element("tee", "split2")
q_stage2 = Element("queue", "q_stage2")
pre2 = Element("qtimlvconverter", "stage_02_preproc")
pre2.set("mode", "roi-batch-cumulative")
infer2 = Element("qtimltflite", "stage_02_inference")
post2 = Element("qtimlpostprocess", "stage_02_postproc")
mlf2 = TextFilter()
metamux2 = Element("qtimetamux", "metamux_2")

pipeline.add(source)
pipeline.add(demux)
pipeline.add(parser)
pipeline.add(decoder)
pipeline.add(q_dec)
pipeline.add_stream_filter("vf", vf)
pipeline.add(split1)
pipeline.add(q_stage1)
pipeline.add(pre1)
pipeline.add(infer1)
pipeline.add(post1)
pipeline.add_stream_filter("mlf1", mlf1)
pipeline.add(metamux1)
pipeline.add(split2)
pipeline.add(q_stage2)
pipeline.add(pre2)
pipeline.add(infer2)
pipeline.add(post2)
pipeline.add_stream_filter("mlf2", mlf2)
pipeline.add(metamux2)
pipeline.add(overlay)
pipeline.add(display)

pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split1")
pipeline.link("split1", "metamux_1")
pipeline.link("split1", "q_stage1", "stage_01_preproc",
              "stage_01_inference", "stage_01_postproc", "mlf1", "metamux_1")
pipeline.link("metamux_1", "split2")
pipeline.link("split2", "metamux_2")
pipeline.link("split2", "q_stage2", "stage_02_preproc",
              "stage_02_inference", "stage_02_postproc", "mlf2", "metamux_2")
pipeline.link("metamux_2", "overlay", "display")
```

Rules:

- Keep queues minimal: add them at branch boundaries and between high-latency ML stages when needed.
- Do not rely on `qtimlvconverter`, `qtimltflite`, or `qtimlpostprocess` as a substitute for the video passthrough branch when overlaying metadata.
- Add all stream filters with `pipeline.add_stream_filter(...)`. Do not call `pipeline.add(vf)`, `pipeline.add(mlf1)`, or `pipeline.add(mlf2)`.
- If choosing fused ML-bin daisy-chain instead, keep the ML-bin chain linear and do not mix it with the discrete two-mux topology unless the user explicitly asks.

### Gesture Recognition Daisy-Chain

Use this named topology when the request asks for palm detection, hand landmark detection, gesture embedding, and gesture classification. Do not approximate it as a generic four-stage daisy-chain and do not create one `qtimetamux` per model stage. The SDK examples and GStreamer app-builder use a gesture-specific graph with two metadata merge points:

```text
camera/decode -> VideoFilter(NV12) -> split1
split1 video branch -> metamux_1 -> qtimetatransform(module=roi-palmd) -> split2
split1 palm branch -> queue -> qtimlvconverter(image-batch-non-cumulative)
                  -> qtimltflite(hand_detector, delegate=gpu)
                  -> qtimlpostprocess(module=palmd, labels, settings, results=1, bbox-stabilization=True)
                  -> TextFilter() -> metamux_1

split2 video branch -> metamux_2 -> qtivoverlay -> display/file
split2 hand branch -> queue -> qtimlvconverter(roi-batch-non-cumulative or user-provided mode)
                 -> qtimltflite(hand_landmarks_detector, delegate=gpu) -> split3
split3 landmark branch -> qtimlpostprocess(module=hlandmark, labels, settings, results=6)
                      -> TextFilter() -> metamux_2
split3 gesture branch -> qtimlpostprocess(module=tensor)
                     -> qtimltflite(gesture_embedder, delegate=gpu)
                     -> qtimltflite(canned_gesture_classifier, delegate=gpu)
                     -> qtimlpostprocess(module=mobilenet, labels, results=8)
                     -> TextFilter() -> metamux_2
```

Minimal Python wiring shape:

```python
split1 = Element("tee", "split1")
metamux1 = Element("qtimetamux", "metamux_1")
metatransform = Element("qtimetatransform", "roi_transform")
metatransform.set("module", "roi-palmd")
split2 = Element("tee", "split2")

stage1_pre = Element("qtimlvconverter", "stage_01_preproc")
stage1_pre.set("mode", "image-batch-non-cumulative")
stage1_infer = Element("qtimltflite", "stage_01_inference")
stage1_infer.set("delegate", "gpu")
stage1_post = Element("qtimlpostprocess", "stage_01_postproc")
stage1_post.set("module", "palmd")
stage1_post.set("bbox-stabilization", True)
stage1_text = TextFilter()

stage2_pre = Element("qtimlvconverter", "stage_02_preproc")
stage2_pre.set("mode", "roi-batch-non-cumulative")
stage2_infer = Element("qtimltflite", "stage_02_inference")
stage2_infer.set("delegate", "gpu")
split3 = Element("tee", "split3")

landmark_post = Element("qtimlpostprocess", "stage_02_1_postproc")
landmark_post.set("module", "hlandmark")
landmark_text = TextFilter()

tensor_post = Element("qtimlpostprocess", "stage_02_2_postproc")
tensor_post.set("module", "tensor")
embedder = Element("qtimltflite", "stage_03_1_inference")
embedder.set("delegate", "gpu")
classifier = Element("qtimltflite", "stage_03_2_inference")
classifier.set("delegate", "gpu")
gesture_post = Element("qtimlpostprocess", "stage_03_postproc")
gesture_post.set("module", "mobilenet")
gesture_text = TextFilter()
metamux2 = Element("qtimetamux", "metamux_2")

pipeline.link("source", "vf", "split1")
pipeline.link("split1", "metamux_1")
pipeline.link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
              "stage_01_postproc", "stage1_text", "metamux_1")
pipeline.link("metamux_1", "roi_transform", "split2")
pipeline.link("split2", "metamux_2")
pipeline.link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference", "split3")
pipeline.link("split3", "stage_02_1_postproc", "landmark_text", "metamux_2")
pipeline.link("split3", "stage_02_2_postproc", "stage_03_1_inference",
              "stage_03_2_inference", "stage_03_postproc", "gesture_text", "metamux_2")
pipeline.link("metamux_2", "overlay", "display")
```

Rules:

- Preserve user-provided source plugin names and model/label/settings paths. If the user says `qticamsrc`, use `qticamsrc`; do not rewrite it to `qtiqmmfsrc`.
- Use exactly two `qtimetamux` merge points: one after palm detection and one for final hand landmarks plus gesture labels.
- Place `qtimetatransform module="roi-palmd"` on the main stream after `metamux_1`, before `split2`.
- Stage 2 must split after landmark inference. One branch performs `hlandmark`; the other converts landmark tensors into the embedder/classifier chain through `qtimlpostprocess module="tensor"`.
- Stage 3 and stage 4 are inference-only between the tensor postprocess and final classification postprocess; do not add `qtimlvconverter` stages for them unless a reference or user request explicitly requires it.
- Gesture classifier output uses `qtimlpostprocess module="mobilenet"` unless the user explicitly provides another documented module.
- For the prompt mode `roi-batch-non-cumulative`, preserve that mode. Do not override it with the generic two-stage `roi-batch-cumulative` default.
- For GPU delegate requests, set `delegate="gpu"` on all `qtimltflite` stages and do not add external delegate path/options.

## Overlay

Use:

`metadata branch -> TextFilter() -> qtimetamux`

before:

`qtimetamux -> qtivoverlay -> display/file`
