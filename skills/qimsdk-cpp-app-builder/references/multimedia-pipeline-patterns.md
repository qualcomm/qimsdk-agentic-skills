# Multimedia Pipelines

## Use This Reference For

- Multimedia-only pipeline requests: camera display, camera capture/encode, multi-camera composition, video playback, transform/flip/scale/rotate, audio capture/playback, AV record/playback
- No AI inference stages in the pipeline
- For **mixed multimedia + AI** requests (e.g., camera + object detection), load this file AND the AI pipeline references

**C++ SDK note:** All patterns in this file describe element topology using `gst-launch-1.0` notation. When generating C++ SDK apps, translate the same element/property topology into `pipeline.add("factory", "name", "prop", value, ...)` and `.link(...)` chains using the patterns in `references/pipeline-construction.md`. Plugin names, properties, and caps constraints are identical across output modes.

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
rtspsrc location=<RTSP_URL> latency=200 ! rtph264depay ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! video/x-raw,format=NV12
```

### File Decode Chains

**H.264:**
```
filesrc location=<FILE> ! qtdemux ! [queue !] h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! video/x-raw,format=NV12
```
- Add `queue` after `qtdemux` when demux is the sole demuxer with a single video pad (playback pipelines). Always keep the queue after hardware decode.

**H.265:**
```
filesrc location=<FILE> ! qtdemux ! [queue !] h265parse ! v4l2h265dec capture-io-mode=4 output-io-mode=4 ! queue ! video/x-raw,format=NV12
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
- `sink_N::position="<col*cell_w, row*cell_h>"`, `sink_N::dimensions="<cell_w, cell_h>"`

Example 4×2 grid (1920×1080 total, 8 streams):
- Cell: 480×540
- Row 0: sink_0–3 at y=0, sink_4–7 at y=540

---

## Queue Placement Rules (Multimedia)

Keep multimedia chains lean. Queue is required only at these positions:

| Position | Rule |
|---|---|
| Before each encoder | `queue ! v4l2h264enc ...` and `queue ! v4l2h265enc ...` |
| Immediately after each hardware decoder | `v4l2h264dec ... ! queue ! <next>` and `v4l2h265dec ... ! queue ! <next>` |
| After encoder, before h264parse/h265parse | `v4l2h264enc ... ! queue ! h264parse` (in camera encode pipelines) |
| Before and after `mp4mux` | `... ! mp4mux ! queue ! filesink` |
| On each `camsrc.` branch | One queue after each `camsrc. !` branch split |
| After composer, before `tee` | `mixer. ! queue ! tee name=t_split` |
| On each `tee` branch | `t_split. ! queue ! v4l2h264enc ...` |
| After `qtdemux` | In single-demux playback pipelines: `qtdemux ! queue ! h264parse` |
| In AV record audio chain | Between `lamemp3enc`, `mpegaudioparse`, and `mp4mux` |

**Do NOT** add queue between every element in simple linear chains. The queue immediately after hardware decode is the required exception and is never considered redundant.

### tee vs fan-in elements

`tee` **splits** one stream into multiple destinations. It belongs after the element whose output needs to reach more than one place.

`qtivcomposer`, `mp4mux`, and `qtimetamux` are **fan-in** elements — they receive from multiple sources via their own on-request sink pads. Each source connects directly to a named sink pad (e.g., `mixer.sink_0`, `muxer.`). **Never insert a `tee` before a fan-in element's input.** The fan-in element already handles multiple inputs by design.

Correct mental model:
- One source → multiple destinations: use `tee`
- Multiple sources → one element: use the element's own sink pads directly

---

## Audio Pipeline Prerequisites

**All audio and AV templates require PulseAudio setup before running:**

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
| `sync=true` on display pad when running alongside encode pads | Use `waylandsink sync=false fullscreen=true enable-last-sample=false` for display in multi-encode pipelines |
| `pulsesrc` without `do-timestamp=true provide-clock=false` in AV record | Both properties required for A/V sync; missing either causes audio/video drift |
| Running audio pipeline without `wpctl set-default` | Run `wpctl set-default <node_no.>` first |
| 16-stream grid playback on non-IQ-9075 hardware | 16 simultaneous hardware decode is supported only on IQ-9075 |
| `output-io-mode=4` on `v4l2h264enc` in AV record or camera-direct encode pipeline | Use `output-io-mode=5` (`dmabuf-import`) for AV record and camera-direct encode; decoded file/RTSP encode uses `output-io-mode=4` |
| Camera caps without `interlace-mode=progressive,colorimetry=bt601` in AV record | Required for correct color metadata in the encoded MP4 — always include in the camera caps filter for AV record: `video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1,interlace-mode=progressive,colorimetry=bt601` |
| `qtivtransform` between `qtivcomposer` output and encoder | Remove it — `qtivcomposer` outputs NV12 natively; use caps filter directly: `! video/x-raw,format=NV12 ! v4l2h264enc ...` |
| `mount-point` property on `qtirtspbin` | Correct property name is `mpoint` |
| `qtirtspbin address=127.0.0.1` for external network clients | Use `address=0.0.0.0`; default `127.0.0.1` is localhost only |
| Declaring `qtivcomposer` after source elements in gst-launch | Declare composer **first** — it is referenced by pad name before sources appear |
| Forgetting `-e` flag in recording pipelines | Always use `-e` to ensure EOS is sent on interrupt (prevents incomplete MP4 files) |
| Adding `audioconvert` to MP3 playback or AV playback audio branch | Do NOT add — `mpg123audiodec` outputs `S16LE` compatible with `pulsesink` directly. Only include `audioconvert` after `pulsesrc` (capture) or `wavparse` (WAV playback). |
| Adding `audioresample` to multimedia audio pipelines | Omit — `pulsesrc`/`pulsesink` negotiate compatible rates. Only include when bridging mismatched sample rates (e.g., AI audio pipelines). |
| Using audio AI templates (YAMNet/qtimlaconverter) for multimedia audio | These are completely different topologies. Audio AI uses `qtimlaconverter`, `audiobuffersplit`, inference → load appropriate AI references. Multimedia audio uses `pulsesrc`/`pulsesink`/`lamemp3enc`/`wavenc` — no inference elements. |
