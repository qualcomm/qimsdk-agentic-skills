# C App Development

## Purpose

Canonical GStreamer C app generation, API usage, build, callbacks, and C-specific validation guidance.

## Load When

Load for any generated or reviewed GStreamer C sample app.

## This File Owns

- C app structure and lifecycle
- Dynamic pad callbacks, bus handling, and cleanup
- QIM SDK C API property-setting patterns
- CMake/build instructions and optional JSON config pattern

## This File Does Not Own

- Artifact file count and README contract; use artifact-contract.md
- Plugin property catalog; use plugin-catalog.md
- Non-C gst-launch syntax; use pattern references

---


---

## C Sample App Generation

# C Sample App Generation

## Use This Reference For

- Generating complete, self-contained GStreamer C sample apps for any QIMSDK AI pipeline
- Choosing elements and patterns that match the pipeline topology
- Reasoning through unfamiliar pipelines systematically

Load this file with the relevant AI or multimedia pattern reference, plus
`source-sink-patterns.md`, `pipeline-utilities.md`, `plugin-catalog.md`, and
`artifact-contract.md` when generating a complete C app artifact.

---

## Standard Steps to Generate Any C App

Follow these steps in order for every C app request:

### Step 1 — Identify the Pipeline Shape

From the user prompt, answer these questions — they determine everything downstream:
- How many inputs? (one file, one camera, multiple files)
- How many AI stages? (single model, two-stage daisy-chain, parallel models)
- What is the output? (display, file, both)
- Any special output type? Segmentation (`deeplab-argmax`, `yolov8-seg`) and depth (`midas-v2`) output rendered frames with alpha blending — no capsfilter after `qtimlpostprocess`. Super resolution (`srnet`) outputs RGB frames. All other modules (detection, classification, pose, face) also output rendered frames with no capsfilter needed. See Step 4 for per-module details.

Every AI C app pipeline follows this shape — the overlay topology chosen (A or B) determines what goes between the AI branch and the final sink:
```
[source] → [decode] → [tee]
tee → [passthrough branch] → [qtivcomposer (Topology B) OR qtimetamux (Topology A)]
tee → [AI branch: qtimlvconverter → infer → qtimlpostprocess] → [qtivcomposer or qtimetamux]
[qtivcomposer or qtimetamux+qtivoverlay] → [output sink]
```
More inputs, more AI stages = more tees. See Step 4 for topology choice.

### Step 2 — Choose Source Elements

Use `source-sink-patterns.md`. Key decisions:
- `filesrc`/`rtspsrc` have dynamic pads → need `on_pad_added` callback in C
- `qtiqmmfsrc` has static pads → no callback needed
- USB camera may need `qtivtransform` if format is YUY2 (not NV12)
- After decode, always capsfilter to `video/x-raw,format=NV12` before the AI chain

### Step 3 — Choose Inference Element

Use `plugin-catalog.md` — only allowed elements:
- TFLite (`.tflite`) → `qtimltflite` + delegate property (HTP, GPU, or CPU)
- QNN (`.bin`) → `qtimlqnn` + backend property path
- SNPE (`.dlc`) → `qtimlsnpe` + delegate property

Preprocessing: always `qtimlvconverter`. For daisy-chain stage 2: set `mode=roi-batch-cumulative` — this tells the converter to crop ROIs from stage 1 detections instead of using the full frame.

### Step 4 — Choose Overlay Topology

Two valid topologies exist for **both** gst-launch and C apps. Choose based on what the user needs — not based on whether you're generating a command or a C app.

**Topology A — Standard Overlay (qtimetamux + qtivoverlay)**
- Use for standard annotation: bounding boxes, classification labels, keypoints drawn on video
- Simpler pipeline; default when user doesn't specify a layout preference
- `qtimlpostprocess → text/x-raw → qtimetamux → qtivoverlay → sink`

**Topology B — Composer Blend (qtivcomposer, no capsfilter)**
- Use for side-by-side, picture-in-picture, alpha blending, multi-stream composition
- `qtimlpostprocess → qtivcomposer (sink_1); raw video → qtivcomposer (sink_0)` — no capsfilter between `qtimlpostprocess` and the composer; pinning `width`/`height` there fails caps fixation regardless of format
- **Sink pad `position`/`dimensions` sizing — see `plugin-catalog.md`'s "What `position`/`dimensions` must actually be set to" note.** For a single full-screen stream, do not guess a small tile size (e.g. a model's typical inference input dimensions) — leave `position`/`dimensions` unset (default = full input size) or set both pads to the source's real decoded resolution. Only use tile-sized values for genuine multistream/PiP/side-by-side layouts.

| User intent | Choose |
|-------------|--------|
| Annotate video with boxes/labels/keypoints | A |
| Side-by-side or PiP layout | B |
| Alpha-blended overlay (segmentation, depth) | B |
| Multi-stream grid | B |
| Not specified, single model | A (simpler default) |

**Only three categories force the topology in current known-good templates — everything else supports both:**
- `image-segmentation` modules (`deeplab-argmax`, `yolov8-seg`), `depth-estimation` (`midas-v2`), and `super-resolution` (`srnet`) output frames only in current known-good templates → **must use B**
- All other categories — `object-detection`, `image-classification`, `pose-estimation`, `audio-classification` (including `yolov8`, `mobilenet`, `qfd`, `qfr`, `lite-3dmm`, `hrnet`, `yamnet`, etc.) — support **both** `text/x-raw` and `video/x-raw` → choose based on user intent. See `plugin-catalog.md`'s "Module Output Types — Format Support by Category" table for the authoritative list.
- **Preference when `qtivcomposer` is already required elsewhere in the pipeline** (multistream, PiP, side-by-side): for AI branches whose module category supports `video/x-raw`, prefer wiring `qtimlpostprocess`'s mask output into an **additional** `qtivcomposer` sink pad (Topology B) — paired with that stream's existing raw-passthrough sink pad at the same position/dimensions — over adding `qtimetamux`+`qtivoverlay`. **The mask is transparent boxes/labels only, with no video content — it must never replace the raw-passthrough pad, only supplement it, matching the `deeplab-argmax`/`srnet` segmentation pattern's sink_0 (passthrough) + sink_1 (mask) pairing.** Do not introduce `qtivcomposer` into an otherwise non-compositing pipeline solely for this optimization.

See `c-app-development.md` for full C code for both topologies, including `gst_element_link_filtered` for the `text/x-raw` caps in Topology A — Topology B uses no capsfilter at all between `qtimlpostprocess` and the composer.

### Step 5 — Choose Output Sink

Use `source-sink-patterns.md`. Key decisions:
- Display only → `waylandsink(sync=true, fullscreen=true)`
- File only → `v4l2h264enc → h264parse → mp4mux → filesink`
- Both → tee after overlay, one branch to each sink
- IO modes for encoder: camera source upstream → `capture-io-mode=dmabuf output-io-mode=dmabuf-import` (4/5); file/RTSP source upstream → `capture-io-mode=dmabuf output-io-mode=dmabuf` (4/4); AV record (encoder + audio into mp4mux) → 4/5 regardless of source

### Step 6 — Generate main.c

Using the structure and QIM SDK-specific property patterns in this file:

1. Standard includes + `#define` path constants from prompt
2. `GstAppContext` struct (add `parse` field if using qtdemux/rtspsrc)
3. Inline helpers: `gst_element_set_enum_property`, `get_enum_value` (if needed), `set_composer_pad` (if using qtivcomposer)
4. `on_pad_added` callback — only for qtdemux/rtspsrc. For multiple sources, use per-stream struct pattern from `c-app-development.md`.
5. Bus callbacks: `error_cb`, `warning_cb`, `eos_cb`
6. `handle_interrupt_signal` — sends EOS if PLAYING, quits main loop otherwise
7. `create_pipeline()`:
   - Declare all elements NULL → create with null checks + goto → set properties → `gst_bin_add_many` → link
   - IO modes via `gst_element_set_enum_property` not `g_object_set`
   - TFLite delegate via `gst_structure_from_string` for options struct
   - Module lookup via `get_enum_value` — never hardcode the integer
8. `main()`: `gst_init` → create loop + pipeline → `create_pipeline` → bus signals → SIGINT/SIGTERM → `GST_STATE_PAUSED` (not PLAYING directly — see `c-app-development.md`) → `g_main_loop_run` → teardown

### Step 7 — Generate CMakeLists.txt

Use template from `c-app-development.md`. Binary name derived from pipeline description.

### Step 8 — Generate README.md

Per `artifact-contract.md`:
- `Pipeline Flow` with `Text Summary` and `Mermaid Diagram` matching the actual element chain
- Assumptions and limitations (codec, display server, model format). If `qtimlpostprocess module=` is undocumented in `plugin-catalog.md`'s Supported Module Table and `tensors=`/`layers=` was omitted, state that assumption here — see `plugin-catalog.md`'s "Tensor Filter — Decision Rule," "Elevated uncertainty for undocumented modules."
- Run command
- Path placeholders to fill
- QLI build link at the end — always, no inline build steps

---

## Element Selection Rules

### Inference Element

Always use the explicit chain — never `qtimlvideotflitebin`:
- TFLite/HTP → `qtimlvconverter → qtimltflite(external delegate, HTP) → qtimlpostprocess`
- QNN → `qtimlvconverter → qtimlqnn(backend=/usr/lib/libQnnHtp.so) → qtimlpostprocess`
- SNPE → `qtimlvconverter → qtimlsnpe(delegate=dsp) → qtimlpostprocess`

Note: `delegate` enum nicks are always lowercase (`dsp`, `gpu`, `none`, `aip`).
`tensors`/`layers` (property name — never `output-tensors` or `tensor-names`) is an
**optional output filter**, not a required part of this chain — default is
unfiltered (emit every model output tensor). Only set it when there's a known
reason to filter/reorder; see `plugin-catalog.md`'s "Tensor Filter — Decision
Rule" before adding it.

### Queue Placement

Reasoning: queues decouple threads. Place them:
- After each `tee` branch (required — prevents one branch blocking another)
- Between inference and postprocess (inference is hardware-accelerated, postprocess is CPU — decoupling avoids stalls)
- NOT between every stage in a linear sequence

### Unfamiliar Pipeline

If the user asks for something not in the documented examples, apply the shape from Step 1. The sources, inference elements, and sinks are all documented. The only question is topology — how many of each and how they connect. Reason through it:
- N parallel models on same source → N tee branches, each with full AI chain, all feeding one qtivcomposer
- N independent sources → N decode chains, each with own tee + AI chain, all feeding one qtivcomposer
- Mixed model types → each branch picks its own inference element and delegate

### When Something is Unknown

- Module name unknown → use `<POSTPROC_MODULE>` placeholder, note in README
- Model output dimensions (segmentation/SR) → use `<OUTPUT_W>` / `<OUTPUT_H>` placeholders, note in README
- Backend path unknown → use `/usr/lib/libQnn<Backend>.so` pattern with placeholder
- Plugin behavior unknown → document assumption in README, do not invent behavior

---

## Validation Checklist

- [ ] Every element declared NULL at top of `create_pipeline`
- [ ] Every `gst_element_factory_make` has null check + goto
- [ ] Do not declare a local `gboolean ret` scratch variable for link results; link inline and fail directly to cleanup
- [ ] `v4l2h264dec` IO modes set via `gst_element_set_enum_property` using string nicks (`"dmabuf"`)
- [ ] TFLite `delegate` set using `GST_ML_TFLITE_DELEGATE_*` constants from `gst_sample_apps_utils.h`; external delegate options use `gst_structure_from_string`
- [ ] TFLite delegate uses `gst_structure_from_string` for options
- [ ] `qtimlpostprocess` module set via `get_enum_value` — never hardcoded integer
- [ ] `gst_bin_add_many` before `gst_element_link_many`
- [ ] Dynamic pad signal connected after adding qtdemux/rtspsrc
- [ ] `gst_pad_is_linked` check in `on_pad_added` prevents double-linking
- [ ] Bus unreffed after signal connections
- [ ] Pipeline set to PAUSED first (not directly to PLAYING)
- [ ] Teardown: `GST_STATE_NULL` before unref
- [ ] `CMakeLists.txt` uses `LANGUAGES C CXX`, links `gstreamer-1.0`
- [ ] `Pipeline Flow` in README matches actual code

---

## App Structures by Pipeline Type (Reference Examples)

These are examples — not an exhaustive list. Reason from Step 1 for anything else.

### Single-Stream (file input → display)
```
filesrc → qtdemux → h264parse → v4l2h264dec → NV12 caps → queue[1] → tee
tee → queue[2] → qtivcomposer (sink_0, raw passthrough)
tee → queue[4] → qtimlvconverter → qtimltflite → qtimlpostprocess → queue[7] → qtivcomposer (sink_1, no capsfilter)
qtivcomposer → queue[3] → fpsdisplaysink
```
Dynamic pad: qtdemux → queue[0] → h264parse...

### Two-Stage Daisy-Chain (file input → display)
Stage 1 output feeds qtivcomposer sink_1 (no capsfilter), stage 2 output feeds additional qtivcomposer sinks. Stage 2 qtimlvconverter uses `roi-batch-cumulative`.

### Three-Stage Face Recognition Daisy-Chain (camera/RTSP input → display)
Use the three-stage face recognition topology from `ai-pipeline-patterns.md` Template 17 for the equivalent gst-launch flow and full topology/labels/settings table. This is structurally different from the generic two-stage daisy-chain above:
- Uses **Topology A** (`qtimetamux` + `qtivoverlay`) end-to-end, not `qtivcomposer` — each stage's `qtimlpostprocess` output is `text/x-raw`, muxed via its own `qtimetamux` instance (3 total: detection, landmark, recognition), not a BGRA mask blended by a composer.
- Stage order is fixed: detection (`qfd`) → landmark/3DMM pose (`lite-3dmm`) → recognition (`qfr`). Do not run recognition on the raw detection ROI — it must consume the landmark-aligned crop from stage 2.
- Stage 1 `qtimlvconverter mode=image-batch-non-cumulative`; Stages 2 and 3 use `mode=roi-batch-cumulative` (set the enum either with `g_object_set_property` and `GValue` or `gst_element_set_enum_property`; match whichever pattern the rest of the generated file uses for consistency).
- Preferred sources are camera (`qtiqmmfsrc`) or RTSP (`rtspsrc → rtph264depay → h264parse → v4l2h264dec`). A file-source variant (`filesrc → qtdemux → h264parse → v4l2h264dec`) is structurally valid because the stages downstream of decode are the same; flag it as an extrapolated source variant if generated.
- Each stage's `qtimlpostprocess` needs its own `labels`/`settings`/`results` — never share one `GValue`/string across stages. See Template 17's per-stage table in `ai-pipeline-patterns.md` for exactly which of labels/settings each stage requires (stage 2 has no labels file at all).
- Module IDs resolved via `get_enum_value(element, "module", "<nick>")` per stage — `"qfd"`, `"lite-3dmm"`, `"qfr"` in that order; never hardcode enum integers (see Module Nick Names Reference above).

### Segmentation (file input → display)
Same as single-stream but qtivcomposer sink_1 has `alpha=0.5` set — no capsfilter between `qtimlpostprocess` and the composer; the composer sink-pad `dimensions` sizes the tile instead.

### Multi-Source (N independent files → composed display)
N independent `filesrc → decode` chains, each with own qtdemux dynamic pad + AI branch, all feeding one `qtivcomposer` with N sink pads.

---

## Multimedia App Structures (Non-AI)

For multimedia C apps (no AI inference), load `multimedia-pipeline-patterns.md` and this file. The topologies below correspond to the gst-launch templates in `multimedia-pipeline-patterns.md`.

### Camera Single-Stream Display
```
qtiqmmfsrc → NV12 caps → waylandsink
```
No tee, no AI, no dynamic pads. `gst_element_link()` works without explicit pad request.

### Camera Single-Stream Encode
```
qtiqmmfsrc → NV12 caps → queue → v4l2h264enc → queue → h264parse → mp4mux → queue → filesink
```
Multi-pad: declare `video_0::type=preview video_1::type=video` via `gst_child_proxy_set()`. Queue before encoder.

### Camera JPEG Snapshot + Encode (QLI only, gst-pipeline-app)
```
qtiqmmfsrc (video_N pad) → NV12 caps → queue → v4l2h264enc → mp4mux → filesink
qtiqmmfsrc (image_N pad) → JPEG → multifilesink
qtiqmmfsrc (video_N pad) → NV12 caps → waylandsink
```
Image pad request: `gst_element_request_pad_simple(camsrc, "image_1")`. No `qtijpegenc`.

### Multi-Camera Compose + Encode + UDP (side-by-side or PiP)
```
qtiqmmfsrc(cam0) → NV12 caps → queue → qtivcomposer.sink_0
qtiqmmfsrc(cam1) → NV12 caps → queue → qtivcomposer.sink_1
qtivcomposer → queue → tee
tee → queue → v4l2h264enc → mp4mux → filesink
tee → queue → v4l2h264enc → h264parse config-interval=-1 → rtph264pay → udpsink
```
Two separate `v4l2h264enc` instances (one per tee branch). Composer file output uses direct NV12 caps before the encoder.

### Video File Playback
```
filesrc → qtdemux → queue → h264parse → v4l2h264dec → NV12 caps → queue → waylandsink
```
Dynamic pad callback for qtdemux. For H.265: `h265parse → v4l2h265dec`.

### Video N-Stream Grid Playback
```
qtivcomposer(N sink pads) → queue → waylandsink
N × (filesrc → qtdemux → h264parse → v4l2h264dec → NV12 → queue → comp.sink_N)
```
Composer declared first. N independent decode chains with dynamic pad callbacks.

### Video Transcode (AVC → HEVC, C app only)
```
filesrc → qtdemux → queue → h264parse → v4l2h264dec → NV12 caps
→ v4l2h265enc → h265parse → mp4mux → filesink
```
No gst-launch template (transcode is C-app-only). Dynamic pad for qtdemux input.

### AV Playback (H.264 video + MP3 audio from MP4)
```
filesrc → qtdemux (pad-added callback)
  video pad → queue → h264parse → v4l2h264dec → waylandsink
  audio pad → queue → mpegaudioparse → mpg123audiodec → pulsesink
```
Dual `pad-added` routing via caps inspection. See `c-app-development.md` — qtdemux dual-track section.

### AV Record (camera + microphone → MP4)
```
pulsesrc → audio/x-raw caps → audioconvert → queue → lamemp3enc → queue → mpegaudioparse → queue → mp4mux.
qtiqmmfsrc → NV12 caps (interlace-mode=progressive,colorimetry=bt601) → queue → v4l2h264enc(output-io-mode=dmabuf-import) → queue → h264parse → mp4mux. → filesink
```
`pulsesrc do-timestamp=TRUE provide-clock=FALSE` — both required. `output-io-mode=dmabuf-import` on encoder.

---

### C-App-Only Multimedia Topologies (No gst-launch Template)

These require C apps — they are not representable as static gst-launch commands:

| App | Why C app required |
|---|---|
| Runtime stream add/remove | Dynamic `gst_bin_add` / `gst_element_set_state` |
| Stream activate/deactivate (resolution change) | `qticamsrc.video-pads-activation-mode=signal` + signal-based activation |
| Camera switch (swap sensor mid-stream) | `g_object_set(camsrc, "camera-switch-index", N, NULL)` while PLAYING |
| Burst capture mode | `g_signal_emit_by_name(camsrc, "capture-image", ...)` action signal |
| RTMP streaming | `flvmux → rtmp2sink` — requires runtime config |
| Smart codec | `qtismartvencbin` with ROI/bitrate/FPS control signals |

---

## C App Patterns

# C App Patterns

## Use This Reference For

- Generating the standard C GStreamer application structure for any QIMSDK AI pipeline
- Writing lifecycle management, bus handling, cleanup, and dynamic pad linking
- Every generated C app follows this exact structural pattern — only the pipeline elements and properties change

## Values and Placeholders Rule

- All path values (`#define INPUT_FILE`, `#define MODEL_PATH`, etc.) must be populated from the user's prompt Configuration section — never invented or defaulted by the skill.
- If the user did not provide a value, use an explicit placeholder (e.g. `"<INPUT_FILE>"`, `"<MODEL_PATH>"`) so the user knows what to fill in.
- Model names, label file names, and backend settings come from the prompt — the skill does not default these. A new model or label file provided by the user should just work without any skill changes.

---

## Standard Includes

```c
#include <stdio.h>
#include <glib-unix.h>
#include <gst/gst.h>
#include <gst/video/video.h>
#include <glib.h>

#include <gst/sampleapps/gst_sample_apps_utils.h>
```

Use the full sub-path `<gst/sampleapps/gst_sample_apps_utils.h>`. **Never use** `"gstappsutils.h"`, `<gst_sample_apps_utils.h>`, or any other form — they will fail to compile.

`gst_sample_apps_utils.h` provides all of the following — **do NOT redefine or reimplement any of them in `main.c`**:

| Symbol | Type | Notes |
|--------|------|-------|
| `GstAppContext` | struct | Fields: `pipeline`, `mloop` (NOT `loop`, NOT `main_loop`) |
| `gst_element_set_enum_property()` | function | Use for all enum properties (io-mode, mode, image-disposition, etc.) |
| `get_enum_value()` | function | Look up enum integer by nick |
| `error_cb()` | callback | Bus error handler |
| `warning_cb()` | callback | Bus warning handler |
| `eos_cb()` | callback | Bus EOS handler |
| `state_changed_cb()` | callback | Bus state-changed handler |
| `handle_interrupt_signal()` | callback | SIGINT/SIGTERM handler |

Any `typedef struct { ... } GstAppContext;` in `main.c` causes a **`-Werror=conflicting-types` build failure**. If the skill generates it, delete it.

Do not call helper functions that are not declared by `gst_sample_apps_utils.h`.
In particular, **never use `init_app_context()`, `deinit_app_context()`,
`register_bus_signals()`, `setup_interrupt_handler()`,
`gst_set_default_bus_callback()`, or `bus_callback`**; they are not part of the
sample-app utility API. Initialize `GstAppContext` fields directly, set up the
bus with `gst_pipeline_get_bus()`, `gst_bus_add_signal_watch()`, and direct
`g_signal_connect()` calls to `error_cb`, `warning_cb`, `eos_cb`, and
`state_changed_cb`, install SIGINT and SIGTERM with `g_unix_signal_add (SIGINT,
handle_interrupt_signal, &appctx)` and `g_unix_signal_add (SIGTERM,
handle_interrupt_signal, &appctx)`, then clean up by setting the pipeline to
`GST_STATE_NULL`, unreffing the pipeline, and unreffing `appctx.mloop`.

**GstAppContext field names — exact spelling required:**
```c
appctx.pipeline  /* GstElement*  — the pipeline */
appctx.mloop     /* GMainLoop*   — NOT appctx.loop, NOT appctx.main_loop */
```

---

## App Context Struct

`GstAppContext` is defined in `gst_sample_apps_utils.h` — do not redefine it:

```c
/* From gst_sample_apps_utils.h: */
typedef struct {
  GstElement *pipeline;
  GList      *plugins;
  GMainLoop  *mloop;
} GstAppContext;
```

For apps that need dynamic pad linking (filesrc/rtspsrc), add a local struct to carry the downstream element alongside the app context — or pass the parse element directly as the `userdata` to `on_pad_added`.

---

## Standard App Structure

A generated app has these sections in this order:

```
1. context struct (use GstAppContext from gst_sample_apps_utils.h — do not redefine)
2. on_pad_added() — dynamic pad callback (when using qtdemux or rtspsrc)
3. create_pipeline() — all element creation, property setting, linking
4. main() — init, create, bus setup, state, loop, teardown
```

Bus callbacks (`error_cb`, `warning_cb`, `eos_cb`, `state_changed_cb`) and `handle_interrupt_signal` come from `gst_sample_apps_utils.h` — do not reimplement them. Connect them directly in `main()`.

---

## create_pipeline() — Standard Template

Every pipeline creation function follows this exact pattern:

```c
static gboolean
create_pipeline (GstAppContext *appctx, const gchar *input_file,
    const gchar *model_path, const gchar *labels_path)
{
  GstElement *element_a = NULL;
  GstElement *element_b = NULL;
  /* ... declare all elements as NULL ... */
  GstCaps *caps = NULL;

  /* Step 1: Create all elements */
  element_a = gst_element_factory_make ("factory-name", "instance-name");
  if (!element_a) {
    g_printerr ("Failed to create element_a\n");
    goto cleanup;
  }
  /* ... create all elements, check each one ... */

  /* Step 2: Set properties on elements */
  g_object_set (element_a, "property", value, NULL);
  /* ... set all properties ... */

  /* Step 3: Add all elements to the pipeline */
  gst_bin_add_many (GST_BIN (appctx->pipeline),
      element_a, element_b, /* ... */ NULL);

  /* Step 4: Link elements — use inline boolean directly, not a gboolean ret variable */
  if (!gst_element_link_many (element_a, element_b, /* ... */ NULL)) {
    g_printerr ("Failed to link pipeline\n");
    goto cleanup_pipeline;
  }

  /* Step 5: Connect dynamic pad signals (if using qtdemux or rtspsrc) */
  g_signal_connect (qtdemux, "pad-added",
      G_CALLBACK (on_pad_added), appctx->parse);

  if (caps)
    gst_caps_unref (caps);
  return TRUE;

cleanup_pipeline:
  gst_object_unref (appctx->pipeline);
  appctx->pipeline = NULL;
  if (caps)
    gst_caps_unref (caps);
  return FALSE;

cleanup:
  /* Unref only elements not yet added to pipeline */
  if (element_a) gst_object_unref (element_a);
  if (element_b) gst_object_unref (element_b);
  if (caps) gst_caps_unref (caps);
  return FALSE;
}
```

**Key rules:**
- Declare every element as NULL at the top
- Check every `gst_element_factory_make()` call — if NULL, go to cleanup
- After `gst_bin_add_many()`, the pipeline owns the elements — use `cleanup_pipeline` label, not `cleanup`
- Before `gst_bin_add_many()`, elements are unowned — use `cleanup` label
- Always unref caps after setting them on a capsfilter

---

## Dynamic Pad Callback

Required when using `qtdemux` (file input) or `rtspsrc` (RTSP input).

In all sample apps, the dynamic pad connects to `queue[0]` — not directly to `h264parse`. The queue decouples the demuxer from the decode chain:

```c
static void
on_pad_added (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  GstElement *queue = (GstElement *) userdata;  /* queue[0], NOT h264parse */
  GstPad *sinkpad;
  GstPadLinkReturn ret;

  sinkpad = gst_element_get_static_pad (queue, "sink");
  if (!sinkpad) {
    g_printerr ("Failed to get sink pad\n");
    return;
  }

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  ret = gst_pad_link (srcpad, sinkpad);
  if (GST_PAD_LINK_FAILED (ret)) {
    g_printerr ("Failed to link dynamic pad\n");
  }

  gst_object_unref (sinkpad);
}
```

The static chain from queue[0] onwards is linked separately:
```c
/* queue[0] → h264parse → v4l2h264dec → capsfilter(NV12) → queue[1] → tee */
gst_element_link_many (queue[0], h264parse, v4l2h264dec, v4l2h264dec_caps, queue[1], tee, NULL);
```

Connect after adding qtdemux to the pipeline:
```c
g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), queue[0]);
```

The `gst_pad_is_linked` check prevents double-linking when qtdemux fires `pad-added` for multiple streams (e.g. audio + video).

**Expected runtime warning:** When decoding an MP4 that contains both video and audio tracks, `qtdemux` fires `pad-added` for both pads. The callback will be called twice: once for the video pad (which links successfully) and once for the audio pad (which fails because the video queue sink is already linked). The log line `Failed to link dynamic pad` for the audio pad is **expected and benign** — not a pipeline error. The pipeline will still run correctly on the video stream.

---

## Bus Callbacks

### Error Callback

```c
static void
error_cb (GstBus *bus, GstMessage *message, gpointer userdata)
{
  GMainLoop *mloop = (GMainLoop *) userdata;
  GError *error = NULL;
  gchar *debug = NULL;

  gst_message_parse_error (message, &error, &debug);
  gst_object_default_error (GST_MESSAGE_SRC (message), error, debug);

  g_free (debug);
  g_error_free (error);

  g_main_loop_quit (mloop);
}
```

Note: `gst_object_default_error()` is preferred over `g_printerr()` directly — it includes the source element name in the log output, making errors much easier to diagnose.

### Warning Callback

```c
static void
warning_cb (GstBus *bus, GstMessage *message, gpointer userdata)
{
  GError *error = NULL;
  gchar *debug = NULL;

  gst_message_parse_warning (message, &error, &debug);
  gst_object_default_error (GST_MESSAGE_SRC (message), error, debug);

  g_free (debug);
  g_error_free (error);
}
```

### EOS Callback

```c
static void
eos_cb (GstBus *bus, GstMessage *message, gpointer userdata)
{
  GMainLoop *mloop = (GMainLoop *) userdata;

  g_print ("\nEnd-Of-Stream reached.\n");
  g_main_loop_quit (mloop);
}
```

### State Changed Callback (optional, useful for debugging)

```c
static void
state_changed_cb (GstBus *bus, GstMessage *message, gpointer userdata)
{
  GstElement *pipeline = GST_ELEMENT (userdata);
  GstState old_state, new_state, pending_state;

  if (GST_MESSAGE_SRC (message) != GST_OBJECT_CAST (pipeline))
    return;

  gst_message_parse_state_changed (message, &old_state, &new_state,
      &pending_state);
  g_print ("Pipeline state changed from %s to %s\n",
      gst_element_state_get_name (old_state),
      gst_element_state_get_name (new_state));
}
```

---

## Interrupt Signal Handler

```c
static gboolean
handle_interrupt_signal (gpointer userdata)
{
  GstAppContext *appctx = (GstAppContext *) userdata;
  GstState state, pending;

  g_print ("\nReceived interrupt signal, sending EOS...\n");

  if (!gst_element_get_state (appctx->pipeline, &state, &pending,
          GST_CLOCK_TIME_NONE)) {
    gst_element_send_event (appctx->pipeline, gst_event_new_eos ());
    return TRUE;
  }

  if (state == GST_STATE_PLAYING) {
    gst_element_send_event (appctx->pipeline, gst_event_new_eos ());
  } else {
    g_main_loop_quit (appctx->mloop);
  }
  return TRUE;
}
```

**Why check state before EOS:** If the pipeline is not in PLAYING state, sending EOS may hang. Directly quitting the main loop is safer for non-playing states.

---

## Standard main() Function

```c
int
main (int argc, char *argv[])
{
  GstAppContext appctx = {};
  GstBus *bus = NULL;
  guint intrpt_watch_id = 0;
  guint term_watch_id = 0;
  gint ret = 0;

  /* Initialize GStreamer */
  gst_init (&argc, &argv);

  /* Create pipeline container */
  appctx.pipeline = gst_pipeline_new ("app-pipeline");
  if (!appctx.pipeline) {
    g_printerr ("Failed to create pipeline\n");
    ret = -1;
    goto done;
  }

  /* Create main loop */
  appctx.mloop = g_main_loop_new (NULL, FALSE);
  if (!appctx.mloop) {
    g_printerr ("Failed to create main loop\n");
    ret = -1;
    goto done;
  }

  /* Build pipeline */
  if (!create_pipe (&appctx, /* ...options... */)) {
    g_printerr ("Failed to build pipeline\n");
    ret = -1;
    goto done;
  }

  /* Set up bus — use callbacks from gst_sample_apps_utils.h */
  bus = gst_pipeline_get_bus (GST_PIPELINE (appctx.pipeline));
  gst_bus_add_signal_watch (bus);
  g_signal_connect (bus, "message::error",         G_CALLBACK (error_cb),         appctx.mloop);
  g_signal_connect (bus, "message::warning",       G_CALLBACK (warning_cb),       appctx.mloop);
  g_signal_connect (bus, "message::eos",           G_CALLBACK (eos_cb),           appctx.mloop);
  g_signal_connect (bus, "message::state-changed", G_CALLBACK (state_changed_cb), appctx.pipeline);
  gst_object_unref (bus);

  /* Set up interrupt handlers — from gst_sample_apps_utils.h */
  intrpt_watch_id = g_unix_signal_add (SIGINT, handle_interrupt_signal, &appctx);
  term_watch_id = g_unix_signal_add (SIGTERM, handle_interrupt_signal, &appctx);

  /* Start pipeline — PAUSED first, state_changed_cb transitions to PLAYING */
  switch (gst_element_set_state (appctx.pipeline, GST_STATE_PAUSED)) {
    case GST_STATE_CHANGE_FAILURE:
      g_printerr ("Failed to set pipeline to PAUSED\n");
      ret = -1;
      goto done;
    case GST_STATE_CHANGE_NO_PREROLL:
      gst_element_set_state (appctx.pipeline, GST_STATE_PLAYING);
      break;
    case GST_STATE_CHANGE_ASYNC:
    case GST_STATE_CHANGE_SUCCESS:
      break;
  }

  g_main_loop_run (appctx.mloop);

done:
  if (intrpt_watch_id)
    g_source_remove (intrpt_watch_id);
  if (term_watch_id)
    g_source_remove (term_watch_id);

  if (appctx.pipeline) {
    gst_element_set_state (appctx.pipeline, GST_STATE_NULL);
    gst_object_unref (appctx.pipeline);
  }
  if (appctx.mloop)
    g_main_loop_unref (appctx.mloop);

  gst_deinit ();
  return ret;
}
```

**Notes:**
- `error_cb`, `warning_cb`, `eos_cb`, `state_changed_cb`, `handle_interrupt_signal` all come from `gst_sample_apps_utils.h`
- Always unref the bus immediately after connecting signals
- PAUSED first — `state_changed_cb` auto-transitions to PLAYING; handles live sources correctly

---

## Pad Linking Strategy

**The core principle: `gst_element_link()` handles pad selection automatically in the vast majority of cases. Use explicit pad operations only when you need to target or configure a specific named pad.**

- **`gst_element_link(A, B)`** — GStreamer inspects A's available src pads and B's available sink pads, negotiates caps, and connects them. For elements with request pads (tee, qtivcomposer), it automatically requests a new pad. Use this for all normal element chaining.

- **`gst_element_get_static_pad()`** — use only to read or set properties on a specific pad (e.g., setting position/dimensions on a qtivcomposer sink pad). Never use this for linking.

- **`gst_element_request_pad_simple()`** — use only when you need to hold a reference to a specific named pad for a purpose other than immediate linking (e.g., qticamsrc multi-stream pad activation via `gst_child_proxy_set()`).

- **Dynamic pads (qtdemux, rtspsrc)** — these pads don't exist until data flows. Connect via `pad-added` signal callback, not at pipeline construction time. See the Dynamic Pad Callback section.

When in doubt: try `gst_element_link()` first. Only reach for explicit pad operations if you need to name or configure the specific pad being connected.

## Tee Branching Pattern

Always use `gst_element_link()` — it auto-requests `tee` src pads. The sample apps never
use `gst_element_get_request_pad()` for `tee` or `qtivcomposer`:

```c
/* Correct — used by all QIMSDK sample apps */
gst_element_link (tee, queue_display);   /* auto-requests tee src_%u */
gst_element_link (tee, queue_ai);        /* auto-requests tee src_%u */
```

**`gst_element_get_request_pad()` is deprecated as of GStreamer 1.20 and the build
environment has `-Werror=deprecated-declarations`, making it a hard build error.
Never use `gst_element_get_request_pad()` or `gst_element_request_pad_simple()`
for `tee` or `qtivcomposer` pads in generated C apps.**

The same `gst_element_link()` pattern applies when linking into `qtivcomposer` —
consecutive `gst_element_link()` calls each auto-request the next `sink_N` pad:

```c
/* Passthrough branch feeds sink_0 */
gst_element_link (queue_display, qtivcomposer);
/* AI branch feeds sink_1 */
gst_element_link (queue_ai_out, qtivcomposer);
```

**Each tee branch gets exactly one `gst_element_link()` call to its intended consumer.** Do not call `gst_element_link(tee, consumer)` a second time for a consumer that a queued branch (`gst_element_link(tee, queue); gst_element_link(queue, consumer)`) already terminates at — every direct `gst_element_link(tee, ...)` call auto-requests its own new tee src pad, so a redundant direct link claims a second tee output and either fails to link (the consumer's next pad-template slot doesn't match) or silently attaches to an unintended pad, leaving the queued branch's own consumer-side pad unlinked. Before adding a link from a tee, check whether that same consumer already has a queued chain from the same tee.

Use explicit pad lookup (`gst_element_get_static_pad`) only when reading or setting
pad properties (e.g. position/dimensions for qtivcomposer layout) — never for linking.

---

## Capsfilter Pattern

For setting NV12 format after decoder:

```c
GstElement *capsfilter = gst_element_factory_make ("capsfilter", "nv12_caps");
GstCaps *caps = gst_caps_new_simple ("video/x-raw",
    "format", G_TYPE_STRING, "NV12", NULL);
g_object_set (G_OBJECT (capsfilter), "caps", caps, NULL);
gst_caps_unref (caps);
```

For camera with resolution constraints:

```c
GstCaps *caps = gst_caps_new_simple ("video/x-raw",
    "format",    G_TYPE_STRING,     "NV12",
    "width",     G_TYPE_INT,        1920,
    "height",    G_TYPE_INT,        1080,
    "framerate", GST_TYPE_FRACTION, 30, 1,
    NULL);
g_object_set (G_OBJECT (capsfilter), "caps", caps, NULL);
gst_caps_unref (caps);
```

Always call `gst_caps_unref(caps)` after setting — the capsfilter takes its own reference.

---

## Element Naming Convention

Use descriptive instance names matching the pipeline role:

```c
filesrc         = gst_element_factory_make ("filesrc",         "file_src");
qtdemux         = gst_element_factory_make ("qtdemux",         "demux");
h264parse       = gst_element_factory_make ("h264parse",       "h264_parse");
v4l2h264dec     = gst_element_factory_make ("v4l2h264dec",     "h264_dec");
tee             = gst_element_factory_make ("tee",             "stream_tee");
qtimlvconverter = gst_element_factory_make ("qtimlvconverter", "preproc");
qtimltflite     = gst_element_factory_make ("qtimltflite",     "inference");
qtimlpostprocess= gst_element_factory_make ("qtimlpostprocess","postproc");
qtimetamux      = gst_element_factory_make ("qtimetamux",      "meta_mux");
qtivoverlay     = gst_element_factory_make ("qtivoverlay",     "overlay");
waylandsink     = gst_element_factory_make ("waylandsink",     "display");
```

For multiple queues, number them:

```c
queue[0] = gst_element_factory_make ("queue", "queue_0");
queue[1] = gst_element_factory_make ("queue", "queue_1");
/* etc. */
```

---

## Multi-Stream: Duplicate Element Naming Convention

When a pipeline has N parallel streams (AI Wall, two-stream object detection with two independent sources), each stream needs its own set of elements. Use indexed suffixes to keep instance names unique:

```c
#define NUM_STREAMS 2

GstElement *filesrc[NUM_STREAMS];
GstElement *qtdemux[NUM_STREAMS];
GstElement *h264parse[NUM_STREAMS];
GstElement *v4l2h264dec[NUM_STREAMS];
GstElement *qtimlvconverter[NUM_STREAMS];
GstElement *qtimltflite[NUM_STREAMS];
GstElement *qtimlpostprocess[NUM_STREAMS];

gchar name[64];
for (gint i = 0; i < NUM_STREAMS; i++) {
  snprintf (name, sizeof (name), "filesrc_%d", i);
  filesrc[i] = gst_element_factory_make ("filesrc", name);

  snprintf (name, sizeof (name), "qtdemux_%d", i);
  qtdemux[i] = gst_element_factory_make ("qtdemux", name);

  snprintf (name, sizeof (name), "h264parse_%d", i);
  h264parse[i] = gst_element_factory_make ("h264parse", name);

  snprintf (name, sizeof (name), "qtimltflite_%d", i);
  qtimltflite[i] = gst_element_factory_make ("qtimltflite", name);

  /* check each for NULL and goto cleanup */
}
```

**Rule:** Every element instance in a pipeline must have a unique instance name. Two elements with the same factory name MUST have different instance names — otherwise `gst_bin_get_by_name()` returns the wrong one and pipeline linking becomes unpredictable.

---

## Multi-Stream: Dynamic Pad Callback for Multiple Sources

When you have N independent `qtdemux` or `rtspsrc` elements, you cannot connect the same callback to all of them and pass just the downstream element — each demuxer must link to its own downstream element.

**Pattern: pass a struct with both the target element and stream index**

```c
typedef struct {
  GstElement *parse;   /* the h264parse for this stream */
  gint        stream_index;
} PadAddedData;

static void
on_pad_added_multi (GstElement *element, GstPad *srcpad, gpointer userdata)
{
  PadAddedData *data = (PadAddedData *) userdata;
  GstPad *sinkpad;

  sinkpad = gst_element_get_static_pad (data->parse, "sink");
  if (!sinkpad) return;

  if (gst_pad_is_linked (sinkpad)) {
    gst_object_unref (sinkpad);
    return;
  }

  if (GST_PAD_LINK_FAILED (gst_pad_link (srcpad, sinkpad)))
    g_printerr ("Failed to link pad for stream %d\n", data->stream_index);

  gst_object_unref (sinkpad);
}
```

Connect per demuxer:

```c
static PadAddedData pad_data[NUM_STREAMS]; /* static — must outlive create_pipe() */
for (gint i = 0; i < NUM_STREAMS; i++) {
  pad_data[i].parse        = h264parse[i];
  pad_data[i].stream_index = i;
  g_signal_connect (qtdemux[i], "pad-added",
      G_CALLBACK (on_pad_added_multi), &pad_data[i]);
}
```

**Lifetime note:** `pad_data` must remain valid for the lifetime of the pipeline — always declare it as `static` at function scope. A non-static local goes out of scope when `create_pipe()` returns; `pad-added` fires asynchronously after that, causing use-after-stack-free and garbage stream indices.

---

## C App Element API

# C App Element API — QIMSDK-Specific C API Patterns

## Use This Reference For

- Setting QIMSDK element properties correctly in C (differs from gst-launch string syntax)
- Handling enum properties, delegate options, and GValue arrays
- Patterns that cannot be derived from gst-launch documentation alone

All code in this file assumes `#include <gst/sampleapps/gst_sample_apps_utils.h>` is present. Use the full sub-path — the short form `<gst_sample_apps_utils.h>` does not resolve on Ubuntu aarch64 because `${GST_INCLUDE_DIRS}` already contains `gstreamer-1.0`. Symbols like `GST_ML_TFLITE_DELEGATE_EXTERNAL` come from that header.

---

## qtivtransform — When to Use

Use `qtivtransform` only when there is a documented transform requirement:

1. **Source format conversion or requested transform** — for example `v4l2src` YUY2 to NV12, rotate, flip, crop, or scale via downstream caps.
2. **Buffer-writability fix** — `qtivoverlay` and `qtivcomposer` write in-place and require sole buffer ownership (refcount == 1). If a parallel branch holds the buffer when the writing element runs, the write is silently skipped. Insert `qtivtransform ! video/x-raw,format=NV12` on the video branch **before `qtimetamux`** to force a copy and give the writer sole ownership (see `source-sink-patterns.md`). Always required when a parallel branch has `qtivcomposer`; also required under load when a parallel branch has `filesink` or `qtimlmetaparser`. In daisy-chain pipelines, apply before the first mux only when required — later inter-stage transforms disrupt Stage 1 ROI metadata.

For encode-chain placement, follow `plugin-catalog.md` and the relevant known-good topology.

---

## v4l2h264dec / v4l2h264enc — IO Mode

In gst-launch: `capture-io-mode=4 output-io-mode=4`

In C, these are enum properties set by string name:

```c
/* decode */
gst_element_set_enum_property (v4l2h264dec, "capture-io-mode", "dmabuf");
gst_element_set_enum_property (v4l2h264dec, "output-io-mode",  "dmabuf");

/* encode */
gst_element_set_enum_property (v4l2h264enc, "capture-io-mode", "dmabuf");
gst_element_set_enum_property (v4l2h264enc, "output-io-mode",  "dmabuf-import");
```

Both `gst_element_set_enum_property` and `get_enum_value` are declared (non-static) in `gst_sample_apps_utils.h`. **Never inline these in `main.c`** — adding a `static` implementation alongside the header declaration causes a `-Werror=static-declaration-follows-non-static` build error.

---

## qtimlvconverter — Mode and Image-Disposition (Enum Properties)

`mode` and `image-disposition` are **GObject enum properties** — they MUST be set with `gst_element_set_enum_property()`, NOT with `g_object_set()` using string values. This applies to any QTI element with GObject enum properties: passing a string to `g_object_set` results in a garbage integer value and a GLib-GObject-CRITICAL warning at runtime: `value "..." of type 'GstMLVideoConversionMode' is invalid or out of range`.

**Mode and image-disposition selection for Stage 2 (daisy-chain):**

| Stage 2 requirement | mode | image-disposition | Example models |
|---|---|---|---|
| Model needs a **batch of ROIs accumulated** before inference | `roi-batch-cumulative` | `centre` | hrnet, qpd, mobilenet on detected ROIs |
| Model needs **each ROI processed immediately**, one at a time | `roi-batch-non-cumulative` | *(omit)* | hlandmark (gesture) |

When in doubt: if the model processes all detected objects at once → cumulative; if it processes each detection independently as it arrives → non-cumulative.

```c
/* Pose estimation Stage 2: CORRECT */
gst_element_set_enum_property (mlvconv_2, "mode",              "roi-batch-cumulative");
gst_element_set_enum_property (mlvconv_2, "image-disposition", "centre");

/* Gesture Stage 2: CORRECT — no image-disposition */
gst_element_set_enum_property (mlvconv_2, "mode", "roi-batch-non-cumulative");

/* WRONG — do not use g_object_set for enum properties */
g_object_set (mlvconv_2, "mode", "roi-batch-cumulative", NULL);   /* WRONG */
```

Stage 1 `qtimlvconverter` uses the default mode (`image-batch-non-cumulative`) — no property setting needed.

---

## qtimetatransform — Module (Enum Property)

`module` on `qtimetatransform` is a **GObject enum property** — it MUST be set with `gst_element_set_enum_property()`, NOT with `g_object_set()` using a string. Using `g_object_set` produces a garbage integer value, a GLib-GObject-CRITICAL at runtime, and the pipeline fails with "Resource not found".

```c
/* CORRECT */
gst_element_set_enum_property (qtimetatransform, "module", "roi-palmd");

/* WRONG — causes runtime CRITICAL and pipeline failure */
g_object_set (G_OBJECT (qtimetatransform), "module", "roi-palmd", NULL);  /* WRONG */
```

This applies to all `qtimetatransform` module values (e.g. `roi-palmd` for gesture recognition, `roi-label-moving-average` for label-confidence smoothing, `roi-auto-framing` for auto-framing, and `roi-person-merge` for person ROI merge transforms).

---

## qtiobjtracker — Algorithm (Enum Property) and Parameters

`algo` is a **GObject enum property** — set with `gst_element_set_enum_property()`, not `g_object_set()` with a string (same rule as every other QIM SDK enum property). Only `bytetrack` is currently a valid nick.

```c
gst_element_set_enum_property (qtiobjtracker, "algo", "bytetrack");
```

`parameters` is a plain string property holding a serialized `GstStructure` of algorithm-specific tuning fields — set normally with `g_object_set()`. For `bytetrack`: `frame-rate`, `track-buffer`, `wh-smooth-factor`, `track-thresh`, `high-thresh`.

```c
g_object_set (G_OBJECT (qtiobjtracker), "parameters",
    "frame-rate=(int)30,track-buffer=(int)30", NULL);
```

Place `qtiobjtracker` downstream of `qtimetamux` and upstream of the overlay/consumer stage:

```c
gst_element_link_many (qtimetamux, queue, qtiobjtracker, queue, qtivoverlay, NULL);
```

---

## qtimltflite — HTP External Delegate

In gst-launch:
```
delegate=external external-delegate-path=libQnnTFLiteDelegate.so
external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
```

In C for HTP/DSP backend — `GST_ML_TFLITE_DELEGATE_EXTERNAL` is available via `gst_sample_apps_utils.h`. **Property names use hyphens** (`external-delegate-path`, `external-delegate-options`), matching the plugin's registered GObject properties and the gst-launch form above — do not substitute underscores. **`log_level=(string)1` must be included**, matching the gst-launch form:
```c
GstStructure *delegate_options = gst_structure_from_string (
    "QNNExternalDelegate,backend_type=htp,log_level=(string)1;", NULL);

g_object_set (G_OBJECT (qtimltflite),
    "model",    model_path,
    "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
    NULL);
g_object_set (G_OBJECT (qtimltflite),
    "external-delegate-path",    "libQnnTFLiteDelegate.so",
    "external-delegate-options", delegate_options,
    NULL);
gst_structure_free (delegate_options);
```

**For high-throughput cases (multistream, AI wall, daisy-chain secondary stages), add `htp_performance_mode=(string)2,` before `log_level`** — same rule as the gst-launch form documented above:
```c
GstStructure *delegate_options = gst_structure_from_string (
    "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
    "log_level=(string)1;", NULL);
```

In C for GPU backend:
```c
GstStructure *delegate_options = gst_structure_from_string (
    "QNNExternalDelegate,backend_type=gpu;", NULL);

g_object_set (G_OBJECT (qtimltflite),
    "model",    model_path,
    "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL,
    NULL);
g_object_set (G_OBJECT (qtimltflite),
    "external-delegate-path",    "libQnnTFLiteDelegate.so",
    "external-delegate-options", delegate_options,
    NULL);
gst_structure_free (delegate_options);
```

For CPU (no delegate):
```c
g_object_set (G_OBJECT (qtimltflite),
    "model",    model_path,
    "delegate", GST_ML_TFLITE_DELEGATE_NONE,
    NULL);
```

`GST_ML_TFLITE_DELEGATE_EXTERNAL`, `GST_ML_TFLITE_DELEGATE_NONE`, `GST_ML_TFLITE_DELEGATE_GPU` are defined in `gst_sample_apps_utils.h` which is available in the QIMSDK build environment via the `gst/sampleapps/` include path.

---

## qtimlvconverter — Mode Property

In gst-launch: `mode=roi-batch-cumulative` or no mode (defaults to standard)

In C:
```c
/* Default / stage-1 daisy-chain */
/* No mode property needed — default behavior is image-batch-non-cumulative */

/* Stage-2 daisy-chain (ROI-based) */
gst_element_set_enum_property (qtimlvconverter_stage2, "mode",
    "roi-batch-cumulative");

/* For pose estimation stage-2, also set image-disposition */
gst_element_set_enum_property (qtimlvconverter_stage2, "image-disposition",
    "centre");
```

---

## qtimlpostprocess — Module and Settings

In gst-launch: `module=yolov8 labels=/etc/labels/yolov8.json settings="{\"confidence\": 51.0}"`

In C, the `module` property stores an enum integer internally, but generated
apps must resolve it with `get_enum_value()` by nick name. Do not hardcode
numeric module IDs from any table; numeric IDs can drift across builds and
make generated code brittle.

```c
/* Resolve module by element property enum nick. */
gint module_id;
module_id = get_enum_value (qtimlpostprocess, "module", "yolov8");
if (module_id < 0) {
  g_printerr ("Module 'yolov8' not found\n");
  goto cleanup;
}
g_object_set (G_OBJECT (qtimlpostprocess), "module", module_id, NULL);
```

`get_enum_value()` takes exactly three arguments: the element pointer, property
name, and enum nick: `get_enum_value (qtimlpostprocess, "module", "yolov8")`.
It returns the integer value directly. **Never call it with `GST_TYPE_*`, only
two arguments, or an output pointer such as `&module_val`**; those forms do not
match the declared function.

Inline implementation of `get_enum_value` — **DO NOT include in main.c**. `get_enum_value` is already declared non-static in `gst_sample_apps_utils.h`; adding a `static` copy causes a `-Werror=static-declaration-follows-non-static` build error. Call `get_enum_value()` directly — it is available after `#include <gst/sampleapps/gst_sample_apps_utils.h>`.

Setting settings (confidence threshold):

```c
gchar settings_str[64];
snprintf (settings_str, sizeof (settings_str),
    "{\"confidence\": %.1f}", 51.0);
g_object_set (G_OBJECT (qtimlpostprocess),
    "labels",   labels_path,
    "settings", settings_str,
    "results",  10,
    NULL);
```

Settings as file path (used in pose estimation, gesture recognition):

```c
g_object_set (G_OBJECT (qtimlpostprocess),
    "labels",   labels_path,
    "settings", "/etc/labels/foot_track_net_settings.json",
    NULL);
```

---

## qtivcomposer — Position and Dimensions

In gst-launch: `sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>"`

In C, these are `GValue` arrays set on the pad directly:

```c
static void
set_composer_pad (GstElement *composer, const gchar *pad_name,
    gint x, gint y, gint w, gint h)
{
  GstPad *pad = gst_element_get_static_pad (composer, pad_name);
  if (!pad) return;

  GValue position  = G_VALUE_INIT;
  GValue dimension = G_VALUE_INIT;
  GValue val       = G_VALUE_INIT;

  g_value_init (&position,  GST_TYPE_ARRAY);
  g_value_init (&dimension, GST_TYPE_ARRAY);
  g_value_init (&val, G_TYPE_INT);

  g_value_set_int (&val, x); gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, y); gst_value_array_append_value (&position, &val);
  g_value_set_int (&val, w); gst_value_array_append_value (&dimension, &val);
  g_value_set_int (&val, h); gst_value_array_append_value (&dimension, &val);

  g_object_set_property (G_OBJECT (pad), "position",   &position);
  g_object_set_property (G_OBJECT (pad), "dimensions", &dimension);

  g_value_unset (&position);
  g_value_unset (&dimension);
  g_value_unset (&val);
  gst_object_unref (pad);
}
```

Usage:

```c
/* Side-by-side: raw left (960x1080), AI right (960x1080) */
set_composer_pad (qtivcomposer, "sink_0",    0, 0, 960, 1080);
set_composer_pad (qtivcomposer, "sink_1",  960, 0, 960, 1080);
```

**The number of `set_composer_pad` calls must exactly match the number of branches actually linked into that composer** — re-derive this count after every topology edit; do not carry over a call count from an earlier draft. A `set_composer_pad` call for a `sink_N` pad that no longer has anything linked to it is a silent no-op (or, if that pad index was never created, `gst_element_get_static_pad` returns NULL and the call does nothing) — it will not error, but it also means the tile it was meant to configure never gets sized/positioned. This most often happens when a branch that used to feed the composer as two raw sink pads (e.g. a passthrough tile plus a separately rendered mask tile) is refactored to pre-compose those two into one finished tile through a local `qtivcomposer` first — the top-level composer then needs one fewer `set_composer_pad` call than before the refactor, not the same count with an index left over.

---

## AI Overlay Topologies — Two Valid Options for Both gst-launch and C Apps

Both topologies work in both gst-launch and C apps. The QIM sample apps happen to use Topology A in gst-launch and Topology B in C apps, but this is not a restriction — choose based on what the user needs.

---

### Topology A — Standard Overlay (qtimetamux + qtivoverlay)

**Use when:** user wants standard AI annotation overlaid on video (bounding boxes, labels, keypoints). Simpler pipeline. Output is a single annotated video stream.

**How qtimlpostprocess connects:** outputs `text/x-raw` metadata → feeds into `qtimetamux` → `qtivoverlay` draws annotations onto the video frame.

gst-launch form:
```bash
filesrc ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t \
t. ! queue ! meta_mux. \
t. ! queue ! qtimlvconverter ! qtimltflite model=<MODEL> ! qtimlpostprocess module=<MOD> labels=<LABELS> ! text/x-raw ! meta_mux. \
qtimetamux name=meta_mux ! qtivoverlay ! waylandsink sync=true
```

C app form — the same elements, wired with `gst_element_link_many`:
```c
/* tee → queue[0] → qtimetamux (passthrough/video branch) */
gst_element_link_many (tee, queue[0], qtimetamux, NULL);

/* tee → queue[1] → qtimlvconverter → qtimltflite → qtimlpostprocess → qtimetamux (AI branch) */
/* Link AI branch up to postprocess */
gst_element_link_many (tee, queue[1], qtimlvconverter, qtimltflite, qtimlpostprocess, NULL);
/* Link postprocess → qtimetamux with text/x-raw caps filter */
GstCaps *text_caps = gst_caps_from_string ("text/x-raw");
gst_element_link_filtered (qtimlpostprocess, qtimetamux, text_caps);
gst_caps_unref (text_caps);

/* qtimetamux → qtivoverlay → waylandsink */
gst_element_link_many (qtimetamux, qtivoverlay, waylandsink, NULL);
```

Do not translate gst-launch named-pad shorthand into invented C pad requests.
`qtimetamux` has a normal media sink named `sink`; metadata pads are requested
as `data_%u`. Generated C apps should use normal element linking for the video
path and `gst_element_link_filtered()` with `gst_caps_from_string("text/x-raw")`
for postprocess metadata unless a source reference provides a more specific C
sample. Never request `qtimetamux` pads named `sink_0` or `sink_1`.

Configure `waylandsink` and `qtivoverlay`:
```c
g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);
/* qtivoverlay needs no special properties for basic annotation */
```

---

### Topology B — Composer Blend (qtivcomposer, no capsfilter)

**Use when:** user wants side-by-side comparison, picture-in-picture, alpha blending, or multi-stream composition. More flexible layout control. Used in `gst-sample-apps` C examples.

**How qtimlpostprocess connects:** outputs a rendered `video/x-raw` frame directly into `qtivcomposer` sink_1 — **no capsfilter in between**; raw passthrough video feeds `qtivcomposer` sink_0. Device-verified: a capsfilter that pins `width`/`height` on this branch fails caps fixation (`Fixated width in filter caps is not supported with current post-process type!`) regardless of the `format=` value used — this is not a format problem, so `BGRA`→`RGBA` relabeling does not fix it. Omit the capsfilter entirely and size the branch via the composer sink-pad `dimensions`.

gst-launch form:
```bash
filesrc ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t \
t. ! queue ! qtivcomposer name=comp sink_0::position="<0,0>" sink_0::dimensions="<960,1080>" sink_1::position="<960,0>" sink_1::dimensions="<960,1080>" \
t. ! queue ! qtimlvconverter ! qtimltflite model=<MODEL> ! qtimlpostprocess module=<MOD> labels=<LABELS> ! queue ! comp. \
comp. ! queue ! waylandsink sync=true
```

C app form:
```c
/* tee → queue[0] → qtivcomposer (sink_0 passthrough) */
gst_element_link_many (tee, queue[0], qtivcomposer, NULL);

/* tee → queue[1] → qtimlvconverter → qtimltflite → qtimlpostprocess → queue[2] → qtivcomposer (sink_1), no capsfilter */
gst_element_link_many (tee, queue[1], qtimlvconverter, qtimltflite, qtimlpostprocess, queue[2], qtivcomposer, NULL);

/* qtivcomposer → queue[3] → fpsdisplaysink */
gst_element_link_many (qtivcomposer, queue[3], fpsdisplaysink, NULL);

/* Set qtivcomposer pad positions — dimensions size the tile since no capsfilter pins it */
set_composer_pad (qtivcomposer, "sink_0",   0, 0, 960, 1080);
set_composer_pad (qtivcomposer, "sink_1", 960, 0, 960, 1080);
```

---

### Decision Criteria

| Need | Topology |
|------|----------|
| Standard annotation (boxes, labels, keypoints) on video | A — qtimetamux + qtivoverlay |
| Side-by-side comparison (e.g. original vs processed) | B — qtivcomposer |
| Picture-in-picture | B — qtivcomposer |
| Alpha blending (e.g. segmentation mask over video) | B — qtivcomposer with `alpha` property |
| Multi-stream composition (multiple sources in grid) | B — qtivcomposer with N sinks |
| User doesn't specify, single AI model, display output | A — simpler, default choice |
| Super resolution (side-by-side output comparison) | B — qtivcomposer |

---

### Per-task postprocess output and topology

Format support is determined by the module's **task category**, not the individual module name — see `plugin-catalog.md`'s "Module Output Types — Format Support by Category" table for the full category list. Only `image-segmentation`, `depth-estimation`, and `super-resolution` are format-restricted in current known-good templates; every detection/classification/pose/audio-classification module supports both `text/x-raw` and `video/x-raw` (image mask):

| Category | Example modules | Output | Compatible topology |
|--------|--------|--------|-------------------|
| `object-detection`, `image-classification`, `pose-estimation`, `audio-classification` | `yolov8`, `yolov5`, `yolo-nas`, `qfd`, `qpd`, `mobilenet`, `mobilenet-softmax`, `qfr`, `qfr-softmax`, `hrnet`, `lite-3dmm`, `yamnet` | `text/x-raw` metadata OR rendered `video/x-raw` frame | **Both** — use A for annotation (and when the metadata trail is needed for tracking/RTSP/logging), B for layout control or when reusing a `qtivcomposer` already present in the pipeline |
| `image-segmentation` | `deeplab-argmax`, `yolov8-seg` | rendered `video/x-raw` frame (mask) only | **B only** — use qtivcomposer with alpha |
| `depth-estimation` | `midas-v2` | rendered `video/x-raw` frame (depth visualization) only | **B only** — use qtivcomposer with alpha |
| `super-resolution` | `srnet` | `video/x-raw,RGB` frame only | **B only** — use qtivcomposer for side-by-side |

**No capsfilter after `qtimlpostprocess` for Topology B** — for every category in the table above, feed the postprocess output directly into `queue ! qtivcomposer` with no `video/x-raw` capsfilter in between. Device-verified: pinning `width`/`height` on this branch fails caps fixation (`Fixated width in filter caps is not supported with current post-process type!`) no matter what `format=` is used. Size the branch entirely via the `qtivcomposer` sink-pad `dimensions` — `640x360` is the standard default tile size to set there, `520x520` for `deeplab-argmax` segmentation, `256x144` for `midas-v2` depth. Use the value from the user's prompt if provided, otherwise use these defaults and note them in the README.

**Note on Topology A text/x-raw:** In C apps using Topology A, use `gst_element_link_filtered` with `gst_caps_from_string("text/x-raw")` to connect postprocess to qtimetamux — not `gst_element_link_many` directly, as the caps negotiation requires the explicit filter.

**Preference when `qtivcomposer` is already present:** if the pipeline already requires `qtivcomposer` for another reason (multistream/AI-wall, side-by-side, PiP) and an AI branch's module supports `video/x-raw` per the category table, prefer wiring that branch's `video/x-raw,RGBA` mask output into an **additional** `qtivcomposer` sink pad — paired with that stream's existing raw-passthrough sink pad at the same `position`/`dimensions` — instead of adding `qtimetamux`+`qtivoverlay` for that stream. **The mask output contains only drawn boxes/labels on a transparent background, not the video frame — wiring it to a sink pad with no matching raw-video pad produces a tile with no video, only floating annotations over the composer's background fill.** This is the same two-pad shape `deeplab-argmax`/`srnet` already use out of necessity. Do not introduce `qtivcomposer` into an otherwise non-compositing pipeline just to gain this optimization; Topology A remains the default for plain single-stream requests. See `plugin-catalog.md` for the full rule.

---

## Segmentation — Special Postprocess Output

Segmentation pipelines do NOT use the standard `text/x-raw → qtimetamux → qtivoverlay` chain.

The segmentation postprocess (`module=deeplab-argmax`) outputs a rendered `video/x-raw` frame — a mask frame, not metadata. It is blended with the original video using `qtivcomposer` with alpha.

**Do NOT insert a capsfilter between `qtimlpostprocess` and `qtivcomposer`.** Device-verified: pinning `width`/`height` on this branch fails caps fixation with `Fixated width in filter caps is not supported with current post-process type!`, regardless of `format=`. Link `qtimlpostprocess` straight into the composer sink pad and size the tile via the composer's `dimensions` property instead:

```c
gst_element_link_many (qtimlpostprocess, qtivcomposer, NULL);

/* qtivcomposer with alpha for blending */
GstPad *blend_pad = gst_element_get_static_pad (qtivcomposer, "sink_1");
g_object_set (G_OBJECT (blend_pad), "alpha", 0.5, NULL);
gst_object_unref (blend_pad);
```

Also: `qtimlvconverter` has an `image-disposition` property for segmentation that controls how the input frame is resized to match model input:

```c
/* Options: GST_ML_VIDEO_DISPOSITION_TOP_LEFT, _CENTRE, _STRETCH */
/* Default for segmentation: stretch */
g_object_set (G_OBJECT (qtimlvconverter), "image-disposition",
    GST_ML_VIDEO_DISPOSITION_STRETCH, NULL);
```

Or via string using `gst_element_set_enum_property`:
```c
gst_element_set_enum_property (qtimlvconverter, "image-disposition", "stretch");
/* other values: "top-left", "centre" */
```

Pipeline flow for segmentation:
```
tee → queue → qtivcomposer (sink_0 = raw video)
tee → queue → qtimlvconverter(image-disposition=stretch) → qtimltflite → qtimlpostprocess(deeplab-argmax)
    → queue → qtivcomposer (sink_1, alpha=0.5, no capsfilter)
qtivcomposer → queue → waylandsink
```

---

## Super Resolution — Special Postprocess Output

Super resolution (`module=srnet`) also outputs `video/x-raw,format=RGB` — not metadata. The output frame is the upscaled version.

```c
GstCaps *sr_caps = gst_caps_new_simple ("video/x-raw",
    "format", G_TYPE_STRING, "RGB", NULL);
g_object_set (G_OBJECT (sr_capsfilter), "caps", sr_caps, NULL);
gst_caps_unref (sr_caps);
```

Pipeline flow for super resolution (side-by-side comparison):
```
tee → queue → qtivcomposer (sink_0 = original)
tee → qtimlvconverter → qtimltflite → qtimlpostprocess(srnet)
    → video/x-raw,RGB → queue → qtivcomposer (sink_1)
qtivcomposer → queue → waylandsink
```

---

## waylandsink — Standard Properties

```c
g_object_set (G_OBJECT (waylandsink),
    "sync",       TRUE,
    "fullscreen", TRUE,
    NULL);
```

---

## qtimetamux — Standard Pattern

No properties to set. Just add to pipeline and link:

```c
qtimetamux = gst_element_factory_make ("qtimetamux", "meta_mux");
```

The main video path and AI metadata path both link into it:
- `tee_branch_display → queue → qtimetamux` (normal media sink `sink`)
- `postprocess → text/x-raw → queue → qtimetamux` (metadata caps select the metadata path)

Then: `qtimetamux → qtivoverlay → waylandsink`

In C, do not use `gst_element_request_pad_simple(qtimetamux, "sink_0")` or
`"sink_1"`; those are not `qtimetamux` pad-template names. If manual metadata
pad handling is ever required, use the documented `data_%u` request pad name and
keep the postprocess output filtered to `text/x-raw`.

---

## Module Nick Names Reference

From `plugin-catalog.md` — key mappings for C `get_enum_value()` calls:

| Pipeline Type | Nick Name to Pass |
|--------------|------------------|
| Object detection (YOLOX/YOLOv8) | `"yolov8"` |
| Face detection | `"qfd"` |
| Face landmark / 3DMM pose (face recognition Stage 2) | `"lite-3dmm"` |
| Face recognition / classification (face recognition Stage 3) | `"qfr"` |
| Classification (MobileNet) | `"mobilenet"` |
| Segmentation | `"deeplab-argmax"` |
| Pose estimation (HRNet) | `"hrnet"` |
| Person/foot detection (QPD) | `"qpd"` |
| Super resolution | `"srnet"` |
| Audio classification (YAMNet) | `"yamnet"` |

---

## qtimlqnn — Full C API

For QNN models (`.bin` files), the inference element is `qtimlqnn` and uses a `backend` property (a path to the QNN library), not a `delegate`:

```c
/* DSP/HTP backend */
g_object_set (G_OBJECT (qtimlqnn), "model",   model_path, NULL);
g_object_set (G_OBJECT (qtimlqnn), "backend", "/usr/lib/libQnnHtp.so", NULL);

/* CPU backend */
g_object_set (G_OBJECT (qtimlqnn), "backend", "/usr/lib/libQnnCpu.so", NULL);

/* GPU backend */
g_object_set (G_OBJECT (qtimlqnn), "backend", "/usr/lib/libQnnGpu.so", NULL);
```

**System library** — always set explicitly alongside backend:
```c
g_object_set (G_OBJECT (qtimlqnn), "system", "/usr/lib/libQnnSystem.so", NULL);
```
The system library property is `"system"` — NOT `"backend-extra"`, NOT `"system-lib"`.

**Output tensors** — optional output filter; default is unfiltered (all model
outputs emitted, native order). Only set this when there's a known reason to
filter/reorder (see `plugin-catalog.md`'s "Tensor Filter — Decision Rule") — most
QNN pipelines with a known `qtimlpostprocess module=` omit it entirely. When it is
needed, use GstValueArray, NOT a plain string. Property name is `"tensors"` — NOT `"tensor-names"`, NOT `"output-tensors"`:
```c
GValue tensors = G_VALUE_INIT;
GValue val = G_VALUE_INIT;
g_value_init (&tensors, GST_TYPE_ARRAY);
g_value_init (&val, G_TYPE_STRING);

g_value_set_string (&val, "boxes");
gst_value_array_append_value (&tensors, &val);
g_value_set_string (&val, "scores");
gst_value_array_append_value (&tensors, &val);
g_value_set_string (&val, "class_idx");
gst_value_array_append_value (&tensors, &val);

g_object_set_property (G_OBJECT (qtimlqnn), "tensors", &tensors);
g_value_unset (&tensors);
g_value_unset (&val);
```

---

## qtimlsnpe — Full C API

For SNPE models (`.dlc` files), the inference element is `qtimlsnpe`.

**Model and delegate** — `delegate` is an enum, set via `gst_element_set_enum_property` with lowercase nick:
```c
g_object_set (G_OBJECT (qtimlsnpe), "model", model_path, NULL);
gst_element_set_enum_property (qtimlsnpe, "delegate", "dsp");   /* lowercase: dsp, gpu, none, aip */
```

**Output tensors (SNPEv2)** — optional output filter; default is unfiltered.
SNPE `.dlc` compiles more often retain extra/debug output nodes than QNN/TFLite
exports do, so setting this is a common SNPE habit in practice — but it is still
conditional on a known reason to filter, not a hard requirement of the runtime
(see `plugin-catalog.md`'s "Tensor Filter — Decision Rule"). When it is needed,
use GstValueArray. Property name is `"tensors"` — NOT `"output"`, NOT `"output-tensors"`, NOT `"tensor-names"`:
```c
GValue tensors = G_VALUE_INIT;
GValue val = G_VALUE_INIT;
g_value_init (&tensors, GST_TYPE_ARRAY);
g_value_init (&val, G_TYPE_STRING);

g_value_set_string (&val, "boxes");
gst_value_array_append_value (&tensors, &val);
g_value_set_string (&val, "scores");
gst_value_array_append_value (&tensors, &val);
g_value_set_string (&val, "class_idx");
gst_value_array_append_value (&tensors, &val);

g_object_set_property (G_OBJECT (qtimlsnpe), "tensors", &tensors);
g_value_unset (&tensors);
g_value_unset (&val);
```

**Output layers (SNPEv1)** — same pattern but property name is `"layers"`:
```c
g_object_set_property (G_OBJECT (qtimlsnpe), "layers", &layers_array);
```

Do NOT set both `"tensors"` and `"layers"` — setting one clears the other.

---

## Audio — qtimlaconverter Properties

The audio preprocessing element `qtimlaconverter` requires these properties for YAMNet-style classification:

```c
g_object_set (G_OBJECT (qtimlaconverter), "sample-rate", 16000, NULL);
gst_element_set_enum_property (qtimlaconverter, "feature", "lmfe");
g_object_set (G_OBJECT (qtimlaconverter),
    "params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;", NULL);
```

These values come from the model's audio processing requirements — use as-is for YAMNet; use placeholders for other audio models.

`audiobuffersplit` output buffer size for YAMNet (16kHz, 0.96s chunks):
```c
g_object_set (G_OBJECT (audiobuffersplit), "output-buffer-size", 31200, NULL);
```

Audio capsfilter for pulsesrc (microphone input):
```c
GstCaps *audio_caps = gst_caps_new_simple ("audio/x-raw",
    "format", G_TYPE_STRING, "S16LE", NULL);
g_object_set (G_OBJECT (audio_capsfilter), "caps", audio_caps, NULL);
gst_caps_unref (audio_caps);
```

---

## SmartCodec C-App — AI-Driven Encode + Display (File Source, qtismartvencbin, Wayland Display)

A "smartcodec", "smart codec", "smart encoder", "adaptive encode", or "AI-driven encoding" request is this pattern. `qtismartvencbin` dynamically controls encoder bitrate/GOP/framerate based on AI inference results.

**What makes this different from a plain detection app:** `qtismartvencbin` has three sink pads — `sink` (raw video to encode), `sink_ctrl` (second raw video copy for internal control), and `sink_ml` (AI text/x-raw metadata that drives bitrate decisions). The tee must split decoded video to all three branches, and `qtimlpostprocess` text/x-raw output must split to both `sink_ml` (encode control) and `qtimetamux` (display overlay).

**Non-negotiable rules:**

1. **Three tee branches from decoded video:**
   - `sink` branch: `tee → queue_sc → capsfilter_enc(NV12) → qtismartvencbin sink`
   - `sink_ctrl` branch: `tee → queue_ctrl → capsfilter_ctrl(NV12) → qtismartvencbin sink_ctrl`
   - AI/display branch: `tee → queue_ai → qtimlvconverter → qtimltflite → qtimlpostprocess → detection_tee`

   **Both `capsfilter_enc` and `capsfilter_ctrl` are required on the `sink`/`sink_ctrl` branches** — they break GStreamer's backward caps-query propagation through the tee. Without them, two NV12 branches sharing a tee cause a qtdemux "Internal data stream error (-5)" caps conflict. Caps: `video/x-raw, format=NV12` only — no framerate, no width, no height.

   **Do NOT connect `sink_ml` for file-source apps.** `qtismartvencbin` is an aggregator — connecting `sink_ml` makes it block waiting for text/x-raw on that pad. With file source + HTP delegate, the model takes ~5s to load before any inference output arrives; the aggregator stalls and qtdemux errors with `streaming stopped, reason error (-5)` during preroll. The AI branch (`qtimlpostprocess` output) should feed only the display overlay (`qtimetamux` data pad), not `sink_ml`. The smartcodec bin still encodes and adjusts bitrate using its internal logic — `sink_ml` is optional AI-driven control, not required for the bin to function.

2. **`detection_tee` after `qtimlpostprocess`** — splits text/x-raw to:
   - `detection_tee → queue_ml → qtismartvencbin sink_ml` (AI encode control)
   - `detection_tee → queue_meta → qtimetamux data pad` (display overlay)
   A fourth tee branch for passthrough video feeds `qtimetamux sink`.

3. **`qtismartvencbin` sink pads are static — use `gst_element_get_static_pad`**, not `gst_element_get_request_pad`. Link manually with `gst_pad_link` (same pattern as the golden reference). **Link all three sink pads BEFORE linking the encode output chain** — `gst_element_link_many(qtismartvencbin, ...)` for the output must come after all sink pad links, otherwise `qtismartvencbin` cannot determine its input caps and caps-negotiation on the output chain fails:
   ```c
   GstPad *sc_src  = gst_element_get_static_pad (queue_sc,       "src");
   GstPad *sc_sink = gst_element_get_static_pad (qtismartvencbin, "sink");
   gst_pad_link (sc_src, sc_sink);

   GstPad *ctrl_src  = gst_element_get_static_pad (queue_ctrl,      "src");
   GstPad *ctrl_sink = gst_element_get_static_pad (qtismartvencbin, "sink_ctrl");
   gst_pad_link (ctrl_src, ctrl_sink);

   GstPad *ml_src  = gst_element_get_static_pad (queue_ml,        "src");
   GstPad *ml_sink = gst_element_get_static_pad (qtismartvencbin, "sink_ml");
   gst_pad_link (ml_src, ml_sink);
   ```

4. **`qtismartvencbin` key properties** (from golden reference):
   ```c
   g_object_set (G_OBJECT (qtismartvencbin),
       "default-gop", 30,
       "max-gop",     600,
       "encoder",     2,        /* H.264 */
       "max-bitrate", 1000000,
       NULL);
   ```

5. **Encode output chain — link AFTER all sink pad links:** `qtismartvencbin src → queue → h264parse → mp4mux → queue → filesink` — do NOT add `v4l2h264enc`. `qtismartvencbin` is a bin that already includes the encoder internally; its src pad outputs a compressed H.264 bitstream directly. Call `gst_element_link_many(qtismartvencbin, queue_enc, h264parse2, mp4mux, ...)` only after all three `gst_pad_link` calls for `sink`, `sink_ctrl`, and `sink_ml` have completed.

6. **Display output chain (Topology A):** `tee → queue_disp → qtimetamux sink` + detection_tee overlay branch → `qtimetamux data` → `qtivoverlay → waylandsink`

7. **Full element chain (file source):**
   ```
   filesrc → qtdemux → [queue0] → h264parse → v4l2h264dec → NV12 capsfilter → queue1 → tee
     tee → queue_sc   → capsfilter_enc(NV12)  → qtismartvencbin(sink) [manual pad link]
     tee → queue_ctrl → capsfilter_ctrl(NV12) → qtismartvencbin(sink_ctrl) [manual pad link]
     tee → queue_ai   → qtimlvconverter → qtimltflite → qtimlpostprocess
                        → text/x-raw → queue_meta → qtimetamux (data pad, display overlay only)
     tee → queue_disp → qtimetamux (sink pad, passthrough video)
   qtimetamux → qtivoverlay → waylandsink
   qtismartvencbin(src) → queue → h264parse → mp4mux → queue → filesink
   ```
   Note: `qtimlpostprocess` feeds only `qtimetamux` (display). Do NOT connect it to `qtismartvencbin sink_ml`.

8. **`filesink` output path**: use `/root/Downloads/qimsdk_samples/output/<name>_out.mp4` on QLI — never `$HOME` in C defines.

9. **Artifact is async** (has `filesink`) — run to natural EOS, do not use timeout.

---

## Metadata Parser C-App — Appsink-Based Bounding Box Parsing (File Source, Detection, Wayland Display)

A "metadata parser example", "parse inference metadata", "appsink metadata", or "programmatic bounding box extraction" request is this pattern — it is not a plain detection overlay.

**What makes this different from a plain detection app:** After inference, `detection_tee` splits into two `qtimlpostprocess` instances:
- **Display branch:** `qtimlvdetection[0] → detection_filter(RGBA capsfilter, no width/height) → qtivcomposer (sink_1)` for on-screen overlay
- **Metadata branch:** `qtimlvdetection[1] → appsink_caps(text/x-raw) → queue → appsink` feeding an `appsink_callback` that reads bounding boxes programmatically

**This pattern uses Topology B (qtivcomposer)** — NOT Topology A (qtimetamux/qtivoverlay). The raw video passthrough goes to `qtivcomposer sink_0` and the RGBA overlay branch goes to `qtivcomposer sink_1`. Do NOT route the RGBA mask output to `qtimetamux` — `qtimetamux`'s video sink only accepts raw passthrough, not a rendered mask. Do NOT pin `width`/`height` on `detection_filter` — device-verified: pinning dimensions on the capsfilter immediately after `qtimlpostprocess` fails caps fixation (`Fixated width in filter caps is not supported with current post-process type!`) regardless of format; leave it bare (`format=RGBA` only) and size the tile via the composer sink-pad `dimensions`.

**Non-negotiable rules:**

1. **Two `qtimlpostprocess` instances** — one for each branch. Both configured identically (same module, labels, settings, results). Named `qtimlvdetection[0]` (display/RGBA) and `qtimlvdetection[1]` (metadata/text). Do not re-use the same instance on both branches.

2. **`appsink` configuration:**
   ```c
   g_object_set (G_OBJECT (appsink), "emit-signals", TRUE, NULL);
   g_signal_connect (appsink, "new-sample", G_CALLBACK (appsink_callback), NULL);
   ```
   `appsink_caps` is a named `capsfilter` element set to `text/x-raw` — same as `gst_caps_new_simple("text/x-raw", NULL, NULL)`.

3. **`appsink_callback` using `gst_value_deserialize`** — pull sample, map buffer, deserialize as `GST_TYPE_LIST`:

   > **Pull/push ONLY via action signals — never the `gst_app_*` C API.** Use `g_signal_emit_by_name(appsink, "pull-sample", &sample, &ret)` and `g_signal_emit_by_name(appsrc, "push-buffer", buf, &ret)`. Do **NOT** call `gst_app_sink_pull_sample()` / `gst_app_src_push_buffer()` — those symbols live in `libgstapp` (`gstreamer-app-1.0`), which the sample-app `CMakeLists.txt` template does **not** link, so they fail at link time with `undefined reference to 'gst_app_sink_pull_sample'`. The action-signal form needs only `gstreamer-1.0` (already linked). Including `<gst/app/gstappsink.h>` for the `GstAppSink*` type in the callback signature is fine — it's header-only; the link error comes only from calling the functions. This applies to every appsink/appsrc callback (metadata parser, event encoder, any zero-copy appsrc feed).
   >
   > **Container types in the deserialized metadata are NOT interchangeable:** the top level is a `GST_TYPE_LIST` (use `gst_value_list_get_size`/`gst_value_list_get_value`), but each entry's `"bounding-boxes"` is a **`GstValueArray`** (use `gst_value_array_get_size`/`gst_value_array_get_value`), and `"rectangle"` is also a `GstValueArray`. Using `gst_value_list_*` or `GST_VALUE_HOLDS_LIST` on `bounding-boxes` silently finds zero boxes (no crash, no error) — the app builds and runs but never triggers on any detection. Each bbox element is a boxed `GstStructure` (`GST_STRUCTURE(g_value_get_boxed(v))`) whose **name is the class label** (`gst_structure_get_name(bbox)` → `"person"`).

   ```c
   GstFlowReturn appsink_callback (GstElement *appsink, gpointer user_data) {
     GValue vlist = G_VALUE_INIT;
     GstSample *sample = NULL;
     GstBuffer *buffer = NULL;
     GstMapInfo memmap = {};
     GstFlowReturn ret = GST_FLOW_OK;

     g_signal_emit_by_name (appsink, "pull-sample", &sample, &ret);
     if (ret != GST_FLOW_OK || !sample) goto exit;

     buffer = gst_sample_get_buffer (sample);
     if (!buffer || !gst_buffer_map (buffer, &memmap, GST_MAP_READ)) goto exit;

     gchar *data = g_new0 (gchar, memmap.size + 1);
     memcpy (data, memmap.data, memmap.size);

     gchar *ctx = NULL;
     gchar *token = strtok_r (data, "\n", &ctx);

     g_value_init (&vlist, GST_TYPE_LIST);
     if (!gst_value_deserialize (&vlist, token)) { g_free (data); goto exit; }

     guint size = gst_value_list_get_size (&vlist);
     for (guint idx = 0; idx < size; idx++) {
       const GValue *value = gst_value_list_get_value (&vlist, idx);
       GstStructure *entry = GST_STRUCTURE (g_value_get_boxed (value));

       const GValue *bboxes = gst_structure_get_value (entry, "bounding-boxes");
       guint bbox_size = gst_value_array_get_size (bboxes);
       for (guint i = 0; i < bbox_size; i++) {
         const GValue *bval = gst_value_array_get_value (bboxes, i);
         GstStructure *bbox = GST_STRUCTURE (g_value_get_boxed (bval));
         const gchar *label = gst_structure_get_name (bbox);
         gdouble confidence;
         gst_structure_get_double (bbox, "confidence", &confidence);
         const GValue *rect = gst_structure_get_value (bbox, "rectangle");
         gfloat x = g_value_get_float (gst_value_array_get_value (rect, 0));
         gfloat y = g_value_get_float (gst_value_array_get_value (rect, 1));
         gfloat w = g_value_get_float (gst_value_array_get_value (rect, 2));
         gfloat h = g_value_get_float (gst_value_array_get_value (rect, 3));
         g_print ("Label: %s  conf: %.2f  box:[%.3f,%.3f,%.3f,%.3f]\n",
             label, confidence, x, y, w, h);
       }
     }
     g_free (data);
     g_value_unset (&vlist);
   exit:
     if (buffer) gst_buffer_unmap (buffer, &memmap);
     if (sample) gst_sample_unref (sample);
     return GST_FLOW_OK;
   }
   ```

4. **Element chain (file source, Topology B — qtivcomposer display):**
   ```
   filesrc → qtdemux → [queue0] → h264parse → v4l2h264dec → NV12 capsfilter → queue1 → tee
     tee → queue2 → qtivcomposer (sink_0, passthrough video)
     tee → queue3 → qtimlvconverter → qtimltflite → detection_tee
       detection_tee → qtimlvdetection[0] → detection_filter(RGBA capsfilter) → queue4 → qtivcomposer (sink_1, overlay)
       detection_tee → qtimlvdetection[1] → appsink_caps(text/x-raw) → queue5 → appsink (metadata branch)
   qtivcomposer → queue6 → waylandsink (or fpsdisplaysink wrapping waylandsink)
   ```
   - Single `on_pad_added` for qtdemux → queue0 (video only; no audio in this pipeline).
   - `detection_filter` RGBA caps: `video/x-raw, format=RGBA` — no framerate, no width/height required. Do NOT pin width/height here — device-verified, it fails caps fixation regardless of format.
   - `appsink_caps` caps: `gst_caps_new_simple("text/x-raw", NULL, NULL)` — no additional fields.
   - `qtivcomposer sink_0` position/dimensions: set explicitly to source resolution (e.g. 1920×1080). `sink_1` left unset (defaults to input frame size).
   - Use the `build_pad_property`/`set_composer_pad` helper from `gst_sample_apps_utils.h` to set sink_0 dimensions.

5. **Do NOT use Topology A (qtimetamux/qtivoverlay) for this pattern.** `qtimetamux`'s video sink accepts raw passthrough only; the RGBA output from `qtimlpostprocess` cannot link to it. The composer (Topology B) is required.

6. **Includes:** no `json-glib` needed for inline-constant apps.

---

## Audio Classification C-App — Exact Structure (File Source, MP3/FLAC, YAMNet, Wayland Display)

Reference source (golden, device-verified): Reproduce this structure exactly. Deviations cause the `qtivcomposer` aggregator to deadlock (one frame renders, display freezes, zero error logged, all streaming threads idle forever).

**Non-negotiable rules (all verified on real QLI hardware):**

1. **`on_pad_added` — dual blind-link.** Connect `g_signal_connect(qtdemux, "pad-added", ...)` **twice** — once with the video queue as userdata, once with the audio queue. The same simple callback attempts `gst_pad_link(pad, queue->sink)` each time; one succeeds (caps match), one silently fails. Do NOT write a single caps-inspecting/dispatching callback — it produces a permanent compositor aggregator deadlock after preroll.
   ```c
   static void on_pad_added (GstElement *demux, GstPad *srcpad, gpointer userdata) {
     GstElement *queue = (GstElement *) userdata;
     GstPad *sinkpad = gst_element_get_static_pad (queue, "sink");
     gst_pad_link (srcpad, sinkpad);  /* silently fails for the wrong pad — that's correct */
     gst_object_unref (sinkpad);
   }
   /* Connect twice, each with a different queue: */
   g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), video_queue);
   g_signal_connect (qtdemux, "pad-added", G_CALLBACK (on_pad_added), audio_queue);
   ```

2. **Exact element chain (MP3, file source) — verified working:**
   ```
   filesrc → qtdemux
     qtdemux [video_0] → queue[0] → h264parse → v4l2h264dec → v4l2h264dec_caps(NV12) → qtivcomposer(sink_0)
     qtdemux [audio_0] → queue[1] → mpegaudioparse → mpg123audiodec
                       → audioconvert → audioresample → audiobuffersplit
                       → queue[2] → qtimlaconverter → qtimltflite → qtimlpostprocess
                       → classification_filter(RGBA 368x64 capsfilter) → queue[3] → qtivcomposer(sink_1)
   qtivcomposer → queue[4] → waylandsink
   ```
   - For FLAC: replace `mpegaudioparse → mpg123audiodec` with `flacparse → flacdec`.
   - **No queue between `qtimlaconverter` and `qtimltflite`** — the gst-launch template shows one, the C app does not use it (causes deadlock).
   - **No queue between `v4l2h264dec_caps` and `qtivcomposer`** — the NV12 capsfilter connects directly to the composer.
   - `classification_filter` is a separate named `capsfilter` element, not `gst_element_link_filtered`. Create it with `gst_element_factory_make("capsfilter", "classification_filter")`, set caps via `g_object_set`, link normally.

3. **`qtivcomposer` position/dimensions — set both pads explicitly:**
   ```c
   set_composer_pad (qtivcomposer, "sink_0", 0, 0, 1920, 1080);    /* full-frame video */
   set_composer_pad (qtivcomposer, "sink_1", 30, 30, 480, 270);    /* small audio overlay */
   ```
   Use `gst_element_get_static_pad` (both pads already exist after linking).

   **If this exact CPU-delegate/no-delegate audio-classification pipeline ever deadlocks specifically after adding/changing `sink_1` position/dimensions** (not the dual-blind-link deadlock in rule 1, which has a different symptom/cause): do NOT try to work around it by pinning `width`/`height` on the `classification_filter` capsfilter right after `qtimlpostprocess` — that capsfilter must stay unpinned (see rule 2 above and `plugin-catalog.md`'s render-overlay caps note); pinning dimensions there fails caps fixation regardless of format and is a separate, unrelated failure. If `set_composer_pad` geometry on `sink_1` is the confirmed, reproducible trigger for the deadlock, insert a `videoscale`/`capsfilter` pair *between* `classification_filter` and the composer (after caps have already negotiated freely) to fix the tile's on-screen size instead, and skip the `set_composer_pad` call for that pad. Only reach for this if the geometry call itself is the reproducible trigger — do not preemptively drop it from the documented working structure above.

4. **`waylandsink` must omit `sync` entirely** (audio-classification pipelines never set `sync` to `TRUE` or `FALSE` — see `ai-pipeline-patterns.md` Route A3):
   ```c
   g_object_set (G_OBJECT (waylandsink), "fullscreen", TRUE, NULL);
   ```

---

## fpsdisplaysink — Properties

When using `fpsdisplaysink` as the display sink (wraps `waylandsink` with FPS overlay):

```c
/* Create waylandsink first, then pass to fpsdisplaysink */
g_object_set (G_OBJECT (waylandsink), "sync", TRUE, "fullscreen", TRUE, NULL);

g_object_set (G_OBJECT (fpsdisplaysink),
    "sync",                    TRUE,
    "signal-fps-measurements", TRUE,
    "text-overlay",            TRUE,
    "video-sink",              waylandsink,
    NULL);
```

`fpsdisplaysink` is an optional wrapper — use `waylandsink` directly for simpler apps. Use `fpsdisplaysink` only when FPS display is needed.

---

## Pipeline State Management — PAUSED then PLAYING Pattern

The sample apps use `GST_STATE_PAUSED` first, then the `state_changed_cb` automatically transitions to PLAYING. This is more robust than going directly to PLAYING, especially for camera sources and live sources that return `GST_STATE_CHANGE_NO_PREROLL`:

```c
switch (gst_element_set_state (pipeline, GST_STATE_PAUSED)) {
  case GST_STATE_CHANGE_FAILURE:
    g_printerr ("Failed to transition to PAUSED\n");
    goto error;
  case GST_STATE_CHANGE_NO_PREROLL:
    /* Live source — transition directly to PLAYING */
    gst_element_set_state (pipeline, GST_STATE_PLAYING);
    break;
  case GST_STATE_CHANGE_ASYNC:
    /* Will complete async — state_changed_cb handles PLAYING transition */
    break;
  case GST_STATE_CHANGE_SUCCESS:
    /* Already prerolled — go to PLAYING */
    gst_element_set_state (pipeline, GST_STATE_PLAYING);
    break;
}
```

With `state_changed_cb` handling the PAUSED→PLAYING transition automatically (see `c-app-development.md`), you can simplify to just setting PAUSED and letting the callback drive PLAYING. This is the pattern used by all sample apps.

---

## Dual-Pipeline Event Encoder — Conditional Recording on Detection

Do not invent an alternate detection-trigger mechanism — the two points below are the actual, tested design; deviating from them (e.g. reading `GstVideoRegionOfInterestMeta` off a video buffer) produces code that does not even link, since that meta type has no public API export in this SDK.

**Two separate `GstElement *pipeline` instances**, each with its own `GstBus`, own state, own EOS callback (`pipeline_eos_cb` for main, `recording_eos_cb` for the recording pipeline):
- `pipeline_main`: source → tee → **two parallel branches**:
  1. Display/composition branch: tee → `qtivcomposer` (raw passthrough sink pad)
  2. AI branch: tee → `qtimlvconverter` → `qtimltflite` → queue → a **second tee** (`detection_tee`) that splits the raw tensor output into two independent `qtimlpostprocess` instances *before* postprocessing — not one postprocess feeding two consumers:
     - `qtimlpostprocess` #1 → capsfilter → `qtivcomposer` (overlay sink pad, for on-screen boxes). **This overlay capsfilter must be `video/x-raw,format=RGBA`, NOT `BGRA`.** `qtimlpostprocess`'s src pad advertises only `video/x-raw,format={RGBA,RGBx}` on this build — a `BGRA` capsfilter fails to link (`no link possible from qtimlpostprocess to <capsfilter>`) at pipeline build. Leave width/height unset — the composer sink pad sizes the tile (see plugin-catalog's "RGBA never BGRA" and "do not pin width/height when a composer sizes it" notes). The golden reference and older examples say `BGRA` here because they used the deprecated `qtimlvdetection` wrapper; that is stale for the current `qtimlpostprocess`.
     - `qtimlpostprocess` #2 → `capsfilter caps=text/x-raw` → `appsink` (metadata parsing, see below)
  - `qtivcomposer` output is **immediately pinned to NV12 via a dedicated capsfilter** (`appsrc_filter1` in the reference) *before* `composer_tee`. This is mandatory, not optional: the composer's overlay sink pad negotiates RGBA (to match the `qtimlpostprocess` mask), so without an explicit downstream NV12 capsfilter the composer's src pad negotiates RGBA end-to-end — which the recording `appsrc`/`v4l2h264enc` (NV12-only) will reject with `not-negotiated (-4)` at runtime (`gst_base_src_loop... streaming stopped, reason not-negotiated`). This failure is silent at build/link time and only appears once the pipeline reaches PLAYING and a detection fires — always add this capsfilter, never skip it because "the composer already outputs video/x-raw."
  - **`format=NV12` alone on this capsfilter is insufficient — it must also pin exact `width`, `height`, `interlace-mode=progressive`, and `colorimetry=bt601`.** Without dimensions, `v4l2h264enc` cannot negotiate a hardware encoder configuration downstream and `not-negotiated (-4)` fires even though the format matches:
    ```c
    GstCaps *caps = gst_caps_new_simple ("video/x-raw",
        "format", G_TYPE_STRING, "NV12",
        "width", G_TYPE_INT, <decoded frame width>,
        "height", G_TYPE_INT, <decoded frame height>,
        "interlace-mode", G_TYPE_STRING, "progressive",
        "colorimetry", G_TYPE_STRING, "bt601", NULL);
    ```
    Use `1920x1088` only as a documented fallback — derive actual width/height from the input source's real decoded resolution when known (`gst-discoverer-1.0` on the input file/stream), otherwise use the fallback values and note the assumption in the README.
  - **Do NOT add `framerate` to this capsfilter.** This capsfilter lives inside `pipeline_main`'s connected graph, downstream of the real decoder (`v4l2h264dec` etc.), whose framerate is already fixed by the source's actual caps (e.g. `30000/1001` for a 29.97fps source, not exactly `30/1`). Pinning a hardcoded `framerate=30/1` here creates a caps conflict that GStreamer's caps-query proxying propagates backward through the *entire* connected bin — this breaks pad linking everywhere in `pipeline_main`, including the completely unrelated `qtdemux`→`queue0` dynamic pad link at pipeline startup (observed failure: `gst_pad_link_full: link between demux:video_0 and queue_0:sink failed: no common format`, `qtdemux ... streaming stopped, reason not-linked (-1)`), even though that link has nothing to do with the composer. The in-graph NV12 capsfilter must omit `framerate`; verified via `gst-launch-1.0` A/B test: identical decode chain with a downstream `framerate=30/1` capsfilter breaks `qtdemux`'s pad-added link; without it, the chain negotiates cleanly end to end.
  - After the NV12 capsfilter, tee again (`composer_tee`): one branch → `fpsdisplaysink`(wraps `waylandsink`) for display, the other branch → a second `appsink` (`composer_appsink`, `emit-signals=TRUE`) whose `new-sample` callback copies the composed buffer and pushes it into the recording pipeline's `appsrc` via `push-buffer`. This is how video reaches the recording pipeline — always the **post-overlay/composed, NV12** frame, never a raw pre-inference tap and never the raw BGRA composer output.
- `pipeline_recoding` (built once at startup, kept in `GST_STATE_NULL`): `appsrc` (caps must match the capsfilter above on format/width/height/interlace-mode/colorimetry, **plus an explicit `framerate`** — e.g. `framerate=30/1` — since this pipeline is disconnected from any real decoder and `appsrc` needs its own declared frame timing; the reference's isolated recording `appsrc` sets `framerate` even though the in-graph `appsrc_filter1` above does not, precisely because only this `appsrc` has no upstream to derive timing from) → queue → `v4l2h264enc` → `h264parse` → queue → `mp4mux` → `filesink`. Filesink `location` is set dynamically (e.g. `/etc/media/output-<N>.mp4`, N incremented per recording) immediately before each `GST_STATE_PLAYING` transition — never set once at construction time.

**Detection trigger is metadata-based, not buffer-meta-based.** The `#2` postprocess branch's output is `text/x-raw`, a serialized `GstStructure` list (NOT `GstVideoRegionOfInterestMeta`, which is not exported by this SDK build and will fail to link). The `appsink`'s `new-sample` handler:
1. Pulls the sample, maps the buffer, deserializes it into a `GST_TYPE_LIST` of `GstStructure` entries via `gst_value_deserialize`. **Pull with the `"pull-sample"` action signal and push into the recording `appsrc` with the `"push-buffer"` action signal — never `gst_app_sink_pull_sample()`/`gst_app_src_push_buffer()`** (they need `gstreamer-app-1.0`, which the CMake template does not link → `undefined reference` at link time). See the "Metadata Parser" appsink rule above for the full explanation and the container-type gotcha.
2. For each entry, reads the `"bounding-boxes"` **`GstValueArray`** (use `gst_value_array_get_size`/`gst_value_array_get_value`, NOT `gst_value_list_*` — the wrong accessor silently finds zero boxes and recording never triggers); each element is itself a boxed `GstStructure` **whose structure name is the detection label** (e.g. `gst_structure_get_name(bbox_entry)` returns `"person"`), with fields `confidence` (double) and `rectangle` (float array `[x,y,w,h]`, normalized 0–1).
3. Counts entries matching the target label. Non-zero count starts recording (`GST_STATE_PLAYING` on the recording pipeline, resuming if already built) and resets a no-detection frame counter; the counter increments on every zero-count frame while recording is active. After a threshold (150 frames in the reference) with no detection, sends EOS to the recording pipeline and pauses it — never SIGKILL/force-NULL the recording pipeline as the ordinary stop path, since `mp4mux` needs the EOS to flush the moov atom.

**Finalize the recording on MAIN-pipeline EOS too — not just the no-detection threshold.** With a finite file source, the common case is the input reaching EOS *while a recording is still active* (the clip is person-heavy and never hits the 150-frame no-detection stop). You MUST install a custom handler on the **main** pipeline's `message::eos` (do NOT leave the generic `eos_cb` from `gst_sample_apps_utils` on it — that only quits the loop). The handler, if a recording is in progress, must: (a) end the appsrc stream via the `"end-of-stream"` action signal, (b) **synchronously wait for the recording pipeline's EOS on its own bus** with `gst_bus_timed_pop_filtered` — NOT `gst_element_get_state`, and NOT relying on the async `recording_eos_cb` signal watch, because the main loop is about to quit and will never dispatch it — then (c) set the recording pipeline to NULL, THEN (d) `g_main_loop_quit`. Skipping this (or using the state-wait / async-watch variants) produces an MP4 with bytes but **no moov atom → unplayable** (device-verified: file was 173 MB but `gst-discoverer-1.0` rejected it; the async `recording_eos_cb` never fired because the loop quit first).

```c
static void
main_eos_cb (GstBus *bus, GstMessage *msg, gpointer userdata)
{
  MyAppCtx *ctx = userdata;
  gboolean was_recording;
  g_mutex_lock (&ctx->mutex);
  was_recording = ctx->recording; ctx->recording = FALSE;
  g_mutex_unlock (&ctx->mutex);
  if (was_recording && ctx->pipeline_recording) {
    GstFlowReturn r = GST_FLOW_OK;
    g_signal_emit_by_name (ctx->appsrc_rec, "end-of-stream", &r);   /* action signal, not gst_app_src_end_of_stream */
    /* Wait for EOS on the recording bus directly — the async recording_eos_cb
     * will NOT run once we quit the loop below. */
    GstBus *rb = gst_pipeline_get_bus (GST_PIPELINE (ctx->pipeline_recording));
    GstMessage *m = gst_bus_timed_pop_filtered (rb, 5 * GST_SECOND,
        GST_MESSAGE_EOS | GST_MESSAGE_ERROR);
    if (m) gst_message_unref (m);
    gst_object_unref (rb);
    gst_element_set_state (ctx->pipeline_recording, GST_STATE_NULL);   /* moov now flushed */
  }
  g_main_loop_quit (ctx->mloop);
}
/* Wire it on the MAIN bus instead of eos_cb:
   g_signal_connect (main_bus, "message::eos", G_CALLBACK (main_eos_cb), &ctx); */
```

Keep the recording pipeline in `GST_STATE_NULL` until the detection event fires, and transition it directly to `GST_STATE_PLAYING` from the detection callback — never call `set_state(pipeline_recording, GST_STATE_PAUSED)` first. `state_changed_cb` from `gst_sample_apps_utils` auto-advances any pipeline from PAUSED→PLAYING, which would start the recording pipeline immediately and race with the main pipeline:

```c
/* In the detection callback (appsink_detection) — CORRECT */
static GstFlowReturn
appsink_detection (GstAppSink *appsink, gpointer userdata)
{
  MyAppCtx *ctx = (MyAppCtx *) userdata;

  if (person_detected && !ctx->recording) {
    ctx->recording = TRUE;
    /* Start directly to PLAYING — do NOT use PAUSED first for the recording pipeline */
    gst_element_set_state (ctx->pipeline_recording, GST_STATE_PLAYING);
  }
  /* ... */
}
```

Do NOT do this anywhere before detection fires:
```c
/* WRONG — causes immediate PLAYING via state_changed_cb auto-advance */
gst_element_set_state (pipeline_recording, GST_STATE_PAUSED);
```

Starting directly to PLAYING (not via a bare PAUSED call) does not trigger the auto-advance in `state_changed_cb` because the READY→PAUSED transition has `pending == GST_STATE_PLAYING`, not `VOID_PENDING`, and the callback's guard condition (`new_state == GST_STATE_PAUSED && pending == GST_STATE_VOID_PENDING`) does not fire.

**`qtivcomposer` sink pad `position`/`dimensions` — see `plugin-catalog.md`'s "What `position`/`dimensions` must actually be set to" note (Topology B section) for the full rule and the observed failure symptom (video confined to a small corner tile, rest of screen showing flat `background` fill).** Applied to this event encoder: this is single-stream full-screen composition, not multi-stream tiling — the golden reference sets `sink_0`'s `position`/`dimensions` to the source's real resolution (`1920x1080`) and leaves `sink_1` (the overlay mask pad) entirely unset, relying on `qtivcomposer`'s documented default (`dimensions` empty = same as input dimensions = full destination). Do not borrow a small tile size (e.g. `640x360`) from a typical-model-output-dimensions table for either pad in this single-stream case.

---
## Camera Source in C Apps

**Default element name:** `qtiqmmfsrc` unless `qticamsrc` is explicitly requested or confirmed on the target. `qticamsrc` and `qtiqmmfsrc` share the same source type and property/pad behavior in the refreshed catalog, but target package availability and rank differ.

For portable sample-app-style camera code, prefer `create_camera_source_bin("camera_source_bin")` from `gst_sample_apps_utils` when the app does not need direct camera-specific controls. The helper returns a CamX-backed `qtiqmmfsrc` when CamX is present; otherwise it builds a fallback `libcamerasrc -> qtivtransform` bin with a ghost `src` pad. Use direct `qtiqmmfsrc`/`qticamsrc` element creation when the request needs camera properties, per-pad properties, image pads, action signals, EIS/VHDR, or explicit pad activation.

### Basic single-pad setup

```c
GstElement *camsrc = gst_element_factory_make("qtiqmmfsrc", "camsrc");
if (!camsrc) { g_printerr("Failed to create qtiqmmfsrc\n"); goto cleanup; }
g_object_set(camsrc, "camera", 0, NULL);
/* Single-pad camera: gst_element_link() works without explicit pad request */
```

### Portable helper setup

```c
GstElement *camsrc = create_camera_source_bin("camera_source_bin");
if (!camsrc) { g_printerr("Failed to create camera source bin\n"); goto cleanup; }
/* The helper exposes a normal src pad and hides qtiqmmfsrc/libcamerasrc selection. */
```

### Multi-pad setup (multiple video streams)

`qtiqmmfsrc`/`qticamsrc` implements `GstChildProxy` — pad properties are set via `gst_child_proxy_set()`:

```c
/* Request pads by name */
GstPad *pad0 = gst_element_request_pad_simple(camsrc, "video_0");
GstPad *pad1 = gst_element_request_pad_simple(camsrc, "video_1");
/* Never use gst_element_get_request_pad() — deprecated */

/* Set stream roles via GstChildProxy interface */
gst_child_proxy_set(GST_CHILD_PROXY(camsrc),
    "video_0::type", "preview", NULL);
gst_child_proxy_set(GST_CHILD_PROXY(camsrc),
    "video_1::type", "video", NULL);

/* Release pad references when done */
gst_object_unref(pad0);
gst_object_unref(pad1);
```

### Image pad (live JPEG snapshots)

```c
GstPad *img_pad = gst_element_request_pad_simple(camsrc, "image_1");
/* image_1 delivers pre-encoded JPEG from ISP — do NOT add qtijpegenc after this pad */
gst_object_unref(img_pad);
```

### Per-pad crop (runtime-adjustable)

```c
/* Set crop on video_0 pad at runtime */
GValue crop_val = G_VALUE_INIT;
g_value_init(&crop_val, GST_TYPE_ARRAY);
/* ... add <X,Y,W,H> int values to GstValueArray ... */
gst_child_proxy_set_property(GST_CHILD_PROXY(camsrc), "video_0::crop", &crop_val);
g_value_unset(&crop_val);
```

### Key rules for qtiqmmfsrc/qticamsrc in C apps

- Always use `gst_element_request_pad_simple()` — never the deprecated `gst_element_get_request_pad()`
- For single-stream apps, no pad request needed — `gst_element_link()` uses the default first video pad
- Use `gst_child_proxy_set()` for per-pad property changes (type, crop, framerate, rotate)
- `image_N` pads deliver JPEG directly — no `qtijpegenc` needed or desired
- Current source registers `qticamsrcdeviceprovider`; use `gst-device-monitor-1.0` for camera enumeration when the provider is installed.
- For generic multistream camera C apps, prefer `create_camera_source_bin() ! qtivsplit` and use `NV12` caps on the encoder branch. Preserve direct camera element usage when the app needs camera-source properties or action signals.

---

## v4l2h264enc / v4l2h265enc — Encoder IO Modes in C Apps

Encoder IO modes are V4L2 enums — always set with string nicks via `gst_element_set_enum_property()`, NOT integer values:

```c
/* Standard zero-copy encode (capture-io-mode=4, output-io-mode=4) */
gst_element_set_enum_property(enc, "capture-io-mode", "dmabuf");
gst_element_set_enum_property(enc, "output-io-mode",  "dmabuf-import");
```

IO mode enum nick → integer mapping:
- `"auto"` → 0
- `"rw"` → 1
- `"mmap"` → 2
- `"userptr"` → 3
- `"dmabuf"` → 4 (use for `capture-io-mode` in all encode pipelines)
- `"dmabuf-import"` → 5 (use for `output-io-mode` in AV record pipelines specifically)

**For AV record pipelines (camera + microphone → mp4mux):** `output-io-mode` must be `"dmabuf-import"` (value 5):

```c
/* AV record — use dmabuf-import for output-io-mode */
gst_element_set_enum_property(enc, "capture-io-mode", "dmabuf");
gst_element_set_enum_property(enc, "output-io-mode",  "dmabuf-import");
```

**Extra controls for bitrate/GOP:**

```c
GstStructure *ctrls = gst_structure_from_string(
    "controls,video_bitrate=1000000,video_gop_size=29;", NULL);
if (ctrls) {
    g_object_set(enc, "extra-controls", ctrls, NULL);
    gst_structure_free(ctrls);
}
```

---

## qtivtransform — Multimedia Rotation/Flip/Scale

The earlier entry covers source conversion and conditional `qtimetamux` writable-buffer handling. For multimedia rotate/flip/scale pipelines:

### Rotation

```c
/* rotate enum nicks: "none", "90CW", "90CCW", "180" */
gst_element_set_enum_property(transform, "rotate", "180");
/* NOT g_object_set with integer — enum must use nick string */
```

### Flip

```c
g_object_set(transform, "flip-horizontal", TRUE, NULL);
g_object_set(transform, "flip-vertical",   FALSE, NULL);
/* bool properties — standard g_object_set */
```

### Scale via downstream capsfilter

`qtivtransform` handles flip + rotate + scale in one pass. Set output resolution via a downstream capsfilter:

```c
GstElement *capsfilter = gst_element_factory_make("capsfilter", NULL);
GstCaps *caps = gst_caps_from_string("video/x-raw,width=1920,height=1080");
g_object_set(capsfilter, "caps", caps, NULL);
gst_caps_unref(caps);
/* Link: qtivtransform → capsfilter */
gst_element_link(transform, capsfilter);
```

---

## Audio Element Chain — Multimedia C Apps

### pulsesrc for AV record

```c
GstElement *pulsesrc = gst_element_factory_make("pulsesrc", "pulsesrc");
if (!pulsesrc) { g_printerr("Failed to create pulsesrc\n"); goto cleanup; }
g_object_set(pulsesrc,
    "do-timestamp",  TRUE,    /* REQUIRED for A/V sync */
    "provide-clock", FALSE,   /* REQUIRED for A/V sync */
    "volume",        10.0,    NULL);
```

**Both `do-timestamp=TRUE` and `provide-clock=FALSE` are required** for A/V sync in AV record pipelines. Missing either causes audio/video drift.

### Audio raw caps filter (before lamemp3enc)

```c
GstElement *audio_caps = gst_element_factory_make("capsfilter", "audio_caps");
GstCaps *raw_caps = gst_caps_from_string(
    "audio/x-raw,format=S16LE,channels=1,rate=48000");
g_object_set(audio_caps, "caps", raw_caps, NULL);
gst_caps_unref(raw_caps);
```

### pulsesink for audio playback

```c
GstElement *pulsesink = gst_element_factory_make("pulsesink", "pulsesink");
if (!pulsesink) { g_printerr("Failed to create pulsesink\n"); goto cleanup; }
g_object_set(pulsesink, "volume", 10.0, NULL);
/* No enum properties — standard g_object_set only */
```

### lamemp3enc, wavenc, wavparse, mpg123audiodec, mpegaudioparse

No special API needed — standard `gst_element_factory_make()` + `gst_element_link()`. None of these have enum properties that require `gst_element_set_enum_property()`.

---

## qtdemux — Dual-Track AV Demux (Video + Audio)

The existing dynamic-pad callback pattern covers single-track video files. For AV files (video + audio), `pad-added` fires twice — once for the video pad, once for the audio pad:

```c
static void on_av_pad_added(GstElement *demux, GstPad *pad, gpointer data) {
    AppContext *ctx = (AppContext *)data;
    GstCaps   *caps = gst_pad_get_current_caps(pad);
    if (!caps) caps = gst_pad_query_caps(pad, NULL);
    GstStructure *s = gst_caps_get_structure(caps, 0);
    const gchar  *media = gst_structure_get_name(s);

    GstElement *downstream_queue = NULL;

    if (g_str_has_prefix(media, "video/x-h264") ||
        g_str_has_prefix(media, "video/x-h265")) {
        downstream_queue = ctx->video_queue;   /* queue → h264parse → decoder → display */
    } else if (g_str_has_prefix(media, "audio/mpeg") ||
               g_str_has_prefix(media, "audio/x-raw")) {
        downstream_queue = ctx->audio_queue;   /* queue → mpegaudioparse → mpg123audiodec → pulsesink */
    }

    if (downstream_queue) {
        GstPad *sink = gst_element_get_static_pad(downstream_queue, "sink");
        if (gst_pad_link(pad, sink) != GST_PAD_LINK_OK) {
            g_printerr("Failed to link demux pad\n");
        }
        gst_object_unref(sink);
    }

    gst_caps_unref(caps);
}

/* Register callback */
g_signal_connect(demux, "pad-added", G_CALLBACK(on_av_pad_added), ctx);
```

Key rules:
- Use `gst_pad_get_current_caps()` to get the negotiated caps on the newly added pad
- Route video caps (`video/x-h264`, `video/x-h265`) to the video decode chain
- Route audio caps (`audio/mpeg` for MP3, `audio/x-raw` for PCM) to the audio decode chain
- Store downstream queue elements in the app context so the callback can access them
- This queue-per-dynamic-pad pattern generalizes beyond this AV-only example: whenever `qtdemux` exposes more than one dynamic pad that runs concurrently, give each pad its own queue immediately at the link point (as `ctx->video_queue`/`ctx->audio_queue` do above), and add further queues at other decoupling boundaries within a branch as needed (e.g. before the AI conversion stage) — see the Audio Classification C-App section's `queue[1]`/`queue[2]` placement for a worked example of queue placement in a concurrent audio/video graph.

---

## qtivcomposer — Multi-Source Multimedia Composition

The existing entry covers GValue array patterns for position/dimensions in AI topologies. For multimedia composition:

- Each composer input is a **decoded NV12 video stream** (not AI metadata, not RGBA frames)
- Correct: `qtivcomposer → video/x-raw,format=NV12 → v4l2h264enc`
- Plugin placement constraints are defined in `plugin-catalog.md`.

```c
/* Set composer pad for multimedia (same GValue array API as AI topology) */
static void set_multimedia_composer_pad(GstElement *comp, const gchar *pad_name,
                                         gint x, gint y, gint w, gint h) {
    GstPad *pad = gst_element_get_static_pad(comp, pad_name);
    if (!pad) pad = gst_element_request_pad_simple(comp, pad_name);

    GValue pos = G_VALUE_INIT, dim = G_VALUE_INIT;
    gst_value_array_init(&pos, 2);
    gst_value_array_init(&dim, 2);
    GValue v = G_VALUE_INIT;
    g_value_init(&v, G_TYPE_INT);
    g_value_set_int(&v, x); gst_value_array_append_value(&pos, &v);
    g_value_set_int(&v, y); gst_value_array_append_value(&pos, &v);
    g_value_set_int(&v, w); gst_value_array_append_value(&dim, &v);
    g_value_set_int(&v, h); gst_value_array_append_value(&dim, &v);
    g_value_unset(&v);

    g_object_set_property(G_OBJECT(pad), "position",   &pos);
    g_object_set_property(G_OBJECT(pad), "dimensions", &dim);
    g_value_unset(&pos);
    g_value_unset(&dim);
    gst_object_unref(pad);
}
```

---

## C App CMake

# C App CMake

## Use This Reference For

- Generating `CMakeLists.txt` for any generated C sample app
- The app is built inside the QIMSDK source tree as described in the build docs

---

## ⚠️ CRITICAL: Always Use This Exact Template — No Variations

Every generated `CMakeLists.txt` **must** follow the standard template below exactly. Do not:
- Use `cmake_minimum_required(VERSION 3.10)` — always `3.16`
- Use `project(... C)` — always `LANGUAGES C CXX`
- Omit `pkg_check_modules(GST_JSON REQUIRED json-glib-1.0)`
- Omit `gstappsutils` from `target_link_libraries`
- Add non-standard libraries like `gstqtimlmeta`, `glib-2.0`, `gobject-2.0` directly
- Omit the `install()` target
- Omit `set(CMAKE_C_FLAGS ...)` with `-Wall -Wextra -Werror`

Any deviation from the standard template will cause build failure in the QIMSDK sample apps tree.

---

## How the Build Works

Per the QLI build documentation, custom apps are built **as subdirectories inside the QIMSDK sample apps tree**, not as standalone projects:

- **QLI:** App goes in the GStreamer sample apps source tree under `gst-sample-apps/gst-<your_app>/`; re-run the parent cmake build

This means:
- `${GST_VERSION_REQUIRED}` is set by the parent cmake
- `${GST_PLUGINS_QTI_OSS_INSTALL_BINDIR}` is set by the parent cmake (e.g. `/usr/bin`)
- `gstappsutils` library is available — built and installed before custom apps
- `gst_sample_apps_utils.h` is available at `${GST_INCLUDE_DIRS}/gstreamer-1.0/gst/sampleapps/`
- `GST_ML_TFLITE_DELEGATE_*` and other enums from `gst_sample_apps_utils.h` are accessible

---

## Standard CMakeLists.txt Template

This is the required CMake pattern for QIM SDK sample apps and relies on parent cmake variables:

```cmake
cmake_minimum_required(VERSION 3.16)
project(GST-AI-<NAME> LANGUAGES C CXX)

set(CMAKE_INCLUDE_CURRENT_DIR ON)

find_package(PkgConfig)

file(GLOB CONFIG_FILE *.json)

pkg_check_modules(GST REQUIRED gstreamer-1.0>=${GST_VERSION_REQUIRED})
pkg_check_modules(GST_JSON REQUIRED json-glib-1.0)

set(GST_EXAMPLE_BIN gst-<binary-name>)

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall -Wextra -Werror")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wno-unused-parameter")

add_executable(${GST_EXAMPLE_BIN}
  main.c
)

target_include_directories(${GST_EXAMPLE_BIN} PRIVATE
  ${GST_INCLUDE_DIRS}
  ${GST_JSON_INCLUDE_DIRS}
)

target_link_libraries(${GST_EXAMPLE_BIN} PRIVATE
  ${GST_LIBRARIES}
  ${GST_JSON_LIBRARIES}
  gstappsutils
)

install(
  TARGETS ${GST_EXAMPLE_BIN}
  RUNTIME DESTINATION ${GST_PLUGINS_QTI_OSS_INSTALL_BINDIR}
  PERMISSIONS OWNER_EXECUTE OWNER_WRITE OWNER_READ
              GROUP_EXECUTE GROUP_READ
              WORLD_EXECUTE WORLD_READ
)
```

Replace `<NAME>` with the generated CMake project name and `<binary-name>` with the generated output binary name.

---

## Notes

- `LANGUAGES C CXX` — declared even for `.c` files, matching QIMSDK sample app convention
- `${GST_VERSION_REQUIRED}` — provided by the parent cmake, not hardcoded here
- `${GST_PLUGINS_QTI_OSS_INSTALL_BINDIR}` — provided by the parent cmake
- `gstappsutils` — the shared utility library; always link it
- `json-glib-1.0` — included for consistency with sample apps; omit if app has no JSON config file
- The `gst_sample_apps_utils.h` header is included via `#include <gst/sampleapps/gst_sample_apps_utils.h>` in `main.c` — the full sub-path is required. Do NOT use the short form `<gst_sample_apps_utils.h>` and do NOT add `${GST_INCLUDE_DIRS}/gstreamer-1.0/gst/sampleapps/` to `target_include_directories` — on Ubuntu aarch64, `${GST_INCLUDE_DIRS}` already contains `gstreamer-1.0` so this would double that segment and the header would not be found.

---

## C App JSON Config

# C App JSON Config (Optional Reference)

## Use This Reference For

- Generating C apps that accept a JSON config file instead of hardcoded paths
- Understanding the JSON config pattern used in QIMSDK sample apps
- Only use this when the user explicitly asks for a config-file-driven app

---

## When to Use

QIMSDK sample apps use JSON config files at `/etc/configs/config_<name>.json` to configure paths, runtime, and source type at runtime without recompiling.

For generated apps, the default is **hardcoded paths** (simpler, no extra dependency). Use this JSON pattern only when the user asks for a configurable app or mentions a config file.

---

## Required Dependency

Add `json-glib-1.0` to CMakeLists.txt (see `c-app-development.md`).

Add to includes in main.c:
```c
#include <json-glib/json-glib.h>
```

---

## Standard JSON Config File Format

Example `config_detection.json`:
```json
{
  "file-path": "/etc/media/video.mp4",
  "model": "/etc/models/yolox_w8a8.tflite",
  "labels": "/etc/labels/yolov8.json",
  "runtime": "dsp",
  "ml-framework": "tflite",
  "yolo-model-type": "yolox",
  "threshold": 51
}
```

---

## Standard JSON Parsing Pattern

```c
static gboolean
parse_config (const gchar *config_file, gchar **file_path,
    gchar **model_path, gchar **labels_path)
{
  JsonParser *parser = json_parser_new ();
  GError *error = NULL;

  if (!json_parser_load_from_file (parser, config_file, &error)) {
    g_printerr ("Failed to parse config: %s\n", error->message);
    g_error_free (error);
    g_object_unref (parser);
    return FALSE;
  }

  JsonNode *root = json_parser_get_root (parser);
  if (!JSON_NODE_HOLDS_OBJECT (root)) {
    g_printerr ("Invalid config format\n");
    g_object_unref (parser);
    return FALSE;
  }

  JsonObject *obj = json_node_get_object (root);

  if (json_object_has_member (obj, "file-path"))
    *file_path = g_strdup (json_object_get_string_member (obj, "file-path"));

  if (json_object_has_member (obj, "model"))
    *model_path = g_strdup (json_object_get_string_member (obj, "model"));

  if (json_object_has_member (obj, "labels"))
    *labels_path = g_strdup (json_object_get_string_member (obj, "labels"));

  g_object_unref (parser);
  return TRUE;
}
```

---

## Default Config File Path

QIMSDK apps default to `/etc/configs/config_<name>.json` on device.

In main():
```c
const gchar *config_file = argc > 1 ? argv[1] : DEFAULT_CONFIG_FILE;
if (!parse_config (config_file, &file_path, &model_path, &labels_path)) {
  ret = EXIT_FAILURE;
  goto done;
}
```

---

## C App Error Handling

# Error Handling

## Use This Reference For

- Robust C/C++ sample app generation
- Pipeline lifecycle handling in production-style apps

## Mandatory Bus Handlers

Include handlers for:

- `GST_MESSAGE_ERROR`
- `GST_MESSAGE_WARNING`
- `GST_MESSAGE_EOS`
- `GST_MESSAGE_STATE_CHANGED` (pipeline-scoped)

## Lifecycle Rules

- Handle Ctrl+C (SIGINT) by sending EOS where appropriate.
- Transition pipeline to `GST_STATE_NULL` on shutdown.
- Unref elements and loop resources in teardown.

## Multi-Stream Batch Inference C-App (qtibatch + qtimldemux, File Source, Wayland Display)

### What this pattern is for

Use when the request asks for **multi-stream batched inference** — e.g. "12-stream multi-batch inference", "batch inference from file", or any C-app where multiple streams are grouped into batches for a single `qtimltflite` instance per group. The defining elements are `qtibatch` (aggregates N streams into one batched buffer) and `qtimldemux` (splits batch inference output back to per-stream results). This pattern replaces the naive approach of one `qtimltflite` per stream.

### Key structural rules

1. **Group streams into batch groups**: `NUM_BATCH_GROUPS = NUM_STREAMS / BATCH_SIZE`. Each batch group shares one `qtibatch`, one `qtimlvconverter`, one `qtimltflite`, and one `qtimldemux`.

2. **Per-stream tee splits into two branches**:
   - Passthrough: `tee → queue → qtivcomposer` (raw video, auto-requests composer sink pad)
   - AI: `tee → queue → qtibatch[b]` (b = stream_index / BATCH_SIZE)

3. **Batch inference chain** (one per batch group):
   ```
   qtibatch[b] → queue → qtimlvconverter[b] → queue → qtimltflite[b] → queue → qtimldemux[b]
   ```

4. **Post-processing** (one per stream, fed by qtimldemux):
   ```
   qtimldemux[b] → queue → qtimlpostprocess[i] → queue → qtivcomposer
   ```
   - `qtimlpostprocess` outputs a rendered frame (not `text/x-raw`) — no `qtimetamux`/`qtivoverlay` used here
   - **No capsfilter between `qtimlpostprocess[i]` and `qtivcomposer`** — device-verified: pinning `width`/`height` there fails caps fixation (`Fixated width in filter caps is not supported with current post-process type!`) regardless of `format=`. Link directly into the composer sink pad; the sink-pad `dimensions` sizes the tile.
   - This feeds a **second** composer sink pad for the same stream (mask overlay)

5. **qtivcomposer has 2 sink pads per stream**: even index = raw passthrough, odd index = mask (no capsfilter). Set position/dimensions on both to the same grid cell. The mask pad is at `i * 2 + 1`, passthrough at `i * 2`.

6. **qtimlvconverter per batch group uses default mode** (image-batch-non-cumulative) — no mode property needed.

7. **HTP distribution**: round-robin across available HTP cores — `htp_id = b % htp_count` where `htp_count = access("/dev/fastrpc-cdsp1") == 0 ? 1 : 2`.

8. **File descriptor limit**: with 12 streams, raise `RLIMIT_NOFILE` to 4096 at startup via `setrlimit`.

9. **Dynamic pad**: `on_pad_added` checks caps contain "video" before linking; links `qtdemux[i]` video pad → `stream_queue[i][0]`.

### Element naming conventions

- Per-stream: `filesrc_N`, `qtdemux_N`, `h264parse_N`, `v4l2h264dec_N`, `nv12_caps_N`, `stream_tee_N`, `qtimlpostproc_N`, `sq_N_Q` (stream queues)
- Per-batch-group: `qtibatch_B`, `qtimlvconv_B`, `qtimltflite_B`, `qtimldemux_B`, `bq_B_Q` (batch queues)
- Queue slots per stream: `[0]`=post-demux (pad-added target), `[1]`=after-decoder, `[2]`=tee-passthrough, `[3]`=tee-AI, `[4]`=postproc-to-composer (no capsfilter)
- Queue slots per batch group: `[0]`=after-qtibatch, `[1]`=after-qtimlvconverter, `[2]`=after-qtimltflite

### Composer pad setup

```c
/* Two pads per stream: passthrough (even) and mask (odd), same position */
snprintf(name, sizeof(name), "sink_%d", i * 2);       /* passthrough */
set_composer_pad(composer, name, x, y, CELL_W, CELL_H);
snprintf(name, sizeof(name), "sink_%d", i * 2 + 1);   /* mask */
set_composer_pad(composer, name, x, y, CELL_W, CELL_H);
```

### Link order (critical — do NOT deviate)

There are exactly 4 loops in this order. Do NOT split them differently.

**Loop 1 — ONE single per-stream loop covering ALL per-stream links:**
```c
for (i = 0; i < NUM_STREAMS; i++) {
    b = i / BATCH_SIZE;
    gst_element_link(filesrc[i], qtdemux[i]);                                      // static filesrc→qtdemux
    gst_element_link_many(sq[i][0], h264parse[i], v4l2h264dec[i],
                          nv12_caps[i], sq[i][1], tee[i], NULL);                   // decode chain
    gst_element_link_many(tee[i], sq[i][2], composer, NULL);                       // passthrough → composer (gets sink_2i)
    gst_element_link_many(tee[i], sq[i][3], qtibatch[b], NULL);                    // AI branch → qtibatch
    gst_element_link_many(qtimldemux[b], sq[i][4], postproc[i],
                          composer, NULL);                                          // demux out → composer (gets sink_2i+1), no capsfilter
}
```
**Why one loop:** `qtivcomposer` auto-assigns sink pad numbers in link order. For stream 0, passthrough gets `sink_0`, mask gets `sink_1`. For stream 1, passthrough gets `sink_2`, mask gets `sink_3`. `set_composer_pad` then sets positions on `sink_0`(passthrough_0), `sink_1`(mask_0), `sink_2`(passthrough_1), etc. If you split into separate loops — all 12 passthrough first then all 12 mask — passthrough gets `sink_0..11` and mask gets `sink_12..23`, which mismatches the position assignments → blue tiles for half the streams.

**Loop 2 — batch chain (separate loop, after Loop 1):**
```c
for (b = 0; b < NUM_BATCH_GROUPS; b++) {
    gst_element_link_many(qtibatch[b], bq[b][0], qtimlvconv[b],
                          bq[b][1], qtimltflite[b], bq[b][2], qtimldemux[b], NULL);
}
```

**Loop 3 — sink chain (single link):**
```c
gst_element_link_many(composer, comp_queue, waylandsink, NULL);
```

**Loop 4 — pad-added callbacks (separate loop AFTER all linking):**
```c
for (i = 0; i < NUM_STREAMS; i++) {
    pad_data[i].queue        = sq[i][0];  /* static — must outlive create_pipe() */
    pad_data[i].stream_index = i;
    g_signal_connect(qtdemux[i], "pad-added", G_CALLBACK(on_pad_added), &pad_data[i]);
}
```
Note: `pad_data` MUST be declared `static` — it must outlive `create_pipe()` since `pad-added` fires asynchronously during preroll. A non-static local goes out of scope and causes use-after-stack-free (garbage stream indices, NULL queue pointers).

**Loop 5 — `set_composer_pad` (MUST be after Loop 1, after pads exist):**
```c
for (i = 0; i < NUM_STREAMS; i++) {
    col = i % GRID_COLS; row = i / GRID_COLS;
    x = col * CELL_W;    y = row * CELL_H;
    snprintf(name, sizeof(name), "sink_%d", i * 2);     /* passthrough */
    set_composer_pad(composer, name, x, y, CELL_W, CELL_H);
    snprintf(name, sizeof(name), "sink_%d", i * 2 + 1); /* mask, no capsfilter */
    set_composer_pad(composer, name, x, y, CELL_W, CELL_H);
}
```
**Critical:** `qtivcomposer` only creates sink pads when `gst_element_link` is called. Calling `set_composer_pad` before the link loops means the pads don't exist yet — `gst_element_get_static_pad` returns NULL, positions are never set, all streams render at position 0,0 → only 1 tile visible.

### Properties skeleton

```c
/* v4l2h264dec IO modes (per stream) */
gst_element_set_enum_property(v4l2h264dec[i], "capture-io-mode", "dmabuf");
gst_element_set_enum_property(v4l2h264dec[i], "output-io-mode",  "dmabuf");

/* NV12 caps after decoder */
caps = gst_caps_new_simple("video/x-raw", "format", G_TYPE_STRING, "NV12", NULL);
g_object_set(nv12_caps[i], "caps", caps, NULL);

/* qtimlpostprocess per stream */
module_id = get_enum_value(qtimlpostproc[i], "module", "yolov8");
g_object_set(qtimlpostproc[i], "module", module_id, "labels", LABELS_PATH,
             "settings", "{\"confidence\": 51.0}", "results", 10, NULL);
/* No capsfilter after qtimlpostproc[i] — it links directly into the composer;
   pinning width/height here fails caps fixation regardless of format. */

/* qtimltflite per batch group */
snprintf(delegate_str, sizeof(delegate_str),
    "QNNExternalDelegate,backend_type=htp,htp_device_id=(string)%u,"
    "htp_performance_mode=(string)2,log_level=(string)1;", b % htp_count);
delegate_options = gst_structure_from_string(delegate_str, NULL);
g_object_set(qtimltflite[b], "model", MODEL_PATH,
             "delegate", GST_ML_TFLITE_DELEGATE_EXTERNAL, NULL);
g_object_set(qtimltflite[b], "external-delegate-path", "libQnnTFLiteDelegate.so",
             "external-delegate-options", delegate_options, NULL);
gst_structure_free(delegate_options);
```

### What NOT to do

- Do NOT use `qtimetamux`/`qtivoverlay` — this pattern uses direct-to-composer (Topology B), no capsfilter after `qtimlpostprocess`
- Do NOT create one `qtimltflite` per stream — batch groups share one instance
- Do NOT omit `qtimldemux` — inference output is batched and must be demuxed before per-stream postprocess
- Do NOT use `qtimlpostprocess` with `text/x-raw` output — it must output a rendered frame for direct composer feed
- **Do NOT use `gst_element_request_pad_simple` on `qtimldemux` to get `src_N` pads explicitly.** Always use `gst_element_link_many(qtimldemux[b], sq[i][4], postproc[i], composer, NULL)` which auto-requests src pads in order. Explicit `src_N` pad requests produce blue tiles — verified on hardware.
- **Do NOT call `set_composer_pad` before linking.** `qtivcomposer` only creates sink pads when `gst_element_link` is called — pads don't exist before that. Call `set_composer_pad` in a separate loop AFTER Loop 4 (pad-added callbacks), not before the link loops.
- **Do NOT split per-stream links across multiple loops.** The entire per-stream link sequence (filesrc, decode, passthrough, AI branch, demux output) MUST be in ONE loop as shown in Loop 1. Separate loops cause wrong composer pad assignment → blue tiles.

## Multi-Stream Per-Stream Inference C-App (non-batched, File Source, Wayland Grid)

### What this pattern is for

Use when the request asks for **multi-stream inference with one independent inference call per stream** — e.g. "32-stream multistream inference", "run detection independently on each stream (non-batched)", explicitly contrasted with the `qtibatch` batch variant. There is NO `qtibatch`/`qtimldemux`; each stream owns its full `qtimlvconverter → qtimltflite → qtimlpostprocess` chain.

### Overlay topology — use Topology A per stream, feeding one composer sink pad

For a **detection or classification** module (`yolov8`, `yolox`→`yolov8`, `mobilenet`, `qfd`, etc.), each stream is a self-contained Topology A chain whose overlaid NV12 video then enters the grid composer on ONE sink pad:

```
per stream i:
  filesrc → qtdemux → h264parse → v4l2h264dec → NV12 caps → queue → tee
    tee (passthrough) → queue → qtimetamux
    tee (AI)          → queue → qtimlvconverter → queue → qtimltflite
                        → queue → qtimlpostprocess → text/x-raw → queue → qtimetamux
    qtimetamux → queue → qtivoverlay → queue → qtivcomposer(sink_i)
qtivcomposer → queue → waylandsink
```

- **ONE composer sink pad per stream** (`sink_i`), not two. The overlay is burned in by `qtivoverlay` before the composer, so there is no separate mask pad.
- Link postprocess→queue with `gst_element_link_filtered(postproc, queue, gst_caps_from_string("text/x-raw"))` — the same rule as single-stream Topology A.
- Grid cell layout via `set_composer_pad(composer, "sink_i", col*cell_w, row*cell_h, cell_w, cell_h)` computed from the stream count (1→1x1, 2-4→2x2, 5-9→3x3, 10-16→4x4, 17-25→5x5, 26-32→6x6).
- Raise `RLIMIT_NOFILE` (e.g. 10000) at startup for high stream counts, and the launcher must still run `ulimit -n 10000` before the binary (device-verified requirement for multistream).

This is the device-verified shape for the non-batched multistream detection app. The **batch** variant above (RGBA direct-to-composer, two pads/stream) is a different pattern gated on `qtibatch`; do not mix them.

### Why NOT the BGRA-mask topology

The deprecated `qtimlvdetection`/`qtimlvpose`/`qtimlvclassification` wrappers rendered a **BGRA** mask that fed `qtivcomposer` directly. The current `qtimlpostprocess` does **not** — its src pad advertises only `video/x-raw,format={RGBA,RGBx}`, `text/x-raw`, and `neural-network/tensors` (verify with `gst-inspect-1.0 qtimlpostprocess`). A `capsfilter` set to **BGRA** after `qtimlpostprocess` therefore fails to link with "no link possible from qtimlpostprocess to <capsfilter>" at pipeline build (device-verified). Relabeling that capsfilter to **RGBA** does not fully fix it either if `width`/`height` are still pinned — that fails differently, with `Fixated width in filter caps is not supported with current post-process type!` (also device-verified). The correct fix is to **omit the capsfilter after `qtimlpostprocess` entirely** for a mask-into-composer feed — let caps negotiate and size the tile via the composer sink-pad `dimensions` — and only segmentation/depth/super-resolution modules actually negotiate a video mask there; detection/classification metadata must go the `text/x-raw → qtimetamux → qtivoverlay` route.

## Runtime Safety

- Validate element creation before linking.
- Validate state transitions.
- Do not continue running after unrecoverable pipeline error.

## Anti-Patterns

- Missing EOS handler in sample app
- Missing state transition diagnostics
- Partial teardown that leaks pipeline resources
