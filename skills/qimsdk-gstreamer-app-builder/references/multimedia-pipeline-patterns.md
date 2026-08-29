# Multimedia Pipeline Patterns

## Purpose

Canonical non-AI multimedia routing, building blocks, and verified MM templates.

## Load When

Load for multimedia-only capture, display, encode, playback, transform, streaming, audio, AV, and composition requests.

## This File Owns

- Multimedia request routing
- Camera/display/record/playback/transform/audio/AV patterns
- MM-1 through MM-21 verified templates
- Multimedia anti-patterns and composition layout patterns

## This File Does Not Own

- AI inference topology; use ai-pipeline-patterns.md
- Plugin property catalog; use plugin-catalog.md
- Artifact file contract; use artifact-contract.md

---


---

## Multimedia Routing and Building Blocks

# Multimedia Pipelines

## Use This Reference For

- Multimedia-only gst-launch requests: camera display, camera capture/encode, multi-camera composition, video playback, transform/flip/scale/rotate, audio capture/playback, AV record/playback
- No AI inference stages in the pipeline
- For **mixed multimedia + AI** requests (e.g., camera + object detection), load this file AND the AI pipeline references

---

## gst-launch-1.0 vs gst-pipeline-app

**Default: always use `gst-launch-1.0`** for all multimedia pipelines.

`gst-pipeline-app` is a special-purpose interactive utility (available from QIM SDK 2.0 RC3). Use it **only** when the pipeline requires runtime signal interaction that `gst-launch-1.0` cannot support:

| Scenario | Use |
|---|---|
| All standard multimedia pipelines (camera display, encode, playback, AV record, composition, grid playback) | `gst-launch-1.0` |
| `qticamsrc` JPEG image pad / snapshot capture | **`gst-pipeline-app`** — image pads are on-demand; `capture-image` signal must be sent interactively after PLAYING |
| Any pipeline requiring `capture-image`, `cancel-capture`, or `video-pads-activation` action signals at runtime | **`gst-pipeline-app`** |

`gst-pipeline-app` presents an interactive menu after the pipeline reaches PLAYING, allowing you to send signals to named elements. All other pipelines run without interaction via `gst-launch-1.0`.

---

## Request Routing Table

Use the template in this file that matches the user's request:

| Request type | Template | Notes |
|---|---|---|
| Camera display — ISP / USB / RTSP (single stream) | MM-1 | Three source variants in one template |
| Three streams from single `qticamsrc` + `qtivcomposer` | MM-2 | `qtivtransform` per branch |
| Camera → H.264 MP4 record | MM-3 | |
| Camera → RTSP UDP stream (gst-rtsp-server pattern) | MM-4 | Two-step: server process + pipeline |
| Camera dual-stream 4K + 480p to separate files | MM-5 | Two `camsrc. !` branches |
| Camera three-stream AVC + HEVC + display | MM-6 | `sync=false` on display pad |
| Camera JPEG snapshot (image pad) + AVC record + display | MM-7 | **requires `gst-pipeline-app`** — image pads are on-demand, not streaming |
| Dual camera (two `qticamsrc` instances) multi-stream | MM-8 | `camera=0` and `camera=1` |
| Multi-camera side-by-side compose → MP4 + RTSP UDP | MM-9 | `qtivcomposer` → `tee` → two encode branches |
| Multi-camera PiP compose → MP4 + RTSP UDP | MM-10 | Same topology as MM-9, different layout |
| Transform — rotate | MM-11 | `qtivtransform rotate=180` (enum nick) |
| Transform — downscale + horizontal flip | MM-12 | caps filter for scale |
| Single H.264 file playback | MM-13 | |
| Dual parallel file playback (two separate processes) | MM-14 | Two gst-launch commands |
| 8-stream grid playback (4×2) | MM-15 | `--gst-debug=3` |
| 16-stream grid playback (4×4) | MM-16 | |
| PCM audio capture → WAV file | MM-17 | wpctl prerequisite |
| PCM audio playback from WAV | MM-18 | wpctl prerequisite |
| MP3 audio playback | MM-19 | wpctl prerequisite |
| AV record H.264 + MP3 → MP4 | MM-20 | wpctl + special encoder IO mode |
| AV playback H.264 + MP3 from MP4 | MM-21 | wpctl + dual-pad qtdemux |

---

## Canonical Building Blocks

### Camera Sources

**ISP camera (qticamsrc):**
```
qticamsrc [name=camsrc] [camera=<N>] ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1
```
- Default `camera=0`. Always set `format=NV12` in caps filter.

**USB camera (v4l2src):**
```
v4l2src device=<DEVICE_NODE> ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12
```
- USB sources output YUY2. `qtivtransform` is **required** for YUY2→NV12 conversion.

**RTSP source:**
```
rtspsrc location=<RTSP_URL> latency=200 ! rtph264depay ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

### File Decode Chains

**H.264:**
```
filesrc location=<FILE> ! qtdemux ! [queue !] h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```
- Add `queue` after `qtdemux` when demux is the sole demuxer with a single video pad (playback pipelines).

**H.265:**
```
filesrc location=<FILE> ! qtdemux ! [queue !] h265parse ! v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12
```

### Encode to File Chains

**H.264 MP4:**
```
queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! queue ! h264parse ! mp4mux ! queue ! filesink location=<OUTPUT_MP4>
```

**H.265 MP4:**
```
queue ! v4l2h265enc capture-io-mode=4 output-io-mode=4 ! queue ! h265parse ! mp4mux ! queue ! filesink location=<OUTPUT_MP4>
```

### Composition and Display

**Multi-stream compose:**
```
qtivcomposer name=comp \
  sink_0::position="<X0,Y0>" sink_0::dimensions="<W0,H0>" \
  sink_1::position="<X1,Y1>" sink_1::dimensions="<W1,H1>" ! \
queue ! waylandsink fullscreen=true
```
- Declare `qtivcomposer` at the **top** of the gst-launch command — it is referenced as a named element before source elements appear in declaration order.

  **General rule:** In gst-launch, any element that receives inputs from multiple sources via named sink pads must be declared before the elements that reference it. `gst-launch` parses the command linearly — a named element cannot be referenced before it is instantiated. This applies to `qtivcomposer`, `mp4mux name=muxer`, `qtimetamux name=meta_mux`, and any other hub element addressed by name downstream.

**Single stream display:**
```
... ! waylandsink fullscreen=true sync=true
```

### Audio Chains

**Capture (PCM/WAV):**
```
pulsesrc volume=<V> ! audioconvert ! wavenc ! filesink location=<OUTPUT_WAV>
```

**Capture for AV sync (AV record):**
```
pulsesrc do-timestamp=true provide-clock=false volume=<V> ! audio/x-raw,format=S16LE,channels=1,rate=48000 ! audioconvert ! queue ! lamemp3enc ! queue ! mpegaudioparse ! queue ! mp4mux name=muxer
```
- `do-timestamp=true` and `provide-clock=false` are **both required** for A/V sync. Missing either causes drift.

**Playback:**
```
... ! audioconvert ! pulsesink volume=<V>
```

---

## qticamsrc Multi-Pad Patterns

### Single pad (implicit)
```bash
qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! ...
```
No explicit pad request needed. First video pad used.

### Multi-pad video (declare stream roles)
```bash
qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video
camsrc. ! video/x-raw,format=NV12,width=<W1>,height=<H1>,framerate=30/1 ! ...   # first branch
camsrc. ! video/x-raw,format=NV12,width=<W2>,height=<H2>,framerate=30/1 ! ...   # second branch
```
- Declare `video_N::type=preview|video` roles on the element property line.
- Each `camsrc. !` accesses the next undrained pad in declaration order.
- Both `preview` and `video` pads output NV12; the `type` sets the ISP stream role (controls ISP pipeline path).

### Image pad (JPEG snapshots) — requires `gst-pipeline-app`

> **Image pads are on-demand, not continuous streams. Use `gst-pipeline-app`, not `gst-launch-1.0`.**

```bash
# Declare all pad types on the element property line:
qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video image_0::type=jpeg

# Access by sequential index across ALL pad types:
# 2 video pads declared before image_0 → image pad is camsrc.image_2
camsrc.video_0 ! ...
camsrc.video_1 ! ...
camsrc.image_2 ! image/jpeg,width=<W>,height=<H>,framerate=<FPS>/1 ! multifilesink location=<DIR>/frame_%04d.jpg sync=true async=false
```

- `image_0::type=jpeg` **must be declared** on the element property line — without it the image pad never activates
- Pad index = total pads declared before it (2 video pads → image pad is `image_2`, not `image_0`)
- After pipeline reaches PLAYING, trigger capture via interactive menu: `capture-image` → `still` → `<N>`
- ISP delivers **pre-encoded JPEG** directly — do NOT add `qtijpegenc`
- **Platform-dependent** — not all chipsets support image pads; silently produces no output if unsupported
- `gst-launch-1.0` will NOT produce snapshots — image pads require interactive signal trigger via `gst-pipeline-app`

### Two camera instances
```bash
qticamsrc name=camsrc_0 camera=0 video_0::type=video video_1::type=preview
qticamsrc name=camsrc_1 camera=1 video_0::type=video video_1::type=preview
```
- Each camera is an independent element instance with its own pad set.
- Use different `name=` values: `camsrc_0`, `camsrc_1`.

---

## qtivcomposer Layout Patterns

### Side-by-side (two streams)
```bash
qtivcomposer name=mixer \
  sink_0::position="<0,0>" sink_0::dimensions="<W,H>" \
  sink_1::position="<W,0>" sink_1::dimensions="<W,H>"
```
Streams placed horizontally adjacent. Output width = 2×W.

### Picture-in-Picture (PiP)
```bash
qtivcomposer name=mixer \
  sink_0::position="<0,0>" sink_0::dimensions="<1280,720>" \
  sink_1::position="<590,310>" sink_1::dimensions="<640,360>"
```
Main stream fills frame; overlay stream centered at `<590,310>` within 1280×720.

### N-stream grid
For a grid of `cols × rows` in a `total_w × total_h` output:
- `cell_w = total_w / cols`, `cell_h = total_h / rows`
- Apply the AR-fit formula from `generation-rules.md` Multi-Stream Layout Rule to compute `position` and `dimensions` for each stream — do not set `dimensions = <cell_w, cell_h>` directly, as that stretches streams when the cell AR differs from the source AR.
- Do not set a custom `background` value on `qtivcomposer` — leave it at its default.

Grid selection: pick the most-square factor pair `(cols, rows)` where `cols × rows = N` and `cols ≥ rows` (e.g., N=8 → 4×2, N=16 → 4×4). For prime N, fall back to `cols = ceil(sqrt(N))`, `rows = ceil(N / cols)`; omit sink pads for empty tail slots.

---

## Queue Placement Rules (Multimedia)

Keep multimedia chains lean. Queue is required only at these positions:

| Position | Rule |
|---|---|
| Immediately after any hardware decoder | `v4l2h264dec ... ! queue ! <next>` — always, regardless of what `<next>` is; see pipeline-utilities.md → Queue Usage |
| Before each encoder | `queue ! v4l2h264enc ...` and `queue ! v4l2h265enc ...` |
| After encoder, before h264parse/h265parse | `v4l2h264enc ... ! queue ! h264parse` (in camera encode pipelines) |
| Before and after `mp4mux` | `... ! mp4mux ! queue ! filesink` |
| On each `camsrc.` branch | One queue after each `camsrc. !` branch split |
| After composer, before `tee` | `mixer. ! queue ! tee name=t_split` |
| On each `tee` branch | `t_split. ! queue ! v4l2h264enc ...` |
| After `qtdemux` | In single-demux playback pipelines: `qtdemux ! queue ! h264parse` |
| In AV record audio chain | Between `lamemp3enc`, `mpegaudioparse`, and `mp4mux` |

**Do NOT** add queue between every element in simple linear chains — e.g. no queue needed between `h264parse` and `v4l2h264dec` (parser feeding decoder). The one exception in every chain is immediately *after* the decoder, per the table above: `h264parse ! v4l2h264dec ! queue ! waylandsink`, not `h264parse ! v4l2h264dec ! waylandsink`.

### tee vs fan-in elements

`tee` **splits** one stream into multiple destinations. It belongs after the element whose output needs to reach more than one place.

`qtivcomposer`, `mp4mux`, and `qtimetamux` are **fan-in** elements — they receive from multiple sources via their own on-request sink pads. Each source connects directly to a named sink pad (e.g., `mixer.sink_0`, `muxer.`). **Never insert a `tee` before a fan-in element's input.** The fan-in element already handles multiple inputs by design.

Correct mental model:
- One source → multiple destinations: use `tee`
- Multiple sources → one element: use the element's own sink pads directly

---

## C App vs gst-launch Decision

**Prefer C app when:**
- Multiple coordinated pipelines or runtime stream add/remove
- Signal handling and graceful EOS required
- Camera reconfiguration or runtime stream activation/deactivation
- Custom bus handlers for error/warning/state transitions
- Camera switch without stopping the pipeline
- Burst capture mode

**gst-launch is sufficient when:**
- Single static pipeline wiring
- Quick prototyping or one-shot runs

---

## Audio Pipeline Prerequisites

**All audio and AV templates (MM-17 through MM-21) require PulseAudio setup before running:**

```bash
# Check available audio sources and their node numbers
wpctl status

# Set the default audio source (replace <node_no.> with actual node number)
wpctl set-default <node_no.>
```

Failure to configure results in silent failure, pipeline error, or no-audio-device error at runtime.

---

## Anti-Patterns

| Anti-pattern | Correct pattern |
|---|---|
| `v4l2src` for ISP/MIPI camera | Use the documented ISP camera source from `plugin-catalog.md` |
| No `qtivtransform` after `v4l2src` (USB camera) | `v4l2src ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12` |
| `qtijpegenc` in chain after `camsrc.image_N` | Not needed — ISP delivers JPEG on `image_N` pad directly; remove `qtijpegenc` |
| Unquoted JPEG caps | Quote: `image/jpeg,width=<W>,height=<H>,framerate=<FPS>/1` |
| Using `gst-launch-1.0` for JPEG snapshot pipeline | Use `gst-pipeline-app` — image pads are on-demand and require interactive `capture-image` signal trigger; `gst-launch-1.0` will silently produce zero JPEG files |
| Missing `image_0::type=jpeg` declaration | Must declare `image_0::type=jpeg` on the element property line; without it the image pad never activates |
| Accessing image pad as `camsrc.image_0` when video pads are declared | Pad index is sequential across ALL pad types — with 2 video pads, image pad is `camsrc.image_2` |
| `sync=true` on display pad when running alongside encode pads | Use `waylandsink sync=false fullscreen=true enable-last-sample=false` for display in multi-encode pipelines (MM-6) |
| `pulsesrc` without `do-timestamp=true provide-clock=false` in AV record | Both properties required for A/V sync; missing either causes audio/video drift |
| Running audio pipeline without `wpctl set-default` | Run `wpctl set-default <node_no.>` first |
| `output-io-mode` on `v4l2h264enc` | Use `output-io-mode=5` (dmabuf-import) when upstream is a **camera source** (`qtiqmmfsrc`/`qticamsrc`) or when encoder feeds **AV record** (`mp4mux` + audio). Use `output-io-mode=4` when upstream is a file or RTSP source decoded through a v4l2 decoder. See `source-sink-patterns.md` File Output section. |
| Composer file-output encode path | `qtivcomposer ! video/x-raw,format=NV12 ! v4l2h264enc ...` |
| `mount-point` property on `qtirtspbin` | Correct property name is `mpoint` |
| `qtirtspbin address=127.0.0.1` for external network clients | Use `address=0.0.0.0`; default `127.0.0.1` is localhost only |
| Declaring `qtivcomposer` after source elements in gst-launch | Declare composer **first** — it is referenced by pad name before sources appear |
| Forgetting `-e` flag in recording pipelines | Always use `-e` to ensure EOS is sent on interrupt (prevents incomplete MP4 files) |
| Adding `audioconvert` to MP3 playback (MM-19) or AV playback audio branch (MM-21) | Do NOT add — `mpg123audiodec` outputs `S16LE` compatible with `pulsesink` directly. Only include `audioconvert` after `pulsesrc` (capture) or `wavparse` (WAV playback). |
| Adding `audioresample` to multimedia audio pipelines | Omit in MM-17 through MM-21 — `pulsesrc`/`pulsesink` negotiate compatible rates. Only include when bridging mismatched sample rates (e.g., AI audio pipelines). |
| Using audio AI templates (YAMNet/qtimlaconverter) for multimedia audio | These are completely different topologies. Audio AI uses `qtimlaconverter`, `audiobuffersplit`, inference → load `ai-pipeline-patterns.md`. Multimedia audio (MM-17–MM-21) uses `pulsesrc`/`pulsesink`/`lamemp3enc`/`wavenc` — no inference elements. |
| `output-io-mode` integer in C app code | Use string nicks with `gst_element_set_enum_property()`: `"dmabuf"` for 4, `"dmabuf-import"` for 5. Never set these with raw integer `g_object_set`. |

---

## Verified Multimedia Pipeline Templates

# Multimedia Known Good Pipelines

All commands are canonical and ready to run. Replace `<PLACEHOLDER>` tokens before running.

`qtimlpostprocess settings=...` is NOT applicable to multimedia pipelines — these templates have no AI stages.

---

## MM-1: Single Stream Camera Display (ISP / USB / RTSP)

Three source variants — choose the one matching the input source type.

### MM-1a: ISP Camera

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc ! \
  video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  waylandsink fullscreen=true sync=true
```

**Placeholders:** None — uses camera=0 by default. Add `camera=<N>` if not camera 0.

**Notes:**
- `fullscreen=true sync=true` are the skill defaults for display output, including live camera sources — this command includes `sync=true` explicitly.

---

### MM-1b: USB Camera

```bash
gst-launch-1.0 -e --gst-debug=2 \
  v4l2src device=<DEVICE_NODE> ! \
  video/x-raw,format=YUY2 ! \
  qtivtransform ! video/x-raw,format=NV12 ! \
  waylandsink fullscreen=true sync=true
```

**Placeholders:** `<DEVICE_NODE>` — e.g., `/dev/video0`

**Notes:**
- USB cameras output YUY2. `qtivtransform` is **required** for YUY2→NV12 conversion.
- Keep `qtivtransform`; downstream elements require NV12.

---

### MM-1c: RTSP Camera

```bash
gst-launch-1.0 -e --gst-debug=2 \
  rtspsrc location=<RTSP_URL> latency=200 ! \
  rtph264depay ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
  waylandsink fullscreen=true sync=true
```

**Placeholders:** `<RTSP_URL>` — e.g., `rtsp://192.168.1.100:8554/stream`

**Notes:**
- `latency=200` provides 200ms jitter buffer for network RTSP streams.
- For H.265 RTSP: replace `rtph264depay ! h264parse ! v4l2h264dec` with `rtph265depay ! h265parse ! v4l2h265dec`.

---

## MM-2: Three Stream Display (Single qticamsrc + qtivcomposer)

```bash
gst-launch-1.0 -e -v \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"   sink_0::dimensions="<480,270>" \
    sink_1::position="<480,0>" sink_1::dimensions="<480,270>" \
    sink_2::position="<960,0>" sink_2::dimensions="<480,270>" \
  mixer. ! queue ! waylandsink fullscreen=true \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! qtivtransform ! video/x-raw,width=480,height=270 \
           ! mixer.sink_0 \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! qtivtransform ! video/x-raw,width=480,height=270 \
           ! mixer.sink_1 \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! qtivtransform ! video/x-raw,width=480,height=270 \
           ! mixer.sink_2
```

**Placeholders:** None — uses 1920×1080 source, 480×270 display cells.

**Notes:**
- `qtivcomposer` declared at top — referenced by name before camera element appears.
- `video_0::type=preview video_1::type=video` sets ISP stream roles.
- Three `camsrc. !` branches tap the same physical camera; each branch gets its own pad in order.

---

## MM-3: One Stream 1080p AVC Video Record

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video ! \
  video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
  queue ! h264parse ! mp4mux ! queue ! \
  filesink location=<OUTPUT_MP4>
```

**Placeholders:** `<OUTPUT_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/output.mp4`

**Notes:**
- Queue before `v4l2h264enc` (required for DMA writability — same as AI pipelines).
- Queue between encoder and mux; queue between mux and filesink.
- No queue between `h264parse` and `mp4mux` in this canonical form.
- `-e` flag ensures EOS is sent on Ctrl+C — prevents incomplete/corrupt MP4 file.

---

## MM-4: One Stream RTSP from Live Source (gst-rtsp-server + UDP)

**This is a two-step process: start the RTSP server first, then run the pipeline.**

```bash
# Step 1: Start RTSP server (run in background or separate terminal)
gst-rtsp-server -p 8900 -m /live \
  "( udpsrc name=pay0 port=8554 caps=\"application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96\" )" &

# Step 2: Run pipeline
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
  h264parse config-interval=-1 ! rtph264pay pt=96 ! \
  udpsink host=127.0.0.1 port=8554
```

**Placeholders:** None for default localhost RTSP. For external access, change `host=127.0.0.1` to target host or `0.0.0.0`.

**Notes:**
- `gst-rtsp-server` binds to port 8900; RTSP URL: `rtsp://127.0.0.1:8900/live`
- `h264parse config-interval=-1` injects SPS/PPS with every IDR frame — required for RTSP clients to reconnect.
- `rtph264pay pt=96` — payload type must match server caps (encoding-name=H264, payload=96).
- `udpsink host=127.0.0.1 port=8554` — pipeline sends RTP to localhost where server listens.
- `gst-rtsp-server` is a GStreamer system utility; install on Ubuntu with `apt install gstreamer1.0-rtsp`.

### MM-4b: RTSP via qtirtspbin (Single Step — No Separate Server Process)

Use `qtirtspbin` to embed the RTSP server directly in the pipeline — simpler, no separate `gst-rtsp-server` process needed:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
  h264parse config-interval=1 ! \
  qtirtspbin address=0.0.0.0 port=8900 mpoint=/live
```

Connect with: `rtsp://<DEVICE_IP>:8900/live`

**Notes:**
- `qtirtspbin` properties: `address=0.0.0.0` (all interfaces), `port` (string, default `"8900"`), `mpoint` (NOT `mount-point`; default `"/live"`).
- `h264parse config-interval=1` — insert SPS/PPS every second for client reconnection.
- `qtirtspbin` accepts `video/x-h264`, `video/x-h265`, `audio/mpeg`, or `text/x-raw` on `sink_%u` on-request pads.

---

## MM-5: Two Streams — 4K AVC and 480p AVC from Live Source

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! video/x-raw,format=NV12,width=3840,height=2160,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=<OUTPUT_4K_MP4> \
  camsrc. ! video/x-raw,format=NV12,width=640,height=480,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=<OUTPUT_480P_MP4>
```

**Placeholders:**
- `<OUTPUT_4K_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/video_4k.mp4`
- `<OUTPUT_480P_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/video_480p.mp4`

**Notes:**
- Two completely independent encode→mux→file chains; one `mp4mux` per output.
- Resolution is selected via caps filter on each branch — not a camera property.
- Camera must support simultaneous 4K and 480p output pads (sensor-dependent).

---

## MM-6: Three Streams — 1080p AVC + 1080p HEVC + 1080p Display

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=<OUTPUT_AVC_MP4> \
  camsrc. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
      queue ! v4l2h265enc capture-io-mode=4 output-io-mode=4 ! \
      queue ! h265parse ! mp4mux ! queue ! filesink location=<OUTPUT_HEVC_MP4> \
  camsrc. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
      waylandsink sync=false fullscreen=true enable-last-sample=false
```

**Placeholders:**
- `<OUTPUT_AVC_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/video_avc.mp4`
- `<OUTPUT_HEVC_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/video_hevc.mp4`

**Notes:**
- Display pad uses `sync=false fullscreen=true enable-last-sample=false` — `sync=false` is **required** when display runs alongside two encode sinks to avoid clock synchronization stalls.
- Three independent `camsrc. !` branches from single camera.

---

## MM-7: JPEG Snapshot + 1080p AVC + 1080p Display

> **⚠️ QLI (Qualcomm Linux) builds only.** `qticamsrc` and image pad support require the QLI proprietary camera stack (`qcom-multimedia-proprietary-image`). This pipeline does not apply to Ubuntu on-device builds which use `qtiqmmfsrc` instead.
>
> **⚠️ Image capture requires `gst-pipeline-app`, NOT `gst-launch-1.0`.**
> Image pads on `qticamsrc` are on-demand, not continuous streams. Capture is triggered interactively via the `capture-image` signal. Platform support for image pads is chipset-dependent — if cam-server crashes on PREROLLING, the device firmware does not support image pads.

```bash
gst-pipeline-app -e \
  qticamsrc name=camsrc camera=0 \
  video_0::type=preview \
  video_1::type=video \
  image_0::type=jpeg \
  \
  camsrc.video_0 ! queue ! \
  video/x-raw,format=NV12_Q08C,width=1280,height=720,framerate=30/1 ! \
  waylandsink sync=false fullscreen=true \
  \
  camsrc.video_1 ! queue ! \
  video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  v4l2h264enc ! h264parse ! mp4mux ! \
  filesink location=<OUTPUT_AVC_MP4> async=false \
  \
  camsrc.image_2 ! \
  image/jpeg,width=1920,height=1080,framerate=5/1 ! \
  multifilesink location=<SNAPSHOT_DIR>/frame_%04d.jpg sync=true async=false
```

**After the pipeline starts, trigger image capture via the interactive menu:**
```
PLAYING          → type: 3
Plugin Mode      → type: p
camsrc           → type the element index shown in the list (e.g. 6)
capture-image    → type the signal index shown (e.g. 37)
still            → type: 0
3                → number of snapshots
```
**Note: the menu uses numeric indices, not text names.** After selecting the element (step 3), the app shows a numbered list of properties and signals — find `capture-image` in the signals section and type its number. The index varies by device and SDK version.

**Placeholders:**
- `<OUTPUT_AVC_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/output/720p_video.mp4`
- `<SNAPSHOT_DIR>` — e.g., `$HOME/Downloads/qimsdk_samples/media/output`

**Notes:**
- `image_0::type=jpeg` **must be declared** on the element property line — without it, the image pad never activates and produces zero frames.
- **Pad indexing is sequential across ALL pad types** — with 2 video pads (`video_0`, `video_1`) declared, the image pad is accessed as `camsrc.image_2` (not `camsrc.image_0`). Index = total pads declared before it.
- `capture-image` signal parameters: `still` = per-frame metadata, `3` = number of snapshots.
- `framerate=5/1` on image caps — captures at 5 fps when triggered; use lower values to avoid large burst output.
- `multifilesink location=.../frame_%04d.jpg` — `%04d` writes `frame_0000.jpg`, `frame_0001.jpg`, etc.
- **Image pad support is chipset-dependent** — not all platforms expose image pads. If the pipeline starts successfully but no JPEG files appear after triggering capture, the hardware does not support image pads.
- Do NOT use `gst-launch-1.0` for image capture — image pads are on-demand and will silently produce no output in `gst-launch-1.0` without the interactive signal trigger.
- Do NOT add `qtijpegenc` — the ISP delivers JPEG directly on image pads.

---

## MM-8: Dual Camera Multi-Stream (Two qticamsrc Instances)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc_0 camera=0 video_0::type=video video_1::type=preview \
  camsrc_0. ! video/x-raw,format=NV12,width=3840,height=2160,framerate=30/1 \
      ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse ! mp4mux ! filesink location=<MAIN_4K_MP4> \
  camsrc_0. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
      ! queue ! waylandsink sync=false \
  qticamsrc name=camsrc_1 camera=1 video_0::type=video video_1::type=preview \
  camsrc_1. ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
      ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse ! mp4mux ! filesink location=<SECONDARY_720P_MP4> \
  camsrc_1. ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
      ! queue ! filesink location=<SECONDARY_720P_YUV>
```

**Placeholders:**
- `<MAIN_4K_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/main_4k.mp4`
- `<SECONDARY_720P_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/secondary_720p.mp4`
- `<SECONDARY_720P_YUV>` — e.g., `$HOME/Downloads/qimsdk_samples/media/secondary_720p.yuv` (raw NV12 bytes)

**Notes:**
- Two independent `qticamsrc` instances: `camera=0` and `camera=1`.
- Display pad uses `sync=false` — this branch runs alongside a parallel 4K encode branch off the same `camsrc_0` source, so `sync=false` applies under the multi-sink clock-stall exception (not because the source is a camera).
- Raw YUV filesink writes raw NV12 bytes — no container, no encode.
- `video_0::type=video` is the main encode pad; `video_1::type=preview` is the display/monitor pad.

---

## MM-9: Side-by-Side Compose → MP4 + RTSP UDP

```bash
gst-launch-1.0 -e -v \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"    sink_0::dimensions="<1280,720>" \
    sink_1::position="<1280,0>" sink_1::dimensions="<1280,720>" \
  mixer. ! queue ! tee name=t_split \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse config-interval=-1 ! rtph264pay pt=96 \
      ! udpsink host=127.0.0.1 port=8554 \
  qticamsrc name=camsrc_0 camera=0 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_0 \
  qticamsrc name=camsrc_1 camera=1 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_1
```

**Placeholders:** `<OUTPUT_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/sidebyside.mp4`

**Notes:**
- Composer declared at top; two 1280×720 streams → 2560×720 composed output.
- `mixer. ! queue ! tee name=t_split` — queue between composer and tee is **required**.
- Two **separate** `v4l2h264enc` instances — one per tee branch.
- MP4 branch: standard `h264parse ! mp4mux ! filesink`.
- RTSP branch: `h264parse config-interval=-1 ! rtph264pay pt=96 ! udpsink` — use with `gst-rtsp-server` (see MM-4).
- For RTSP available to external clients: change `udpsink host=127.0.0.1` to `host=0.0.0.0`.

---

## MM-10: Picture-in-Picture (PiP) Compose → MP4 + RTSP UDP

```bash
gst-launch-1.0 -e -v \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"     sink_0::dimensions="<1280,720>" \
    sink_1::position="<590,310>" sink_1::dimensions="<640,360>" \
  mixer. ! queue ! tee name=t_split \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 \
      ! queue ! h264parse config-interval=-1 ! rtph264pay pt=96 \
      ! udpsink host=127.0.0.1 port=8554 \
  qticamsrc name=camsrc_0 camera=0 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_0 \
  qticamsrc name=camsrc_1 camera=1 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_1
```

**Placeholders:** `<OUTPUT_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/pip.mp4`

**Notes:**
- Identical topology to MM-9 except the composition layout.
- `sink_0` fills the full 1280×720 frame (background/main stream).
- `sink_1::position="<590,310>"` — PiP overlay at center of 1280×720 frame (center of 640×360 at `590 + 320 = 910`, `310 + 180 = 490`).
- All queue/tee/encode rules identical to MM-9.

---

## MM-11: Transform — Rotate 180°

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080 ! \
  qtivtransform rotate=180 ! \
  waylandsink fullscreen=true sync=true
```

**Placeholders:** None.

**Notes:**
- `rotate=180` is the enum nick — **not an integer**; valid nicks: `none`, `90CW`, `90CCW`, `180`.
- Do not use `rotate=180` as an integer (will fail or produce wrong rotation).

---

## MM-12: Transform — Downscale 4K to 1080p + Horizontal Flip

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc ! video/x-raw,format=NV12,width=3840,height=2160 ! \
  qtivtransform flip-horizontal=true ! \
  video/x-raw,width=1920,height=1080 ! \
  waylandsink fullscreen=true sync=true
```

**Placeholders:** None.

**Notes:**
- Downstream caps filter `video/x-raw,width=1920,height=1080` negotiates output resolution.
- `qtivtransform` handles flip + downscale in **one pass** — no separate `videoscale` element needed.
- `flip-horizontal=true` mirrors the frame horizontally.
- For flip-only (no scale): remove the downstream caps filter.
- For rotate + flip together: `qtivtransform rotate=90CW flip-horizontal=true`.

---

## MM-13: Single Stream H.264 File Playback

```bash
gst-launch-1.0 -e -v \
  filesrc location=<INPUT_FILE> ! \
  qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink fullscreen=true sync=true
```

**Placeholders:** `<INPUT_FILE>` — e.g., `$HOME/Downloads/qimsdk_samples/media/video.mp4`

**Notes:**
- Queue after `qtdemux` (single-demux playback).
- Queue after decoder before `waylandsink`.
- For H.265: replace `h264parse ! v4l2h264dec` with `h265parse ! v4l2h265dec`.
- For AV file playback (video + audio), use MM-21 instead.

---

## MM-14: Dual Parallel Video Playback (Two Separate Processes)

**This template launches two independent gst-launch processes.**

```bash
# Instance 1 — runs in background
gst-launch-1.0 -e -v \
  filesrc location=<INPUT_FILE_1> ! \
  qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink &

# Instance 2 — runs in foreground
gst-launch-1.0 -e -v \
  filesrc location=<INPUT_FILE_2> ! \
  qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink
```

**Placeholders:**
- `<INPUT_FILE_1>`, `<INPUT_FILE_2>` — may be the same file

**Notes:**
- This is **NOT a single pipeline** — two independent processes running concurrently.
- First process runs with `&` (background); second runs in foreground.
- **Artifact output:** `pipeline.sh` contains both commands separated by a blank line; first command ends with ` &`. Include `wait` at the end to hold until both exit: `wait`.
- For simultaneous display of both streams in one window, use MM-15 (qtivcomposer) instead.

---

## MM-15: 8-Stream Grid Playback (4×2)

```bash
gst-launch-1.0 -e --gst-debug=3 \
  qtivcomposer name=comp \
    sink_0::position="<0, 135>"    sink_0::dimensions="<480, 270>" \
    sink_1::position="<480, 135>"  sink_1::dimensions="<480, 270>" \
    sink_2::position="<960, 135>"  sink_2::dimensions="<480, 270>" \
    sink_3::position="<1440, 135>" sink_3::dimensions="<480, 270>" \
    sink_4::position="<0, 675>"    sink_4::dimensions="<480, 270>" \
    sink_5::position="<480, 675>"  sink_5::dimensions="<480, 270>" \
    sink_6::position="<960, 675>"  sink_6::dimensions="<480, 270>" \
    sink_7::position="<1440, 675>" sink_7::dimensions="<480, 270>" ! \
  queue ! waylandsink fullscreen=true \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_0 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_1 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_3 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_4 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_5 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_6 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_7
```

**Placeholders:** `<INPUT_FILE>` — all 8 chains may use same or different files.

**Notes:**
- `--gst-debug=3` (not `--gst-debug=2`) — level 3 provides element-level state change messages needed to debug multi-stream synchronization issues.
- Composer declared at top; 8 sink pads form a 4×2 grid.
- AR-fit applied: cell is 480×540 (8:9), source is 1920×1080 (16:9). scale=0.25 → fit=480×270, pad_y=135. Each stream sits centered vertically in its cell with letterbox bars above and below, rendered in `qtivcomposer`'s default background color.
- Output canvas: 1920×1080 (4 cols × 480, 2 rows × 540).
- 8 independent decode chains, all routing to `comp.sink_N`.
- For different input files per stream: replace individual `<INPUT_FILE>` placeholders with specific paths.

**File-output variant:** replace `queue ! waylandsink fullscreen=true` with an explicit NV12 capsfilter carrying the full canvas size, then encode:
```text
video/x-raw,format=NV12,width=1920,height=1080 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```
The explicit `width=1920,height=1080` is required here, unlike the composer-to-encoder file-output pattern used elsewhere in this skill (where the composer's negotiated output is already the intended canvas). For a grid built from sink pad bounding boxes that don't individually sum to an aligned canvas — as here, where the 8 cells' combined bounding box can resolve to an odd height (e.g. 945, not 1080) — `qtivcomposer` negotiates its output frame from that bounding box, not from the canvas implied by the grid math. `waylandsink` tolerates an odd-height frame; a hardware encoder does not (`msm_vidc: resolution is not even` at `STREAMON`). Confirmed on-device: without the explicit capsfilter, encode fails; with it, the composer is forced to output the full even canvas and encode succeeds.

---

## MM-16: 16-Stream Grid Playback (4×4)

```bash
gst-launch-1.0 -e --gst-debug=3 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>"     sink_0::dimensions="<480, 270>" \
    sink_1::position="<480, 0>"   sink_1::dimensions="<480, 270>" \
    sink_2::position="<960, 0>"   sink_2::dimensions="<480, 270>" \
    sink_3::position="<1440, 0>"  sink_3::dimensions="<480, 270>" \
    sink_4::position="<0, 270>"   sink_4::dimensions="<480, 270>" \
    sink_5::position="<480, 270>" sink_5::dimensions="<480, 270>" \
    sink_6::position="<960, 270>" sink_6::dimensions="<480, 270>" \
    sink_7::position="<1440, 270>" sink_7::dimensions="<480, 270>" \
    sink_8::position="<0, 540>"    sink_8::dimensions="<480, 270>" \
    sink_9::position="<480, 540>"  sink_9::dimensions="<480, 270>" \
    sink_10::position="<960, 540>" sink_10::dimensions="<480, 270>" \
    sink_11::position="<1440, 540>" sink_11::dimensions="<480, 270>" \
    sink_12::position="<0, 810>"    sink_12::dimensions="<480, 270>" \
    sink_13::position="<480, 810>"  sink_13::dimensions="<480, 270>" \
    sink_14::position="<960, 810>"  sink_14::dimensions="<480, 270>" \
    sink_15::position="<1440, 810>" sink_15::dimensions="<480, 270>" ! \
  queue ! waylandsink fullscreen=true \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_0 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_1 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_3 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_4 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_5 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_6 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_7 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_8 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_9 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_10 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_11 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_12 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_13 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_14 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_15
```

**Placeholders:** `<INPUT_FILE>` — all 16 chains may use same or different files.

**Notes:**
- 16 independent decode chains.
- `--gst-debug=3` per MM-15 rationale.

---

## MM-17: PCM Audio Capture → WAV File

**Prerequisite:** `wpctl set-default <node_no.>` — configure audio input before running.

```bash
gst-launch-1.0 -v \
  pulsesrc volume=10 ! \
  audioconvert ! \
  wavenc ! \
  filesink location=<OUTPUT_WAV>
```

**Placeholders:** `<OUTPUT_WAV>` — e.g., `$HOME/Downloads/qimsdk_samples/media/capture.wav`

**Notes:**
- `pulsesrc volume=10` — volume range 0–10 in these pipelines.
- `audioconvert` normalizes PCM format before WAV encoding.
- `wavenc` produces standard WAV container.
- No `audioresample` — `pulsesrc` and `wavenc` negotiate a compatible sample rate directly; `audioresample` is only needed when bridging mismatched rates (e.g., AI audio pipelines with fixed tensor input rates).
- Run `wpctl status` to identify the audio source node number.

---

## MM-18: PCM Audio Playback from WAV File

**Prerequisite:** `wpctl set-default <node_no.>` — configure audio output before running.

```bash
gst-launch-1.0 -e \
  filesrc location=<INPUT_WAV> ! \
  wavparse ! \
  audioconvert ! \
  pulsesink volume=10
```

**Placeholders:** `<INPUT_WAV>` — e.g., `$HOME/Downloads/qimsdk_samples/media/capture.wav`

**Notes:**
- `wavparse` demultiplexes WAV container and outputs raw PCM.
- `audioconvert` normalizes format for `pulsesink`.

---

## MM-19: MP3 Audio Playback (Software Decode)

**Prerequisite:** `wpctl set-default <node_no.>` — configure audio output before running.

```bash
gst-launch-1.0 -e \
  filesrc location=<INPUT_MP3> ! \
  mpegaudioparse ! \
  mpg123audiodec ! \
  pulsesink volume=10
```

**Placeholders:** `<INPUT_MP3>` — e.g., `$HOME/Downloads/qimsdk_samples/media/audio.mp3`

**Notes:**
- `mpegaudioparse` frames the MPEG audio bitstream.
- `mpg123audiodec` decodes MP3 to PCM.
- No `audioconvert` — `mpg123audiodec` outputs `audio/x-raw,format=S16LE` which is directly compatible with `pulsesink`; unlike `wavparse` which may output a different format requiring conversion. Do NOT add `audioconvert` here.

---

## MM-20: AV Record — 1080p H.264 + MP3 Audio → MP4

**Prerequisite:** `wpctl set-default <node_no.>` — configure audio input before running.

```bash
gst-launch-1.0 -e \
pulsesrc do-timestamp=true provide-clock=false volume=10 ! \
audio/x-raw,format=S16LE,channels=1,rate=48000 ! \
audioconvert ! queue ! \
lamemp3enc ! queue ! \
mpegaudioparse ! queue ! \
mp4mux name=muxer ! queue ! \
filesink location=<OUTPUT_AV_MP4> \
qticamsrc ! \
video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1,interlace-mode=progressive,colorimetry=bt601 ! \
queue ! \
v4l2h264enc capture-io-mode=4 output-io-mode=5 extra-controls="controls,video_bitrate=1000000,video_gop_size=29;" ! \
queue ! \
h264parse ! \
muxer.
```

**Placeholders:** `<OUTPUT_AV_MP4>` — e.g., `$HOME/Downloads/qimsdk_samples/media/1080p_AVC_MP3.mp4`

**Notes:**
- `pulsesrc do-timestamp=true provide-clock=false` — **both properties required** for A/V sync. Missing either causes audio/video drift.
- Audio raw caps `audio/x-raw,format=S16LE,channels=1,rate=48000` before `audioconvert`.
- `lamemp3enc ! mpegaudioparse` — `mpegaudioparse` is required before `mp4mux` (muxer needs framed MPEG audio).
- `mp4mux name=muxer` — receives audio on default sink; video on explicit `muxer.` pad.
- `v4l2h264enc output-io-mode=5` — **`output-io-mode=5` (`dmabuf-import`) is required for AV record, NOT the standard `output-io-mode=4`**. This is the only pipeline in MM-1 through MM-21 that uses mode 5. All other encode pipelines use `output-io-mode=4`.
- `extra-controls="controls,video_bitrate=1000000,video_gop_size=29;"` — V4L2 control format: starts with `controls,`, semicolon-terminated. `video_bitrate` in bits/s (1000000 = 1 Mbps). To customize bitrate: `extra-controls="controls,video_bitrate=5000000,video_gop_size=60;"` for 5 Mbps / 60-frame GOP.
- `interlace-mode=progressive,colorimetry=bt601` in camera caps — required for AV recording to set correct color metadata.
- Queues in audio chain between `lamemp3enc`, `mpegaudioparse`, and `mp4mux` — all required.
- wpctl prerequisite required before running.

---

## MM-21: AV Playback — H.264 + MP3 from MP4

**Prerequisite:** `wpctl set-default <node_no.>` — configure audio output before running.

```bash
gst-launch-1.0 -e \
  filesrc location=<INPUT_AV_MP4> ! \
  qtdemux name=demux \
  demux. ! queue ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! \
    waylandsink fullscreen=true \
  demux. ! queue ! mpegaudioparse ! mpg123audiodec ! pulsesink volume=10
```

**Placeholders:** `<INPUT_AV_MP4>` — MP4 file with H.264 video + MP3 audio tracks.

**Notes:**
- `qtdemux name=demux` exposes two dynamic pads (video + audio) via `pad-added` signal.
- **⚠️ Pad order is NOT guaranteed in gst-launch** — `demux. !` branches are matched to pads in the order GStreamer emits them, which depends on the MP4 track order. For reliable routing in gst-launch, use caps filters: append `! video/x-raw` or `! audio/x-raw` after the appropriate parse element to enforce type matching. For robust routing in C apps, use `pad-added` callback with caps inspection (see `c-app-development.md` — qtdemux dual-track section) — this is the recommended approach for production code.
- Video: `h264parse → v4l2h264dec → queue → waylandsink fullscreen=true sync=true` (skill default; the queue after the decoder is required regardless of what follows it — see `plugin-catalog.md`'s `queue` entry).
- Audio: `mpegaudioparse → mpg123audiodec → pulsesink` — no `audioconvert`; `mpg123audiodec` outputs `audio/x-raw,format=S16LE` compatible with `pulsesink` directly.
- For H.265 video: replace `h264parse ! v4l2h264dec` with `h265parse ! v4l2h265dec`.
- wpctl prerequisite required before running.
