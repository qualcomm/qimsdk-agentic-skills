# Source and Sink Patterns

## Purpose

Canonical input/output, decode, encode, display, file, network, and transport patterns.

## Load When

Load when a task depends on source type, sink type, decode/encode chain, display behavior, RTSP, sockets, or appsink/appsrc.

## This File Owns

- File, camera, USB, RTSP, and socket source chains
- Display, file, network, appsink, and appsrc sink chains
- Decode and encode entry/exit chains
- Source-format normalization decisions such as NV12 conversion

## This File Does Not Own

- Plugin property catalog; use plugin-catalog.md
- AI stage topology; use ai-pipeline-patterns.md
- Queue/tee policy; use pipeline-utilities.md

---


---

## Source and Sink Chains

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
  - `filesrc ! qtdemux ! h264parse ! v4l2h264dec ...`
  - `filesrc ! qtdemux ! h265parse ! v4l2h265dec ...`
- Built-in camera pattern:
  - `qtiqmmfsrc ...`
- RTSP pattern:
  - `rtspsrc ... ! rtph264depay ! h264parse ! v4l2h264dec ...`
  - `rtspsrc ... ! rtph265depay ! h265parse ! v4l2h265dec ...`

### Source Output Format Rule

- Always provide `video/x-raw,format=NV12` from source/decode pipelines before branching (`tee`) or AI preprocessing.
- Apply this to camera and decoded file/RTSP paths unless the user explicitly requests a different documented source format.
- The decode chains shown above (and below) end at the decoder for brevity — a `queue` always follows the decoder before whatever comes next, regardless of what that is; see `plugin-catalog.md`'s `queue` entry and `pipeline-utilities.md`'s Queue Usage for the full rule.

If the stream codec is unknown, state assumptions clearly.

For C/C++ app code (not raw `gst-launch` commands), treat `qtdemux` and `rtspsrc` source pads as dynamic and wire them with `pad-added` callbacks into downstream queue sink pads.

## Common Sources

### File Input

- Use `filesrc` for offline media
- Follow with demux/parser and hardware decode as needed
- Typical chain for H.264 MP4 content:

```text
filesrc → qtdemux → h264parse → v4l2h264dec → video/x-raw,format=NV12
```

Use a queue after `qtdemux` only when decoupling dynamic-pad behavior is explicitly required.

### Built-In Camera

- Use `qtiqmmfsrc`
- If the user does not provide camera resolution or framerate, default to `1920x1080 @ 30fps` with NV12 caps and list that assumption in the generated README. Preserve user-provided camera resolution and framerate exactly when present.
- Typical example form:

```text
qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1
```

Alternative camera caps used in AI sample apps:

```text
qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12_Q08C,width=1280,height=720,framerate=30/1
```

### Buffer-Writability Warning (`qtivoverlay` / `qtivcomposer`)

See SKILL.md's *Buffer writability — `qtivoverlay` and `qtivcomposer`* for the full mechanism and trigger conditions. Summary: both elements write in-place and require sole buffer ownership (refcount == 1); after a `tee`, a parallel branch that hasn't released the buffer causes the write to be silently skipped.

Fix — insert `qtivtransform ! video/x-raw,format=NV12` immediately before `qtimetamux` on the overlay chain's video branch:

```text
t. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <MUX_NAME>.
```

**Wrong placements:**
- `videoconvert` before `qtivoverlay` — strips QTI-specific metadata; bounding boxes disappear silently
- `qtivtransform` before `qtivoverlay` but after `qtimetamux` — too late; `qtimetamux` has already failed to attach metadata upstream
- Any video transform on a daisy-chain inter-stage branch carrying ROI metadata to a later stage — strips the ROI metadata that stage needs (separate constraint, applies regardless of the writability rule)

### Leaf `appsink` off a tee that also feeds `qtimlvconverter` — DMA buffer-pool poisoning

Same buffer-ownership family as the `qtimetamux` writability case, different symptom. When a leaf `appsink` (a frame/metadata tap with no downstream) pulls buffers off a `tee` whose other branch feeds `qtimlvconverter` (the AI preprocess), the appsink's system-memory buffer-pool allocation query propagates back through the tee and forces the AI branch's buffers to non-DMA memory. `qtimlvconverter`/`qtivtransform` then fail at runtime with:

```text
video-converter-engine ... Buffer <addr> does not have FD memory!
qtimlvconverter ... Failed to process buffers!
```

and the pipeline runs with **0 inference** (it appears to play — no crash — but produces no ML output). Fix: insert a `qtivtransform` immediately before the leaf `appsink` to isolate its allocation from the shared tee (forces a private, non-DMA copy for the tap while the AI branch keeps its DMA/FD buffers):

```text
t. ! queue ! <overlay/compose path> ! tee name=t2
t2. ! queue ! waylandsink
t2. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! appsink
```

Applies to any `tee → appsink` frame/metadata tap (event-recording frame relays, `appsink` metadata parsers) coexisting with a `qtimlvconverter` branch off the same tee. Apply conditionally — a plain single-branch `tee → appsink` with no `qtimlvconverter` sharing the tee does not need it.

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
rtspsrc location=<RTSP-URL> ! rtph264depay ! h264parse ! v4l2h264dec ! video/x-raw,format=NV12
```

## Common Sinks

### Display

- `waylandsink` for on-device display
- In AI pipelines, place it after `qtivoverlay` or after `qtivcomposer` if composing multiple streams
- Default display sync policy: `waylandsink fullscreen=true sync=true` unless the user explicitly asks for unsynced/lower-latency output or a task-specific template says otherwise. **Exception: audio classification pipelines omit the `sync` property entirely** (see `ai-pipeline-patterns.md` Template 12). Use `sync=true` for ordinary single-output display/playback, including camera display, file playback, and RTSP display. Use `sync=false` only for documented multi-sink/live-parallel cases such as display running alongside encode sinks, large multistream composer grids, or explicit low-latency requests; pair multi-sink display cases with `enable-last-sample=false` when the template calls for it.

Preferred display sink in this skill: `waylandsink`

### File Recording

- Typical pattern:

```text
... → v4l2h264enc → h264parse → mp4mux → filesink location=...
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

**Critical:** Each `qtirtspbin` instance must use a unique port — two instances cannot share the same port. Use different ports for video and metadata (e.g. 8900 for video, 8901 for metadata). Verified on device: client receives `ObjectDetection` structured buffers via `rtspsrc location=rtsp://<ip>:8901/metadata ! rtpgstdepay ! fakesink dump=true`.

**Known constraint — RTSP loopback decode on Qualcomm Linux:**
On some Qualcomm Linux devices, `v4l2h264dec` refuses caps from `rtspsrc` at runtime with `caps video/x-h264, stream-format=byte-stream, alignment=au ... not accepted` — even though the documented chain `rtspsrc ! rtph264depay ! h264parse ! v4l2h264dec` looks correct. This appears to be a driver-level constraint specific to certain SDK versions.

**Workaround:** If you need both a raw RTSP stream and an AI-processed RTSP stream from the same camera, do NOT chain them via RTSP loopback (camera → RTSP → decode → AI → RTSP). Instead, use a single pipeline with a `tee` and two independent encode+RTSP output branches:

```text
qtiqmmfsrc → NV12 caps → queue → tee name=t_src
  t_src branch 1 → queue → v4l2h264enc → h264parse → qtirtspbin port=8900 mpoint=/live    (raw)
  t_src branch 2 → queue → tee name=t → AI pipeline → v4l2h264enc → h264parse → qtirtspbin port=8901 mpoint=/detect  (AI overlay)
```

This avoids the decode step entirely and works reliably since both branches stay within the same pipeline using DMA-backed buffers throughout.

### WebRTC Serving

- Use upstream `webrtcbin` only when user explicitly requests WebRTC output.
- Use encoded RTP input into `webrtcbin` (for example H.264):

```text
... → v4l2h264enc capture-io-mode=4 output-io-mode=4 → h264parse config-interval=1 → rtph264pay pt=96 → webrtcbin
```

- Treat signaling and ICE exchange as application-level concerns; do not invent signaling servers/URLs unless user provides them.

### Appsink

- Use when C/C++ code must receive processed frames or metadata outside the pipeline

### Cross-Process / Container Transport

- Use `qtisocketsink` to send FD-backed buffers over a UNIX socket
- Use `qtisocketsrc` in the receiving pipeline
- This is the preferred modular zero-copy option when the design spans multiple processes or containers

## Source/Sink Selection Guidance

- Use `filesrc` for reproducible test media
- Use `qtiqmmfsrc` for Qualcomm camera use cases
- Use `rtspsrc` when the stream originates on the network
- Use `waylandsink` for live preview
- Use `filesink` for archival output
- Use `appsink` for app integration
- Use `qtisocketsink` and `qtisocketsrc` for zero-copy process boundaries
- Use `webrtcbin` only when browser/WebRTC delivery is explicitly requested

## Completeness Checks for Source/Sink

- Every generated pipeline must contain exactly one explicit source entry point and one explicit output target path.
- If output is display, include one display sink path that is fed by the final branch.
- If output is file, include mux/encode chain where required before `filesink`.

---

## Input Source Types

# Input Types

## Use This Reference For

- Selecting input source chains for generated pipelines
- Building complete decode entry paths for file, camera, USB, and RTSP sources

## File Inputs

H.264 file input pattern:

```text
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

H.265 file input pattern:

```text
filesrc location=<INPUT_FILE> ! qtdemux ! h265parse ! v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

## Built-In Camera Input

```text
qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1
```

Use `name=camsrc` as the element instance name. Set `camera=0` for `qtiqmmfsrc` camera selection.
If the user provides resolution or framerate, use those exact values instead of the default. If the prompt omits them, keep the `1920x1080 @ 30fps` default concrete in generated commands/C apps and document it in README assumptions.

Alternative camera caps used in sample Python/C++ apps:

```text
qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12_Q08C,width=<W>,height=<H>,framerate=<FPS>/1
```

## USB Camera Input

```text
v4l2src device=<DEVICE_NODE> ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12
```

## RTSP Input

H.264 RTSP pattern:

```text
rtspsrc location=<RTSP_URL> ! rtph264depay ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

H.265 RTSP pattern:

```text
rtspsrc location=<RTSP_URL> ! rtph265depay ! h265parse ! v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

## Input Validation Rules

- Always choose one input pattern only.
- Ensure parser/decoder match the expected stream codec.
- End input section with raw video caps before AI preprocessing stages.
- Add queue after `qtdemux` only when explicit decoupling/robustness is needed.
- Always add `queue` immediately after the hardware decoder, before whatever follows (see plugin-catalog.md → `queue` entry and pipeline-utilities.md → Queue Usage).
- If codec is unknown, state assumption and keep placeholders.

---

## Output Types

# Output Types

## Use This Reference For

- Selecting output sink chains
- Finishing generated pipelines with valid terminal branches

## Display Output

Preferred display pattern (all source types — file, camera, RTSP — and normal composed multi-stream, e.g. a 4-stream AI wall):

```text
... ! qtivoverlay ! waylandsink fullscreen=true sync=true
```

For composed multi-stream output:

```text
... ! qtivcomposer ... ! queue ! waylandsink fullscreen=true sync=true
```

`sync=true` is the default for every source type, including live camera sources (`qticamsrc`, `qtiqmmfsrc`, `v4l2src`) and ordinary composed/multi-stream display. Use `sync=false` only for one of these documented exceptions:

- **More than 8 independent concurrently active input streams** AND the topology is processing-heavy (shared/batched inference, or a large composer grid where HTP/batch preroll makes frames arrive well behind their PTS — see `ai-pipeline-patterns.md` "Batched Multi-Stream AI"). Count independent input streams (`filesrc`/`rtspsrc`/`qticamsrc`/`qtiqmmfsrc`/`v4l2src` instances), not AI branches, composer tiles, or multiple output pads from one source. A 4-stream AI wall, a simple multi-stream playback grid, or a batch group of 8 or fewer streams stays `sync=true`.
- Display shares a `tee`/composer source with a parallel encode, file, or metadata sink that can stall the display clock:

  ```text
  ... ! qtivoverlay ! waylandsink sync=false fullscreen=true enable-last-sample=false
  ```

- The user explicitly asks for lower latency over A/V sync:

  ```text
  ... ! qtivoverlay ! waylandsink fullscreen=true sync=false
  ```

Audio-classification display pipelines omit the `sync` property entirely — see `ai-pipeline-patterns.md` "Route A3" / Template 12.

Note: `fullscreen=true` is the canonical default for all display pipelines regardless of source type. Do not omit it for file, camera, or RTSP sources.

## qtivcomposer Pad Property Syntax

Pad properties for `qtivcomposer` use the `sink_N::property="<value>"` syntax:

```bash
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<W, H>" \
  sink_1::position="<W, 0>" sink_1::dimensions="<W, H>"
```

- `position="<x, y>"` — top-left corner of the pane in pixels
- `dimensions="<width, height>"` — AR-fit size of the stream (not the full cell — see `generation-rules.md` Multi-Stream Layout Rule)

**The number of declared `sink_N::position`/`sink_N::dimensions` pairs must exactly match the number of branches actually linked into that `qtivcomposer` instance** — re-derive this count after every topology edit, not carry over a count from an earlier draft. A `sink_N::position`/`dimensions` pair for a pad index that no longer has anything linked to it (or was never created) is a silent no-op — it doesn't error, but the tile it would have configured never gets sized/positioned. This most often happens when a branch that used to feed the composer as two raw sink pads (e.g. a passthrough tile plus a separately rendered mask tile) is refactored to pre-compose those two into one finished tile through a local `qtivcomposer` first — the top-level composer then needs one fewer `sink_N` pair than before the refactor, not the same count with a leftover index.
- Use `sink_0`, `sink_1`, etc. for each input pad
- Do NOT use `xpos=`, `ypos=`, `width=`, `height=` as separate properties — this is incorrect syntax

## File Output

**IO mode selection for v4l2 encoders depends on the upstream source (v4l2 driver 1.4+):**
- **Camera source** (`qtiqmmfsrc`/`qticamsrc` upstream): `capture-io-mode=4 output-io-mode=5` — encoder imports the camera's natively-allocated DMA buffer FDs
- **File or RTSP source** (upstream decoded via `v4l2h264dec`/`v4l2h265dec`): `capture-io-mode=4 output-io-mode=4` — driver manages both sides
- **AV record** (encoder feeds `mp4mux` alongside audio): `capture-io-mode=4 output-io-mode=5` regardless of source

H.264 file recording from file/RTSP source:

```text
... ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```

H.264 file recording from camera source:

```text
qtiqmmfsrc ... ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```

H.264 RTSP-serving output (any source):

```text
... ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! h264parse config-interval=1 ! qtirtspbin
```

H.265 file recording from file/RTSP source:

```text
... ! v4l2h265enc capture-io-mode=4 output-io-mode=4 ! h265parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```

## Appsink Output

```text
... ! appsink name=appsink sync=false emit-signals=true
```

## Socket Transport Output

```text
... ! qtisocketsink socket=<SOCKET_PATH>
```

## WebRTC Output

Use WebRTC only when explicitly requested. Canonical egress pattern:

```text
... ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse config-interval=1 ! rtph264pay pt=96 ! webrtcbin
```

## Output Validation Rules

- Every final pipeline must have one explicit output target branch.
- If output is display, ensure overlay/compose stage feeds display sink.
- If output is `waylandsink`, default to `fullscreen=true sync=true` for every source type, including live camera. Use `sync=false` only for the documented exceptions: more than 8 independent processing-heavy/batched streams with late-frame risk, a parallel encode/file/metadata sink sharing the display's tee/composer (clock-stall case), an explicit low-latency request, or audio-classification display (omit `sync` entirely).
- If output is file, ensure encode + parse + mux chain before filesink.
- Do not leave terminal branches unconnected.
