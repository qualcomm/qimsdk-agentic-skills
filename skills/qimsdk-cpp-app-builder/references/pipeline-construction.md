# Pipeline Construction Patterns

Patterns here are grounded in the C++ SDK headers and the curated topology rules in this skill bundle.

## Default App Style (Use By Default)

Unless user asks for a staged runtime flow, generate this style:

- `#include <qti/qimsdk.h>`
- configure logs at startup
- explicit object construction: create `Element`/wrapper/filter objects, configure with `.set(...)`, then add them to `Pipeline`
- run with `pipeline.execute()`

## Syntax Fidelity Rules (SDK Pattern Parity)

- Match syntax families documented in this skill bundle only.
- Preferred default is explicit object style:
  - `Element elem("factory", "name"); elem.set(...); pipeline.add(elem);`
  - wrapper classes such as `MLVConverter`, `MLPostprocess`, and `MLVideo*Bin` when wrapper-specific callbacks are needed
  - `pipeline.add_stream_filter("name", filter);`
  - `pipeline.link("a", "b", ...);`
- Use fluent factory style only when explicitly requested or when preserving an existing fluent app:
  - `pipeline.add("factory", "name", "prop", value, ...)`
- Do not mix fluent and wrapper styles in one generated app unless user explicitly asks.
- Do not introduce non-SDK helper abstractions or custom builder wrappers.

## Pattern A: Single-Stream Explicit Pipeline

Use for straightforward requests.

```cpp
qti::Pipeline pipeline("ml-pipeline");
qti::Element source("filesrc", "src");
source.set("location", "<INPUT_FILE>");

qti::Element demux("qtdemux", "demux");
qti::Element parser("h264parse", "parse");

qti::Element decoder("v4l2h264dec", "decoder");
decoder.set("output-io-mode", 4);
decoder.set("capture-io-mode", 4);

qti::Element q_dec("queue", "q_dec");
auto video_filter = qti::VideoFilter().format("NV12");

qti::Element display("waylandsink", "display");
display.set("sync", true);
display.set("fullscreen", true);

pipeline.add(source)
        .add(demux)
        .add(parser)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("vf", video_filter)
        .add(display)
        .link("src", "demux", "parse", "decoder", "q_dec", "vf", "display");

pipeline.execute();
```

Notes:

- For MP4 demux flows, default to `qtdemux -> h264parse`.
- Add queue after `qtdemux` only when explicit decoupling/robustness is needed.
- Always keep a queue immediately after hardware decode.
- Default `waylandsink fullscreen=true sync=true` for every source type, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`) — camera source type alone is not a reason to use `sync=false`. Use `sync=false` only for the three documented exceptions in "Display Sink Sync Policy" below (>8-stream processing-heavy topology, multi-sink clock-stall, or explicit user low-latency request).

## Pattern A1: MP4 + AI Overlay + Metadata Mux

Use this when request asks for overlay with model metadata.

```cpp
qti::Pipeline pipeline("ml-pipeline");
pipeline
    .add("filesrc", "src", "location", "<INPUT_MP4>")
    .add("qtdemux", "demux")
    .add("h264parse", "parse")
    .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
    .add("queue", "q_dec")
    .add_stream_filter("vf", qti::VideoFilter().format("NV12"))
    .add("tee", "split")
    .add("queue", "q_ai")
    .add("qtimlvconverter", "pre")
    .add("qtimltflite", "infer",
         "delegate", "external",
         "external-delegate-path", "libQnnTFLiteDelegate.so",
         "external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
         "model", "<MODEL_PATH>")
    .add("qtimlpostprocess", "post",
         "module", "<POSTPROC_MODULE>",
         "labels", "<LABELS_PATH>",
         "settings", "<SETTINGS_JSON_OR_PATH>")
    .add_stream_filter("mlf", qti::TextFilter())
    .add("qtimetamux", "metamux")
    .add("qtivoverlay", "overlay")
    .add("waylandsink", "display", "sync", true, "async", true, "fullscreen", true)
    .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split")
    .link("split", "metamux")
    .link("split", "q_ai", "pre", "infer", "post", "mlf", "metamux")
    .link("metamux", "overlay", "display");

pipeline.execute();
```

## Pattern A1d: Direct-To-Composer AI Overlay

Use this topology when the selected postprocess path produces rendered video frames or when the user explicitly asks for composer-based overlay/alpha-blending/side-by-side output.

```cpp
qti::Pipeline pipeline("composer-ai-overlay");
pipeline
    .add("filesrc", "src", "location", "<INPUT_MP4>")
    .add("qtdemux", "demux")
    .add("h264parse", "parse")
    .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
    .add("queue", "q_dec")
    .add_stream_filter("vf", qti::VideoFilter().format("NV12"))
    .add("tee", "split")
    .add("queue", "q_video")
    .add("queue", "q_ai")
    .add("qtimlvconverter", "pre")
    .add("queue", "q_infer")
    .add("qtimltflite", "infer", "delegate", "external", "model", "<MODEL_PATH>")
    .add("queue", "q_post")
    .add("qtimlpostprocess", "post", "module", "<POSTPROC_MODULE>", "labels", "<LABELS_PATH>")
    .add_stream_filter("render_filter", qti::VideoFilter().format("RGBA"))
    .add("qtivcomposer", "composer")
    .add("waylandsink", "display", "fullscreen", true, "sync", true)
    .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split")
    .link("split", "q_video", "composer")
    .link("split", "q_ai", "pre", "q_infer", "infer", "q_post", "post", "render_filter", "composer")
    .link("composer", "display");

pipeline.get("composer").input(1).set("alpha", 0.5);
pipeline.execute();
```

Rules:

- Use `qtimetamux + qtivoverlay` for text metadata overlays such as boxes/keypoints/class labels when no composer is otherwise needed.
- Use `qtivcomposer` when the postprocess branch emits or renders video frames (`video/x-raw` RGBA masks, segmentation overlays, SR comparison panes, side-by-side output). The render filter must be `qti::VideoFilter().format("RGBA")` — `qtimlpostprocess` src caps are `{RGBA, RGBx}`; `BGRA` fails to link. Do not pin `.resolution()` when a composer sizes the tile (breaks caps fixation); let the composer sink-pad `dimensions` scale it. See `plugin-catalog.md` "Module Output Types".
- Do not send a rendered-video branch through `TextFilter()` or `qtimetamux`.
- Do not add `qtivtransform` after `qtivcomposer` before encode; constrain composer output with an NV12 `VideoFilter` before the encoder if file output requires NV12.
- Set composer input alpha/geometry only when the requested layout is clear. If geometry affects correctness and is not specified, ask.

## Pattern A1e: Segmentation / Video-Output Postprocess

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

- Built-in segmentation modules and custom `MLSegmentations` callbacks use this composer topology.
- For monodepth/depth-map postprocess (`midas-v2`), the render filter is `VideoFilter().format("RGBA")` with NO pinned resolution — `qtimlpostprocess` emits `{RGBA, RGBx}` (BGRA fails to link) and pinning the size (e.g. `.resolution(256, 144)`) makes postproc caps fixation fail. `qtivcomposer` scales the RGBA depth branch into the requested pane through its sink-pad `dimensions`.
- Do not force segmentation into `TextFilter -> qtimetamux -> qtivoverlay`; that is a text metadata topology.
- For custom segmentation callbacks, follow `references/ml-and-postprocess.md` for `MLSegmentations` and explicit bool returns.

## Pattern A1f: Super Resolution

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
- The render `VideoFilter` after `srnet` postprocess MUST be `.format("RGBA")` (device-verified; `RGB` fails to link) — same rule as every other `qtimlpostprocess` rendered-video branch. Do not pin `.resolution()` on it when a downstream composer sizes the tile.
- For side-by-side comparison, use `qtivcomposer` geometry: passthrough left and SR output right. Ask if layout dimensions are not specified.
- For MP4 output, place `VideoFilter().format("NV12")` directly after `qtivcomposer` before `v4l2h264enc`; do not insert `qtivtransform` after the composer.

## Pattern A1g: Audio AI Classification

Use this for YAMNet or audio-classification-over-video requests. Audio AI is not a `TextFilter`/`qtimetamux` overlay. `qtdemux` naturally splits the video and audio pads.

```text
filesrc -> qtdemux
demux video pad -> queue -> h264parse -> v4l2h264dec -> VideoFilter(NV12) -> queue -> qtivcomposer
demux audio pad -> queue -> flacparse -> flacdec -> queue -> audioconvert -> audioresample
              -> audiobuffersplit(output-buffer-size=31200) -> queue
              -> qtimlaconverter(sample-rate=16000, feature=lmfe, params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;")
              -> queue -> qtimltflite -> qtimlpostprocess(module=yamnet, settings={"confidence": 10.0}, results=3)
              -> VideoFilter(RGBA) -> queue -> qtivcomposer
qtivcomposer -> display/file
```

Rules:

- Use `module="yamnet"` and `results=3` unless user overrides.
- Default confidence for YAMNet is `{"confidence": 10.0}` unless user provides another threshold.
- Do not set a delegate in the canonical CPU path unless the user requests a backend.
- The audio overlay branch outputs an RGBA label panel and feeds `qtivcomposer`; do not route it through `TextFilter` or `qtimetamux`. Use `VideoFilter().format("RGBA")` (BGRA fails to link).
- **HTP/default-delegate audio path:** size the panel via the composer sink-pad `dimensions` (e.g. `composer.input(1).set("dimensions", {368, 64})`).
- **CPU audio path (`delegate="none"` / no backend):** do NOT set composer pad `position`/`dimensions` for the audio panel input — pin the panel size on the render filter itself (`VideoFilter().format("RGBA").resolution(368, 64)`) and let the composer take that native size. Setting explicit pad geometry on this specific CPU-audio + rendered-panel combination has been observed to deadlock composer preroll (the pipeline never reaches `PLAYING`). This is a topology-specific interaction on the CPU audio path, not a blanket ban on composer geometry elsewhere.
- For display, this audio-classification pattern omits `sync` on `waylandsink` entirely — do not set `sync=true` or `sync=false`. (This omission is specific to audio-classification display; other display pipelines default to `sync=true`. See "Display Sink Sync Policy".)
- For MP4 output, use an NV12 filter immediately after `qtivcomposer` before the encoder.
- In C++ dynamic-pad audio/video graphs, put a queue immediately after each `qtdemux` branch before parsing or decoding, and put another queue after the audio decoder before `audioconvert`. This decouples concurrent preroll/scheduling; the minimal direct `qtdemux -> parser` form is reserved for simple single-stream video paths.

## Pattern A4: Face Recognition Daisy-Chain

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

## Pattern A5: AI Metadata Parser / Extractor

Use this when the user asks to parse inference metadata, extract bounding boxes programmatically, count objects, or send metadata to an app boundary. This is not a plain overlay-only request.

```text
source/decode/camera -> VideoFilter(NV12) -> tee
tee video branch -> qtimetamux -> qtivoverlay -> display/file
tee AI branch -> qtimlvconverter -> qtimltflite -> qtimlpostprocess -> tee/detection split
detection split display branch -> TextFilter -> qtimetamux
detection split app branch -> qtimlmetaextractor or appsink/AppSink
```

Rules:

- Keep the display overlay branch and metadata-parser branch separate after `qtimlpostprocess`.
- Use `qtimlmetaextractor` only when the request is for serialized/raw metadata extraction and the plugin is available on the target.
- Use `qti::AppSink` with `set_buffer_consumer(...)` for C++-side consumption. Do not invent metadata decoding logic if the SDK reference does not provide the exact structure; leave explicit TODO comments for app-specific parsing.

## Pattern A6: Batched Multi-Stream AI

Use this when many streams run the same detector/classifier and the request calls for batching or a high stream count where one inference instance per stream is inappropriate (roughly 8+ streams sharing one detector/classifier). The naive approach — one `qtimltflite` per stream — instantiates N independent graphs on the HTP and exhausts its memory (`error 6020`). Batching lets B streams share one graph.

### How the batch pattern works

Reason from what each element actually does to the buffers flowing through it; the topology and rules below follow directly from this.

- **`qtibatch`** stacks one buffer from each of its linked `sink_%u` request pads into a single batched buffer. There is no `batch-size` property — the batch depth is however many streams you `pipeline.link(...)` into it, and that depth travels downstream as caps (`qtimlvconverter` writes `views=<depth>`; the model must be compiled for that same depth, e.g. a `..._batch_4.tflite` for a 4-stream group — a batch-1 model fed a 4-frame buffer forces the delegate to instantiate the graph 4 times on the HTP and blows up memory). `qtibatch` also tags each stacked frame with a `stream-id` equal to that pad's position in link order — this tag is what makes correct demultiplexing possible later. It only emits once every linked pad has produced a buffer, so every requested sink pad must actually receive a stream or the whole group stalls.
- **`qtimldemux`** reverses `qtibatch`: it has request src pads, auto-numbered in link order, and for each channel in the batched tensor it routes to the output whose position matches the `stream-id` tag — not by inspecting content. Concretely: **the k-th stream you linked into `qtibatch` comes out of the k-th output you link out of `qtimldemux`.** This is an index match, not a convention you have to remember to preserve — but you must link demux outputs in the exact same order you linked batch inputs, or stream k's detection results land on stream j's tile.
- **`qtimlpostprocess`**'s rendered output (RGBA) is a transparent mask containing only drawn boxes/labels/text — it is never the video frame (see `plugin-catalog.md` "Preference: reuse an existing `qtivcomposer` directly"). A batched pipeline that feeds only this mask into the composer, with no matching passthrough pad, displays a colored detection canvas instead of video — the original decoded frame was never wired in. The passthrough branch per stream is therefore not optional scaffolding; it is required by what the mask actually contains.
- **`qtivcomposer`** blends sink pads by request-pad creation order when `zorder` is not set explicitly (earlier-created pads paint underneath, later ones on top). Two pads per stream at identical `position`/`dimensions` — passthrough underneath, detection mask on top — is what makes the video show through with boxes overlaid; link/configure the passthrough pad before the detection pad for each stream to get this z-order without setting it explicitly.

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

- **Every element name must be unique across the WHOLE pipeline, not just within its build phase — `qtimldemux` must NOT reuse the `demux_` prefix that per-stream `qtdemux` elements use.** A common generation mistake is naming the per-stream demuxers `demux_0..demux_(N-1)` and the per-group batch demuxers `demux_0..demux_(G-1)`: those two independent index ranges overlap (stream 0's `demux_0` vs group 0's `demux_0`), and `pipeline.add()` → `gst_bin_add()` fails on the duplicate name, surfacing at runtime as `Exception: Failed to add external element in the pipeline`. Name the batch-group demux with a distinct prefix such as `mldemux_<g>`. This applies to any two element families whose numeric index ranges could overlap — give each family its own prefix. (Device-verified: this exact collision aborted a 12-stream/3-group build before it ever reached PLAYING.)
- **A `tee` (here `split_<i>`) has multiple request src pads; ONE chained `pipeline.link(a, b, c, ...)` call only walks a single path off it. Link EACH tee leg with its own `pipeline.link(...)` call.** For the passthrough + AI split that means two explicit calls — `pipeline.link("split_<i>", "qpass_<i>")` AND `pipeline.link("split_<i>", "qai_<i>")` — not one chained call that happens to mention only one leg. If the passthrough leg is left unlinked, its downstream queue's sink pad stays `NOT_LINKED`: the composer receives no video for that tile, preroll never completes (the pipeline appears to run forever and never reaches EOS), and the dangling pad can surface nondeterministically as a hang or a segfault. (Device-verified: a missing `split_<i> -> qpass_<i>` link was the root cause of both a blank display and an intermittent crash.)
- `qtibatch` batch size is determined by linked sink pads and caps, not by a `batch-size` property; use a model compiled for that batch depth (a batch-4 model for a 4-stream group).
- Every stream fed into a `qtibatch` group must reach it — a group stalls entirely if one of its linked pads never receives a buffer.
- Link `qtimldemux` outputs in the exact same order the corresponding streams were linked into `qtibatch`; this is how per-stream identity survives the round trip (see "How the batch pattern works" above). Swapped order silently swaps detections between tiles — it does not error.
- Every stream that feeds a batch group also needs its own passthrough branch straight from that stream's `tee` into the composer — never wire only the rendered mask. See `plugin-catalog.md` "Preference: reuse an existing `qtivcomposer` directly" for why the mask alone is insufficient.
- The render `qti::VideoFilter` after `qtimlpostprocess` must be `.format("RGBA")` with NO pinned resolution (`BGRA` fails to link; pinning w/h breaks caps fixation — see `plugin-catalog.md` "Module Output Types"). Composer pad `position`/`dimensions` scale that rendered output into the tile.
- Configure each `qtivcomposer` sink pad through `composer.input(<pad-index>).set("position", {x, y})` and `.set("dimensions", {w, h})`. Do not use scalar pad names such as `x`, `y`, `width`, or `height`.
- **If any parallel branch contains a `qtivcomposer` (or the passthrough tile is composed while a `qtimetamux`/`qtivoverlay` overlay chain runs on another branch off the same tee), the composer holds tee buffers for stream sync (refcount > 1) and the in-place overlay silently draws nothing.** Insert `qtivtransform ! VideoFilter(NV12)` on each overlay branch's passthrough leg (before `qtimetamux`) to force sole buffer ownership. See `source-sink-patterns.md`'s writability guidance.
- Do not force overlay format to NV12; use the rendered output format/dimensions documented for the selected module unless a reference requires otherwise.
- Use `waylandsink sync=false` only when this batched AI wall exceeds 8 independent concurrently active input streams with shared/batched inference (long HTP/batch preroll makes frames arrive well behind their PTS, so `sync=true` drops late frames and can freeze/blacken the display). A batch group of 8 or fewer streams stays `sync=true`; do not flag every batched pipeline as needing `sync=false`.
- For multiple HTP/NPU batch groups, round-robin inference instances across
  available HTP devices when practical. Detect dual HTP with
  `access("/dev/fastrpc-cdsp1", F_OK) == 0`; use
  `htp_device_id=(string)<group_index % htp_count>` and
  `htp_performance_mode=(string)2` in each batch group's
  `external-delegate-options` unless the user provides exact delegate options.
- For high stream counts with many independent file/RTSP decode branches, raise
  the process file descriptor limit before constructing/executing the pipeline.
  Use `10000` for 24-32 stream artifacts; warn in the README that users can run
  `ulimit -n 10000` first if the program cannot raise the limit.

## GStreamer Topology Parity

The C++ SDK must preserve GStreamer-supported application topologies even when
the refreshed C++ examples do not include a named counterpart. Translate the
same graph into explicit `qti::Element` construction, `pipeline.add(...)`,
`pipeline.add_stream_filter(...)`, and explicit `pipeline.link(...)` calls.

### Mixed AI Wall

For mixed AI walls, keep one independent source/decode branch per stream, run
the requested AI stage on each branch, and connect each rendered result to a
named `qtivcomposer` sink pad. Do not collapse different model stages into one
shared inference path unless batching is explicitly requested.

```text
stream_N -> NV12 -> AI preprocess/inference/postprocess -> overlay or composer sink_N
all passthrough/AI outputs -> qtivcomposer -> display or encoder
```

Use one queue per branch where scheduling requires it. Use direct composer input
for video-output postprocess modules; use `TextFilter -> qtimetamux ->
qtivoverlay` for metadata-output postprocess modules. Preserve the existing
separate branch-link and downstream-link ordering rules.

For parallel inferencing where multiple rendered AI branches feed one
`qtivcomposer`, use the reference parallel-inference pattern captured here and
keep caps and tile geometry separate:

- Each postprocess `VideoFilter` is `.format("RGBA")` with NO pinned resolution
  (`BGRA` fails to link; pinning w/h breaks caps fixation — see
  `plugin-catalog.md` "Module Output Types"). The module's native rendered size
  flows through; do not reuse the composer tile size for the caps.
- Composer sink-pad `position`/`dimensions` define the display tile and may be
  larger or smaller than the rendered RGBA output.
- Configure `qtivcomposer` layout with pad array properties only (C++
  brace-init/`std::vector<int>`): `composer.input(i).set("position", {x, y})`
  and `composer.input(i).set("dimensions", {w, h})`. Do not use scalar pad names
  such as `x`, `y`, `width`, or `height`.
- **Buffer ownership under a shared tee:** when one branch uses
  `qtimetamux`/`qtivoverlay` (in-place metadata draw) while another branch off
  the same teed source feeds a `qtivcomposer`, the composer holds the tee buffers
  for stream sync so the overlay cannot get a writable buffer and draws nothing
  (models still infer — only the overlay is blank). Insert `qtivtransform` + an
  NV12 `VideoFilter` on each overlay branch's passthrough leg before
  `qtimetamux`. See `source-sink-patterns.md`.
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
  include the same file descriptor limit guard at the start of `main()` before
  constructing the pipeline:

```cpp
#include <iostream>
#include <sys/resource.h>

rlimit rl{10000, 10000};
if (setrlimit(RLIMIT_NOFILE, &rl) != 0) {
  std::cerr << "Warning: failed to raise fd limit; run 'ulimit -n 10000' first\n";
}
```

- Multistream/file-source composer grids use `waylandsink sync=false` only when the grid exceeds 8 independent concurrently active input streams AND the topology is processing-heavy (shared/batched inference, or a grid where HTP/batch preroll makes frames arrive well behind their PTS). Simple grids of 8 or fewer streams, or grids without that late-frame risk, stay on the `sync=true` default — do not blanket-apply `sync=false` to every multistream composer grid.
- **Composer input count is an invariant, not a fixed number.** A `qtivcomposer`'s
  configured pad geometry (`comp.input(N).set(...)`) must exactly match the
  number of branches actually `.link()`-ed into it — no more, no fewer. This
  matters most after refactoring one branch's *shape*: if a branch that used to
  feed the composer as two raw pads (a passthrough tile plus a separately
  rendered mask tile) is changed to pre-compose those two into one finished
  tile through a local `qtivcomposer` first (see the segmentation sub-case
  below), the top-level composer now receives one input for that task, not two.
  Any leftover `comp.input(<old-index>).set(...)` call for a pad that is no
  longer linked throws `Port: cannot resolve target pad` at graph-construction
  time — before PLAYING, so it looks like a plumbing bug rather than what it
  is: stale geometry from an earlier topology. When you change how many
  branches feed one composer, re-derive every `input(N)` index from the
  current `.link()` calls instead of carrying forward the previous count.
  Concrete example: a 4-task parallel wall (classification, pose, detection,
  segmentation) has a top-level grid with exactly 4 inputs. Three tasks
  (classification/pose/detection) each output one already-overlaid tile
  straight into the grid. Segmentation is video-output-only (no
  `qtimetamux`/`qtivoverlay`), so it first alpha-blends its passthrough copy
  and its rendered RGBA mask on a small *local* `qtivcomposer` (2 inputs), and
  only that composed NV12 result — one tile — joins the top-level grid as its
  4th input. The top-level grid must configure exactly 4 `input(N)` pads
  (0-3); it must not also carry a 5th `input(4)` from an earlier draft that
  gave segmentation two direct top-level pads.
- **Parallel-branch buffer ownership needs an explicit copy on every overlay
  passthrough leg.** When N independent AI branches share one `tee` and each
  overlays its own result with `qtimetamux`/`qtivoverlay` (rather than
  everyone feeding one composer), give every branch's passthrough leg its own
  `qtivtransform ! VideoFilter(NV12)` copy before that branch's `qtimetamux`.
  Without it, only one overlay renders — usually silently, no error, no
  crash — while the others draw nothing, because a shared unwritable buffer
  makes at most one consumer able to draw in place. This generalizes the
  buffer-ownership rule above (shared tee + `qtivcomposer` consumer) to the
  "no composer at all, just N sibling overlay branches" case: the same
  writability constraint applies whenever more than one buffer-mutating
  consumer reads off the same tee pad.

### Cross-Process Zero-Copy

For a process or container boundary, construct the catalogued socket elements
with the generic C++ API:

```cpp
pipeline.add("qtisocketsink", "socket_sink", "socket", "<SOCKET_PATH.sock>");
// Receiver process:
receiver.add("qtisocketsrc", "socket_source", "socket", "<SOCKET_PATH.sock>");
```

The socket path must match on both sides and buffers must remain FD-backed. Do
not map buffers into application memory or insert an unnecessary conversion
around the boundary. `qti::AppSrc`/`qti::AppSink` are in-process callbacks and
are not substitutes for socket transport. Keep normal queue placement and NV12
normalization rules unchanged.

### API-Validated Feature Routes

Face enrollment, smart-codec control, and camera snapshot/stream-activation
actions remain valid use-case routes whose exact C++ signal, request-pad, or
control API must be confirmed from the target SDK before adding method-level
generation rules. Do not invent wrapper methods for these. Until the API is
confirmed, preserve the plugin topology and document the unresolved control
hook in the artifact README.

Event-triggered recording is device-confirmed — do not treat it as
unconfirmed. Use the resident-pipeline/gated-callback/moved-buffer pattern in
"Pattern B: AppSink -> AppSrc Bridge" → "Event-triggered recording variant"
above.

## Pattern A1b: ML-bin Fluent Syntax (Sample-App Parity)

Use ML-bin only when request is ML-bin centric (`qtimlvideotflitebin` / `MLVideo*Bin`) and preserve sample-app property names. Default generated code should still use explicit wrapper construction:

```cpp
qti::MLVideoTFLiteBin mlbin("mlbin");
mlbin.set("inference-delegate", "external");
mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
mlbin.set("inference-model", "<MODEL_PATH>");
mlbin.set("postprocess-module", "<POSTPROC_MODULE>");
mlbin.set("postprocess-labels", "<LABELS_PATH>");
pipeline.add(mlbin);
```

Fluent syntax is supported when the user asks for fluent/implicit style or when editing an existing fluent app:

```cpp
qti::Pipeline pipeline("mlbin-pipeline");
pipeline
    .add("filesrc", "src", "location", "<INPUT_MP4>")
    .add("qtdemux", "demux")
    .add("h264parse", "parse")
    .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
    .add("qtimlvideotflitebin", "mlbin",
         "inference-delegate", "external",
         "inference-external-delegate-path", "libQnnTFLiteDelegate.so",
         "inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
         "inference-model", "<MODEL_PATH>",
         "postprocess-module", "<POSTPROC_MODULE>",
         "postprocess-labels", "<LABELS_PATH>")
    .add("qtivoverlay", "overlay")
    .add("waylandsink", "display", "sync", true, "async", true, "fullscreen", true)
    .execute();
```

Rules for ML-bin syntax:

- Keep `inference-*` / `postprocess-*` keys exactly as shown above.
- Do not rewrite ML-bin keys to direct `qtimltflite` / `qtimlpostprocess` key names.
- If user asks for direct-element topology instead of ML-bin, use Pattern A/A2 instead.

## Pattern A1c: Daisy-Chained ML Bins (Multi-Stage, SDK-Idiomatic)

For a multi-stage/multi-model pipeline (e.g. first-stage detector feeding a second-stage classifier/detector on detected ROIs), prefer chaining two (or more) fused ML bins directly over the discrete `qtimlvconverter`/`qtimltflite`/`qtimlpostprocess` per-stage form when the topology can stay linear. Set `"preprocess-mode", "roi-batch-cumulative"` on every bin after the first. This is the SDK-idiomatic pattern for linear fused PPE-style pipelines (verified against the SDK's own `test_mlbin_ppe.cc` example):

```cpp
#include <cstdlib>
#include <iostream>
#include <stdexcept>

#include <qti/qimsdk.h>

using namespace qti;

namespace {
std::string expand_home(const std::string& suffix) {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem path: " + suffix);
  }
  return std::string(home) + suffix;
}
}  // namespace

void create_and_execute_pipeline() {
  Pipeline pipeline("mlbin-pipeline");
  pipeline.add("filesrc", "src", "location", expand_home("/media/ppe_video.mp4"))
          .add("qtdemux", "demux")
          .add("h264parse", "parse")
          .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
          .add_stream_filter("videofilter", VideoFilter().format("NV12"))
          .add("qtimlvideotflitebin", "mlbin1",
               "inference-delegate", "external",
               "inference-external-delegate-path", "libQnnTFLiteDelegate.so",
               "inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
               "inference-model", expand_home("/models/foot_track_net-person-foot-detection-w8a8.tflite"),
               "postprocess-module", "qpd",
               "postprocess-labels", expand_home("/labels/foot_track_net.json"),
               "postprocess-settings", expand_home("/labels/foot_track_net_settings.json"))
          .add("qtimlvideotflitebin", "mlbin2",
               "preprocess-mode", "roi-batch-cumulative",
               "inference-delegate", "external",
               "inference-external-delegate-path", "libQnnTFLiteDelegate.so",
               "inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
               "inference-model", expand_home("/models/gear_guard_net-ppe-detection-w8a8.tflite"),
               "postprocess-module", "yolov8",
               "postprocess-labels", expand_home("/labels/gear_guard_net.json"))
          .add("qtivoverlay", "overlay")
          .add("waylandsink", "display", "fullscreen", true, "sync", true)
          .execute();
}

int main() {
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
```

Note there is no explicit `.link()` call — the whole chain relies on insertion-order auto-linking.

Do not adapt this ML-bin pattern into `tee -> queue -> mlbin -> TextFilter -> qtimetamux` branches. If the request requires original-video branch preservation or overlay of both stage outputs through tee/metadata fan-in, use Pattern A2 with discrete `qtimlvconverter`/`qtimltflite`/`qtimlpostprocess` stages. Only use the ML-bin chain when the fused stages stay directly in the media path before overlay/display.

## Pattern A2: Two-Stage Daisy-Chain (Two Mux Topology)

Use for stage-1 full-frame + stage-2 ROI requests where original video plus both stages must be overlaid.

```cpp
qti::Pipeline pipeline("daisy-chain");
pipeline
    .add("filesrc", "src", "location", "<INPUT_MP4>")
    .add("qtdemux", "demux")
    .add("h264parse", "parse")
    .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
    .add("queue", "q_dec")
    .add_stream_filter("vf", qti::VideoFilter().format("NV12"))

    .add("tee", "split1")
    .add("queue", "q_video_1")
    .add("queue", "q_stage1")
    .add("qtimlvconverter", "stage_01_preproc", "mode", "image-batch-non-cumulative")
    .add("qtimltflite", "stage_01_inference", "model", "<STAGE1_MODEL>")
    .add("qtimlpostprocess", "stage_01_postproc",
         "module", "<STAGE1_MODULE>",
         "labels", "<STAGE1_LABELS>",
         "settings", "<STAGE1_SETTINGS_JSON_OR_PATH>")
    .add_stream_filter("mlf_s1", qti::TextFilter())
    .add("qtimetamux", "metamux_1")

    .add("tee", "split2")
    .add("queue", "q_video_2")
    .add("queue", "q_stage2")
    .add("qtimlvconverter", "stage_02_preproc", "mode", "roi-batch-cumulative")
    .add("qtimltflite", "stage_02_inference", "model", "<STAGE2_MODEL>")
    .add("qtimlpostprocess", "stage_02_postproc",
         "module", "<STAGE2_MODULE>",
         "labels", "<STAGE2_LABELS>",
         "settings", "<STAGE2_SETTINGS_JSON_OR_PATH>")
    .add_stream_filter("mlf_s2", qti::TextFilter())
    .add("qtimetamux", "metamux_2")

    .add("qtivoverlay", "overlay")
    .add("waylandsink", "display", "sync", true, "async", true, "fullscreen", true)

    .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split1")
    .link("split1", "q_video_1", "metamux_1")
    .link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference", "stage_01_postproc", "mlf_s1", "metamux_1")
    .link("metamux_1", "split2")
    .link("split2", "q_video_2", "metamux_2")
    .link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference", "stage_02_postproc", "mlf_s2", "metamux_2")
    .link("metamux_2", "overlay", "display");

pipeline.execute();
```

Rules for this pattern:

- Stage-1 converter mode must be `image-batch-non-cumulative`.
- Stage-2 converter mode must be `roi-batch-cumulative`.
- Keep `TextFilter()` before each `qtimetamux` metadata input branch.
- Keep queue usage minimal: branch-isolation queues only.
- Do not replace this with a single linear `stage_01_postproc -> stage_02_preproc -> stage_02_postproc -> TextFilter -> qtimetamux -> qtivoverlay` chain when the request asks to overlay original video plus both stage outputs.

## Pattern A3: Gesture Recognition Daisy-Chain (Palm -> Landmark -> Gesture)

Use this named topology for four-stage gesture recognition requests: palm detection, hand landmark detection, gesture embedding, and gesture classification. Keep this topology in parity with the GStreamer app-builder topology.

Do not approximate gesture recognition as a generic four-stage daisy-chain with one `qtimetamux` per model stage. Gesture recognition uses two metadata merge points plus a metadata transform:

```text
camera/decode -> VideoFilter(NV12) -> split1
split1 video branch -> metamux_1 -> qtimetatransform(module=roi-palmd) -> split2
split1 palm branch -> queue -> qtimlvconverter(image-batch-non-cumulative)
                  -> qtimltflite(hand_detector, delegate=gpu)
                  -> qtimlpostprocess(module=palmd, labels, settings, results=1, bbox-stabilization=true)
                  -> TextFilter -> metamux_1

split2 video branch -> metamux_2 -> qtivoverlay -> display/file
split2 hand branch -> queue -> qtimlvconverter(roi-batch-non-cumulative or user-provided mode)
                 -> qtimltflite(hand_landmarks_detector, delegate=gpu) -> split3
split3 landmark branch -> qtimlpostprocess(module=hlandmark, labels, settings, results=6)
                      -> TextFilter -> metamux_2
split3 gesture branch -> qtimlpostprocess(module=tensor)
                     -> qtimltflite(gesture_embedder, delegate=gpu)
                     -> qtimltflite(canned_gesture_classifier, delegate=gpu)
                     -> qtimlpostprocess(module=mobilenet, labels, results=8)
                     -> TextFilter -> metamux_2
```

Minimal C++ link shape:

```cpp
qti::Element metatransform("qtimetatransform", "roi_transform");
metatransform.set("module", "roi-palmd");

pipeline
    .add("tee", "split1")
    .add("queue", "q_stage1")
    .add("qtimlvconverter", "stage_01_preproc", "mode", "image-batch-non-cumulative")
    .add("qtimltflite", "stage_01_inference", "delegate", "gpu", "model", "<PALM_MODEL>")
    .add("qtimlpostprocess", "stage_01_postproc",
         "module", "palmd", "labels", "<PALM_LABELS>", "settings", "<PALM_SETTINGS>",
         "results", 1, "bbox-stabilization", true)
    .add_stream_filter("stage1_text", qti::TextFilter())
    .add("qtimetamux", "metamux_1")
    .add(metatransform)
    .add("tee", "split2")
    .add("queue", "q_stage2")
    .add("qtimlvconverter", "stage_02_preproc", "mode", "roi-batch-non-cumulative")
    .add("qtimltflite", "stage_02_inference", "delegate", "gpu", "model", "<LANDMARK_MODEL>")
    .add("tee", "split3")
    .add("qtimlpostprocess", "stage_02_1_postproc",
         "module", "hlandmark", "labels", "<LANDMARK_LABELS>", "settings", "<LANDMARK_SETTINGS>", "results", 6)
    .add_stream_filter("landmark_text", qti::TextFilter())
    .add("qtimlpostprocess", "stage_02_2_postproc", "module", "tensor")
    .add("qtimltflite", "stage_03_1_inference", "delegate", "gpu", "model", "<EMBEDDER_MODEL>")
    .add("qtimltflite", "stage_03_2_inference", "delegate", "gpu", "model", "<CLASSIFIER_MODEL>")
    .add("qtimlpostprocess", "stage_03_postproc",
         "module", "mobilenet", "labels", "<GESTURE_LABELS>", "results", 8)
    .add_stream_filter("gesture_text", qti::TextFilter())
    .add("qtimetamux", "metamux_2")
    .add("qtivoverlay", "overlay")
    .add("waylandsink", "display", "fullscreen", true, "sync", true)
    .link("source", "vf", "split1")
    .link("split1", "metamux_1")
    .link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
          "stage_01_postproc", "stage1_text", "metamux_1")
    .link("metamux_1", "roi_transform", "split2")
    .link("split2", "metamux_2")
    .link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference", "split3")
    .link("split3", "stage_02_1_postproc", "landmark_text", "metamux_2")
    .link("split3", "stage_02_2_postproc", "stage_03_1_inference",
          "stage_03_2_inference", "stage_03_postproc", "gesture_text", "metamux_2")
    .link("metamux_2", "overlay", "display");
```

Rules for this pattern:

- Preserve user-provided source plugin names and model/label/settings paths. If the user says `qticamsrc`, use `qticamsrc`; do not rewrite it to `qtiqmmfsrc`.
- Use exactly two `qtimetamux` merge points: one after palm detection and one for final hand landmarks plus gesture labels.
- Place `qtimetatransform module="roi-palmd"` on the main stream after `metamux_1`, before `split2`.
- Stage 2 must split after landmark inference. One branch performs `hlandmark`; the other performs `tensor -> gesture_embedder inference -> gesture_classifier inference -> mobilenet`.
- Stage 3 and stage 4 are inference-only between `qtimlpostprocess module=tensor` and the final `qtimlpostprocess module=mobilenet`; do not add `qtimlvconverter` stages for them unless a reference or user request explicitly requires it.
- For the prompt mode `roi-batch-non-cumulative`, preserve that mode. Do not override it with the generic two-stage `roi-batch-cumulative` default.
- For GPU delegate requests, set `delegate="gpu"` on all `qtimltflite` stages and do not add external delegate path/options.

## HOME_PATH Convention

Resolve `HOME_PATH` once at file scope and build every `inference-model` / `postprocess-labels` / `postprocess-settings` path (and any `filesrc "location"` media path) as `HOME_PATH + "/models/<MODEL_FILE>"` string concatenation, rather than a hardcoded absolute path. C++ string literals never expand `$HOME` — resolve it via a checked helper, not a bare `std::getenv("HOME")` (which is undefined behavior if `HOME` is unset) or a silent `? : ""` fallback (which builds an invalid relative path instead of failing loudly):

```cpp
std::string expand_home(const std::string& suffix) {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem path: " + suffix);
  }
  return std::string(home) + suffix;
}

const std::string MODEL_PATH = expand_home("/models/<MODEL_FILE>");
```

Use this convention consistently when the prompt indicates a `$HOME`-relative layout. Only fall back to a bare `<MODEL_PATH>`/`<LABELS_PATH>` placeholder when the user has not indicated a `$HOME`-relative layout (`~/models`, `~/labels`, `~/media`) at all.

## End-to-End Template: Camera → Display (Multimedia-Only)

```cpp
#include <iostream>
#include <qti/qimsdk.h>
using namespace qti;

void create_and_execute_pipeline() {
  Pipeline pipeline("cam-pipeline");
  pipeline.add("qticamsrc", "source", "camera", 0)
          .add_stream_filter("videofilter", VideoFilter().format("NV12").resolution(1920, 1080).framerate(30))
          .add("waylandsink", "display", "fullscreen", true, "sync", true)
          .execute();
}

int main() {
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);
  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }
  return 0;
}
```

## End-to-End Template: Camera → Inference (Fused ML Bin) → Overlay → Display (AI Pipeline)

Uses explicit `Element` objects (wrapper style) — preferred when elements must be retained for later access or the app is meant as documentation-quality reference code:

```cpp
#include <iostream>
#include <cstdlib>
#include <qti/qimsdk.h>
using namespace qti;

void create_and_execute_pipeline() {
  Element source("qticamsrc", "source");
  source.set("camera", 0);

  Element mlbin("qtimlvideotflitebin", "mlbin");
  mlbin.set("inference-delegate", "external");
  mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
  mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  mlbin.set("inference-model", "<MODEL_PATH>");
  mlbin.set("postprocess-module", "yolov8");
  mlbin.set("postprocess-labels", "<LABELS_PATH>");

  Element overlay("qtivoverlay", "overlay");
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  Pipeline pipeline("camera-yolov8-mlbin-pipeline");
  pipeline.add(source)
          .add_stream_filter("videofilter", VideoFilter().format("NV12").resolution(1920, 1080).framerate(30))
          .add(mlbin)
          .add(overlay)
          .add(display)
          .execute();
}

int main() {
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);
  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }
  return 0;
}
```

## Pattern B: AppSink -> AppSrc Bridge

Use when app performs CPU-side buffer handoff.

```cpp
qti::Pipeline producer("producer-pipeline");
qti::Pipeline consumer("consumer-pipeline");

qti::AppSink appsink("appsink");
qti::AppSrc appsrc("appsrc");

producer.add("qticamsrc", "source", "camera", 0)
        .add_stream_filter("vf", qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30))
        .add(appsink);

appsrc.set("is-live", true);
appsrc.set("block", true);
appsrc.set("format", qti::AppSrc::Format::TIME);
appsrc.set("do-timestamp", true);
appsrc.set("caps", qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30));

consumer.add(appsrc)
        .add("waylandsink", "display", "fullscreen", true, "sync", true);

appsink.set_buffer_consumer([&](qti::Buffer b) {
  appsrc.push_buffer(std::move(b));
});

consumer.start();
producer.start();
consumer.wait();
producer.wait();
consumer.stop();
producer.stop();
```

For decoupled producer/consumer threads, use a hand-rolled thread-safe queue (`mutex` + `condition_variable` + `std::queue<Buffer>`) between the appsink callback and the appsrc push — only when the user explicitly asks for queued/threaded buffer handling.

Use this two-pipeline staged lifecycle only for AppSrc/AppSink bridge requests. Start the consumer/AppSrc pipeline before the producer/AppSink pipeline. For normal single-pipeline apps, default to `execute()`.

### Event-triggered recording variant

For event-triggered recording, keep the recorder pipeline resident and PLAYING with `is-live=true`, `format=TIME`, `do-timestamp=true`, and fully fixed video caps. The metadata AppSink callback should only update an in-memory gate; do not call `Pipeline::start()`, `stop()`, or `eos()` from that callback because synchronous state transitions can deadlock the producer/display pipeline. While the gate is active, forward the composed video AppSink's move-only `qti::Buffer` directly with `AppSrc::push_buffer(std::move(buffer))`; this preserves DMA-backed buffer ownership and avoids a system-memory copy that can crash a hardware encoder. Finalize the recorder with EOS after the main pipeline reaches EOS. For copied AppSrc buffers or file-source-style inputs, use the encoder's driver-managed `capture-io-mode=4` / `output-io-mode=4` pairing; reserve `output-io-mode=5` for the documented camera/AV-record import cases.

## Pattern C: Camera Wrapper

```cpp
qti::Pipeline pipeline("cam");
pipeline.add("qticamsrc", "source", "camera", 0)
        .add_stream_filter("vf", qti::VideoFilter().format("NV12"))
        .add("waylandsink", "display", "fullscreen", true, "sync", true)
        .start();
auto cam = pipeline.get<qti::CamSrc>("source");
cam.image_capture();
// or:
// cam.image_capture(qti::CamSrc::CaptureMode::kBurst, 3);
```

The refreshed examples construct the camera source with the concrete `qticamsrc` factory, then retrieve the typed `CamSrc` wrapper by name with `pipeline.get<qti::CamSrc>("source")` when camera capture control is needed.

## Display Sink Sync Policy

This is the authoritative sync policy for `waylandsink` in this skill. Every other section's sync guidance follows this policy.

- **Default**: `"fullscreen", true, "sync", true` — synchronized fullscreen rendering, for every single-display pipeline, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`). Camera source type alone is never a reason to use `sync=false`.
- **Exception 1 — high-stream-count, processing-heavy topology**: use `sync=false` only when there are more than 8 independent concurrently active input streams (count physical/independent sources: `filesrc`, `rtspsrc`, `qticamsrc`, `qtiqmmfsrc`, `v4l2src` instances — do not count AI branches off one tee, composer tiles from one source, or multiple output pads/sinks from one physical camera) AND the topology is processing-heavy (shared/batched inference, or a large composer grid where HTP/batch preroll makes frames arrive well behind their PTS, causing `sync=true` to drop late frames and freeze/blacken the display). A 4-stream AI wall, a simple multi-stream playback grid, or a batch group of 8 or fewer streams stays `sync=true` — do not flag every multistream/batched pipeline as needing `sync=false`.
- **Exception 2 — multi-sink clock stall**: when display shares a tee/composer source with a parallel encode/file/metadata sink that can stall the display clock, use `"sync", false, "fullscreen", true, "enable-last-sample", false`. This exception is stream-count-independent — it can apply even with a single stream. Pair with `enable-last-sample=false` when it is also a multi-sink camera pipeline.
- **Exception 3 — explicit user request**: use `sync=false` when the user explicitly requests lower latency over A/V sync.
- **Audio classification pipelines**: omit `sync` entirely — `"fullscreen", true` only. Do not add `"sync", true` or `"sync", false`.

```cpp
pipeline.add("waylandsink", "display", "fullscreen", true, "sync", true);
```

## Branching (Tee / Composer / Metamux) in C++

```cpp
pipeline.add("tee", "split")
        .link("split", "q1", "mlmuxer")
        .link("split", "q2", "preprocessing", "inferencing", "postprocessing", "mlf", "mlmuxer");
```

Composer per-input alpha:
```cpp
pipeline.get("composer").input(1).set("alpha", 0.5);
```

## Naming and Linking Rules

- Every `link(...)` name must match an existing `add(..., unique_name, ...)`.
- For branches, use separate `link(...)` chains.
- For generic element handles, use `pipeline.get("<name>")`.
- Use `get<T>(...)` only for wrappers that support typed retrieval in this SDK.
- Preserve user-provided element names when present.
- Do not hardcode mux sink pad names (`sink_0`, `sink_1`) unless explicitly confirmed.

## Queue Placement Rules

- For single-input pipelines, avoid queue between every stage.
- Keep queue isolation after `tee` branches.
- Always keep a queue immediately after hardware decode.
- Add demux queue only when needed for decoupling/robustness.
- Reject adjacent/redundant queue chains.

## YAML Config Mode

Use YAML config mode only when the user asks for YAML-based pipeline declaration inside C++.

Constructor:

```cpp
qti::Pipeline pipeline("name", config_string);
```

Observed YAML structure in SDK apps:

```yaml
pipeline:
  elements:
    - type: filesrc
      name: src
      location: <INPUT_MP4>
    - type: qtdemux
      name: demux
    - type: h264parse
      name: parse
    - type: v4l2h264dec
      name: decoder
      output-io-mode: 4
      capture-io-mode: 4
    - type: queue
      name: q_dec
    - type: filter
      name: vf
      video:
        format: NV12
    - type: tee
      name: split
    - type: filter
      name: mlf
      text: {}
  links:
    - [src, demux, parse, decoder, q_dec, vf, split]
    - [split, q2, mlmuxer]
```

After `Pipeline(name, config)`, typed elements can be retrieved and customized:

- `auto appsrc = pipeline.get<qti::AppSrc>("src");`
- `auto appsink = pipeline.get<qti::AppSink>("sink");`

Rules:

- Keep YAML keys aligned with patterns observed in SDK `*_config.cc` apps.
- Support both YAML situations:
  - user asks for a YAML-driven C++ app but does not say the YAML already exists: generate `main.cc`, `CMakeLists.txt`, `README.md`, and the YAML config file.
  - user explicitly says the YAML already exists or is externally provided: generate only the loader app/build files and README; include the exact README phrase `External YAML provided by user`.
- For generated YAML configs, use the SDK parser schema: top-level `pipeline:`, then `elements:` and `links:`.
- In `elements:`, every item must have `type:` and `name:`. For normal elements, `type:` is the element factory (`type: qticamsrc`, `type: tee`, `type: qtimltflite`); never use `factory:`.
- Put element properties as flat keys beside `type:` and `name:`; never wrap them in `properties:`.
- For generated YAML stream filters, use `type: filter` plus a `video:`, `text:`, `tensor:`, `image:`, `h264:`, `audio:`, or `caps:` block.
- If a requested YAML key is not grounded in examples, use placeholder comments in README.

## Deliverable Rule

- Generated app deliverables must include `main.cc`, `CMakeLists.txt`, and `README.md`.
- `README.md` must include `Steps to Compile` (the Yocto build link only — see `references/artifact-contract.md`), `Steps to Run on QLI`, and `Pipeline Flow` with `Text Summary` and `Mermaid Diagram` subsections.
