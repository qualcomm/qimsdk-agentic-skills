# Sources and Sinks

## Use This Reference For

- Selecting a valid input source for multimedia or AI pipelines
- Choosing the correct output target for display, file, appsink, RTSP, or cross-process transport

## Scope Rule

- Stay within what is defined in this skill reference set.
- It is valid to use standard upstream GStreamer source, parser, queue, tee, sink, and appsink/appsrc elements when they appear in the documented QIM SDK examples.
- Do not introduce extra vendor-specific plugin families that are not grounded in the QIM SDK reference sources.

## Approved Source Patterns

- File source pattern:
  - `filesrc ! qtdemux ! h264parse ! v4l2h264dec ... ! queue ! ...`
  - `filesrc ! qtdemux ! h265parse ! v4l2h265dec ... ! queue ! ...`
- Built-in camera pattern:
  - `qticamsrc ...`
- RTSP pattern:
  - `rtspsrc ... ! rtph264depay ! h264parse ! v4l2h264dec ... ! queue ! ...`
  - `rtspsrc ... ! rtph265depay ! h265parse ! v4l2h265dec ... ! queue ! ...`

### Source Output Format Rule

- Always provide `video/x-raw,format=NV12` from source/decode pipelines before branching (`tee`) or AI preprocessing.
- Apply this to camera and decoded file/RTSP paths unless the user explicitly requests a different documented source format.

If the stream codec is unknown, state assumptions clearly.

**C++ SDK note for dynamic pads (`qtdemux`, `rtspsrc`):** In the C++ SDK, treat `qtdemux` and `rtspsrc` source pads as handled automatically by the SDK — for a straight-line hop through a dynamic-pad element (most commonly `qtdemux` → `h264parse`), just add them in insertion order; the SDK defers/completes the pad-added link internally, so no hand-written `pad-added` callback is required. This differs from the plain-C app convention where `qtdemux`/`rtspsrc` require a manually written pad-added callback linking into a downstream queue.

## Common Sources

### File Input

- Use `filesrc` for offline media
- Follow with demux/parser and hardware decode as needed
- Typical chain for H.264 MP4 content:

```text
filesrc → qtdemux → h264parse → v4l2h264dec → queue → video/x-raw,format=NV12
```

Use a queue after `qtdemux` when the demuxed branches run concurrently (for example, H.264 video plus audio AI): each dynamic video/audio pad should enter its own queue before parsing/decoding so one branch cannot block the other's preroll or scheduling. Always use a queue immediately after any hardware decoder before whatever follows downstream. For simple single-stream video playback, the direct dynamic-pad hop may remain minimal when no concurrent branch needs decoupling.

### Built-In Camera

- Use the documented ISP camera source from `plugin-catalog.md` (`qticamsrc` / `qtiqmmfsrc`).
- If the user does not provide camera resolution or framerate, constrain the camera stream to `1920x1080 @ 30fps` before display, branching, or AI preprocessing and list that assumption in the generated README.
- Preserve user-provided camera resolution and framerate exactly when present.
- Typical example form:

```text
qticamsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1
```

Alternative camera caps used in AI sample apps:

```text
qticamsrc camera=0 name=camsrc ! video/x-raw,format=NV12_Q08C,width=1280,height=720,framerate=30/1
```

### `qtimetamux`/`qtivoverlay` Buffer-Writability Warning

`qtimetamux` attaches metadata to the video buffer, and the plugin source checks `gst_buffer_is_writable()` before adding metadata; `qtivoverlay` draws boxes/masks in-place and has the same sole-ownership requirement. If the buffer received by either is not writable, `qtimetamux` logs `Unable to attach metadata ... not writable!` and metadata is not attached (and `qtivoverlay` silently draws nothing on its downstream buffer).

Camera sources are not assumed inherently non-writable. The reference source confirms the `qtimetamux` writable-buffer requirement, not a blanket ISP camera non-writable-output rule.

**Fix when the source tee also feeds `qtivcomposer`, when the runtime warning is present, or when a known-good source pattern requires conversion:** On the video branch that feeds `qtimetamux`, insert the documented conversion/copy stage before the mux. `qtivcomposer` on a sibling branch is **always** a trigger (it holds buffers for stream sync, so refcount is guaranteed > 1 by the time `qtimetamux` runs); `filesink` and `qtimlmetaparser` siblings are fast but can still hold the buffer under load, so apply the same fix defensively when one of those shares the tee:

```text
t. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <MUX_NAME>.
```

**Wrong placements:**
- `videoconvert` before `qtivoverlay` — strips QTI-specific metadata; bounding boxes disappear silently
- `qtivtransform` before `qtivoverlay` — too late if `qtimetamux` already failed to attach metadata upstream

**Daisy-chain rule:**
Keep later inter-stage branches metadata-preserving. In multi-stage pipelines, a transform between `metamux_1` output and Stage 2 ROI processing can disrupt the ROI metadata needed by `qtimlvconverter` in `roi-batch-cumulative` mode.

Apply this conditionally, not as a blanket rule for every ISP camera pipeline. Prefer the documented known-good topology first, and add `qtivtransform` before `qtimetamux` when the same tee/source stream also feeds `qtivcomposer` (always required), when a sibling branch has `filesink` or `qtimlmetaparser` (required under load), for a documented conversion/copy requirement, or for an observed `qtimetamux` writability failure.

### Leaf `appsink` off a tee that also feeds `qtimlvconverter` — DMA buffer-pool poisoning

Same buffer-ownership family as the `qtimetamux` writability case above, different symptom. When a leaf `appsink` (a metadata/frame tap with no downstream) pulls buffers off a `tee` whose other branch feeds `qtimlvconverter` (the AI preprocess), the appsink's system-memory buffer-pool allocation query propagates back through the tee and forces the AI branch's buffers to non-DMA memory. `qtimlvconverter`/`qtivtransform` then fail at runtime with:

```
video-converter-engine ... Buffer <addr> does not have FD memory!
qtimlvconverter ... Failed to process buffers!
```

and the pipeline runs with **0 inference** (it appears to play — no crash — but no ML output is ever produced). Fix: insert a `qtivtransform` immediately before the leaf `appsink` to isolate its allocation from the shared tee (forces a private, non-DMA copy for the tap while the AI branch keeps its DMA/FD buffers):

```text
t. ! queue ! <overlay/compose path> ! tee name=t2
t2. ! queue ! waylandsink
t2. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! appsink
```

This is the appsink analogue of the `qtivtransform`-before-`qtimetamux` rule: a tee shared with a DMA/ML consumer needs a `qtivtransform` on whichever branch would otherwise poison or be starved of the pool. Applies to any `tee → appsink` frame/metadata tap (event-recording frame relays, metadata-parser appsinks) that coexists with a `qtimlvconverter` branch off the same tee.

Apply conditionally — a plain single-branch `tee → appsink` with no `qtimlvconverter` sharing the tee does not need it.

### USB Camera

- Use `v4l2src`
- Inspect device and supported formats first with `v4l2-ctl`
- Common conversion path used in AI docs:

```text
v4l2src device=/dev/video0 ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12
```

### RTSP Camera

- Use `rtspsrc` and depayload/parse/decode before AI or display
- Typical chain:

```text
rtspsrc location=<RTSP-URL> ! rtph264depay ! h264parse ! v4l2h264dec ! queue ! video/x-raw,format=NV12
```

## Common Sinks

### Display

- `waylandsink` for on-device display
- In AI pipelines, place it after `qtivoverlay` or after `qtivcomposer` if composing multiple streams
- Default display sync policy (canonical statement in `pipeline-construction.md` "Display Sink Sync Policy"):
  - Default for every source type, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`): `waylandsink fullscreen=true sync=true`. Camera source type alone is not a reason to use `sync=false`.
  - Multistream/composer grids: `waylandsink fullscreen=true sync=false` only when there are more than 8 independent concurrently active input streams AND the topology is processing-heavy (shared/batched inference, or a grid where HTP/batch preroll makes frames arrive well behind their PTS). A 4-stream AI wall or a batch group of 8 or fewer streams stays `sync=true`.
  - Multi-sink pipelines (display sharing a tee/composer source with a parallel encode/file/metadata sink): `waylandsink fullscreen=true sync=false enable-last-sample=false` to avoid clock stalls — this applies regardless of stream count, even with a single stream.
  - Audio classification display: omit the `sync` property entirely

Preferred display sink in this skill: `waylandsink`

### File Recording

- IO mode selection for v4l2 encoders depends on what actually allocated the buffer arriving at the encoder's input, not the pipeline's original source type in isolation:
  - Camera source (`qticamsrc` upstream, camera-native DMA import): `capture-io-mode=4 output-io-mode=5`
  - File or RTSP source decoded through hardware decoder (driver-managed/transform-produced NV12): `capture-io-mode=4 output-io-mode=4`
  - AppSrc-fed encoder branch (moved DMA-backed buffer forwarded via `push_buffer(std::move(...))`, e.g. event-triggered recording): `capture-io-mode=4 output-io-mode=4` — same as file/RTSP, because the buffer is still driver-managed NV12, not camera-native import
  - AV record where audio is muxed into MP4: `capture-io-mode=4 output-io-mode=5`

- Typical file/RTSP-source pattern:

```text
... → v4l2h264enc capture-io-mode=4 output-io-mode=4 → h264parse → mp4mux → filesink location=...
```

### RTSP Serving

- For network serving output from encoded H.264 streams, use:

```text
... → v4l2h264enc capture-io-mode=4 output-io-mode=5 → h264parse config-interval=1 → qtirtspbin port=8900 mpoint=/live address=0.0.0.0
```

**`qtirtspbin` property names (exact — do not guess):**
- `mpoint` — mount point string, e.g. `mpoint=/live` (NOT `mount-point`)
- `port` — port number as string, e.g. `port=8900`
- `address` — server IP string; **always set `address=0.0.0.0`** to bind on all interfaces — the default `127.0.0.1` binds localhost only and makes the stream unreachable from any other machine

**Serving metadata (text/x-raw) over RTSP:**
`qtirtspbin` accepts `text/x-raw` on a separate sink pad alongside video. Feed the `text/x-raw` output from `qtimlpostprocess` into a second `qtirtspbin` instance on a different port. The plugin uses `rtpgstpay` internally; the client receives with `rtspsrc ! rtpgstdepay`.

```text
qtimlpostprocess ... ! text/x-raw ! tee name=meta_tee
meta_tee. ! queue ! qtimetamux.                                          (for overlay)
meta_tee. ! queue ! qtirtspbin port=8901 mpoint=/metadata address=0.0.0.0   (metadata RTSP)
```

**Critical:** Each `qtirtspbin` instance must use a unique port — two instances cannot share the same port. Use different ports for video and metadata (e.g. 8900 for video, 8901 for metadata).

**Known constraint — RTSP loopback decode on Qualcomm Linux:**
On some Qualcomm Linux devices, `v4l2h264dec` refuses caps from `rtspsrc` at runtime with `caps video/x-h264, stream-format=byte-stream, alignment=au ... not accepted` — even though the documented chain `rtspsrc ! rtph264depay ! h264parse ! v4l2h264dec` looks correct. This appears to be a driver-level constraint specific to certain SDK versions.

**Workaround:** If you need both a raw RTSP stream and an AI-processed RTSP stream from the same camera, do NOT chain them via RTSP loopback. Instead, use a single pipeline with a `tee` and two independent encode+RTSP output branches:

```text
qticamsrc → NV12 caps → queue → tee name=t_src
  t_src branch 1 → queue → v4l2h264enc → h264parse → qtirtspbin port=8900 mpoint=/live    (raw)
  t_src branch 2 → queue → tee name=t → AI pipeline → v4l2h264enc → h264parse → qtirtspbin port=8901 mpoint=/detect  (AI overlay)
```

### WebRTC Serving

- Use upstream `webrtcbin` only when user explicitly requests WebRTC output.
- Use encoded RTP input into `webrtcbin` (for example H.264):

```text
... → v4l2h264enc capture-io-mode=4 output-io-mode=4 → h264parse config-interval=1 → rtph264pay pt=96 → webrtcbin
```

- Treat signaling and ICE exchange as application-level concerns; do not invent signaling servers/URLs unless user provides them.

### Appsink

- Use when C/C++ code must receive processed frames or metadata outside the pipeline
- In C++ SDK: use `qti::AppSink` wrapper with `set_buffer_consumer(...)` callback

### Cross-Process / Container Transport

- Use `qtisocketsink` to send FD-backed buffers over a UNIX socket
- Use `qtisocketsrc` in the receiving pipeline
- This is the preferred modular zero-copy option when the design spans multiple processes or containers

## Source/Sink Selection Guidance

- Use `filesrc` for reproducible test media
- Use the documented ISP camera source from `plugin-catalog.md` for Qualcomm camera use cases
- Use `rtspsrc` when the stream originates on the network
- Use `waylandsink` for live preview
- Use `filesink` for archival output
- Use `appsink` / `qti::AppSink` for app integration
- Use `qtisocketsink` and `qtisocketsrc` for zero-copy process boundaries
- Use `webrtcbin` only when browser/WebRTC delivery is explicitly requested

## Completeness Checks for Source/Sink

- Every generated pipeline must contain exactly one explicit source entry point and one explicit output target path.
- If output is display, include one display sink path that is fed by the final branch.
- If output is file, include mux/encode chain where required before `filesink`.
