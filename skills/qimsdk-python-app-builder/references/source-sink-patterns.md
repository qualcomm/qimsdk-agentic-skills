# Source and Sink Patterns

## MP4 H.264 File Decode to Display

Use explicit construction by default:

```python
source = Element("filesrc", "source").set("location", "<INPUT_FILE>")
demux = Element("qtdemux", "demux")
parser = Element("h264parse", "parser")
decoder = Element("v4l2h264dec", "decoder").set("capture-io-mode", 4, "output-io-mode", 4)
q_dec = Element("queue", "q_dec")
vf = VideoFilter().format("NV12")
display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)

pipeline.add(source).add(demux).add(parser).add(decoder).add(q_dec)
pipeline.add_stream_filter("vf", vf)
pipeline.add(display)
pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "display")
```

## Camera to Display

```python
source = Element("qticamsrc", "source").set("camera", 0)
vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)
display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)
```

Default camera id is `0`.
If the user does not provide camera resolution or framerate, default the camera stream to `1920x1080 @ 30fps` through the `VideoFilter` and list that assumption in the generated README. Preserve user-provided camera resolution and framerate exactly when present.
Live camera sources default `sync=True` on the display sink, same as every other source type — camera source type alone is not a reason to use `sync=False`. Only switch to `sync=False` for one of the three documented exceptions: more than 8 independent concurrently active streams in a processing-heavy topology, a display that shares a tee/composer source with a parallel encode/file/metadata sink that can stall the display clock, or an explicit user request for lower latency over A/V sync.

## USB Camera to Display

Use `v4l2src` for USB/V4L2 cameras. Pin the USB camera output to YUY2 before `qtivtransform`, then constrain the transformed stream to NV12:

```python
source = Element("v4l2src", "source").set("device", "<DEVICE_NODE>")
yuy2 = VideoFilter().format("YUY2")
transform = Element("qtivtransform", "transform")
nv12 = VideoFilter().format("NV12")
display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)

pipeline.add(source)
pipeline.add_stream_filter("yuy2", yuy2)
pipeline.add(transform)
pipeline.add_stream_filter("nv12", nv12)
pipeline.add(display)
pipeline.link("source", "yuy2", "transform", "nv12", "display")
```

If the user does not provide a USB device path, use `<DEVICE_NODE>` or `/dev/video2` only when explicitly matching the SDK sample. `qtivtransform` is required in this USB-camera pattern for YUY2-to-NV12 conversion/copy; this does not change the general rule that `qtivtransform` should not be inserted blindly in every camera pipeline. Do not apply the ISP-camera `1920x1080 @ 30fps` default to USB camera caps unless the user requests it or a loaded sample explicitly requires that mode.

## File Output

For MP4 output:

```python
encoder = Element("v4l2h264enc", "encoder")
parser = Element("h264parse", "parse_out")
mux = Element("mp4mux", "mux")
sink = Element("filesink", "sink").set("location", "<OUTPUT_FILE>")

pipeline.eos(True)
```

Encoder IO mode selection — select from what actually allocates the buffer arriving at the encoder's input, not from the pipeline's original source type in isolation: use `capture-io-mode=4`, `output-io-mode=4` when the encoder's immediate input is driver-managed/transform-produced NV12 (file/RTSP source decoded through the hardware decoder, or a USB (`v4l2src`) source that has already passed through `qtivtransform` before reaching the encoder — the transform, not the camera, now owns buffer allocation), and reserve `output-io-mode=5` (dmabuf-import) for a camera source (`qticamsrc`/`qtiqmmfsrc`) feeding the encoder directly with no intervening transform/composite stage, or for an AV-record branch where audio is muxed into MP4 (use `output-io-mode=5` there regardless of source). Quick lookup for the common cases:

- file/RTSP source decoded through hardware decoder: `capture-io-mode=4`, `output-io-mode=4`
- camera source (`qticamsrc` or `qtiqmmfsrc`) feeding encoder directly: `capture-io-mode=4`, `output-io-mode=5`
- USB (`v4l2src`) source that passed through `qtivtransform` before the encoder: `capture-io-mode=4`, `output-io-mode=4` — not `4/5`, even though the ultimate source is a camera
- AV record with audio muxed into MP4: use `output-io-mode=5` regardless of source

Use `pipeline.eos(True)` so the muxer can finalize the file.

## RTSP Input

Use RTSP only when requested. If the user asks for RTSP input, decode before AI or display:

```python
source = Element("rtspsrc", "source").set("location", "<RTSP_URL>")
depay = Element("rtph264depay", "depay")
parser = Element("h264parse", "parser")
decoder = Element("v4l2h264dec", "decoder").set("capture-io-mode", 4, "output-io-mode", 4)
q_dec = Element("queue", "q_dec")
vf = VideoFilter().format("NV12")
```

The documented Python RTSP input pattern covers H.264 RTSP input. Keep H.265 RTSP input as an explicit placeholder unless the target documents it; for RTSP serving, load `multimedia-pipeline-patterns.md` and use the documented `qtirtspbin` pattern.

## Queue and Tee Policy

Use queues when they solve a real topology issue:

- immediately after every hardware decoder (`v4l2h264dec`, `v4l2h265dec`, `v4l2vp9dec`, `v4l2av1dec`) before whatever follows
- after dynamic demux boundaries when decoupling is needed
- after `tee` on each branch
- before/after AI branches when inference or postprocess can block
- before mux/merge boundaries when branches need independent scheduling

Avoid adjacent queues, queues before and after every simple element, and queues in a single linear file-to-display path unless a loaded example requires it.
The decoder queue is the exception to the single-linear-path rule; keep it after hardware decode.

For branch topologies:

1. Add `tee`.
2. Add one `queue` per branch.
3. Link branch names explicitly.
4. Keep metadata branch and video branch aligned with `qtimetamux` before overlay.

## Stream Filters and Paths

Use SDK filters instead of raw caps strings when the SDK provides a wrapper:

- `VideoFilter().format("NV12")`
- `VideoFilter().format("RGBA")`
- `TextFilter()`
- `TensorFilter()`
- `H264Filter()`

Use `TextFilter()` before `qtimetamux` for metadata text branches.

Preserve explicit user paths exactly. If the user omits paths, use placeholders rather than inventing device-specific values. Python strings do not perform shell expansion, and neither do GStreamer element properties — a raw `"$HOME/..."` string passed to `filesrc location` (or a model/labels property) is used verbatim and fails to open. For any `$HOME`-style default, expand it in Python first with `f"{os.environ['HOME']}/..."`. Do not use `os.path.expandvars("$HOME/...")` — it silently leaves the string unexpanded if `HOME` is unset instead of raising, which reproduces the same unresolved-path failure at runtime. See `generation-rules.md` "Defaults".

## AppSrc/AppSink

Use `AppSrc` and `AppSink` wrappers when Python code produces or consumes buffers.

Do not use generic `Element("appsrc", ...)` or `Element("appsink", ...)` when the request needs callback handler convenience APIs.

Callback rules:

- `AppSrc` has no generic `set_handler(...)`. Use `appsrc.set_buffer_producer(producer)` for SDK wrapper producer callbacks. Use `appsrc.get_raw().connect("need-data", handler)` only when the request explicitly needs raw GStreamer `need-data` signal handling.
- `AppSrc` enough-data callbacks use `appsrc.set_enough_handler(handler)`.
- `AppSink` has no generic `set_handler(...)`. Use `appsink.set_buffer_consumer(consumer)` for sample consumption.
- `AppSink` preroll and EOS callbacks use `appsink.set_preroll_handler(handler)` and `appsink.set_eos_handler(handler)` only when those events are requested.
- Never generate `appsrc.set_handler(...)` or `appsink.set_handler(...)`; `set_handler(...)` belongs to `MLVConverter` and discrete `MLPostprocess`; ML-bin custom postprocess uses `set_postprocess_handler(...)`.


## Cross-Process Zero-Copy Transport

Use `qtisocketsink` and `qtisocketsrc` when the request explicitly places a
pipeline boundary between processes or containers and requires FD-backed buffer
transport:

Producer process:

```python
socket_sink = Element("qtisocketsink", "socket_sink")
socket_sink.set("socket", "<SOCKET_PATH.sock>")
```

Receiver process:

```python
socket_source = Element("qtisocketsrc", "socket_source")
socket_source.set("socket", "<SOCKET_PATH.sock>")
```

Rules:

- Use the exact same valid socket path on both sides; preserve a user-provided
  path exactly.
- The transport accepts FD-backed `video/x-raw`, `neural-network/tensors`, and
  `text/x-raw` buffers. Keep the upstream allocation zero-copy capable (for
  example, DMA-backed camera/decoder buffers); do not map buffers into Python
  merely to forward them.
- Do not insert `videoconvert`, an unnecessary `qtivtransform`, or an
  AppSink/AppSrc round-trip around the socket boundary. Add a transform only
  when the requested downstream caps require a documented conversion.
- `AppSrc`/`AppSink` are in-process application callbacks; they are not a
  replacement for `qtisocketsink`/`qtisocketsrc` when the request requires a
  process or container boundary.
- Keep normal queue placement at the producer/receiver pipeline boundaries,
  and preserve NV12 normalization before AI or branching when the source path
  requires it.
- Do not add socket transport to an ordinary single-process pipeline. If the
  request does not specify the process boundary, use the simpler direct link.

This transport rule does not change `qtimetamux` writability guidance: metadata
must still be attached to a writable video buffer before the mux/overlay path.

## RTSP

Use RTSP elements only when requested. If the exact RTSP topology or protocol is unclear, ask before generating.

## Buffer Writability Under Shared Tees

When a `tee` feeds a consumer that needs sole ownership of a buffer while another branch off the same tee holds those buffers, the owning consumer silently fails. Two concrete cases share this root cause and the same fix (insert `qtivtransform` to force a private buffer copy):

**Case 1 — `qtimetamux`/`qtivoverlay` in-place metadata draw.** `qtimetamux` can attach metadata only when the target video buffer is writable (do not assume ISP camera buffers are inherently non-writable). When a `tee` branch feeds `qtimetamux` and the same source also feeds a `qtivcomposer` (which holds buffers for stream sync → refcount > 1), the overlay silently draws nothing — this is **always** a trigger. A sibling branch with `filesink` or `qtimlmetaparser` is fast but can still hold the buffer under load, so apply the same fix defensively when one of those shares the tee. Insert `qtivtransform` + an NV12 filter on the passthrough branch before `qtimetamux`:

```text
tee -> queue -> qtivtransform -> VideoFilter().format("NV12") -> qtimetamux
```

Do not place `qtivtransform` after `qtimetamux` or before `qtivoverlay` (too late for metadata attachment). Do not insert transforms between daisy-chain stages where ROI metadata must remain available to the next `qtimlvconverter`. Do not put `videoconvert` before `qtivoverlay` (it can strip QTI-specific metadata).

**Case 2 — leaf video `AppSink` off a tee that also feeds `qtimlvconverter`.** A leaf `AppSink` pulling frames off a tee propagates a system-memory buffer-pool allocation query back through the tee. That forces the AI branch's buffers to non-DMA memory, and `qtimlvconverter`/`qtivtransform` then fail with `Buffer does not have FD memory!` → `Failed to process buffers` → **0 inference frames** (the pipeline appears to run but no inference happens). Insert a `qtivtransform` immediately before the video `AppSink` to isolate its allocation from the shared tee:

```text
tee -> queue -> ... -> overlay/composer -> tee2 -> queue -> waylandsink
                                            tee2 -> queue -> qtivtransform -> VideoFilter().format("NV12") -> AppSink
```

This is the same shape as Case 1 — a tee shared with a DMA/ML consumer needs a `qtivtransform` on the branch whose consumer would otherwise poison or be starved of the pool.

## Display Default

For EVERY source type -- file/decode/RTSP playback and live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`) alike -- use:

```python
display.set("fullscreen", True)
display.set("sync", True)
```

Camera source type alone is never a reason to switch to `sync=False`. Use `sync=False` only for one of these three documented exceptions:

1. **More than 8 independent concurrently active input streams** AND the topology is processing-heavy (shared/batched inference, or a large composer grid where HTP/batch preroll makes frames arrive well behind their PTS), so `sync=True` would drop late frames and freeze/blacken the display:

   ```python
   display.set("fullscreen", True)
   display.set("sync", False)
   ```

   A 4-stream AI wall, a simple multi-stream playback grid, or a batch group of 8 or fewer streams does not meet this threshold and stays `sync=True`.

2. Display shares a tee/composer source with a parallel encode/file/metadata sink that can stall the display clock (this exception is stream-count-independent -- it can apply even with a single stream):

   ```python
   display.set("fullscreen", True)
   display.set("sync", False)
   display.set("enable-last-sample", False)  # when also a multi-sink camera pipeline
   ```

3. The user explicitly requests lower latency over A/V sync.

Audio-classification display pipelines must omit `sync` entirely rather than setting it to either value.

**Exception: audio classification pipelines omit `sync` entirely** (see `ai-pipeline-patterns.md` "Audio AI Classification"). This exception is scoped to audio classification only — it does not change the ordinary file/decode/RTSP `sync=True` default above.

Set `async` only when the user requests or an example requires it.
