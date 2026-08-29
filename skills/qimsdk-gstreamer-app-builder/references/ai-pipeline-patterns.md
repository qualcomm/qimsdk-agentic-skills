# AI Pipeline Patterns

## Purpose

Canonical AI inference topologies and verified AI pipeline templates.

## Load When

Load for any video/audio AI inference, postprocess, metadata, overlay, daisy-chain, multistream, batching, or AI wall request.

## This File Owns

- AI stage order and topology selection
- Topology A metadata overlay and Topology B composer flows
- Daisy-chain, multistream, audio AI, batching, and zero-copy AI patterns
- Known-good AI pipeline templates

## This File Does Not Own

- Plugin property tables; use plugin-catalog.md
- Generic source/sink chains; use source-sink-patterns.md
- Generic queue/tee rules; use pipeline-utilities.md
- Topology B (direct-to-composer) preference rule and per-module category table; use plugin-catalog.md → "Preference: reuse an existing qtivcomposer"
- C code for both topologies; use c-app-development.md

---


---

## AI Request Routing

# Building AI Pipelines

## Purpose

This file is the routing guide for AI requests. It tells you **which template family to use** so the final output is complete and in-scope.

## Canonical AI Stage Order

Always reason in this order:

1. Source
2. Preprocess
3. Inference
4. Postprocess
5. Metadata use (overlay, compose, transport, storage)

## Request Routing

### Route A: Simple Single-Stream AI Pipeline

Use this route when the user asks for single-stream detection/classification/segmentation/pose with no cascade requirement.

- Use this file with `source-sink-patterns.md`, `pipeline-utilities.md`, `plugin-catalog.md`, and `artifact-contract.md` when generating an artifact.
- Do not add daisy-chain, multistream, or zero-copy complexity.
- Pose estimation (HRNet and other documented top-down pose models) is eligible for Route A: these models can run either directly on full frames (one stage, simpler) or on a detector-cropped ROI (an extra stage, sharper keypoints on small/distant subjects) — see `model-catalog.md`'s HRNetPose row. Match the topology to what the request actually describes; do not default to the two-stage cascade in `## 2b) Daisy-Chain Pose Estimation` just because the model is HRNet. Use `## 2b`'s daisy-chain form only when the request explicitly asks for detector→ROI pose (or names both a person-foot detector and a pose model).

### Route A3: Audio Classification Pipeline (YAMNet / Audio + Video)

Use this route when the user asks for audio classification with YAMNet or any pipeline that processes audio from a video file, runs audio feature extraction and ML inference, and overlays the audio classification result on the video.

**This is a unique topology** — it does NOT use `tee`, `qtimetamux`, or `qtivoverlay`. Instead:
- A single `filesrc ! qtdemux name=demux` provides both video and audio pads
- Video path: `demux. ! queue ! h264parse → v4l2h264dec → NV12 → queue → qtivcomposer name=mixer`
- Audio path: `demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! audiobuffersplit → qtimlaconverter → qtimltflite → qtimlpostprocess module=yamnet → queue → mixer.` (no capsfilter after postprocess)
- Use **Template 12** from `ai-pipeline-patterns.md` as the definitive audio classification pipeline.

**Critical differences from video-only pipelines:**
- `waylandsink fullscreen=true` — do NOT add `sync=true` for audio classification
- `qtimlaconverter feature=lmfe params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"` for YAMNet; `feature=raw` only for raw-waveform-input models (no params needed)
- `audiobuffersplit output-buffer-size=31200` for YAMNet (15600 samples × 2 bytes)
- `qtimlpostprocess module=yamnet` outputs a rendered overlay panel — do NOT insert a capsfilter after postprocess; pinning `width`/`height` (regardless of format) fails caps fixation
- `qtivcomposer name=mixer` (NOT `qtimetamux`/`qtivoverlay`) merges the audio overlay onto the video
- `demux.` (named pad reference) is used to access both qtdemux output pads
- No `tee` element needed — qtdemux naturally demuxes to separate pads

Use Template 12 in this file with `plugin-catalog.md` and `artifact-contract.md` when generating an artifact.

### Route A2: Super Resolution Pipeline

Use this route when the user asks for super resolution, upscaling, or QuickSRNet.

- Use this file with `plugin-catalog.md`, `pipeline-utilities.md`, and `artifact-contract.md` when generating an artifact.
- Use Template 10 from `ai-pipeline-patterns.md` as the definitive SR display pipeline.
- **For file output:** Use Template 10b from `ai-pipeline-patterns.md` — add `video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink` directly after `qtivcomposer`; plugin placement constraints are defined in `plugin-catalog.md`; no `sync=false` on filesink.
- **Do NOT use** `qtimetamux` or `qtivoverlay` — SR uses pure Topology B (qtivcomposer).
- `module=srnet` outputs `video/x-raw,format=RGB` (no dimensions).
- NO queue between `tee` and `qtimlvconverter` in the SR branch.
- `external-delegate-path=libQnnTFLiteDelegate.so` (bare filename, not full path).
- `external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` must be included.

### Route B2: Three-Stage Gesture Recognition Pipeline

Use this route when the user asks for palm detection → hand landmark → gesture classification (three-stage gesture recognition with dual-model Stage 3: embedder + classifier).

- Use Template 11 in this file with `artifact-contract.md` when generating an artifact.
- **Do NOT use** the standard two-stage daisy-chain template — gesture recognition uses a special topology
- `delegate=gpu` for all stages (not external delegate)
- `qtimetatransform module=roi-palmd` is inserted on the main path between `metamux_1` and `t_split_2`
- Stage 2 inference output splits via `t_split_4` into hlandmark branch AND tensor+gesture-inference branch
- Stage 3 uses two sequential `qtimltflite` elements (embedder + classifier) with NO queue between them
- Stage 3 final postprocess uses `module=mobilenet`
- Only TWO qtimetamux elements total (metamux_1 for palm stage, metamux_2 for landmark+gesture merge)
- `bbox-stabilization=true` on Stage 1 postprocess
- **Stage 2 qtimlvconverter: `mode=roi-batch-non-cumulative`** (NOT `roi-batch-cumulative` — gesture uses non-cumulative mode; DO NOT add `image-disposition=centre` for gesture Stage 2). `roi-batch-non-cumulative` processes each ROI immediately without waiting for a full batch.

### Route B3: Face Recognition Daisy-Chain Pipeline

Use this route when the user asks for **face recognition** (identifying/verifying a specific person, not just locating faces), or names any of the three face-recognition-chain models by display name or filename: `face_det_lite` (Lightweight-Face-Detection), `facemap_3dmm` (Facial-Landmark-Detection), `face_attrib_net` / `Facial-Attribute-Detection` (face recognition/embedding). Trigger on any one of these model names even if the other two aren't named — the three-stage chain is implied because these models only function together.

**Do not confuse with plain face detection** — a request for just "detect faces" / "draw boxes around faces" with only `face_det_lite` named and no recognition/identity language uses **Route A** (single-stream detection) with **Template 14**, not this route.

- Use **Template 17** in this file with `model-catalog.md` (Face Detection / Recognition category — resolves `module=` per stage) and `artifact-contract.md` when generating an artifact.
- Three mandatory stages in this exact order — **do not reorder even if the user lists models in a different order in the prompt**: (1) face detection (`qfd`), (2) facial landmark/3DMM pose (`lite-3dmm`), (3) face recognition/classification (`qfr`). Recognition must run on the landmark-aligned crop, not directly on the raw detection ROI — running `qfr` at stage 2 silently degrades recognition accuracy even though the pipeline builds and runs.
- Each stage has its own labels/settings files — they are never shared across stages. If the user supplies filenames that don't obviously map to detection vs. landmark vs. recognition, ask which file is for which stage rather than guessing an assignment.
- File names are not fixed to any particular convention (`face_detection.json`, `face_recognition.json`, etc. are the reference-app's names, not required strings) — accept whatever filenames/paths the user provides and slot them into the stage the content/purpose indicates (e.g. a `settings` file paired with the recognition model is the recognition stage's settings, regardless of its literal name).

### Route B4: Face Registration (Enrollment) Pipeline

Use this route when the user asks to **register**, **enroll**, or **capture** a face for later recognition (e.g. "register a face," "enroll a person," "capture face data for recognition") — this is a distinct request from Route B3's runtime recognition pipeline, even though it shares the same detection/recognition models.

**Flag immediately if the user asks for a `gst-launch-1.0` registration pipeline: it is not possible.** Registration needs the `capture-image` action signal fired on demand, five times, while watching a live preview to catch specific head angles — `gst-launch-1.0` has no interactive console to trigger action signals mid-run; it only builds/runs/tears down a static graph. `gst-pipeline-app` is the only element in the QIM SDK toolchain that exposes this. If a request insists on `gst-launch-1.0` specifically, tell the user why it can't be done and offer the `gst-pipeline-app` form (Template 18) instead — do not attempt to fake interactivity into a `gst-launch-1.0` artifact.

**This is not a variant of Template 17.** Registration is an interactive **capture/enrollment tool pipeline** that produces the artifact (`faceN.bin` + a database entry) Template 17's Stage 3 (`qfr`) needs to match anyone — without it, Template 17 runs but never returns a positive identity match.

- Use **Template 18** in this file with `model-catalog.md` (Face Detection / Recognition category) and `artifact-contract.md` when generating an artifact.
- Only two of the three chain models are used: Stage 1 detection (`qfd`) and the recognition model's raw tensor output (`qtimlvconverter mode=roi-batch-cumulative` → `qtimltflite` on the face_attrib_net model) — **no `qtimlpostprocess module=qfr` stage**; output is written directly to `multifilesink` as raw tensor bins, not classified.
- The facial-landmark (`lite-3dmm`) stage is **not required for registration** — only for runtime recognition (Template 17).
- Camera source only (`qticamsrc video_0::type=video` for preview + `camsrc.image_1` pad for capture) — file/RTSP sources are not applicable since registration requires a live subject to prompt for head angles.
- Output is 5 tensor bins (`tensor_0.bin`...`tensor_4.bin`, one per head angle: front, left, right, up, down) via `multifilesink location=/etc/data/tensor_%d.bin`.
- Post-pipeline steps (outside the generated artifact, must be surfaced to the user in the README): pull the 5 tensor bins off-device, run `facedb.py "<Name>" 512 32 tensor_0.bin ... tensor_4.bin` on a host machine to produce `face.bin`, push it back to the device as `/etc/data/faceN.bin`, then add a matching `{"id": N, "database": "/etc/data/faceN.bin"}` entry to the Template 17 Stage 3 settings file and a `{"id": N, "label": "<Name>"}` entry to the Stage 3 labels file.

### Route B: Daisy-Chain AI Pipeline

Use this route only when the user explicitly asks for cascaded stages (for example stage 1 and stage 2).

- Use this file with `pipeline-utilities.md`, `source-sink-patterns.md`, and `artifact-contract.md` when generating an artifact.
- Ensure two explicit inference stages and ROI handoff semantics.

### Route C: Multistream AI Pipeline

Use this route when the user explicitly asks for multiple simultaneous streams or composition.

- Use this file with `source-sink-patterns.md`, `pipeline-utilities.md`, and `artifact-contract.md` when generating an artifact.
- Compose at the end.
- For AI wall with 4 streams: use the named element declaration pattern from `ai-pipeline-patterns.md` and Template 9 in `ai-pipeline-patterns.md`.
- For **three-stream dual file output** (single ISP camera → composed MP4 + AI-only MP4, no display): use **Template 5b** in `ai-pipeline-patterns.md` — the `out_tee` must be placed AFTER `qtivoverlay` (not before), and both filesinks require `sync=false`.

### Route D: C/C++ App Request

Use this route when the user asks for a sample app or application code.

- Use `c-app-development.md` plus the pipeline reference that matches the task.
- **Event encoder / conditional recording** — trigger on any of: "event encoder", "record on detection", "save clip when X detected", "start recording when/once X appears", "only save footage/video when something happens", "trigger-based/motion-triggered/smart recording", or any request where recording/saving to a file is conditioned on an AI detection result rather than running continuously from start. This applies even if it's only part of a larger request (e.g. "detect people and show them on screen, and also save a clip when someone shows up"). Do not improvise a detection-trigger mechanism. Use `c-app-development.md`'s "Dual-Pipeline Event Encoder" section — dual pipeline (main detection+display, recording kept in `GST_STATE_NULL` until triggered), detection tee feeding two `qtimlpostprocess` instances (one to `qtivcomposer` overlay, one to a metadata `appsink`), and metadata-based (not `GstVideoRegionOfInterestMeta`-based) label counting to start/stop recording.
- **Audio classification C-app** ("audio classification", "classify audio", "YAMNet", "sound classification", any C-app that includes audio AI inference from a file or microphone): use `c-app-development.md`'s "Audio Classification C-App — Exact Structure" section. Do NOT invent a caps-dispatching `on_pad_added` for qtdemux — use dual blind-link signal connections exactly as documented.
- **Metadata parser / appsink bounding box extraction C-app** ("metadata parser example", "parse metadata", "parse inference metadata", "appsink metadata", "programmatic bounding box extraction", "extract detection results in code", "count objects", or any C-app request where the user wants to read or process inference results programmatically — not just display them): use `c-app-development.md`'s "Metadata Parser C-App" section. Do NOT generate a plain detection overlay without the `appsink` metadata-parsing branch. The defining topology: a `detection_tee` after `qtimlpostprocess` feeds two `qtimlpostprocess` instances — one for display overlay (Topology A via `qtimetamux`/`qtivoverlay`), one for `appsink` with `gst_value_deserialize` bounding-box parsing.
- **SmartCodec C-app** ("smartcodec", "smart codec", "smart encoder", "smart video encode", "adaptive encode", "AI-driven encoding", or any C-app that uses `qtismartvencbin`): use `c-app-development.md`'s "SmartCodec C-App" section. Do NOT generate a plain detection overlay — this pattern requires `qtismartvencbin` with three sink pads (`sink`, `sink_ctrl`, `sink_ml`), manual pad links via `gst_element_get_static_pad`/`gst_pad_link`, encode output chain (`v4l2h264enc → h264parse → mp4mux → filesink`), and a `detection_tee` splitting AI output to both `sink_ml` and the display overlay.
- **Multi-stream batch inference C-app** ("multi-stream batch", "multistream batch", "12-stream batch", "multi-batch inference", "batch inference C app", "batch_4", "qtibatch", "batched inference", "multi stream multi batch", or any C-app that groups multiple streams into batches for shared inference instances — the defining characteristic is N streams feeding into fewer shared `qtimltflite` instances via `qtibatch`): use `c-app-development.md`'s "Multi-Stream Batch Inference C-App" section. Do NOT generate a naive per-stream `qtimltflite` approach. Key elements required: `qtibatch` per batch group, shared `qtimlvconverter`/`qtimltflite`/`qtimldemux` per batch group, per-stream `qtimlpostprocess` outputting directly to `qtivcomposer` with no capsfilter (`qtimetamux`/`qtivoverlay` not used), two composer sink pads per stream (even=passthrough, odd=mask), and the exact 4-loop link structure documented in that section.
- **Multi-stream batch inference gst-launch** ("12 stream object detection gst-launch", "12-stream gst-launch batch", "multistream batch gst-launch", or any gst-launch request with ~8+ streams running the same detector/classifier where per-stream `qtimltflite` would exhaust HTP memory — the defining characteristic is N streams needing batch grouping via `qtibatch`): use **Template 17** in this file, and read its "How the batch pattern works" section — do not just copy pad numbers. The shape is: B streams per `qtibatch` → one shared `qtimlvconverter`/`qtimltflite`/`qtimldemux` per group → per-stream `qtimlpostprocess` → `qtivcomposer` (no `qtimetamux`/`qtivoverlay`). Two `qtivcomposer` sink pads per stream (passthrough video + detection overlay at the same position). Link all passthroughs first then all overlays via unqualified `mixer.`, and declare `qtivcomposer` LAST so link order assigns the pad numbers. Requires a batch-N model (`..._batch_N.tflite`) and `htp_device_id` cycled across groups.

### Route E: Zero-Copy Split Pipeline

Use this route when the user explicitly asks for process boundaries, sockets, or zero-copy transport.

- Use `pipeline-utilities.md`, `source-sink-patterns.md`, and `artifact-contract.md` when generating an artifact.

## AI Pipeline Templates

# AI Pipeline Templates

## Use This Reference For

- Building complete single-stream QIM SDK AI pipelines with **`gst-launch-1.0`**
- Reusing canonical AI stage templates for detection, classification, or segmentation

> **Two valid overlay topologies exist for both gst-launch and C apps:**
> - **Topology A** (this file): `qtimetamux → qtivoverlay` — standard annotation overlay; default for single-stream requests
> - **Topology B**: `qtivcomposer` (no capsfilter after `qtimlpostprocess`) — side-by-side, PiP, alpha blend, multi-stream

## Canonical AI Flow

QIM SDK documentation describes a five-stage AI pipeline:

1. Data source
2. AI preprocessing
3. AI inference
4. Post-processing
5. Use AI metadata

For video AI with overlay, use this canonical shape:

```text
source → decode/format → tee
                     ├─ main branch → qtimetamux
                     └─ AI branch → qtimlvconverter → qtimltflite or qtimlqnn → qtimlpostprocess → qtimetamux
qtimetamux → qtivoverlay → sink
```

Minimum branch requirements:

- main branch from `tee` must feed `qtimetamux`
- AI branch from `tee` must end in `qtimetamux`
- `qtimetamux` must feed overlay/display or next stage

## Core AI Plugins

- `qtimlvconverter` — converts frames or ROIs into model-ready tensors
- `qtimltflite` — runs LiteRT/TFLite models; examples often use the external QNN delegate on HTP
- `qtimlqnn` — runs QNN-based model pipelines
- `qtimlpostprocess` — converts output tensors into detections, labels, masks, or other structured results
- `qtimetamux` — synchronizes ML metadata with the main media buffer
- `qtivoverlay` — draws boxes, labels, masks, or other AI results onto video

## Common Inference Pattern

Documentation and examples repeatedly use this pattern for single-stream detection:

```text
filesrc/qtiqmmfsrc/rtspsrc → decode to NV12 → queue → tee
tee main branch (no queue) → qtimetamux
tee AI branch → queue → qtimlvconverter → queue → qtimltflite/qtimlqnn → queue → qtimlpostprocess → text/x-raw → queue → qtimetamux
qtimetamux → qtivoverlay → sink
```

Use this pattern for simple single-stream detection or classification requests unless the user explicitly asks for a more advanced shape such as daisy chaining, multistream composition, or cross-process transport.

## Complete Command Template (Single Stream)

Use this skeleton and fill placeholders explicitly:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  <INFER_ELEMENT_STAGE1> ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE_STAGE1> labels=<LABELS_STAGE1> \
  ! text/x-raw ! queue ! meta_mux.
```

**Key structural rules:**
- Insert `queue` between NV12 caps and `tee` — required for buffer flow before branch split
- Main (passthrough) branch: `tee name=t ! qtimetamux name=...` — NO queue between tee and mux on the passthrough branch
- AI branch: `t. ! queue ! qtimlvconverter ! queue ! <infer> ! queue ! qtimlpostprocess ... ! text/x-raw ! queue ! mux.`
- Four `queue` elements inside the AI branch: after tee, after converter, after infer element, and after postprocess before mux
- `waylandsink` always requires `fullscreen=true sync=true` unless user explicitly overrides

`<INFER_ELEMENT_STAGE1>` should be one of:

- `qtimltflite ...`
- `qtimlqnn ...`

If exact flags are unknown, keep placeholder tokens and state that user must fill them.

`qtimlpostprocess settings=...` is optional; include it only when the user explicitly asks for postprocess config/settings or threshold tuning.

## Delegate and Model Notes

- LiteRT examples commonly use `qtimltflite`
- External delegate examples commonly set:
  - `delegate=external`
  - `external-delegate-path=libQnnTFLiteDelegate.so`
  - `external-delegate-options="QNNExternalDelegate,backend_type=htp..."`
- Do not invent delegate flags; use placeholders if exact values are not available in this skill references set.

## Metadata Usage

The final AI stage is not always display. Metadata can also be:

- overlaid on video with `qtivoverlay`
- passed downstream for RTSP/WebRTC/file recording
- serialized to backend systems
- consumed by appsink or other integration points

## Two-Stream Side-by-Side Topology (Single Camera Source)

When a user requests **raw passthrough + AI overlay composed side-by-side** from a single camera, use the **two-tee topology**:

```text
qtiqmmfsrc → NV12 caps → queue → tee name=t_src
t_src raw branch  → queue → qtivcomposer (sink_0)
t_src AI branch   → queue → tee name=t
  t main branch   → queue → qtimetamux (obj_mux)
  t AI branch     → queue → qtimlvconverter → queue → qtimltflite → queue → qtimlpostprocess → text/x-raw → queue → qtimetamux (obj_mux)
qtimetamux → queue → qtivoverlay → queue → qtivcomposer (sink_1)
qtivcomposer → queue → waylandsink fullscreen=true sync=true
```

**Critical rules for this topology:**
- Two `tee` elements are REQUIRED:
  1. `tee name=t_src` — separates the raw passthrough branch (→ comp.sink_0) from the AI branch
  2. `tee name=t` — within the AI branch, separates the qtimetamux passthrough from the actual AI inference
- The AI branch uses standard Topology A (qtimetamux + qtivoverlay) BEFORE entering the composer
- `comp.sink_1` receives the overlaid video (NV12) from `qtivoverlay`, NOT raw tensors or text/x-raw
- `comp.sink_0` receives the raw NV12 video from `t_src`
- `qtiqmmfsrc` uses `camera=0 name=camsrc` in generated pipelines
- Composer pad properties use `position="<x, y>"` and `dimensions="<width, height>"` syntax

## Three-Stream Topology (Single Camera, Display + File Encode)

When a user requests **raw passthrough + AI overlay composed side-by-side on display AND the AI overlay encoded to file** from a single camera, use the **three-tee topology**:

```text
qtiqmmfsrc → NV12 caps → queue → tee name=t_src
t_src raw branch   → queue → qtivcomposer (comp.sink_0)
t_src AI branch    → queue → tee name=t
  t main branch    → queue → qtimetamux (obj_mux)
  t AI branch      → queue → qtimlvconverter → queue → qtimltflite → queue → qtimlpostprocess → text/x-raw → queue → qtimetamux (obj_mux)
qtimetamux (obj_mux) → queue → tee name=ai_tee
  ai_tee display branch → queue → qtivoverlay → queue → qtivcomposer (comp.sink_1)
  ai_tee file branch    → queue → v4l2h264enc → h264parse → mp4mux → filesink sync=false
qtivcomposer → queue → waylandsink fullscreen=true sync=true
```

**Critical rules for this topology:**
- Three `tee` elements are REQUIRED:
  1. `tee name=t_src` — separates raw passthrough (→ comp.sink_0) from AI branch
  2. `tee name=t` — within the AI branch, separates qtimetamux passthrough from inference
  3. `tee name=ai_tee` — placed AFTER `qtimetamux`, BEFORE `qtivoverlay`, splits into display branch and file encode branch
- `qtivoverlay` appears inside the `ai_tee` display branch — it is NOT on the main path before the tee
- `filesink` must include `sync=false` to prevent clock synchronization issues with the display branch
- `waylandsink fullscreen=true sync=true` for ISP camera pipelines

## Three-Stream Topology (Single Camera, Dual File Encode: Composed + AI-Only)

When a user requests **raw passthrough left + AI overlay right composed to MP4 file AND the AI overlay stream alone encoded to a second MP4 file** from a single camera (no display), use the **three-tee / out_tee topology**:

```text
qtiqmmfsrc → NV12 caps → queue → tee name=t_src
t_src raw branch   → queue → qtivcomposer (comp.sink_0)
t_src AI branch    → queue → tee name=t
  t main branch    → queue → qtivtransform → NV12 → qtimetamux (obj_mux)
  t AI branch      → queue → qtimlvconverter → queue → qtimltflite → queue → qtimlpostprocess → text/x-raw → queue → qtimetamux (obj_mux)
qtimetamux (obj_mux) → queue → qtivoverlay → queue → tee name=out_tee
  out_tee branch 1 → queue → qtivcomposer (comp.sink_1)
  out_tee branch 2 → queue → v4l2h264enc → h264parse → mp4mux → filesink (AI-only MP4) sync=false
qtivcomposer → queue → v4l2h264enc → h264parse → mp4mux → filesink (composed MP4) sync=false
```

**Critical rules for this topology:**
- Three `tee` elements are REQUIRED:
  1. `tee name=t_src` — separates raw passthrough (→ comp.sink_0) from AI branch
  2. `tee name=t` — within the AI branch, separates qtimetamux passthrough from inference; needs `qtivtransform` per SKILL.md's *Buffer writability — `qtivoverlay` and `qtivcomposer`*, since `t`'s buffer traces back to `t_src`, which also feeds `comp.sink_0`
  3. `tee name=out_tee` — placed AFTER `qtimetamux` AND AFTER `qtivoverlay`; splits already-overlaid stream into composer branch and AI-only file branch
- **`qtivoverlay` is on the SHARED path BEFORE `out_tee`** — both file outputs carry the AI overlay
- **Both `filesink` elements require `sync=false`** — two parallel encode sinks from same tee output
- The composed `filesink` (after qtivcomposer) also needs `sync=false` for parallel encode correctness
- See `ai-pipeline-patterns.md` Template 5b for the complete command

## Complete Command Template (Single Stream — File Sink)

When the output is a file (MP4), use this skeleton instead of the display template above:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
  <INFER_ELEMENT_STAGE1> ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE_STAGE1> labels=<LABELS_STAGE1> \
  ! text/x-raw ! queue ! meta_mux.
```

**Key differences from display template:**
- `qtivoverlay` feeds `v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — NOT `waylandsink`
- No `sync=false` on `filesink` for single-output (file-only) pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)

See `ai-pipeline-patterns.md` Template 7 for the complete, fully expanded form.

## Anti-Pattern: v4l2h264dec IO Modes

`v4l2h264dec` **MUST** include `capture-io-mode=4 output-io-mode=4` — bare `v4l2h264dec` without io-mode properties will not use DMA buffer mode and will fail or perform poorly:

```bash
# CORRECT
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12

# WRONG — missing io-mode properties
v4l2h264dec ! video/x-raw,format=NV12
```



- Branch from `tee` that is never rejoined where metadata merge is required
- Overlay path that bypasses metadata merge
- Introducing daisy-chain stage 2 when user asked only for a simple single-stream pipeline
- Emitting partial command fragments instead of one complete runnable pipeline
- Overusing `queue` in single-input pipelines (for example queue between every AI stage) without branch/decoupling need — this does not apply to the queue immediately after a hardware decoder (`v4l2h264dec`/`v4l2h265dec`/etc.), which belongs there regardless of what follows (see `plugin-catalog.md`'s `queue` entry and `pipeline-utilities.md`'s Queue Usage)
- **For two-stream side-by-side from a single camera: using a single tee. Two tees are required — one to split the raw branch from the AI branch, and one within the AI branch to split for `qtimetamux`.**
- **Feeding `qtivcomposer` directly from `qtimlpostprocess` text/x-raw output — the text/x-raw output must go through `qtimetamux` + `qtivoverlay` first before the overlay video enters the composer.**

This anti-pattern applies only to `text/x-raw` output — it is not a blanket ban on feeding AI results into `qtivcomposer`. When `qtivcomposer` is already required elsewhere in the pipeline and the module supports `video/x-raw`, prefer the direct-to-composer path instead of `qtimetamux`+`qtivoverlay` for that stream. See `plugin-catalog.md` → "Preference: reuse an existing qtivcomposer" for the full rule, conditions, and two-sink-pad shape.

## Segmentation Topology (Topology B — Alpha Blend)

Segmentation pipelines use **Topology B** — `qtivcomposer` with alpha blend. Do NOT use `qtimetamux` or `qtivoverlay` for segmentation.

- `module=deeplab-argmax` outputs a rendered `video/x-raw` mask frame (not text metadata) — feed it into `qtivcomposer` with no capsfilter in between; pinning `width`/`height` fails caps fixation
- The mask is blended with the original video using `qtivcomposer` with `sink_1::alpha=<ALPHA>`
- Single `tee name=t`: passthrough → `qtivcomposer sink_0`; AI branch → converter → infer → postprocess → `qtivcomposer sink_1` (no capsfilter)
- See `ai-pipeline-patterns.md` section 8 for the display output template

### Segmentation — File Output Variant

When the output is a file (MP4) instead of display, use a caps filter directly on the composer output to negotiate NV12:

```text
... qtivcomposer name=seg_mix sink_1::alpha=<ALPHA> ! video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> ...
```

- No `sync=false` on `filesink` for single-output (file-only) pipelines
- Plugin placement constraints are defined in `plugin-catalog.md`.
- See `ai-pipeline-patterns.md` section 8b for the complete file-output template

## Super Resolution Topology (Topology B — Side-by-Side Compose)

Super resolution pipelines use **Topology B** — `qtivcomposer` for side-by-side composition. Do NOT use `qtimetamux` or `qtivoverlay` for super resolution.

- `module=srnet` outputs `video/x-raw,format=RGB` (no explicit dimensions constraint)
- The original NV12 passthrough (left/sink_0) and the SR RGB output (right/sink_1) are composed side-by-side using `qtivcomposer`
- Single `tee name=t`: passthrough → `qtivcomposer sink_0`; SR AI branch → `qtimlvconverter` (NO queue before it) → `qtimltflite` → `qtimlpostprocess module=srnet` → `video/x-raw,format=RGB` → `qtivcomposer sink_1`
- Composer is declared inline in the passthrough branch with pad position/dimensions properties
- See `ai-pipeline-patterns.md` section 10 for the complete display template
- **For file output (MP4):** After `qtivcomposer`, use `! video/x-raw,format=NV12 !` directly before `v4l2h264enc`. See section 10b for the complete file-output template.

---

## Runtime Substitution

All supported inference elements occupy the same pipeline position and are runtime swaps when the model format and target plugin availability match.
Only the inference stage changes when swapping runtimes — source, preprocess, postprocess, overlay,
and sink are identical. See `plugin-catalog.md` for full property tables.

**Substitution pattern — swap only the inference element:**

```text
# TFLite HTP (external delegate)
qtimlvconverter ! \
qtimltflite model=<model>.tflite delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! \
qtimlpostprocess module=<MODULE> labels=<labels.json>

# SNPE DSP — swap inference stage only, everything else identical
qtimlvconverter ! \
qtimlsnpe model=<model>.dlc delegate=dsp tensors="<OUTPUT_TENSOR>" ! \
qtimlpostprocess module=<MODULE> labels=<labels.json>

# QNN HTP — swap inference stage only
qtimlvconverter ! \
qtimlqnn model=<model>.so backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so tensors="<OUTPUT_TENSOR>" ! \
qtimlpostprocess module=<MODULE> labels=<labels.json>

# QAIRT HTP — swap inference stage only when QAIRT is requested or model evidence requires it
qtimlvconverter ! \
qtimlqairt model=<model>.dlc backend=libQairtHtp.so ! \
qtimlpostprocess module=<MODULE> labels=<labels.json>

# ONNX QNN — swap inference stage only after target plugin availability is verified
qtimlvconverter ! \
qtimlonnx model=<model>.onnx execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so ! \
qtimlpostprocess module=<MODULE> labels=<labels.json>
```

**`qtimlpostprocess` module selection is runtime-agnostic.** The same `module=yolov8`, `module=qpd`,
etc. work identically for all inference runtimes. Do not change the postprocess stage when swapping.

The `tensors="<OUTPUT_TENSOR>"` shown on the SNPE/QNN lines above is a genuine
placeholder here because `<MODULE>` itself is unresolved — once you know the real
module, `tensors=`/`layers=` becomes optional and usually should be omitted; see
`plugin-catalog.md`'s "Tensor Filter — Decision Rule" before including it in a
concrete pipeline.

**ONNX caveat:** Include the following note in the artifact README unless the target was already verified:
> ⚠️ Verify `gst-inspect-1.0 qtimlonnx` on the target before running this artifact. The source tree contains `qtimlonnx`, but target package/plugin availability can vary.

---

## Preprocess and Inference

# Preprocess and Inference

## Use This Reference For

- Wiring preprocessing and inference stages in AI branches
- Choosing infer placeholder patterns without inventing unsupported flags

## Preprocess Stage

Core preprocess element:

```text
qtimlvconverter name=<PRE_NAME>
```

For daisy-chain stage 1 (full frame):

```text
qtimlvconverter name=<PRE_NAME_STAGE1> mode=image-batch-non-cumulative
```

For daisy-chain secondary stage:

```text
qtimlvconverter name=<PRE_NAME_STAGE2> mode=roi-batch-cumulative image-disposition=centre
```

Note: `image-disposition=centre` is the canonical property for the ROI-based stage 2 preprocessor in daisy-chain pipelines. It centers the cropped ROI region within the tensor, which is the documented default for pose estimation and landmark models.

## Inference Stage

Use one infer element placeholder per stage:

```text
<INFER_ELEMENT_STAGE1>
```

Allowed infer families: `qtimltflite`, `qtimlsnpe`, `qtimlqnn`, `qtimlqairt`, `qtimlonnx`

**Runtime selection rule:** If the user specifies a runtime, use it. If not, ask — backend choice changes the element, required properties, and model format. Do not default to TFLite when the user has not specified a runtime.

**For full property reference, enum nicks, backend paths, and gst-launch patterns for all runtimes, load `plugin-catalog.md`.**

TFLite HTP (most common pattern — kept here for quick reference):
```text
qtimltflite model=<MODEL>.tflite delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
```

**Postprocess is runtime-agnostic:** `qtimlpostprocess` module selection (`module=yolov8`, `module=qpd`, etc.) is identical regardless of runtime. Do not change the postprocess stage when swapping runtimes.

**For secondary inference stages in daisy-chain pipelines (e.g., pose estimation Stage 2, high-throughput ROI processing):** add `htp_performance_mode=(string)2,` before `log_level`:

```text
external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;"
```

**GPU delegate (gesture recognition):** use `delegate=gpu` — no `external-delegate-path` or `external-delegate-options` needed.

## Single-Stage AI Branch Pattern

```text
tee branch ! queue ! qtimlvconverter ! queue ! <INFER_ELEMENT_STAGE1> ! queue ! qtimlpostprocess ... ! text/x-raw ! queue ! qtimetamux
```

**Exception — super resolution:** In SR pipelines, do NOT insert a `queue` between the tee branch and `qtimlvconverter`. The SR branch connects as: `t. ! qtimlvconverter ! queue ! <infer> ...` (no queue before the converter). See `ai-pipeline-patterns.md` Template 10.

**Queue placement in the AI branch (required):**
- `queue` after tee branch pad (before `qtimlvconverter`)
- `queue` after `qtimlvconverter` (before inference)
- `queue` after inference element (before `qtimlpostprocess`)
- `queue` after `qtimlpostprocess` text/x-raw caps (before `qtimetamux`)

## Two-Stage Daisy Branch Pattern

```text
stage1 preprocess (image-batch-non-cumulative)/infer/postprocess -> stage1 metadata merge -> stage2 preprocess (roi-batch-cumulative) -> stage2 infer/postprocess -> final metadata merge
```

## Validation Rules

- Every preprocess stage must feed an infer stage.
- Every infer stage must feed a postprocess stage.
- Do not claim a two-stage cascade without two distinct infer stages.

---

## Postprocess and Metadata

# Postprocess and Metadata

## Use This Reference For

- Converting tensors to usable results
- Merging AI metadata back with the main media path
- Overlaying or forwarding metadata safely

> **Two valid overlay topologies exist for both gst-launch and C apps:**
> - **Topology A** (this file): `qtimlpostprocess → text/x-raw → qtimetamux → qtivoverlay` — standard annotation overlay
> - **Topology B**: `qtimlpostprocess → qtivcomposer` (no capsfilter — let caps negotiate) — side-by-side, alpha blend, multi-stream
>
> **How to choose:** For `image-segmentation`, `depth-estimation`, and `super-resolution` modules (`deeplab-argmax`, `yolov8-seg`, `midas-v2`, `srnet`), the module forces Topology B in current known-good templates — do not switch these to `text/x-raw → qtimetamux` until a current sample or device run verifies the full path. For every other category (`object-detection`, `image-classification`, `pose-estimation`, `audio-classification` — `yolov8`, `mobilenet`, `qfd`, `qfr`, `hrnet`, `yamnet`, etc.), the module supports **both** `text/x-raw` and `video/x-raw`; the choice is a topology decision, not fixed by the module. Default to Topology A for a plain single-stream request. When a `qtivcomposer` is already required elsewhere in the pipeline (multistream, side-by-side, PiP), prefer feeding that branch's `qtimlpostprocess` output directly into a **second** `comp.sink_N` pad (Topology B, with no capsfilter in between) instead of adding `qtimetamux`+`qtivoverlay` for that stream — the mask output is transparent (only boxes/labels, no video), so it must be layered over that same stream's own raw-passthrough sink pad at identical `position`/`dimensions`, exactly like the segmentation/SR pattern's two-sink-pad shape; it is never wired to a single sink pad alone. Never insert a `video/x-raw` capsfilter between `qtimlpostprocess` and the composer sink pad — pinning `width`/`height` there fails caps fixation regardless of format; size via the composer sink-pad `dimensions` instead. See `plugin-catalog.md`'s "Module Output Types — Format Support by Category" table and its "Preference: reuse an existing qtivcomposer directly" section for the exact two-pad wiring, and the "Compose + Overlay (Multistream)" section below for where this applies across Templates 3/4/5/5b/9/9b/13/13b.
>
> See `c-app-development.md` for full C code for both. This file documents Topology A metadata merge patterns.

## Postprocess Stage

Canonical postprocess placeholder:

```text
qtimlpostprocess name=<POST_NAME> module=<POSTPROC_MODULE> labels=<LABELS_FILE>
```

Module selection:

- Read `references/plugin-catalog.md`.
- If request/model family clearly maps to a documented module, set `module` directly.
- If ambiguous, keep `module=<POSTPROC_MODULE_...>` placeholder.
- Never invent new module names.

`settings` is optional and should only be added when user intent explicitly asks for postprocess config/settings or threshold tuning.

When used, `settings` values should be either:

- JSON file path
- inline JSON string (for example confidence tuning): `settings="{\"confidence\": 51.0}"` — note the space after the colon in the canonical format

The canonical inline JSON format for confidence threshold is: `settings="{\"confidence\": <VALUE>}"`

`results` is an optional integer property of `qtimlpostprocess` that controls the top-N output count (for example, top-5 classification labels). Include `results=<N>` only when the user explicitly specifies a number of results. Example: `qtimlpostprocess module=mobilenet results=5 labels=<LABELS>`.

Terminology normalization:

- If prompt says postprocess `config`, interpret it as `settings`.
- If prompt says postprocess `settings`, use `settings`.
- Do not create a `config` property on `qtimlpostprocess`.

Postprocess output path for detection, classification, pose, and face detection modules:

```text
... ! qtimlpostprocess ... ! text/x-raw ! queue ! <MUX_NAME>.
```

## Metadata Merge Stage

Canonical merge stage:

```text
qtimetamux name=<MUX_NAME>
```

Rules:

- main video branch must feed `<MUX_NAME>.`
- AI metadata branch must feed `<MUX_NAME>.`
- merged output from `<MUX_NAME>` feeds overlay/next stage

## Overlay Stage

Canonical overlay pattern (display output) — single-stage pipeline:

```text
... ! qtimetamux name=<MUX_NAME> ! qtivoverlay ! waylandsink fullscreen=true sync=true
```

Canonical overlay pattern (display output) — daisy-chain / multi-stage pipeline:

```text
... ! qtimetamux name=<MUX_NAME> ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true
```

Note: In daisy-chain and multi-stage pipelines, add a `queue` both before `qtivoverlay` (after `qtimetamux`) and after `qtivoverlay` (before `waylandsink`). This is the documented canonical pattern for the pose estimation and two-stage inference pipelines.

For file encoding output after overlay:

```text
... ! qtimetamux name=<MUX_NAME> ! qtivoverlay ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```

Note: The canonical encode-to-file pattern does NOT add a `queue` between `qtivoverlay` and `v4l2h264enc`.

## Compose + Overlay (Multistream)

When requested explicitly:

```text
... per-stream processing ... ! qtivcomposer ... ! queue ! waylandsink fullscreen=true sync=true
```

Every template below that already includes a `qtivcomposer` (Templates 3, 4, 5, 5b, 9, 9b, 13, 13b, and the two-/three-stream side-by-side sections above) defaults each stream's AI branch to the standard `text/x-raw → qtimetamux → qtivoverlay → comp.sink_N` chain. This is the safe default and what these templates show verbatim.

**Generic rule — applies to any of the above templates:** for a given stream, if its module supports `video/x-raw` (see `plugin-catalog.md` → "Preference: reuse an existing qtivcomposer"), replace `qtimetamux`+`qtivoverlay` with a direct feed into a second composer sink pad, paired with the raw-passthrough sink pad:
```text
<decode> ! queue ! tee name=tN
tN. ! queue ! comp.sink_A                                                              (raw video — keep this, do not drop it)
tN. ! queue ! qtimlvconverter ! ... ! qtimlpostprocess module=<MODULE> ... ! queue ! comp.sink_B   (mask, same position/dimensions as sink_A, alpha=1.0)
```
`comp.sink_A` and `comp.sink_B` must use identical `position`/`dimensions`; declare `sink_A` before `sink_B` so default z-order layers the mask on top.

## Audio Classification Topology (yamnet — Topology C)

For YAMNet audio classification pipelines, the topology is unique:
- `qtimlpostprocess module=yamnet` outputs a rendered `video/x-raw` panel (a visual panel, NOT text/x-raw) — feed it into `qtivcomposer` with no capsfilter in between
- This output is composited onto the video stream using `qtivcomposer` (NOT `qtimetamux`/`qtivoverlay`)
- No `tee` is used — `qtdemux name=demux` naturally provides separate video and audio pads
- `demux.` pad is referenced to split into video branch (→ qtivcomposer sink_0) and audio branch (→ AI pipeline → qtivcomposer sink_1)
- `waylandsink fullscreen=true` — do NOT add `sync=true` for audio classification

```text
filesrc ! qtdemux name=demux
demux. → video branch → NV12 → qtivcomposer name=mixer sink_1::position sink_1::dimensions → waylandsink fullscreen=true
demux. → audio branch → flacparse ! flacdec → audioconvert → audioresample → audiobuffersplit → qtimlaconverter → qtimltflite → qtimlpostprocess module=yamnet → queue → mixer.  (no capsfilter after postprocess)
```

See Template 12 in `ai-pipeline-patterns.md` for the complete command.

## Object Tracking (`qtiobjtracker`)

Add `qtiobjtracker` only when the user explicitly asks for persistent object IDs across frames (multi-object tracking), not for plain detection/classification requests.

- **Placement:** always downstream of `qtimetamux` (it reads and rewrites the already-muxed `text/x-raw` detection metadata) and upstream of anything that needs stable track IDs — overlay, zone-crossing logic, or a custom consumer: `... ! qtimetamux name=<MUX_NAME> ! queue ! qtiobjtracker ! queue ! qtivoverlay ! ...`.
- **Properties:** `algo` (only `bytetrack` is currently a valid value — do not invent other algorithm names) and `parameters`, a serialized `GstStructure` string of ByteTrack-specific tuning fields: `frame-rate`, `track-buffer`, `wh-smooth-factor`, `track-thresh`, `high-thresh`. Only set `parameters` when the user asks for tracker tuning; otherwise omit it and rely on the algorithm's defaults.
- Example: `qtiobjtracker algo=bytetrack parameters="frame-rate=(int)30,track-buffer=(int)30"`.
- `qtiobjtracker` does not change the pipeline's overlay topology choice (Topology A vs B) — it only augments the text metadata that Topology A's `qtimetamux`/`qtivoverlay` chain already carries; it is not used with Topology B (rendered-frame/`qtivcomposer`) postprocess outputs.

## Metadata Format Bridging (`qtimlmetaextractor`)

Add `qtimlmetaextractor` only when an upstream element in the request produces *binary* buffer-attached ML metas (`GstVideoRegionOfInterestMeta`/classification/landmarks metas) but a downstream stage in the pipeline needs the `text/x-raw` `GstStructure`-list convention (`qtimetamux`, `qtiobjtracker`, `qtimetatransform`, `qtimlmetaparser`). It has no properties — it is a pure format bridge, not a configurable postprocess stage. Most standard pipelines never need it because `qtimlpostprocess` already emits `text/x-raw` directly; only introduce it when the user's request or an upstream element explicitly produces binary metadata that needs converting.

## Metadata Transform Between Stages (`qtimetatransform`)

Add `qtimetatransform` only for the documented multi-stage handoffs where one stage's ROI/label metadata must be reshaped before the next stage consumes it — do not use it as a generic "metadata processing" placeholder.

- `module=roi-palmd` — gesture-recognition Stage 1→2 handoff. See Template 11 in this file for the canonical placement and pipeline structure.
- `module=roi-label-moving-average` — smooths/averages ROI label confidence across frames; use only when the user asks for temporal smoothing/stabilization of detection labels (distinct from `qtimlpostprocess`'s own `bbox-stabilization=true`, which stabilizes box coordinates, not label confidence).
- `module=roi-auto-framing` — auto-framing ROI transform; use only when the user asks for auto-framing behavior.
- `module=roi-person-merge` — person ROI merge transform; use only when the request needs person ROI merging between stages.
- Set `module` with `gst_element_set_enum_property()` in C apps (never `g_object_set()` with a string) — same enum-property rule as every other QIM SDK enum property.

## Validation Rules

- Do not bypass metadata merge when overlay is requested.
- Do not send postprocess metadata directly to display sink.
- Ensure all metadata branches terminate at a mux stage.
- When an overlay chain (`qtimetamux → qtivoverlay`) and a `qtivcomposer` sink pad draw video from the same `tee`, the overlay chain's video branch needs `qtivtransform` before `qtimetamux` — see SKILL.md, *Buffer writability — `qtivoverlay` and `qtivcomposer`*, and the "`qtimetamux` Writability Rule" section below.

---

## Daisy-Chain AI

# Daisy Chain

## Use This Reference For

- Two-stage cascaded AI pipelines
- Stage 1 full-frame inference and Stage 2 ROI inference
- Three-stage gesture recognition pipelines (palm detection → hand landmark → gesture classification)

**IMPORTANT: Three-stage gesture recognition uses a different topology than the standard two-stage daisy-chain. See Template 11 in `ai-pipeline-patterns.md` for the canonical gesture recognition pipeline.**

## Required Shape

```text
source -> decode -> queue -> tee(t_split_1)

t_split_1 main branch -> queue -> metamux_1.
t_split_1 AI branch   -> queue -> stage1_preproc -> queue -> stage1_inference -> queue -> stage1_postproc -> text/x-raw -> queue -> metamux_1.

qtimetamux(metamux_1) -> queue -> tee(t_split_2)

t_split_2 main branch -> queue -> metamux_2.
t_split_2 AI branch   -> queue -> stage2_preproc(roi-batch-cumulative) -> queue -> stage2_inference -> queue -> stage2_postproc -> text/x-raw -> queue -> metamux_2.

qtimetamux(metamux_2) -> queue -> qtivoverlay -> queue -> waylandsink

For file output (MP4), replace the final sink with the H.264 encode chain:
  qtimetamux(metamux_2) -> queue -> qtivoverlay -> queue -> v4l2h264enc capture-io-mode=4 output-io-mode=4 -> h264parse -> mp4mux -> filesink location=<OUTPUT_MP4>
No `sync=false` on filesink for single-output file pipelines. See Template 2c in ai-pipeline-patterns.md for the full file-output daisy-chain example.
```

## Minimal Rules

- Must contain two distinct inference stages.
- Stage 2 must consume ROIs from stage 1 context.
- Do not claim daisy chain with only one inference stage.
- Keep one explicit final sink path.
- Named elements are declared at the top of the gst-launch command (before `filesrc`), then addressed by name with explicit pad notation (`element_name. ! queue ! next_element.`).
- Always place a `queue` between `qtivoverlay` and `waylandsink`.
- Always place a `queue` after `qtimetamux` output (`metamux_1. ! queue ! tee ...`).
- The main video branch of `t_split_2` feeds `metamux_2.` (it is listed LAST, after the final overlay path).

## Stage 2 Preprocessor Mode Selection

The `qtimlvconverter` mode for Stage 2 depends on what the downstream model needs:

| Stage 2 requirement | mode | image-disposition | Example models |
|---|---|---|---|
| Model needs a **batch of ROIs accumulated** before inference (e.g., runs on all detected persons at once) | `roi-batch-cumulative` | `centre` | hrnet, qpd, mobilenet on detected ROIs |
| Model needs **each ROI processed immediately**, one at a time as detected | `roi-batch-non-cumulative` | *(omit)* | hlandmark (gesture stage 2) |

**How to decide:** Ask whether the Stage 2 model requires all detected ROIs to be present before it can run (→ cumulative) or whether it can process each ROI as soon as it arrives (→ non-cumulative). When in doubt and ROIs should be centered in the tensor, add `image-disposition=centre`.

The command skeleton below uses `roi-batch-cumulative image-disposition=centre`. For models that process ROIs immediately, replace with `mode=roi-batch-non-cumulative` and omit `image-disposition`.

## Command Skeleton

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE1> \
  qtimlpostprocess name=stage_01_postproc module=<POSTPROC_STAGE1> \
  labels=<LABELS_STAGE1> \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE2> \
  qtimlpostprocess name=stage_02_postproc module=<POSTPROC_STAGE2> \
  labels=<LABELS_STAGE2> \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
  t_split_1. ! queue ! metamux_1. metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
  metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! metamux_2.
```

**Optional properties — add only when user explicitly provides values:**
- `results=<N>` on `qtimlpostprocess` — only when user specifies top-N results count (e.g., classification top-5)
- `settings="{\"confidence\": <CONF>}"` on `qtimlpostprocess` — only when user provides a confidence threshold value
- `settings=<PATH>` on `qtimlpostprocess` — only when user provides a settings file path

## Camera Source Adaptation (qtiqmmfsrc)

When the source is `qtiqmmfsrc` instead of a file source, start from the documented file-source topology and change only the source chain. This skeleton has no `qtivcomposer`, so the writability transform (SKILL.md's *Buffer writability — `qtivoverlay` and `qtivcomposer`*) does not apply to it.

**Separate rule — ROI metadata preservation:** in a daisy-chain, never insert a video transform (`qtivtransform`, `videoconvert`, etc.) on an inter-stage branch that carries Stage 1 ROI metadata into a later stage — here, the `t_split_2 → metamux_2` branch. Stage 2 `qtimlvconverter` in `roi-batch-cumulative` mode reads that ROI metadata; a transform on this branch strips or invalidates it, causing full-frame fallback or missing boxes.

Camera-source skeleton (only the source differs from the file skeleton):

```bash
qtiqmmfsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t_split_1 \
t_split_1. ! queue ! stage_01_preproc. ... ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
t_split_1. ! queue ! metamux_1.   ← no qtivtransform needed here (no qtivcomposer in this shape)
metamux_1. ! queue ! tee name=t_split_2 \
t_split_2. ! queue ! stage_02_preproc. ... ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
t_split_2. ! queue ! metamux_2.   ← preserve Stage 1 ROI metadata for Stage 2
```

---

The canonical daisy-chain pattern declares named ML elements at the TOP of the `gst-launch-1.0` command (before the source), separated by whitespace (no `!` connectors). The elements are then wired using explicit named pad addressing:

```bash
element_name. ! queue ! next_element.
```

This means `stage_01_preproc.` is a sink pad reference and `stage_01_preproc.` as a source follows the element name convention. For instance:

```bash
t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference.
```

This connects `t_split_1` branch → queue → into `stage_01_preproc` sink pad, then from `stage_01_preproc` source pad → queue → into `stage_01_inference` sink pad.

---

## Multistream AI

# Multistream

## Use This Reference For

- Two-stream or three-stream AI pipelines
- AI wall style composition
- Final-stage stream composition design
- Scalable layouts that follow sample-app behavior up to high stream counts

## Core Rule

Process each stream independently for as long as possible. Compose only near the end.

## Canonical Pattern

```text
stream1 decode/ai path ┐
stream2 decode/ai path ├-> qtivcomposer -> output
stream3 decode/ai path ┘
```

## Typical Branch Pattern Per Stream (Detection / Classification / Face Detection)

```text
<INPUT_CHAIN_i> -> queue -> tee
tee main -> qtimetamux
tee ai   -> queue -> preprocess -> queue -> infer -> queue -> postprocess -> text/x-raw -> queue -> qtimetamux
qtimetamux -> queue -> qtivoverlay -> queue -> composer sink pad
```

**Important**: Queue is required between `video/x-raw,format=NV12` and `tee` — always include `queue` after the NV12 caps filter and before the tee.

**Optional optimization:** since `comp` is already required for the AI wall, any stream here may drop `qtimetamux`+`qtivoverlay` in favor of feeding `qtimlpostprocess`'s rendered output (no capsfilter) into an **additional**, paired `comp.sink_N` pad (never a sink pad alone — the mask has no video content) — see the generic "Compose + Overlay (Multistream)" rule above for when this applies and how to decide per stream.

## Segmentation Branch Exception (AI Wall / Multi-Stream)

When a **segmentation stream** is one of the parallel AI wall branches, it does NOT use `qtimetamux` + `qtivoverlay`. Instead it uses its own local `qtivcomposer` for alpha-blending:

```text
<INPUT_CHAIN_seg> -> queue -> tee name=seg_tee
seg_tee main -> queue -> qtivcomposer name=seg_mix sink_1::alpha=<ALPHA>   (this is sink_0 — passthrough)
seg_tee ai   -> queue -> preprocess -> queue -> infer -> queue -> postprocess -> queue -> seg_mix.   (this is sink_1, no capsfilter between postprocess and seg_mix)
seg_mix output: ! video/x-raw,format=NV12 ! queue ! comp.sink_N
```

The `video/x-raw,format=NV12` caps cast AFTER `seg_mix` (before the final composer pad) is required.

## Named Element Declaration Pattern

For gst-launch pipelines where multiple elements are referenced by name across different branches, declare all named elements at the **top** of the command before any source or wiring elements:

```text
# Declare all named elements at the TOP — no ! connectors between declarations
qtimlvconverter name=class_pre
qtimltflite name=class_infer model=... delegate=...
qtimlpostprocess name=class_post module=mobilenet ...
qtimetamux name=class_mux
qtivoverlay name=class_overlay
... (declare all streams' elements)
qtivcomposer name=comp sink_0::... sink_3::... ! \   <- note the ! ending this declaration
queue ! waylandsink fullscreen=true sync=true \
# Then wire by pad addressing:
filesrc ... ! tee name=class_tee
class_tee. ! queue ! class_mux.
class_tee. ! queue ! class_pre. class_pre. ! queue ! class_infer. class_infer. ! queue ! class_post. class_post. ! text/x-raw ! queue ! class_mux.
class_mux. ! queue ! class_overlay. class_overlay. ! queue ! comp.sink_0
...
```

See `ai-pipeline-patterns.md` section 9 for the complete 4-stream AI wall template (display output).

For **file output** (encoding the composed 2×2 grid to MP4), see section 9b. The only difference from Template 9 is the final composer output:
- Replace `queue ! waylandsink fullscreen=true sync=true` with:
  `video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>`
- Use a **caps filter** (`! video/x-raw,format=NV12 !`) directly after `qtivcomposer`; plugin placement constraints are defined in `plugin-catalog.md`.

## `qtimetamux` Writability Rule

Placement of the transform required by SKILL.md's *Buffer writability — `qtivoverlay` and `qtivcomposer`*, on templates in this file that feed both a `qtivcomposer` and a `qtimetamux` from the same `tee`:

```text
tee name=t
  t. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <MUX_NAME>.     ← video branch to qtimetamux
  t. ! queue ! qtimlvconverter → infer → postprocess → text/x-raw → <MUX_NAME>.  ← AI branch
```

This applies to the two-tee/three-tee single-camera side-by-side topologies above; plain single-stream Topology A (no `qtivcomposer`) does not use it.

- Each stream branch must be complete and connected.
- Composition must happen after per-stream processing.
- Do not introduce multistream path for single-stream requests.
- Keep each stream isolated with queue stages around `tee`, inference, and metadata merge.
- For decode-heavy file/RTSP app code, use dynamic-pad handling (`qtdemux`/`rtspsrc`) into decode queues per stream.
- Segmentation branches in AI wall use `qtivcomposer` (alpha-blend), NOT `qtimetamux` + `qtivoverlay`.
- After the per-stream segmentation `qtivcomposer`, add `! video/x-raw,format=NV12 !` before feeding the final grid composer.
- **After drafting the pipeline, review each stream:** if the pipeline has `qtivcomposer` and a stream's module supports `video/x-raw`, prefer the direct-to-composer path over `qtimetamux`+`qtivoverlay` for that stream — except when tracking (`qtiobjtracker`) or metadata-downstream consumption is required. See `plugin-catalog.md` → "Preference: reuse an existing qtivcomposer" for the full rule and two-sink-pad shape.

---

## Audio AI Pipelines

# Audio AI Pipelines

## Use This Reference For

- Building any pipeline that involves audio AI inference (audio classification, sound event detection)
- Understanding the unique dual-stream (video + audio) topology used for audio AI
- Getting exact property values, element names, and caps for audio pipelines

> **Audio AI pipelines are structurally unique.** They are NOT standard video AI pipelines with audio added. Read this entire reference before generating any audio pipeline.

---

## Core Concepts

Audio AI pipelines split a single container file into two independent streams:
1. **Video stream** — decoded and sent directly to a composer sink for display passthrough
2. **Audio stream** — decoded, resampled, buffered, feature-extracted, inferred, and postprocessed — the output is a rendered overlay frame (no capsfilter) that gets composed over the video

The video and audio streams are **not linked via tee** — they come from two separate qtdemux dynamic pads and are independently wired into `qtivcomposer`.

---

## How to Choose `feature` and `output-buffer-size`

Two classes of audio models exist:

**Class A — Pre-extracted LMFE models (YAMNet as documented in SDK):**
- Model expects pre-computed log mel spectrogram; use `feature=lmfe params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"`
- For YAMNet: `output-buffer-size=31200` (15600 samples × 2 bytes)

**Class B — Raw waveform models:**
- Model has internal spectrogram extraction; input tensor shape: `[1, N_SAMPLES]`
- Use `feature=raw` — no `params` needed. `output-buffer-size = chunk_duration_s × sample_rate × 2`

**If `feature=lmfe` produces `LogMelSpectrogram is misconfigured`:** the model expects raw waveform — switch to `feature=raw` and remove `params`.

---

## Assumed Defaults — State These When the User Doesn't Specify

When a user says "audio classification pipeline" without specifying model details, the following values are assumed from the AI Hub YAMNet model. **Always state these assumptions in the response** so the user can correct them before running.

| Property | Assumed value | Derived from |
|---|---|---|
| `audio/x-raw,rate=` | `16000` | AI Hub YAMNet expects 16kHz input |
| `audio/x-raw,channels=` | `1` | mono — YAMNet is mono; stereo input must be downmixed via `audioconvert` |
| `audiobuffersplit output-buffer-size=` | `31200` | 15600 samples × 2 bytes/sample (S16LE) |
| `qtimlaconverter sample-rate=` | `16000` | must match the caps filter rate |
| `qtimlaconverter feature=` | `lmfe` | YAMNet expects pre-extracted log mel spectrogram |
| `qtimlaconverter params=` | `"params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"` | required when `feature=lmfe` |
| `qtimlpostprocess settings= confidence` | `10.0` | lower threshold for audio vs video (51.0) |
| `qtimlpostprocess results=` | `3` | top-3 classes |
| overlay position | `sink_1::position="<50, 50>"` | arbitrary default — user may want a different corner |
| overlay size | `sink_1::dimensions="<368, 64>"` | sizes the tile — no capsfilter is used on the YAMNet output, so this composer property alone determines the panel size |
| audio codec in container | FLAC | only documented codec for AI inference; MP3 uses different parse/decode elements |

If any of these don't match the user's model, they must be adjusted before the pipeline will run correctly.

---

## Canonical Pipeline — Audio Classification (AI Hub YAMNet, FLAC file source)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux name=demux \
  demux. ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>" ! \
    queue ! waylandsink fullscreen=true \
  demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! \
    audiobuffersplit output-buffer-size=31200 ! queue ! \
    qtimlaconverter sample-rate=16000 feature=lmfe \
      params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;" ! queue ! \
    qtimltflite name=infeng model=<MODEL_PATH> ! \
    qtimlpostprocess module=yamnet labels=<LABELS_PATH> \
      settings="{\"confidence\": 10.0}" results=3 ! \
    queue ! mixer.
```

**Note on `--gst-debug=2`:** Use `gst-launch-1.0 -e --gst-debug=2` by default for generated audio pipelines unless the user explicitly requests a different debug setting.

**If this canonical pipeline (no `delegate=` set → CPU inference) reproducibly deadlocks specifically after setting/changing `sink_1::position`/`sink_1::dimensions`** (a different symptom from ordinary linking failures — the pipeline reaches `PLAYING` then freezes with no further logging): do NOT try to work around it by pinning `width=`/`height=` on a capsfilter right after `qtimlpostprocess` — that branch must stay unpinned (see the render-overlay caps note in `plugin-catalog.md`); pinning dimensions there fails caps fixation regardless of format and is a separate, unrelated failure. If `sink_1::position`/`sink_1::dimensions` is the confirmed, reproducible trigger for the deadlock, insert a `videoscale ! video/x-raw,width=368,height=64` pair *between* `qtimlpostprocess` and the composer (after caps have negotiated freely) to fix the tile's on-screen size instead, and omit `sink_1::position`/`sink_1::dimensions` for that pad. Only reach for this if the geometry properties are the reproducible trigger — do not drop them from the pipeline above by default.

---

## Topology: Two Independent qtdemux Pads

The pipeline does NOT use `tee`. Instead, `qtdemux name=demux` exposes two dynamic pads:
- `demux.` (video pad) → video decode chain → `qtivcomposer` (main/background sink_0)
- `demux.` (audio pad) → audio decode chain → classification overlay (no capsfilter) → `qtivcomposer sink_1`

```
filesrc → qtdemux name=demux
  demux (video) → queue → h264parse → v4l2h264dec → NV12 → queue → qtivcomposer (background, no sink pad name needed)
  demux (audio) → queue → [audio decode] → [audio feature extract] → qtimltflite → qtimlpostprocess
               → queue → mixer.  (no capsfilter after postprocess; becomes sink_1)
qtivcomposer name=mixer → queue → waylanksink
```

**Key structural rules:**
- `qtivcomposer` receives video directly on the implicit first sink (sink_0 = background video)
- Audio classification overlay goes to `mixer.` — this becomes sink_1
- `sink_1::position="<50, 50>"` and `sink_1::dimensions="<368, 64>"` are set on the **composer declaration line**, not on a separate pad-properties line
- `waylandsink` uses `fullscreen=true` but **NO `sync=true`** — the canonical audio pipeline omits `sync=true`
- Include `--gst-debug=2` in generated commands by default unless the user explicitly requests a different debug setting

---

## Audio Decode Chain

For FLAC audio (the only documented audio codec for AI inference):

```
demux. (audio pad) ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample
```

- `flacparse` — parses FLAC bitstream
- `flacdec` — decodes FLAC to raw PCM
- `audioconvert` — converts sample format
- `audioresample` — resamples to model-required rate
- Two queues: one immediately after the demux audio pad, one immediately after `flacdec` before `audioconvert` — this decouples the demux boundary and the FLAC decode boundary so one concurrent branch's preroll/scheduling cannot block the other. This is a general rule for any pipeline with concurrent dynamic `qtdemux` branches (e.g. audio + video), not specific to this audio-classification template — apply the same queue-after-each-dynamic-pad-plus-queue-at-each-intermediate-decode-boundary pattern whenever `qtdemux` branches run concurrently. Reserve the minimal direct `qtdemux -> parser` hop (no queue) for genuinely single-stream video-only paths.
- For file sources, `audioconvert ! audioresample ! audiobuffersplit` is the documented chain — no intermediate capsfilter needed. For `pulsesrc`/live audio sources, add `audio/x-raw,rate=16000,channels=1` after `audioresample` to force format negotiation

For MP3 audio (seen in gst-ai-audio-classification sample app, not in docs canonical):
```
demux. (audio pad) ! queue ! mpegaudioparse ! mpg123audiodec ! queue ! audioconvert ! audioresample
```

---

## audiobuffersplit — Buffer Sizing

Splits the continuous audio stream into fixed-size chunks for frame-by-frame inference:

```bash
audiobuffersplit output-buffer-size=31200
```

- `output-buffer-size=31200` — for YAMNet: 15600 samples × 2 bytes/sample (S16LE)
- This value is **model-specific** — derive from `n_samples × bytes_per_sample` for other models
- Property name is `output-buffer-size` (hyphen, not underscore)
- Add `audio/x-raw,rate=16000,channels=1` caps before `audiobuffersplit` when the source is `pulsesrc` to ensure format negotiation; file sources handled by `audioconvert`/`audioresample` upstream

---

## qtimlaconverter — Audio Feature Extraction

Converts raw PCM to feature tensors the model expects:

```bash
# For YAMNet (pre-extracted LMFE):
qtimlaconverter sample-rate=16000 feature=lmfe \
  params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"

# For raw waveform models (no internal spectrogram extraction):
qtimlaconverter sample-rate=16000 feature=raw
```

**Critical property rules:**
- `sample-rate` — integer, no quotes: `sample-rate=16000`
- `feature=lmfe` — for YAMNet and models trained on pre-extracted features; **requires `params`**
- `feature=raw` — for raw-waveform-input models; no `params` needed
- `params` — **only for `feature=lmfe`**, semicolon-delimited: `params="params,nfft=<N>,nhop=<N>,nmels=<N>,chunklen=<N>;"` — the string starts with `params,` and ends with `;`
- In C: `g_object_set(qtimlaconverter, "sample-rate", 16000, NULL)` and `gst_element_set_enum_property(qtimlaconverter, "feature", "lmfe")` plus `g_object_set(..., "params", "params,nfft=96,...;", NULL)` for YAMNet; use `"raw"` and omit `params` for raw-waveform models

---

## qtimltflite — Inference for Audio

Audio classification uses the **same** `qtimltflite` element as video pipelines:

```bash
qtimltflite name=infeng model=<MODEL_PATH>
```

**Key difference from video pipelines:**
- **No delegate** is set in the canonical docs pipeline — the audio pipeline runs on CPU by default
- If user specifies a backend (e.g. HTP/NPU or GPU), carry it in from the prompt
- Use `name=infeng` to match the canonical naming

---

## qtimlpostprocess — Audio Classification Output

```bash
qtimlpostprocess module=yamnet labels=<LABELS_PATH> \
  settings="{\"confidence\": 10.0}" results=3
```

- `module=yamnet` — the only audio classification module
- `settings="{\"confidence\": 10.0}"` — lower threshold than video (10.0 vs 51.0) — use this default for YAMNet unless user specifies otherwise
- `results=3` — top-3 audio classes returned
- Output: a rendered overlay frame (`video/x-raw`), not text/x-raw — feed it straight into `qtivcomposer` with no capsfilter in between
- Feeds directly into `qtivcomposer` with no capsfilter — pinning `width`/`height` here fails caps fixation regardless of format; size via the composer sink-pad `dimensions`

**Output caps after postprocess:**

```bash
! queue ! mixer.
```

- Width=368, height=64 — set these on the composer's `sink_1::dimensions`, not on a capsfilter after `qtimlpostprocess` (no capsfilter is used here)
- `mixer.` — feeds qtivcomposer as sink_1

---

## qtivcomposer — Audio Overlay Composition

```bash
qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>"
```

- `sink_1::position="<50, 50>"` — places the audio overlay at x=50, y=50 pixels from top-left
- `sink_1::dimensions="<368, 64>"` — sizes the YAMNet overlay tile; no capsfilter is used after `qtimlpostprocess`, so this composer property is what determines the panel size
- These go **on the composer declaration line** (not a separate pad-properties statement in gst-launch)
- The video feed connects to the implicit first sink (no `sink_0::` needed in gst-launch)

---

## What Changes Between Prompts

Only these values come from the user's prompt — everything else is fixed for YAMNet:

| What user provides | Property |
|-------------------|---------|
| Input file path | `filesrc location=<INPUT_FILE>` |
| Model path | `qtimltflite model=<MODEL_PATH>` |
| Labels path | `qtimlpostprocess labels=<LABELS_PATH>` |
| Backend/delegate | `qtimltflite delegate=...` (omit if not specified) |
| Confidence | `settings="{\"confidence\": <VALUE>}"` (default 10.0) |

---

## Complete Validated gst-launch Template

Use this as the starting point for any audio classification request:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux name=demux \
  demux. ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>" ! \
    queue ! waylandsink fullscreen=true \
  demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! \
    audiobuffersplit output-buffer-size=31200 ! queue ! \
    qtimlaconverter sample-rate=16000 feature=lmfe \
      params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;" ! queue ! \
    qtimltflite name=infeng model=<MODEL_PATH> ! \
    qtimlpostprocess module=yamnet labels=<LABELS_PATH> \
      settings="{\"confidence\": 10.0}" results=3 ! \
    queue ! mixer.
```

---

## File Output Variant — Audio Classification (File → MP4 File)

When the user requests file output instead of display, replace the `qtivcomposer` output chain:

**Display (canonical):**
```bash
qtivcomposer name=mixer ... ! queue ! waylandsink fullscreen=true
```

**File output:**
```bash
qtivcomposer name=mixer ... ! video/x-raw,format=NV12 ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>
```

**Why direct NV12 caps are required:** `v4l2h264enc` requires NV12 input. Use `video/x-raw,format=NV12` directly after `qtivcomposer` so the composer negotiates NV12 output natively. Plugin placement constraints are defined in `plugin-catalog.md`.

**Rules:**
- NO `sync=false` on `filesink` for single-output (file-only) pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section
- All audio path elements and properties are unchanged from the display variant
- Use Template 12b from `ai-pipeline-patterns.md` for the complete file-output command

---

## Common Mistakes to Avoid

| Mistake | Correct |
|---------|---------|
| Using `tee` to split video and audio | No `tee` — qtdemux exposes separate pads |
| Adding `sync=true` to waylandsink | `fullscreen=true` only — no `sync=true` |
| Omitting `--gst-debug=2` in generated commands | Include `-e --gst-debug=2` by default unless user explicitly requests a different debug setting |
| JSON format for `params`: `params="{...}"` | Semicolon-delimited: `params="params,nfft=96,...;"` |
| Putting `sink_1::` properties on a separate line | Put them on the `qtivcomposer` declaration line |
| Forgetting to omit the capsfilter after postprocess | `! queue ! mixer.` — no `video/x-raw` capsfilter at all on this branch |
| Applying HTP/NPU delegate without user specifying it | Audio pipeline is CPU by default |
| Using `qtimetamux` or `qtivoverlay` | Audio uses `qtivcomposer` directly — no metadata path |
| Adding `queue` between `qtivcomposer` and NV12 caps | No `queue` on single path to encoder — only add `queue` when branching |

---

## Verified AI Pipeline Templates

# Known Good Pipelines

This file provides deterministic templates for common scenarios.
Replace placeholders explicitly.
`qtimlpostprocess settings=...` is optional in all templates below and should be added only when explicitly requested. Exception: detection pipelines with a known confidence threshold — `settings="{\"confidence\": 51.0}"` is the canonical default for object detection and face detection; include it when building detection pipelines.

**`bbox-stabilization=true`** on `qtimlpostprocess`: add this property for object detection (yolov8, yolov5, yolo-nas), face detection (qfd), and palm detection (palmd) pipelines when a live camera or RTSP source is used. Omit for file-source pipelines unless user requests it explicitly.

## 1) Single-Stream Detection (File -> Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> ! \
  text/x-raw ! queue ! meta_mux.
```

**Notes:**
- `queue` is required between `video/x-raw,format=NV12` caps and `tee` for buffer flow before branch split.
- Main (passthrough) branch: `tee name=t ! qtimetamux name=...` — NO queue between tee and mux on the passthrough branch.
- AI branch queue order: after tee, after converter, after infer element, after postprocess (before mux).
- Add `settings="{\"confidence\": <VALUE>}"` to `qtimlpostprocess` when user requests confidence threshold tuning.
- Add `results=<N>` to `qtimlpostprocess` when user requests top-N results (e.g., for classification).

## 1b) Single-Stream Classification (File -> Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t ! qtimetamux name=class_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=mobilenet results=<N> labels=<LABELS_PATH> settings="{\"confidence\": <CONF>}" ! \
  text/x-raw ! queue ! class_mux.
```

**Notes:**
- `module=mobilenet` is the default and correct choice for MobileNet-style classification output — use it unless a specific reason to deviate is known. Switch to `module=mobilenet-softmax` only when the model is documented as softmax-normalized or the user explicitly says so. Do not infer the choice from precision (float32 vs w8a8) alone — that correlation is not reliable enough to override the default, and guessing wrong silently degrades classification output (confidence thresholding behaves differently against raw vs normalized scores). If genuinely unsure and it matters, ask.
- `results=<N>` controls the top-N classification labels to return (e.g., `results=5` for top-5). Include only when user specifies the number of results.
- `settings="{\"confidence\": <VALUE>}"` sets the confidence threshold. Include only when user specifies a confidence value.
- This template uses `text/x-raw` + `qtimetamux` (Topology A) since no `qtivcomposer` is otherwise needed here. `mobilenet`/`mobilenet-softmax` also support `video/x-raw` output (see `plugin-catalog.md`'s Module Output Types table) — only prefer that direct-to-composer path when a `qtivcomposer` is already required elsewhere in the pipeline.

## 2) Daisy-Chain Detection → Classification: YOLOX → MobileNet (File → Display)

**Canonical detection-classification daisy-chain** — Stage 1 YOLOX detects objects in full frames; Stage 2 MobileNet classifies detected ROIs.

Named ML elements are declared at the TOP of the gst-launch command (no `!` connectors between them), then wired by explicit pad addressing (`element_name. ! queue ! next_element.`).

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=/etc/models/yolox_w8a8.tflite \
  qtimlpostprocess name=stage_01_postproc module=yolov8 \
  labels=/etc/labels/yolov8.json settings="{\"confidence\": 51.0}" \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=/etc/models/mobilenet_v2_w8a8.tflite \
  qtimlpostprocess name=stage_02_postproc module=mobilenet \
  labels=/etc/labels/mobilenet.json \
  filesrc location=/etc/media/Draw_1080p_180s_30FPS.mp4 ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
  t_split_1. ! queue ! metamux_1. metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
  metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! metamux_2.
```

**Key structural rules:**
- ML elements are declared at the top WITHOUT `!` connectors — they are linked by name later.
- Stage 1 qtimlvconverter uses default mode (no `mode=` needed; `image-batch-non-cumulative` is the default).
- Stage 2 qtimlvconverter uses `mode=roi-batch-cumulative image-disposition=centre` — required for ROI-based classification.
- Stage 1 module: `yolov8` for YOLOX detection output (documented YOLOX compatibility); keeps the documented `settings="{\"confidence\": 51.0}"` object-detection default.
- Stage 2 module: `mobilenet` for MobileNet classification output; omits `settings` by default — the 51.0 confidence default is scoped to object detection/face detection, not classification.
- `tee name=t_split_1` splits: AI branch (→ stage_01_preproc → ... → metamux_1.) AND main video (→ metamux_1.).
- `metamux_1.` output: `metamux_1. ! queue ! tee name=t_split_2`.
- `tee name=t_split_2` splits: AI branch (→ stage_02_preproc → ... → metamux_2.) AND main video (→ metamux_2., listed LAST).
- Final output: `metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true`.
- The `t_split_2. ! queue ! metamux_2.` main video feed is placed AFTER the final overlay/sink line.
- Always include `queue` between `qtivoverlay` and `waylandsink` in daisy-chain pipelines.
- `results=<N>` on `qtimlpostprocess` is optional — include ONLY when user specifies top-N results count.
- `settings="{\"confidence\": <CONF>}"` — include only when user provides a confidence threshold value.
- `settings=<PATH>` — include only when user provides a settings file path.

## 2b) Daisy-Chain Pose Estimation: Person Detection → HRNet Pose (File → Display)

Pose estimation variant — Stage 1 detects persons; Stage 2 estimates body keypoints using HRNet. Use this two-stage form when the request explicitly asks for detector→ROI pose, names a person-foot detector alongside the pose model, or the subject is expected to be small/distant in frame. For a plain single-stage pose request (HRNet running directly on full frames, no detector), use Route A's generic single-stream template instead — see `model-catalog.md`'s HRNetPose row for both options.

Unlike single-stage classification/detection templates, `results=<N>` and `settings=<PATH>` are **mandatory here, not optional** — omitting them risks incorrect keypoint decoding, not just a missing tuning knob. `settings` must be a **file path** to a settings JSON, not inline `{"confidence": N}` JSON. If the user has not supplied the settings file path(s) or `results=` counts, do not invent a filename or number (e.g. `foot_track_net_settings.json`/`hrnet_settings.json` below are illustrative examples only, not real values to copy) — keep the `<SETTINGS_FILE_STAGE1>`/`<SETTINGS_FILE_STAGE2>`/`<RESULTS_STAGE1>`/`<RESULTS_STAGE2>` placeholders in the output and explicitly ask the user for them, the same way you would ask when the inference runtime is unspecified.

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE1> \
  qtimlpostprocess name=stage_01_postproc results=<RESULTS_STAGE1> module=<POSTPROC_STAGE1> \
  labels=<LABELS_STAGE1> settings=<SETTINGS_FILE_STAGE1> \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE2> \
  qtimlpostprocess name=stage_02_postproc results=<RESULTS_STAGE2> module=<POSTPROC_STAGE2> \
  labels=<LABELS_STAGE2> settings=<SETTINGS_FILE_STAGE2> \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
  t_split_1. ! queue ! metamux_1. metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
  metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! metamux_2.
```

**Key structural rules:**
- ML elements are declared at the top WITHOUT `!` connectors — they are linked by name later.
- Stage 1 qtimlvconverter uses default mode (`image-batch-non-cumulative` is the default — no need to set it explicitly).
- Stage 2 qtimlvconverter uses `mode=roi-batch-cumulative image-disposition=centre`.
- `tee name=t_split_1` splits: AI branch (→ stage_01_preproc → ... → metamux_1.) AND main video (→ metamux_1.).
- `metamux_1.` output: `metamux_1. ! queue ! tee name=t_split_2`.
- `tee name=t_split_2` splits: AI branch (→ stage_02_preproc → ... → metamux_2.) AND main video (→ metamux_2., listed LAST).
- Final output: `metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true`.
- The `t_split_2. ! queue ! metamux_2.` main video feed is placed AFTER the final overlay/sink line.
- Always include `queue` between `qtivoverlay` and `waylandsink` in daisy-chain pipelines.
- `settings` for qtimlpostprocess is a file path string in this template — always required for both stages, unlike the generic "include only when user provides a value" rule for single-stage pipelines.
- `results=<N>` is likewise required for both stages here, not optional.

## 2c) Daisy-Chain Detection → Classification: YOLOX → MobileNet (File → MP4 File)

**File-output variant of Template 2.** All ML element declarations and pipeline topology are identical to Template 2 (display). The ONLY difference is the final output: replace `waylandsink fullscreen=true sync=true` with the H.264 encode chain.

**Critical rules (do NOT deviate):**
- `qtivoverlay ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — the `queue` between `qtivoverlay` and `v4l2h264enc` is REQUIRED (same rule as between `qtivoverlay` and `waylandsink` in display pipelines)
- **No `sync=false`** on `filesink` for single-output (file-only) daisy-chain pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)
- `t_split_2. ! queue ! metamux_2.` main video feed is placed AFTER the final encode/filesink line (same as display variant)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE1> \
  qtimlpostprocess name=stage_01_postproc module=yolov8 \
  labels=<LABELS_STAGE1> settings="{\"confidence\": <CONF1>}" \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=<MODEL_STAGE2> \
  qtimlpostprocess name=stage_02_postproc module=mobilenet \
  labels=<LABELS_STAGE2> settings="{\"confidence\": <CONF2>}" \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
  t_split_1. ! queue ! metamux_1. metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
  metamux_2. ! queue ! qtivoverlay ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t_split_2. ! queue ! metamux_2.
```

## 3) Multistream AI Wall (3 Streams -> Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE_1> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t1 \
  filesrc location=<INPUT_FILE_2> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t2 \
  filesrc location=<INPUT_FILE_3> ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t3 \
  t1. ! queue ! m1. t1. ! queue ! qtimlvconverter ! <INFER_STAGE1> ! qtimlpostprocess module=<POSTPROC_STAGE1> labels=<LABELS_STAGE1> ! text/x-raw ! m1. \
  qtimetamux name=m1 ! qtivoverlay ! comp.sink_0 \
  t2. ! queue ! m2. t2. ! queue ! qtimlvconverter ! <INFER_STAGE2> ! qtimlpostprocess module=<POSTPROC_STAGE2> labels=<LABELS_STAGE2> ! text/x-raw ! m2. \
  qtimetamux name=m2 ! qtivoverlay ! comp.sink_1 \
  t3. ! queue ! m3. t3. ! queue ! qtimlvconverter ! <INFER_STAGE3> ! qtimlpostprocess module=<POSTPROC_STAGE3> labels=<LABELS_STAGE3> ! text/x-raw ! m3. \
  qtimetamux name=m3 ! qtivoverlay ! comp.sink_2 \
  qtivcomposer name=comp ! waylandsink fullscreen=true sync=true
```

## 4) Two-Stream Side-by-Side Detection (ISP Camera -> Display)

Single ISP camera: raw passthrough on left, AI inference + overlay on right, composed via qtivcomposer.

**Topology: TWO tee elements.** `tee name=t_src` splits raw/AI branches; `tee name=t` within the AI branch splits for qtimetamux passthrough and inference.

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! queue ! tee name=t_src \
  t_src. ! queue ! comp.sink_0 \
  t_src. ! queue ! tee name=t \
  t. ! queue ! obj_mux. \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": <CONF>}" ! \
  text/x-raw ! queue ! obj_mux. \
  qtimetamux name=obj_mux ! queue ! qtivoverlay ! queue ! comp.sink_1 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<<HALF_W>, <H>>" \
    sink_1::position="<<HALF_W>, 0>" sink_1::dimensions="<<HALF_W>, <H>>" ! \
  queue ! waylandsink fullscreen=true sync=true
```

**Key structural rules for two-stream side-by-side from a single camera:**
- `tee name=t_src` — first split: raw branch goes directly to `comp.sink_0`; second branch feeds second tee
- `tee name=t` — second split (within AI branch): main branch feeds `qtimetamux`; AI branch runs inference
- Main video branch feeds `qtimetamux` directly: `t. ! queue ! obj_mux.`
- `qtimetamux + qtivoverlay` MUST appear before `comp.sink_1` — the AI overlay branch uses standard Topology A merge, then the overlaid video enters the composer
- Composer pad syntax: `sink_N::position="<x, y>"` and `sink_N::dimensions="<width, height>"`
- `waylandsink fullscreen=true sync=true` — fullscreen is standard for ISP camera display pipelines

## 5) Three-Stream Object Detection (ISP Camera -> Display + MP4 File)

Single ISP camera: raw passthrough on left, AI overlay on right (display), and AI overlay also encoded to MP4 file.

**Topology: THREE tee elements.**
- `tee name=t_src` — splits raw branch (→ comp.sink_0) from AI branch
- `tee name=t` — within AI branch, splits for qtimetamux passthrough and inference
- `tee name=ai_tee` — after qtimetamux, splits the overlaid stream into display branch (qtivoverlay → comp.sink_1) and file encode branch

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! queue ! tee name=t_src \
  t_src. ! queue ! comp.sink_0 \
  t_src. ! queue ! tee name=t \
  t. ! queue ! obj_mux. \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": <CONF>}" ! \
  text/x-raw ! queue ! obj_mux. \
  qtimetamux name=obj_mux ! queue ! tee name=ai_tee \
  ai_tee. ! queue ! qtivoverlay ! queue ! comp.sink_1 \
  ai_tee. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> sync=false \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<<HALF_W>, <H>>" \
    sink_1::position="<<HALF_W>, 0>" sink_1::dimensions="<<HALF_W>, <H>>" ! \
  queue ! waylandsink fullscreen=true sync=true
```

**Key structural rules for three-stream (display + file) from a single camera:**
- `tee name=t_src` — first split: raw branch → comp.sink_0; AI branch → second tee
- `tee name=t` — second split (within AI branch): passthrough → qtimetamux; AI branch runs inference
- Main video branch feeds `qtimetamux` directly: `t. ! queue ! obj_mux.`
- `tee name=ai_tee` — third split (AFTER qtimetamux, BEFORE qtivoverlay): display branch → qtivoverlay → comp.sink_1; file branch → v4l2h264enc → h264parse → mp4mux → filesink
- `qtivoverlay` appears in the `ai_tee` display branch, NOT before the tee
- `filesink` must have `sync=false` to avoid clock sync issues with the display branch
- `waylandsink fullscreen=true sync=true` for ISP camera display

**Note:** `comp.sink_1`'s display path uses `qtimetamux`+`qtivoverlay`. If `<POSTPROC_MODULE>`'s category supports `video/x-raw`, the display path alone could feed `comp.sink_1` directly from `qtimlpostprocess`'s output instead (no capsfilter) — but here `ai_tee` also taps this stream's `qtimetamux` output for the MP4 file branch, so removing `qtimetamux` would require restructuring the file branch too; weigh whether the optimization is worth that tradeoff for this specific template.

## 5b) Three-Stream Object Detection (ISP Camera → Dual MP4 File: Composed + AI-Only)

Single ISP camera: raw passthrough left + AI overlay right composed to **MP4 file 1**, AND AI overlay stream alone encoded to **MP4 file 2**. No display output — both outputs are files.

**Topology: THREE tee elements.**
- `tee name=t_src` — splits raw branch (→ comp.sink_0) from AI branch
- `tee name=t` — within AI branch, splits for qtimetamux passthrough and inference
- `tee name=out_tee` — placed AFTER `qtimetamux` AND AFTER `qtivoverlay`, splits the overlaid stream into: composer branch (→ comp.sink_1) and AI-only file encode branch

**Key difference from Template 5:** Because BOTH file outputs require AI overlay applied, `qtivoverlay` appears BEFORE `out_tee` (on the shared path), not inside a branch. The `out_tee` then splits the already-overlaid stream.

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! queue ! tee name=t_src \
  t_src. ! queue ! comp.sink_0 \
  t_src. ! queue ! tee name=t \
  t. ! queue ! obj_mux. \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": <CONF>}" ! \
  text/x-raw ! queue ! obj_mux. \
  qtimetamux name=obj_mux ! queue ! qtivoverlay ! queue ! tee name=out_tee \
  out_tee. ! queue ! comp.sink_1 \
  out_tee. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4_AI_ONLY> sync=false \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<<HALF_W>, <H>>" \
    sink_1::position="<<HALF_W>, 0>" sink_1::dimensions="<<HALF_W>, <H>>" ! \
  queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4_COMPOSED> sync=false
```

**Key structural rules for three-stream (dual file) from a single camera:**
- `tee name=t_src` — first split: raw branch uses direct feed to `comp.sink_0`; AI branch → second tee
- `tee name=t` — second split (within AI branch): passthrough → qtimetamux; AI branch runs inference
- Main video branch feeds `qtimetamux` directly: `t. ! queue ! obj_mux.`
- `qtivoverlay` appears on the SHARED path BEFORE `out_tee` — both file outputs receive the AI overlay
- `tee name=out_tee` — third split (AFTER qtimetamux AND AFTER qtivoverlay): composer branch → comp.sink_1; AI-only file branch → v4l2h264enc → filesink
- **Both `filesink` elements require `sync=false`** — two parallel encode sinks from the same tee output
- `qtivcomposer` output → `queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4_COMPOSED> sync=false` — the composed output also needs `sync=false` when running parallel encode sinks
- Composer pad properties: `sink_0::position="<x, y>" sink_0::dimensions="<w, h>"` (separate position and dimensions, space after comma inside angle brackets)
- No `waylandsink` — this is a file-only pipeline

**Note:** This template requires `qtivoverlay`'s rendered output for the standalone AI-only file (`OUTPUT_MP4_AI_ONLY`), not just for `comp.sink_1` — so the direct-to-composer option from Templates 3/4 does not cleanly apply here unless the AI-only file requirement is also reworked to draw from a separate render.

## 6) Zero-Copy Split (Producer + Consumer)

Producer:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtiqmmfsrc camera=0 ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! \
  queue ! qtisocketsink socket=<SOCKET_PATH>
```

Consumer:

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtisocketsrc socket=<SOCKET_PATH> ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! tee name=s \
  s. ! queue ! zmux. \
  s. ! queue ! qtimlvconverter ! <INFER_ELEMENT_STAGE1> ! \
  qtimlpostprocess module=<POSTPROC_MODULE_STAGE1> labels=<LABELS_STAGE1> ! text/x-raw ! zmux. \
  qtimetamux name=zmux ! qtivoverlay ! waylandsink fullscreen=true sync=true
```

## 7) Single-Stream Detection (File -> MP4 File)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> ! text/x-raw ! queue ! meta_mux.
```

**Critical rules — do NOT deviate from this template:**
- `v4l2h264dec capture-io-mode=4 output-io-mode=4` — both io-mode properties are REQUIRED on the decoder; bare `v4l2h264dec` without them is wrong
- AI branch: `t. ! queue ! qtimlvconverter` — the `queue` immediately after the tee pad reference is REQUIRED; do NOT start the AI branch with `t. ! qtimlvconverter` (missing queue)
- `qtimlpostprocess module=<POSTPROC_MODULE>` — use `module=` property, NOT `postprocessing=`; for YOLOX use `module=yolov8`
- No `sync=false` on `filesink` for single-output (file-only) pipelines
- `v4l2h264enc` also requires `capture-io-mode=4 output-io-mode=4` for zero-copy DMA encode

## 8) Single-Stream Segmentation — Alpha Blend (File -> Display)

**Topology B** — segmentation mask from `deeplab-argmax` is alpha-blended with the original video using `qtivcomposer`. No `qtimetamux` or `qtivoverlay` used.

- `tee name=t` splits the NV12 stream into:
  - Passthrough branch → `qtivcomposer sink_0` (original video)
  - AI branch → `qtimlvconverter → qtimltflite → qtimlpostprocess` → rendered mask (no capsfilter) → `qtivcomposer sink_1`
- `sink_1::alpha=0.5` controls blend weight (user-specified alpha)
- Postprocess output feeds `seg_mix.` with **no capsfilter in between** — pinning `width`/`height` (regardless of format) fails caps fixation with `Fixated width in filter caps is not supported with current post-process type!`; the composer sink-pad `dimensions` sizes the tile instead
- No `qtimetamux` or `qtivoverlay` — this is a frame-blend pipeline, not an annotation pipeline

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=<ALPHA> ! queue ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=deeplab-argmax labels=<LABELS_PATH> ! queue ! seg_mix.
```

**Key structural rules:**
- Single `tee name=t` — one split into passthrough and AI branch (no second tee needed)
- Passthrough branch: `t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=<ALPHA>` — passthrough goes to `sink_0` (implicit), alpha is set on `sink_1`
- AI branch: `t. ! queue ! qtimlvconverter ! queue ! qtimltflite ... ! queue ! qtimlpostprocess module=deeplab-argmax ... ! queue ! seg_mix.`
- No capsfilter is placed between `qtimlpostprocess` and `seg_mix.` — omit it entirely so caps negotiate; pinning `width`/`height` fails caps fixation regardless of format
- Alpha value goes in `sink_1::alpha=<ALPHA>` property on the composer declaration line
- After composer: `! queue ! waylandsink fullscreen=true sync=true`
- **Do NOT use `qtimetamux` or `qtivoverlay`** in segmentation pipelines — this is the exclusive Topology B usage

## 8b) Single-Stream Segmentation — Alpha Blend (File -> MP4 File)

**Topology B** — segmentation mask from `deeplab-argmax` is alpha-blended with the original video using `qtivcomposer`, then the blended output is encoded to an MP4 file.

**CRITICAL difference from Template 8 (display):** After `qtivcomposer`, use `! video/x-raw,format=NV12 !` directly before `v4l2h264enc`. This direct capsfilter path matches compositor-native NV12 negotiation and avoids mmap failures.

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=<ALPHA> ! \
  video/x-raw,format=NV12 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=deeplab-argmax labels=<LABELS_PATH> ! queue ! seg_mix.
```

**Key structural rules:**
- Single `tee name=t` — one split into passthrough and AI branch (no second tee needed)
- Passthrough branch: `t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=<ALPHA>` — passthrough goes to `sink_0` (implicit), alpha is set on `sink_1`
- AI branch: `t. ! queue ! qtimlvconverter ! queue ! qtimltflite ... ! queue ! qtimlpostprocess module=deeplab-argmax ... ! queue ! seg_mix.`
- No capsfilter is placed between `qtimlpostprocess` and `seg_mix.` — omit it entirely so caps negotiate; pinning `width`/`height` fails caps fixation regardless of format
- Alpha value goes in `sink_1::alpha=<ALPHA>` property on the composer declaration line
- **After `qtivcomposer`: `! video/x-raw,format=NV12 !` directly before `v4l2h264enc`** — use the compositor’s native NV12 capsfilter path.
- **No `sync=false`** on `filesink` for single-output (file-only) pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)
- **Do NOT use `qtimetamux` or `qtivoverlay`** in segmentation pipelines — this is the exclusive Topology B usage

## 9) 4-Stream AI Wall — Classification + Face Detection + Segmentation + Object Detection (File → 2×2 Grid Display)

**Canonical 4-stream AI wall** using named ML elements declared at the TOP of the command, then wired by explicit pad addressing. Four independent decode paths feed four parallel AI branches composed into a 2×2 grid.

**Critical segmentation exception**: Stream 3 (segmentation) uses its own `qtivcomposer name=seg_mix` for alpha-blending — NOT `qtimetamux` + `qtivoverlay`. Its output must be cast to `video/x-raw,format=NV12` before feeding the final composer.

```bash
gst-launch-1.0 -e --gst-debug=2 \
qtimlvconverter name=<CLASS_PRE> \
qtimltflite name=<CLASS_INFER> model=<MODEL_CLASS> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<CLASS_POST> results=<RESULTS_CLASS> module=mobilenet labels=<LABELS_CLASS> settings="{\"confidence\": <CONF_CLASS>}" \
qtimetamux name=<CLASS_MUX> \
qtivoverlay name=<CLASS_OVL> \
qtimlvconverter name=<FACE_PRE> \
qtimltflite name=<FACE_INFER> model=<MODEL_FACE> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<FACE_POST> module=qfd results=<RESULTS_FACE> labels=<LABELS_FACE> \
qtimetamux name=<FACE_MUX> \
qtivoverlay name=<FACE_OVL> \
qtimlvconverter name=<SEG_PRE> \
qtimltflite name=<SEG_INFER> model=<MODEL_SEG> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<SEG_POST> module=deeplab-argmax labels=<LABELS_SEG> \
qtivcomposer name=<SEG_MIX> sink_1::alpha=<ALPHA_SEG> \
qtimlvconverter name=<OBJ_PRE> \
qtimltflite name=<OBJ_INFER> model=<MODEL_OBJ> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<OBJ_POST> module=yolov8 labels=<LABELS_OBJ> settings="{\"confidence\": <CONF_OBJ>}" \
qtimetamux name=<OBJ_MUX> \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 540>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 540>" \
  sink_2::position="<0, 540>" sink_2::dimensions="<960, 540>" \
  sink_3::position="<960, 540>" sink_3::dimensions="<960, 540>" ! \
queue ! waylandsink fullscreen=true sync=true \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<CLASS_TEE> \
<CLASS_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <CLASS_MUX>. \
<CLASS_TEE>. ! queue ! <CLASS_PRE>. <CLASS_PRE>. ! queue ! <CLASS_INFER>. <CLASS_INFER>. ! queue ! <CLASS_POST>. <CLASS_POST>. ! text/x-raw ! queue ! <CLASS_MUX>. \
<CLASS_MUX>. ! queue ! <CLASS_OVL>. <CLASS_OVL>. ! queue ! comp.sink_0 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<FACE_TEE> \
<FACE_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <FACE_MUX>. \
<FACE_TEE>. ! queue ! <FACE_PRE>. <FACE_PRE>. ! queue ! <FACE_INFER>. <FACE_INFER>. ! queue ! <FACE_POST>. <FACE_POST>. ! text/x-raw ! queue ! <FACE_MUX>. \
<FACE_MUX>. ! queue ! <FACE_OVL>. <FACE_OVL>. ! queue ! comp.sink_1 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<SEG_TEE> \
<SEG_TEE>. ! queue ! <SEG_MIX>. \
<SEG_TEE>. ! queue ! <SEG_PRE>. <SEG_PRE>. ! queue ! <SEG_INFER>. <SEG_INFER>. ! queue ! <SEG_POST>. <SEG_POST>. ! queue ! <SEG_MIX>. \
<SEG_MIX>. ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<OBJ_TEE> \
<OBJ_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <OBJ_MUX>. \
<OBJ_TEE>. ! queue ! <OBJ_PRE>. <OBJ_PRE>. ! queue ! <OBJ_INFER>. <OBJ_INFER>. ! queue ! <OBJ_POST>. <OBJ_POST>. ! text/x-raw ! queue ! <OBJ_MUX>. \
<OBJ_MUX>. ! queue ! qtivoverlay ! queue ! comp.sink_3
```

**Key structural rules:**
- **Named ML elements declared at the TOP** — all `qtimlvconverter`, `qtimltflite`, `qtimlpostprocess`, `qtimetamux`, `qtivoverlay`, and stream-local `qtivcomposer` elements are declared without `!` connectors at the top; the final `qtivcomposer name=comp` is also declared at top with its 4 sink pad properties, ending with `! \` to connect directly to `queue ! waylandsink`.
- **Four independent decode paths** — each stream has its own `filesrc → qtdemux → h264parse → v4l2h264dec → video/x-raw,format=NV12 → queue → tee`.
- **Classification stream (sink_0)**: `<CLASS_TEE>. → qtivtransform → <CLASS_MUX>.` (passthrough) + AI branch → `<CLASS_MUX>.` → `<CLASS_OVL>.` → `comp.sink_0`. Postprocess output: `text/x-raw`.
- **Face detection stream (sink_1)**: `<FACE_TEE>. → qtivtransform → <FACE_MUX>.` (passthrough) + AI branch → `<FACE_MUX>.` → `<FACE_OVL>.` → `comp.sink_1`. Postprocess output: `text/x-raw`.
- **Segmentation stream (sink_2)**: `<SEG_TEE>. → <SEG_MIX>.` (passthrough/sink_0) + AI branch (no capsfilter) → `<SEG_MIX>.` (sink_1). **No qtimetamux or qtivoverlay**. After `<SEG_MIX>`: `! video/x-raw,format=NV12 ! queue ! comp.sink_2` — the NV12 caps are required.
- **Object detection stream (sink_3)**: `<OBJ_TEE>. → qtivtransform → <OBJ_MUX>.` (passthrough) + AI branch → `<OBJ_MUX>.` → anonymous `qtivoverlay` (NOT named) → `comp.sink_3`. Postprocess output: `text/x-raw`.
- **Final composer output**: declared at top as `qtivcomposer name=comp sink_0::position... sink_3::... ! \` — the `! \` connects directly to `queue ! waylandsink fullscreen=true sync=true`.
- **Grid layout** (960×540 per cell): sink_0 top-left (0,0), sink_1 top-right (960,0), sink_2 bottom-left (0,540), sink_3 bottom-right (960,540). Cell 960×540 is 16:9 — AR-correct for 16:9 sources (no bars). For other source ARs, apply the AR-fit formula from `generation-rules.md` Multi-Stream Layout Rule.
- **Queue on each tee branch** — every branch from a tee has a queue immediately after the tee pad reference.
- **`qtivtransform` on the classification, face-detection, and object-detection passthrough branches** — each of these streams' tee feeds both a `qtimetamux` and (via that stream's overlay) a `comp.sink_N` pad, so per SKILL.md's *Buffer writability — `qtivoverlay` and `qtivcomposer`* rule, the passthrough branch needs `qtivtransform ! video/x-raw,format=NV12` before its `qtimetamux`, or that stream's boxes silently fail to render. The segmentation branch does not need this — it has no `qtimetamux` at all.
- **face detection `results=<N>`** — optional `results` property on `qtimlpostprocess module=qfd` (include only when user specifies).
- **classification `results=<N>` and `settings`** — include only when user specifies.
- **object detection `settings`** — include only when user specifies a confidence threshold.

## 9b) 4-Stream AI Wall — Classification + Face Detection + Segmentation + Object Detection (File → MP4 File)

**File-output variant of Template 9.** All four stream branches are identical to Template 9 (display). The only difference is the final `qtivcomposer name=comp` output: instead of `queue ! waylandsink`, use `video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>`.

**CRITICAL: Use `! video/x-raw,format=NV12 !` directly after `qtivcomposer name=comp`.** This direct composer-to-encoder path aligns with native NV12 negotiation and avoids mmap failures on DMA-backed compositor output.

```bash
gst-launch-1.0 -e --gst-debug=2 \
qtimlvconverter name=<CLASS_PRE> \
qtimltflite name=<CLASS_INFER> model=<MODEL_CLASS> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<CLASS_POST> results=<RESULTS_CLASS> module=mobilenet labels=<LABELS_CLASS> settings="{\"confidence\": <CONF_CLASS>}" \
qtimetamux name=<CLASS_MUX> \
qtivoverlay name=<CLASS_OVL> \
qtimlvconverter name=<FACE_PRE> \
qtimltflite name=<FACE_INFER> model=<MODEL_FACE> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<FACE_POST> module=qfd results=<RESULTS_FACE> labels=<LABELS_FACE> \
qtimetamux name=<FACE_MUX> \
qtivoverlay name=<FACE_OVL> \
qtimlvconverter name=<SEG_PRE> \
qtimltflite name=<SEG_INFER> model=<MODEL_SEG> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<SEG_POST> module=deeplab-argmax labels=<LABELS_SEG> \
qtivcomposer name=<SEG_MIX> sink_1::alpha=<ALPHA_SEG> \
qtimlvconverter name=<OBJ_PRE> \
qtimltflite name=<OBJ_INFER> model=<MODEL_OBJ> delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=<OBJ_POST> module=yolov8 labels=<LABELS_OBJ> settings="{\"confidence\": <CONF_OBJ>}" \
qtimetamux name=<OBJ_MUX> \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 540>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 540>" \
  sink_2::position="<0, 540>" sink_2::dimensions="<960, 540>" \
  sink_3::position="<960, 540>" sink_3::dimensions="<960, 540>" ! \
video/x-raw,format=NV12 ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<CLASS_TEE> \
<CLASS_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <CLASS_MUX>. \
<CLASS_TEE>. ! queue ! <CLASS_PRE>. <CLASS_PRE>. ! queue ! <CLASS_INFER>. <CLASS_INFER>. ! queue ! <CLASS_POST>. <CLASS_POST>. ! text/x-raw ! queue ! <CLASS_MUX>. \
<CLASS_MUX>. ! queue ! <CLASS_OVL>. <CLASS_OVL>. ! queue ! comp.sink_0 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<FACE_TEE> \
<FACE_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <FACE_MUX>. \
<FACE_TEE>. ! queue ! <FACE_PRE>. <FACE_PRE>. ! queue ! <FACE_INFER>. <FACE_INFER>. ! queue ! <FACE_POST>. <FACE_POST>. ! text/x-raw ! queue ! <FACE_MUX>. \
<FACE_MUX>. ! queue ! <FACE_OVL>. <FACE_OVL>. ! queue ! comp.sink_1 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<SEG_TEE> \
<SEG_TEE>. ! queue ! <SEG_MIX>. \
<SEG_TEE>. ! queue ! <SEG_PRE>. <SEG_PRE>. ! queue ! <SEG_INFER>. <SEG_INFER>. ! queue ! <SEG_POST>. <SEG_POST>. ! queue ! <SEG_MIX>. \
<SEG_MIX>. ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=<OBJ_TEE> \
<OBJ_TEE>. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! <OBJ_MUX>. \
<OBJ_TEE>. ! queue ! <OBJ_PRE>. <OBJ_PRE>. ! queue ! <OBJ_INFER>. <OBJ_INFER>. ! queue ! <OBJ_POST>. <OBJ_POST>. ! text/x-raw ! queue ! <OBJ_MUX>. \
<OBJ_MUX>. ! queue ! qtivoverlay ! queue ! comp.sink_3
```

**Key structural rules (differences from Template 9):**
- **Named ML elements declared at the TOP** — identical to Template 9; all stream elements declared without `!` connectors.
- **Four independent decode paths** — each stream has its own `filesrc → qtdemux → h264parse → v4l2h264dec → video/x-raw,format=NV12 → queue → tee`.
- **Final composer output (FILE variant)**: `qtivcomposer name=comp ... ! \` connects to `video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — NOT `queue ! waylandsink`.
- **`! video/x-raw,format=NV12 !` directly after `qtivcomposer name=comp`** — use the compositor’s native NV12 capsfilter path.
- **No `sync=false`** on `filesink` for single-output (file-only) pipelines.
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record).
- All four stream branch topologies (Classification/Topology A, Face Detection/Topology A, Segmentation/Topology B with seg_mix, Object Detection/Topology A) are identical to Template 9 — including the `qtivtransform` on the classification, face-detection, and object-detection passthrough branches (see Template 9's note; each of those streams' tee also feeds a `comp.sink_N` pad).
- **Grid layout** (960×540 per cell): sink_0 top-left (0,0), sink_1 top-right (960,0), sink_2 bottom-left (0,540), sink_3 bottom-right (960,540). Cell 960×540 is 16:9 — AR-correct for 16:9 sources (no bars). For other source ARs, apply the AR-fit formula from `generation-rules.md` Multi-Stream Layout Rule.

### Camera Source Adaptation for the 4-Stream AI Wall

Templates 9/9b are written with four independent `filesrc` decode chains because each stream's video is unrelated. When the request is for a single physical camera feeding all four AI branches instead, do not open `qtiqmmfsrc` four times — a device generally exposes one live feed per `camera=<N>`, and four concurrent opens of the same camera index is not a valid pattern.

Use one `qtiqmmfsrc`, teed four ways, in place of the four `filesrc → qtdemux → h264parse → v4l2h264dec` chains:

```text
qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1 ! queue ! tee name=wall_src
wall_src. ! queue ! tee name=<CLASS_TEE>   ← feeds Template 9/9b's classification branch
wall_src. ! queue ! tee name=<FACE_TEE>    ← feeds the face-detection branch
wall_src. ! queue ! tee name=<SEG_TEE>     ← feeds the segmentation branch
wall_src. ! queue ! tee name=<OBJ_TEE>     ← feeds the object-detection branch
```

Everything downstream of each `<..._TEE>` — preprocess, inference, postprocess, overlay/mix, composition — is unchanged from Templates 9/9b, including the `qtivtransform` on the classification/face/object passthrough branches. If multiple independent camera devices are genuinely available, use one `qtiqmmfsrc camera=<N>` per stream instead of the shared-tee form above.

## 11) Three-Stage Gesture Recognition Daisy-Chain (File → Display)

**Canonical gesture recognition pipeline** — Stage 1 detects palms; Stage 2 estimates hand landmarks on palm ROIs; Stage 3 runs gesture embedding (MODEL_NAME_3) followed by gesture classification (MODEL_NAME_4). This pipeline uses a special topology distinct from the standard two-stage daisy-chain.

**Key structural differences from standard two-stage daisy-chain:**
- `qtimetatransform module=roi-palmd` is inserted **between `metamux_1` and `t_split_2`** — required to transform palm ROI metadata into cropped regions for the landmark stage
- Stage 1 postprocess uses `bbox-stabilization=true` — canonical property for palm detection
- Stage 2 `qtimlvconverter` uses `mode=roi-batch-cumulative` **without** `image-disposition=centre`
- Stage 2 inference output splits into TWO postprocess branches via `tee name=t_split_4`:
  - Branch A: `module=hlandmark` → feeds `metamux_2.` directly (landmark keypoint metadata)
  - Branch B: `module=tensor` → feeds stage_03_1_inference (embedder) → stage_03_2_inference (classifier) → `module=mobilenet` → feeds `metamux_2.` (gesture label metadata)
- Both Stage 2 and Stage 3 postprocess outputs merge into `metamux_2` (only TWO qtimetamux elements total, NOT three)
- Stage 3 uses two sequential `qtimltflite` elements with NO intermediate queue, connected as: `stage_03_1_inference. ! stage_03_2_inference.`

**Stage 3 dual-model pattern** (gesture embedder → classifier):
- No `qtimlvconverter` for Stage 3 — it consumes tensor output from `stage_02_2_postproc` directly
- `stage_03_1_inference` (gesture_embedder.tflite) → directly into `stage_03_2_inference` (canned_gesture_classifier.tflite) — NO queue between them
- `stage_03_postproc module=mobilenet` for gesture label output

**Named element declarations:**
All `qtimlvconverter`, `qtimltflite`, and `qtimlpostprocess` elements are declared at the TOP without `!` connectors. Note that Stage 3 has NO `qtimlvconverter` — it consumes tensors directly from the `tensor` postprocess.

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=gpu \
  model=<MODEL_PALM_DETECTION> \
  qtimlpostprocess name=stage_01_postproc results=2 bbox-stabilization=true module=palmd \
  labels=<LABELS_PALM> settings=<SETTINGS_PALM> \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative \
  qtimltflite name=stage_02_inference delegate=gpu \
  model=<MODEL_HAND_LANDMARK> \
  qtimlpostprocess name=stage_02_1_postproc module=hlandmark \
  labels=<LABELS_LANDMARK> settings=<SETTINGS_LANDMARK> \
  qtimlpostprocess name=stage_02_2_postproc module=tensor \
  qtimltflite name=stage_03_1_inference delegate=gpu \
  model=<MODEL_GESTURE_EMBEDDER> \
  qtimltflite name=stage_03_2_inference delegate=gpu \
  model=<MODEL_GESTURE_CLASSIFIER> \
  qtimlpostprocess name=stage_03_postproc module=mobilenet \
  labels=<LABELS_GESTURE> \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! qtimetamux name=metamux_1 ! queue ! qtimetatransform module=roi-palmd ! \
  queue ! tee name=t_split_2 \
  t_split_1. ! queue ! stage_01_preproc. \
  stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. \
  stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  t_split_2. ! queue ! qtimetamux name=metamux_2 ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! stage_02_preproc. \
  stage_02_preproc. ! queue ! stage_02_inference. \
  stage_02_inference. ! queue ! tee name=t_split_4 \
  t_split_4. ! queue ! stage_02_1_postproc. stage_02_1_postproc. ! text/x-raw ! metamux_2. \
  t_split_4. ! queue ! stage_02_2_postproc. stage_02_2_postproc. ! queue ! \
  stage_03_1_inference. stage_03_1_inference. ! stage_03_2_inference. \
  stage_03_2_inference. ! stage_03_postproc. stage_03_postproc. ! text/x-raw ! metamux_2.
```

**Key structural rules:**
- `qtimetatransform module=roi-palmd` is placed on the MAIN PATH after `metamux_1`, before `tee name=t_split_2` — this is mandatory for gesture recognition to convert palm metadata into ROI crops
- `t_split_1` AI branch goes to `stage_01_preproc.` and feeds `metamux_1.` with `text/x-raw`
- `t_split_1` main branch goes to `qtimetamux name=metamux_1` (passthrough pad, listed FIRST)
- `metamux_1` main output is chained: `! queue ! qtimetatransform module=roi-palmd ! queue ! tee name=t_split_2`
- `t_split_2` main branch goes to `qtimetamux name=metamux_2` (listed FIRST on t_split_2)
- `metamux_2` main output goes to `qtivoverlay ! queue ! waylandsink` — listed immediately after metamux_2 declaration
- `t_split_2` AI branch listed AFTER the sink path: `t_split_2. ! queue ! stage_02_preproc.`
- Stage 2 inference output goes to `tee name=t_split_4` (NOT directly to postprocess)
- `t_split_4` Branch A: `stage_02_1_postproc` (hlandmark) → `text/x-raw ! metamux_2.`
- `t_split_4` Branch B: `stage_02_2_postproc` (tensor) → `stage_03_1_inference.` → `stage_03_2_inference.` → `stage_03_postproc.` → `text/x-raw ! metamux_2.`
- Stage 3 inference elements are connected WITHOUT queue between them: `stage_03_1_inference. ! stage_03_2_inference.`
- `stage_03_postproc module=mobilenet` — gesture classification output uses mobilenet module
- Only TWO qtimetamux elements: `metamux_1` (Stage 1) and `metamux_2` (Stage 2 + Stage 3 merge)
- `delegate=gpu` for all stages (NOT `delegate=external`)
- `bbox-stabilization=true` on Stage 1 postprocess is required for palm detection stability
- `results=2` on Stage 1 postprocess — maximum palm detections per frame
- Stage 2 settings file (hlandmark_settings.json) is mandatory — passed as `settings=<PATH>`
- Stage 2 qtimlvconverter: only `mode=roi-batch-cumulative` — do NOT add `image-disposition=centre` for gesture landmark stage

## 11b) Three-Stage Gesture Recognition Daisy-Chain (File → MP4 File)

**File-output variant of Template 11.** All ML element declarations and pipeline topology are identical to Template 11 (display). The ONLY difference is the final output: replace `waylandsink fullscreen=true sync=true` with the H.264 encode chain.

**Critical rules (do NOT deviate):**
- `qtivoverlay ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — the `queue` between `qtivoverlay` and `v4l2h264enc` is REQUIRED
- **No `sync=false`** on `filesink` for single-output (file-only) gesture recognition pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)
- All other structural rules from Template 11 apply unchanged (qtimetatransform, t_split_4, Stage 3 dual-model, only two metamux elements, delegate=gpu, etc.)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=gpu \
  model=<MODEL_PALM_DETECTION> \
  qtimlpostprocess name=stage_01_postproc results=2 bbox-stabilization=true module=palmd \
  labels=<LABELS_PALM> settings=<SETTINGS_PALM> \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative \
  qtimltflite name=stage_02_inference delegate=gpu \
  model=<MODEL_HAND_LANDMARK> \
  qtimlpostprocess name=stage_02_1_postproc module=hlandmark \
  labels=<LABELS_LANDMARK> settings=<SETTINGS_LANDMARK> \
  qtimlpostprocess name=stage_02_2_postproc module=tensor \
  qtimltflite name=stage_03_1_inference delegate=gpu \
  model=<MODEL_GESTURE_EMBEDDER> \
  qtimltflite name=stage_03_2_inference delegate=gpu \
  model=<MODEL_GESTURE_CLASSIFIER> \
  qtimlpostprocess name=stage_03_postproc module=mobilenet \
  labels=<LABELS_GESTURE> \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! qtimetamux name=metamux_1 ! queue ! qtimetatransform module=roi-palmd ! \
  queue ! tee name=t_split_2 \
  t_split_1. ! queue ! stage_01_preproc. \
  stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. \
  stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  t_split_2. ! queue ! qtimetamux name=metamux_2 ! queue ! qtivoverlay ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t_split_2. ! queue ! stage_02_preproc. \
  stage_02_preproc. ! queue ! stage_02_inference. \
  stage_02_inference. ! queue ! tee name=t_split_4 \
  t_split_4. ! queue ! stage_02_1_postproc. stage_02_1_postproc. ! text/x-raw ! metamux_2. \
  t_split_4. ! queue ! stage_02_2_postproc. stage_02_2_postproc. ! queue ! \
  stage_03_1_inference. stage_03_1_inference. ! stage_03_2_inference. \
  stage_03_2_inference. ! stage_03_postproc. stage_03_postproc. ! text/x-raw ! metamux_2.
```

## 10) Super Resolution — Side-by-Side (File → Display)

**Topology B** — QuickSRNet produces a full upscaled RGB frame via `qtimlpostprocess module=srnet`. The original NV12 passthrough and the SR output are composed side-by-side with `qtivcomposer`. No `qtimetamux` or `qtivoverlay` used.

**Key structural rules:**
- Single `tee name=t` — one split into passthrough and SR AI branch
- Passthrough branch: `t. ! queue ! qtivcomposer name=mixer sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>"` — the passthrough connects implicitly as `sink_0` (first connection)
- SR AI branch: `t. ! qtimlvconverter ! queue !` — **NO queue** between `tee` and `qtimlvconverter` in the SR branch
- After `qtimlpostprocess module=srnet`: caps filter is `video/x-raw,format=RGB` (no explicit dimensions)
- SR branch ends with `! queue ! mixer.` — the unnamed `mixer.` pad becomes `sink_1`
- After composer: `! queue ! waylandsink fullscreen=true sync=true`
- **Do NOT use `qtimetamux` or `qtivoverlay`** — this is a pure Topology B frame-composition pipeline
- `external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` MUST be included
- `external-delegate-path=libQnnTFLiteDelegate.so` — use bare filename, NOT full path

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtivcomposer name=mixer \
    sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" \
    sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! \
  queue ! waylandsink fullscreen=true sync=true \
  t. ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=srnet ! video/x-raw,format=RGB ! queue ! mixer.
```

## 10b) Super Resolution — Side-by-Side (File → MP4 File)

**Topology B** — QuickSRNet produces a full upscaled RGB frame via `qtimlpostprocess module=srnet`. The original NV12 passthrough and the SR output are composed side-by-side with `qtivcomposer`, then the composed output is encoded to an MP4 file.

**CRITICAL difference from Template 10 (display):** After `qtivcomposer`, use `! video/x-raw,format=NV12 !` directly before `v4l2h264enc`. This is the supported direct capsfilter path with native NV12 negotiation.

**Key structural rules:**
- Single `tee name=t` — one split into passthrough and SR AI branch
- Passthrough branch: `t. ! queue ! qtivcomposer name=mixer sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>"` — passthrough connects implicitly as `sink_0` (first connection)
- SR AI branch: `t. ! qtimlvconverter ! queue !` — **NO queue** between `tee` and `qtimlvconverter` in the SR branch
- After `qtimlpostprocess module=srnet`: caps filter is `video/x-raw,format=RGB` (no explicit dimensions)
- SR branch ends with `! queue ! mixer.` — the unnamed `mixer.` pad becomes `sink_1`
- **After `qtivcomposer`: `! video/x-raw,format=NV12 !` directly before `v4l2h264enc`** — use this direct capsfilter path.
- **No `sync=false`** on `filesink` for single-output (file-only) pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)
- **Do NOT use `qtimetamux` or `qtivoverlay`** — pure Topology B

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtivcomposer name=mixer \
    sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" \
    sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! \
  video/x-raw,format=NV12 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4> \
  t. ! qtimlvconverter ! queue ! \
  qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=srnet ! video/x-raw,format=RGB ! queue ! mixer.
```

## 12) Audio Classification — FLAC Audio + H.264 Video (File → Display)

**Unique topology**: single `filesrc ! qtdemux name=demux` splits into TWO branches using named demux pads:
- **Video path**: `demux. ! queue ! h264parse ! v4l2h264dec → NV12 → queue → qtivcomposer name=mixer` (declared inline with overlay pad properties)
- **Audio path**: `demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! audiobuffersplit → qtimlaconverter → qtimltflite → qtimlpostprocess module=yamnet → queue → mixer.` (no capsfilter after postprocess)

The audio classification result is composed over the video using `qtivcomposer` (NOT `qtimetamux`/`qtivoverlay`), with no capsfilter between `qtimlpostprocess` and the composer.

**Critical notes:**
- `waylandsink fullscreen=true` — omit the `sync` property entirely for audio classification pipelines; do not add `sync=true` or `sync=false`
- `qtimlaconverter` uses `params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;"` — semicolon-delimited string format, NOT JSON
- `audiobuffersplit output-buffer-size=<SIZE>` — controls the chunk size fed to qtimlaconverter
- `qtimltflite name=infeng` — give inference element a name for debugging
- `qtimlpostprocess` property order: `settings=... results=... module=yamnet labels=...` — settings and results come BEFORE module and labels
- `yamnet` module output: no capsfilter after `qtimlpostprocess` — feed the rendered panel directly into `queue ! mixer.`; a pinned capsfilter here fails caps fixation regardless of format
- `qtivcomposer name=mixer` is declared **inline on the video path** with `sink_1::position` and `sink_1::dimensions` properties — the audio result connects as `sink_1` (the unnamed `.` pad)
- `demux.` (the named demux element pad) is used to reference both video and audio pads from `qtdemux name=demux`
- Audio decode chain: `flacparse ! flacdec` (two elements, NOT just `flacdec`)
- No `tee` element needed — qtdemux naturally splits video and audio to separate pads
- **No HTP delegate in the canonical docs example** — but if user specifies HTP backend, add: `delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"`

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux name=demux \
  demux. ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>" ! \
  queue ! waylandsink fullscreen=true \
  demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! \
  audiobuffersplit output-buffer-size=<BUFFER_SIZE> ! queue ! \
  qtimlaconverter sample-rate=<SAMPLE_RATE> feature=<FEATURE> \
    params="params,nfft=<NFFT>,nhop=<NHOP>,nmels=<NMELS>,chunklen=<CHUNKLEN>;" ! queue ! \
  qtimltflite name=infeng model=<MODEL_PATH> \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! \
  qtimlpostprocess settings="{\"confidence\": <CONFIDENCE>}" results=<RESULTS> \
    module=yamnet labels=<LABELS_PATH> ! \
  queue ! mixer.
```

**Key structural rules:**
- `filesrc ! qtdemux name=demux` — the demux element MUST be named so both pads can be referenced
- Video branch: starts with `demux. ! queue ! h264parse ! v4l2h264dec ...` — queue immediately after demux pad
- Audio branch: starts with `demux. ! queue ! flacparse ! flacdec ...` — queue immediately after demux pad
- `qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>"` is declared inline in the video path (after NV12 caps) — NOT separately at the top
- The video stream connects implicitly to `sink_0` of the composer (first connection)
- Audio overlay output ends with `! queue ! mixer.` — the unnamed pad becomes `sink_1`
- After `qtimlpostprocess module=yamnet`: no explicit capsfilter — feed the rendered output straight into `queue ! mixer.`; pinning `width`/`height` here fails caps fixation regardless of format
- `audiobuffersplit` uses `output-buffer-size` property (not `buffer-size`)
- `qtimlaconverter` property names: `sample-rate`, `feature`, `params` (params is a string in format `"params,key=value,...;"`)
- `waylandsink fullscreen=true` — omit the `sync` property entirely for audio classification

## 12b) Audio Classification — FLAC Audio + H.264 Video (File → MP4 File)

**File-output variant of Template 12.** All pipeline topology is identical to Template 12 (display). The ONLY difference is the final output: `qtivcomposer` output goes to `video/x-raw,format=NV12 ! v4l2h264enc ! h264parse ! mp4mux ! filesink` instead of `queue ! waylandsink`.

**CRITICAL: Use `! video/x-raw,format=NV12 !` directly after `qtivcomposer`.** This direct capsfilter path aligns with compositor-native NV12 negotiation and avoids mmap failures.

**Critical rules (do NOT deviate):**
- `qtivcomposer name=mixer ... ! video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — direct capsfilter after composer
- **No `sync=false`** on `filesink` for single-output (file-only) audio classification pipelines
- `v4l2h264enc` requires `capture-io-mode=4`; `output-io-mode` depends on upstream source — see `source-sink-patterns.md` File Output section (4 for file/RTSP, 5 for camera source or AV record)
- All audio path rules from Template 12 apply unchanged (flacparse/flacdec, audiobuffersplit, qtimlaconverter, qtimlpostprocess property order)

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux name=demux \
  demux. ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>" ! \
  video/x-raw,format=NV12 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
  filesink location=<OUTPUT_MP4> \
  demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! \
  audiobuffersplit output-buffer-size=<BUFFER_SIZE> ! queue ! \
  qtimlaconverter sample-rate=<SAMPLE_RATE> feature=<FEATURE> \
    params="params,nfft=<NFFT>,nhop=<NHOP>,nmels=<NMELS>,chunklen=<CHUNKLEN>;" ! queue ! \
  qtimltflite name=infeng model=<MODEL_PATH> \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! \
  qtimlpostprocess settings="{\"confidence\": <CONFIDENCE>}" results=<RESULTS> \
    module=yamnet labels=<LABELS_PATH> ! \
  queue ! mixer.
```

**Key structural rules:**
- All audio path and demux topology rules from Template 12 apply unchanged
- `qtivcomposer ! video/x-raw,format=NV12` — caps filter directly after composer (no queue between them)
- `v4l2h264enc capture-io-mode=4 output-io-mode=4` — both IO mode properties required
- **No `sync=false` on `filesink`** for single-output file pipelines
- Include `-e --gst-debug=2` by default in generated audio pipelines unless the user explicitly requests a different debug setting

---

## Input Source Variant Templates

All AI pipelines support four input source types. The source chain is the only thing that changes — the AI stage and output are identical. Templates below show the source-only variation; combine with any AI pipeline template above.

### Template S-1: USB Camera Source (v4l2src → NV12)

```bash
# Replace the filesrc/qtdemux/decode chain with:
v4l2src device=<DEVICE_NODE> ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
text/x-raw ! queue ! meta_mux.
```

**Notes:**
- USB cameras output `video/x-raw,format=YUY2` — `qtivtransform` is **required** for YUY2→NV12 before inference.
- `<DEVICE_NODE>` — e.g., `/dev/video0`
- `bbox-stabilization=true` recommended for detection pipelines with live camera input.

---

### Template S-2: RTSP Source (rtspsrc → NV12)

```bash
# Replace the filesrc/qtdemux/decode chain with:
rtspsrc location=<RTSP_URL> latency=200 ! rtph264depay ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
text/x-raw ! queue ! meta_mux.
```

**Notes:**
- `latency=200` is the standard jitter buffer for network RTSP.
- For H.265 RTSP: replace `rtph264depay ! h264parse ! v4l2h264dec` with `rtph265depay ! h265parse ! v4l2h265dec`.

---

### Template S-3: ISP Camera Source (qticamsrc → NV12)

```bash
# Replace the filesrc/qtdemux/decode chain with:
qticamsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t ! qtimetamux name=meta_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=<POSTPROC_MODULE> labels=<LABELS_PATH> settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
text/x-raw ! queue ! meta_mux.
```

**Notes:**
- ISP camera (`qticamsrc`) outputs NV12 directly — use direct NV12 path unless a selected template calls for an additional transform stage.
- `camera=0` is the default; add `camera=<N>` if needed.
- `bbox-stabilization=true` recommended for live camera detection pipelines.

---

## Template 13: Two-Stream Detection (ISP Camera — Original + Annotated Side-by-Side)

Shows original camera feed alongside AI-annotated feed in side-by-side composition. Useful for comparing raw vs processed output.

```bash
gst-launch-1.0 -e --gst-debug=2 \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! \
queue ! waylandsink fullscreen=true sync=true \
qtimetamux name=obj_mux ! queue ! qtivoverlay ! queue ! comp.sink_1 \
qticamsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t_src \
t_src. ! queue ! comp.sink_0 \
t_src. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=<LABELS_PATH> settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
text/x-raw ! queue ! obj_mux. \
t_src. ! queue ! obj_mux.
```

**Notes:**
- Composer declared at top — referenced before camera element.
- Three branches from `t_src`: (1) raw passthrough → `comp.sink_0`, (2) AI branch → overlay → `comp.sink_1`, (3) passthrough to `obj_mux` for metadata alignment.
- `qtivoverlay` is between `obj_mux` and `comp.sink_1` — annotates the AI branch only.
- Output: left half = raw, right half = AI-annotated.

---

## Template 13b: Three-Stream Detection (ISP Camera — Original + Annotated + MP4 File Output)

Extends Template 13 by also encoding the annotated output to MP4.

```bash
gst-launch-1.0 -e --gst-debug=2 \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! \
queue ! waylandsink fullscreen=true sync=true \
qtimetamux name=obj_mux ! queue ! tee name=ai_tee \
ai_tee. ! queue ! qtivoverlay ! queue ! comp.sink_1 \
ai_tee. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
filesink location=<OUTPUT_MP4> sync=false \
qticamsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t_src \
t_src. ! queue ! comp.sink_0 \
t_src. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=<LABELS_PATH> settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
text/x-raw ! queue ! obj_mux. \
t_src. ! queue ! obj_mux.
```

**Notes:**
- After `obj_mux`, a `tee name=ai_tee` splits annotated output to display and file.
- `sync=false` on filesink — decouples file encoding from display clock.
- Two simultaneous encoder instances share GPU resources.

**Note:** This template needs `qtivoverlay`'s rendered output for both `comp.sink_1` and the MP4 file branch — same caveat as Template 5b: the direct-to-composer option doesn't cleanly apply unless the file branch is also reworked to draw from a separate render.

---

## Template 14: Face Detection (File → Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=face_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=qfd labels=<LABELS_PATH> ! text/x-raw ! queue ! face_mux.
```

**Notes:**
- `module=qfd` for Qualcomm Face Detection.
- `qfd` outputs `text/x-raw` — feeds `qtimetamux`, not `qtivcomposer`.
- No `settings` required in canonical form; add `settings="{\"confidence\": <VALUE>}"` only when user requests threshold tuning.
- For camera sources: replace filesrc/decode chain with Template S-1/S-2/S-3 chain.

---

## Template 17: Three-Stage Face Recognition Daisy-Chain (File → Display)

**Canonical face recognition pipeline** — Stage 1 detects faces on the full frame; Stage 2 runs facial landmark/3DMM pose on the Stage 1 ROI; Stage 3 runs face recognition/embedding on the Stage 2 output. Each stage is muxed into its own `qtimetamux`, chained via `tee`, and the final stage feeds `qtivoverlay ! waylandsink`.

**Stage order is mandatory and non-negotiable — do not reorder based on the order models appear in the user's prompt:**

| Stage | Purpose | Model (reference name) | `qtimlpostprocess module=` | `qtimlvconverter mode=` | Labels required | Settings required | `results` |
|---|---|---|---|---|---|---|---|
| 1 | Face detection (full frame) | face_det_lite / Lightweight-Face-Detection | `qfd` | `image-batch-non-cumulative` | Yes — detection label file | Optional — inline `{"confidence": <N>}` | 6 (reference default) |
| 2 | Facial landmark / 3DMM pose (on Stage 1 ROI) | facemap_3dmm / Facial-Landmark-Detection | `lite-3dmm` | `roi-batch-cumulative` + `image-disposition=centre` | No — landmark-only output, no label file | Yes — mandatory settings file (e.g. `facemap_3dmm_settings.json`) | 6 (reference default) |
| 3 | Face recognition / classification (on Stage 2 ROI) | face_attrib_net / Facial-Attribute-Detection | `qfr` | `roi-batch-cumulative` + `image-disposition=centre` | Yes — recognition label file (registered identities) | Yes — mandatory settings file (contains confidence + face database paths) | 6 (reference default) |

**Non-obvious details that break the pipeline if missed:**
- **Never run recognition (`qfr`) at Stage 2.** It must consume the landmark-aligned crop from Stage 2, not the raw Stage 1 detection ROI directly — this is a silent accuracy bug, not a build/link failure, so it won't surface as an error.
- **Labels/settings are per-stage, never shared.** Stage 1 needs a detection label file; Stage 2 needs no label file at all (only a settings file); Stage 3 needs both a recognition label file (registered names) and a settings file (confidence + face database paths). Do not reuse one labels/settings pair across stages even if the user only supplies one of each — ask which file belongs to which stage instead of guessing.
- **File names are not fixed strings.** `face_detection.json`, `facemap_3dmm_settings.json`, `face_recognition.json`, `face_recognition_settings.json` are conventional names — accept whatever filenames the user actually provides and slot each into the stage its content/pairing indicates (e.g. whichever settings file is paired with the face-recognition model is the Stage 3 settings file, regardless of its literal name).
- **Stage 3's settings file is a face database, not just a threshold.** Per the official docs, the mandatory Stage 3 settings JSON has the shape `{"confidence": <N>, "databases": [{"id": 0, "database": "/etc/data/face0.bin"}, ...]}` — each registered person needs a corresponding entry and a `faceN.bin` binary generated via the device's face-registration procedure (`facedb.py`). Recognition without at least one registered `faceN.bin` + database entry will run but never produce a positive identity match.
- **`qtimlvconverter mode=` differs by stage**: Stage 1 uses `image-batch-non-cumulative` (whole-frame, no ROI yet); Stages 2 and 3 use `roi-batch-cumulative` with `image-disposition=centre` (crop centered on the upstream ROI).
- **`external` delegate (HTP backend) for all three stages** — these are w8a8-only models on device, no float/gpu variant.
- Camera source variant (matches the official face-registration pipeline) uses `qticamsrc video_0::type=video` for the live preview pad and a `qtivtransform ! video/x-raw,format=NV12` on the inference source pad — see `plugin-catalog.md` "Overlay writability" note if composing with `qtivcomposer` elsewhere in the same pipeline.
- **`bbox-stabilization=true` on Stage 1 postprocess is optional here, not required.** The general rule (this file, Template 1 notes) says to add it for `qfd` on live camera/RTSP sources — Template 17's canonical source is camera/RTSP — but this topology keeps stabilization opt-in. Add it only if the user asks for jitter reduction; do not add it by default just because the general camera-source rule would otherwise apply.

**Named element declarations** (all `qtimlvconverter`, `qtimltflite`, `qtimlpostprocess` at the TOP, no `!` connectors):

```bash
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc mode=image-batch-non-cumulative \
  qtimltflite name=stage_01_inference model=<MODEL_FACE_DET> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  qtimlpostprocess name=stage_01_postproc module=qfd results=6 \
  labels=<LABELS_FACE_DET> settings="{\"confidence\": <VALUE>}" \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference model=<MODEL_FACE_LANDMARK> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimlpostprocess name=stage_02_postproc module=lite-3dmm results=6 settings=<SETTINGS_FACEMAP_3DMM> \
  qtimlvconverter name=stage_03_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_03_inference model=<MODEL_FACE_RECOGNITION> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimlpostprocess name=stage_03_postproc module=qfr results=6 \
  labels=<LABELS_FACE_RECOGNITION> settings=<SETTINGS_FACE_RECOGNITION> \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! qtimetamux name=metamux_1 ! queue ! tee name=t_split_2 \
  t_split_1. ! queue ! stage_01_preproc. \
  stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. \
  stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  t_split_2. ! queue ! qtimetamux name=metamux_2 ! queue ! tee name=t_split_3 \
  t_split_2. ! queue ! stage_02_preproc. \
  stage_02_preproc. ! queue ! stage_02_inference. \
  stage_02_inference. ! queue ! stage_02_postproc. \
  stage_02_postproc. ! text/x-raw ! queue ! metamux_2. \
  t_split_3. ! queue ! qtimetamux name=metamux_3 ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_3. ! queue ! stage_03_preproc. \
  stage_03_preproc. ! queue ! stage_03_inference. \
  stage_03_inference. ! queue ! stage_03_postproc. \
  stage_03_postproc. ! text/x-raw ! queue ! metamux_3.
```

**Key structural rules:**
- Three `qtimetamux` elements total (`metamux_1`, `metamux_2`, `metamux_3`) — one per stage, unlike gesture recognition's two-metamux merge pattern (Template 11). Face recognition does not merge stages into a shared mux; each stage's ROI metadata must persist independently through to the final overlay.
- `t_split_1`/`t_split_2`/`t_split_3` each place the passthrough branch to the next `qtimetamux` FIRST, then the AI branch to that stage's `qtimlvconverter` SECOND — matches the declaration order convention used in Template 11.
- Final `metamux_3` feeds `qtivoverlay ! queue ! waylandsink fullscreen=true sync=true` — same overlay/display pattern as every other Topology A template.
- For MP4 file output instead of display: replace the `qtivoverlay ! queue ! waylandsink ...` segment with `qtivoverlay ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=<OUTPUT_MP4>` — no `sync=false` on the filesink for single-output pipelines, consistent with every other file-output template in this file.
- C app equivalent: use three separate `qtimlpostprocess` instances with role-based names such as `qtimlvdetection`, `qtimlvpose`, and `qtimlvclassification`, and set modules with `get_enum_value(element, "module", "<nick>")` — see `c-app-development.md` for the C daisy-chain pattern; module nicks and stage order are identical to the gst-launch form above.

---

## Template 18: Face Registration / Enrollment (Camera → Tensor Capture)

**Cannot be expressed as `gst-launch-1.0`.** Every other template in this file is a static pipeline description with no runtime interaction — `gst-launch-1.0` builds the graph, runs it, and tears it down with no way to trigger an element's action signals while PLAYING. Registration requires firing the `camsrc` element's `capture-image` action signal five separate times, on demand, each time the subject's head reaches a specific angle while watching the live preview. `gst-launch-1.0` exposes no interactive console for this. **This is the one AI use case in this skill that must use `gst-pipeline-app` instead of `gst-launch-1.0` — do not attempt to force this into a `gst-launch-1.0` artifact; if a request insists on `gst-launch-1.0` specifically for registration, tell the user it is not possible and explain why (no runtime action-signal trigger in `gst-launch-1.0`), then offer the `gst-pipeline-app` form instead.**

Registration is a **prerequisite tool pipeline** for Template 17, not an alternate form of it — it produces the `faceN.bin` + database/label entries that Template 17's Stage 3 (`qfr`) needs to return a positive identity match. Running Template 17 without at least one registered face will build and run correctly but never match anyone.

**Structural differences from Template 17 (do not carry these over by mistake):**
- Only 2 of the 3 chain models are used: Stage 1 detection (`qfd`) and the face-recognition model's raw embedding tensor (no postprocess module at all on this stage — output is muxed to `multifilesink`, not classified).
- **No facial-landmark (`lite-3dmm`) stage** — registration skips landmark/pose entirely; it is only needed for runtime recognition alignment (Template 17 Stage 2).
- **No `qtimlpostprocess module=qfr`** — the recognition model's tensor output goes straight to `multifilesink` as raw `.bin` files, not through classification. Registration captures raw facial embeddings, not classification results.
- Camera source only — `qticamsrc video_0::type=video name=camsrc` for the live preview pad, plus `camsrc.image_1` (the on-demand image capture pad — see `plugin-catalog.md` qticamsrc "Image pad rules": image pad index is sequential after all video pads, and `image_N::type=jpeg` must be declared, though this pipeline reads raw NV12 off it via `qtivtransform`, not JPEG).
- Only two `qtimetamux` stages (`metamux_1` for detection ROI, none for the recognition stage — it terminates in `multifilesink`, not an overlay).

```bash
gst-pipeline-app -e \
  qtimlvconverter name=stage_01_preproc mode=image-batch-non-cumulative \
  qtimltflite name=stage_01_inference model=<MODEL_FACE_DET> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp;" \
  qtimlpostprocess name=stage_01_postproc settings="{\"confidence\": <VALUE>}" results=4 \
  module=qfd labels=<LABELS_FACE_DET> \
  qtimlvconverter name=stage_03_preproc mode=roi-batch-cumulative \
  qtimltflite name=stage_03_inference model=<MODEL_FACE_RECOGNITION> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp;" \
  qticamsrc video_0::type=video name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080 ! \
  queue ! waylandsink fullscreen=true sync=true \
  camsrc.image_1 ! video/x-raw,width=1920,height=1080 ! qtivtransform ! video/x-raw,format=NV12 ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! metamux_1. \
  t_split_1. ! queue ! stage_01_preproc. \
  stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. \
  stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  qtimetamux name=metamux_1 ! queue ! tee name=t_split_3 \
  t_split_3. ! queue ! stage_03_preproc. \
  stage_03_preproc. ! queue ! stage_03_inference. \
  stage_03_inference. ! queue ! \
  multifilesink location=/etc/data/tensor_%d.bin sync=true async=false enable-last-sample=false
```

**Interactive capture procedure (must be surfaced to the user — this is not a "run once" pipeline):**
1. Start `gst-pipeline-app`, select `PLAYING` from the interactive menu to move the pipeline to the Playing state.
2. Frame the subject: single person, facing the camera straight-on, landmarks visible in the live preview.
3. Select `Plugin Mode` → `camsrc` → `capture-image` from the menu to fire the action signal; when prompted, enter `1` for `GstImageCaptureMode` (`arg0`) and `1` for `guint` (`arg1`).
4. Repeat step 3 for each of 5 angles in order: front, left (~40°), right (~40°), up (~30°), down (~30°) — keeping facial landmarks visible in the preview at each angle. Each capture writes the next `tensor_N.bin`.
5. Stop the pipeline (`(b)Back`, `(q)Quit`) once all 5 captures are done. Five files (`tensor_0.bin`...`tensor_4.bin`) now exist at `/etc/data/` on-device.

**Post-capture steps (off-pipeline, outside the artifact — must be documented in the generated README, not silently omitted):**
1. Pull all 5 tensor bins from device to host: `scp root@<device-ip>:/etc/data/tensor_N.bin .` (once per file).
2. On the host, download and run `facedb.py` (from `quic/sample-apps-for-qualcomm-linux`) against all 5 bins: `python3 ./facedb.py "<Name of Person>" 512 32 tensor_0.bin tensor_1.bin tensor_2.bin tensor_3.bin tensor_4.bin` — produces a single `face.bin`.
3. Push `face.bin` back to device, renamed per-person: `scp face.bin root@<device-ip>:/etc/data/faceN.bin` (increment `N` per registered person, starting at 0).
4. Add a matching entry to the Template 17 Stage 3 **labels** file: `{"id": N, "color": "0xRRGGBBAA", "label": "<Name of Person>"}`.
5. Add a matching entry to the Template 17 Stage 3 **settings** file's `databases` array: `{"id": N, "database": "/etc/data/faceN.bin"}` (the settings file overall shape is `{"confidence": <N>, "databases": [...]}`).
6. Repeat the full capture→merge→register cycle once per person to be recognized; `id` must be unique and consistent between the labels entry and the databases entry for the same person.

---

## Template 15: Image Classification (File → Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=class_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=mobilenet labels=<LABELS_PATH> ! \
text/x-raw ! queue ! class_mux.
```

**Notes:**
- `module=mobilenet` for standard MobileNet-style classification — this is the default; keep it unless the model is documented as softmax-normalized or the user says otherwise.
- Outputs `text/x-raw` — feeds `qtimetamux`.
- For softmax-normalized model output: use `module=mobilenet-softmax`. Do not infer this from precision alone (the float32-vs-w8a8 split is not a reliable signal) — a wrong guess here silently changes how the confidence threshold behaves against the model's raw scores, not just a cosmetic mismatch.
- Add `results=<N>` when user requests top-N classification results (default is 5).
- `settings="{\"confidence\": <VALUE>}"` — omit by default, per the general rule (settings/confidence is optional and only added when the user asks); the 51.0 confidence default documented elsewhere applies to object detection and face detection, not classification — do not carry it over here.

---

## Template 16: Segmentation with Alpha Blend (File → Display)

```bash
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t \
t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=0.5 ! queue ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=deeplab-argmax labels=<LABELS_PATH> ! \
queue ! seg_mix.
```

**Notes:**
- Segmentation uses `qtivcomposer` with `sink_1::alpha=0.5` — blends mask over original at 50% opacity.
- Passthrough branch: `tee → queue → qtivcomposer.sink_0` (video).
- Segmentation branch: `tee → queue → qtimlvconverter → infer → deeplab-argmax → queue → qtivcomposer.sink_1` (no capsfilter between postprocess and the composer).
- No capsfilter is inserted after `deeplab-argmax` — pinning `width`/`height` (regardless of format) fails caps fixation; let the composer sink-pad `dimensions` size the tile.
- `sink_1::alpha=0.5` is set inline on the composer element; change value for different blend strength.
- No `qtimetamux` — segmentation does NOT use qtimetamux + qtivoverlay path.

---

## Template 16b: Segmentation with Alpha Blend (File → MP4 File Output)

```bash
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t \
t. ! queue ! qtivcomposer name=seg_mix sink_1::alpha=0.5 ! \
video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
filesink location=<OUTPUT_MP4> \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=<MODEL_PATH> delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=deeplab-argmax labels=<LABELS_PATH> ! \
queue ! seg_mix.
```

**Notes:**
- `qtivcomposer ! video/x-raw,format=NV12` caps filter directly after composer.
- `v4l2h264enc capture-io-mode=4 output-io-mode=4` for standard zero-copy encode.

## 13) SNPE DSP Object Detection (File → MP4 File)

Property values (delegate, tensors, confidence) confirmed from QIM SDK SNPE plugin documentation. Topology adapted to Topology A (qtimetamux + qtivoverlay) for annotated file output — the plugin docs show Topology B with qtivcomposer for display; Topology A is the correct choice when encoding annotated bounding-box output to MP4.

**Key notes:**
- `qtimlsnpe delegate=dsp` — Hexagon DSP backend, no backend library path needed
- `tensors="<boxes,scores,class_idx>"` — confirmed tensor names for YOLOX/YOLOv8 DLC models; for other detection models verify tensor names from model documentation. This is the optional output filter (see `plugin-catalog.md`'s "Tensor Filter — Decision Rule") — SNPE commonly needs it in practice, but it is not required by the runtime itself.
- `confidence: 70.0` — value from docs example; tune as needed
- Queue order in AI branch: after tee, after vconverter, after snpe, after postprocess
- Main branch (passthrough): tee → queue → qtimetamux — NO queue between tee and mux on main branch

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtimetamux name=obj_mux ! qtivoverlay ! \
    v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
    filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
    qtimlsnpe delegate=dsp tensors="<boxes,scores,class_idx>" model=<MODEL>.dlc ! queue ! \
    qtimlpostprocess results=10 module=yolov8 labels=<LABELS>.json \
      settings="{\"confidence\": 70.0}" ! text/x-raw ! queue ! obj_mux.
```

---

## 14) QNN HTP Object Detection (File → MP4 File)

Property values (backend, tensors, confidence) confirmed from QIM SDK QNN plugin documentation. Uses Topology A (qtimetamux + qtivoverlay). File sink output.

**Key notes:**
- `backend=/usr/lib/libQnnHtp.so` — HTP/NPU backend; `system=/usr/lib/libQnnSystem.so` always required
- `tensors="<boxes,scores,class_idx>"` — confirmed tensor names for YOLOv8 QNN .bin models. This is the optional output filter (see `plugin-catalog.md`'s "Tensor Filter — Decision Rule") — it is a harmless no-op for this model, not a requirement; QNN pipelines with a known `module=` typically omit it entirely.
- `confidence: 51.0` — value from docs example; tune as needed
- Queue order and topology identical to Template 13 above

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtimetamux name=obj_mux ! qtivoverlay ! \
    v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
    filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
    qtimlqnn model=<MODEL>.bin backend=/usr/lib/libQnnHtp.so \
      system=/usr/lib/libQnnSystem.so tensors="<boxes,scores,class_idx>" ! queue ! \
    qtimlpostprocess module=yolov8 labels=<LABELS>.json \
      settings="{\"confidence\": 51.0}" ! text/x-raw ! queue ! obj_mux.
```

---

## 15) SNPE GPU Classification (File → MP4 File)

Property values confirmed from QIM SDK SNPE plugin documentation (GPU delegate). Uses **Topology B (qtivcomposer)** — SNPE GPU classification outputs a rendered frame (no capsfilter) which composes with the original video, not text/x-raw metadata. File sink output.

**Key notes:**
- `delegate=gpu` — Adreno GPU backend
- `tensors="<class_logits>"` — confirmed tensor names for InceptionV3/MobileNet DLC models
- `module=mobilenet` — classification postprocess module; omits `settings`/confidence by default (the 51.0 confidence default documented for object detection/face detection does not apply to classification) — include a `settings` value only when the user specifies one
- **Topology B**: `tee → qtivcomposer name=mixer` (video path inline) + `qtimlsnpe → qtimlpostprocess → queue → mixer.` (no capsfilter after postprocess)
- `qtivcomposer sink_1::dimensions="<1920,1080>"` — video passthrough occupies full frame
- Postprocess output feeds directly into `queue !` with no capsfilter — this feeds as `sink_1` into composer
- File output: `qtivcomposer ! video/x-raw,format=NV12 ! v4l2h264enc ...`

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=split \
  split. ! queue ! qtivcomposer name=mixer sink_1::dimensions="<1920,1080>" ! \
    video/x-raw,format=NV12 ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! \
    mp4mux ! filesink location=<OUTPUT_MP4> \
  split. ! queue ! qtimlvconverter ! queue ! \
    qtimlsnpe delegate=gpu tensors="<class_logits>" model=<MODEL>.dlc ! queue ! \
    qtimlpostprocess results=1 module=mobilenet labels=<LABELS>.json ! \
    queue ! mixer.
```

---

## 16) QNN GPU Object Detection (File → MP4 File)

Property values confirmed from QIM SDK QNN plugin documentation (GPU backend). Uses Topology A. File sink output.

**Key notes:**
- `backend=/usr/lib/libQnnGpu.so` — Adreno GPU backend; use float models (not quantized) for GPU
- `tensors="<boxes,scores,class_idx>"` — same tensor names as QNN HTP. This is the optional output filter (see `plugin-catalog.md`'s "Tensor Filter — Decision Rule") — a harmless no-op for this model, not a requirement.
- Model format: `.bin` float (e.g. `yolov8_det_float.bin`) — quantized models are for HTP

```bash
gst-launch-1.0 -e --gst-debug=2 \
  filesrc location=<INPUT_FILE> ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t \
  t. ! queue ! qtimetamux name=obj_mux ! qtivoverlay ! \
    v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
    filesink location=<OUTPUT_MP4> \
  t. ! queue ! qtimlvconverter ! queue ! \
    qtimlqnn model=<MODEL>.bin backend=/usr/lib/libQnnGpu.so \
      system=/usr/lib/libQnnSystem.so tensors="<boxes,scores,class_idx>" ! queue ! \
    qtimlpostprocess module=yolov8 labels=<LABELS>.json \
      settings="{\"confidence\": 51.0}" ! text/x-raw ! queue ! obj_mux.
```

---

## Template 17: Multi-Stream Batch Inference gst-launch (12 streams, 3 batch groups × 4 streams)

Use this template when many streams (**more than 8**) need the same detector via HTP/NPU. The naive approach — one `qtimltflite` per stream — puts N independent graphs on the HTP and exhausts its memory (`error 6020`). Batching lets B streams share one graph: `qtibatch` stacks B frames into one buffer, one `qtimltflite` runs the batch, `qtimldemux` unstacks the results. The section below explains exactly how each element behaves so you can size this for any N, rather than copying pad numbers. This N>8 batched/HTP-preroll topology is the multistream case where `waylandsink sync=false` applies (see "Construction procedure" below) — a normal 4-stream AI wall or a batch group of 8 or fewer streams stays `sync=true` (see Pattern A6/Template 16).

### How the batch pattern works — derive it from element behavior

You can construct this pipeline for any stream count by understanding what four elements do to the buffers flowing through them. Everything else follows. (Notation: **N** = total streams, **B** = streams per batch group, **G** = ⌈N/B⌉ groups.)

**`qtibatch` — stacks one buffer from each linked sink pad into a single batched buffer.**
- It has request sink pads `sink_%u`. The number you link *is* the batch size B — there is no `batch-size` property (the only property is `moving-window-size`, default 1). The batch depth travels downstream as the multiview `views` caps field: `qtimlvconverter` puts `views=<depth>` + `multiview-mode=separated` on its src caps (`mlvconverter.c`), and `qtibatch` reads that `views` value back as its `depth` (`batch.c`). So the batch size is expressed in caps, not in a property — which is also why the model must agree with it (next element).
- It tags each stacked frame with a `stream-id` equal to that pad's **position in the link-order list** (`g_list_index(sinkpads, pad)`). This tag is the identity that survives through inference.
- Consequence you must satisfy: it only emits once it has one buffer queued on *every* linked pad. If any input stalls, the whole group stalls. So every sink pad you request must actually receive a stream.

**`qtimltflite` — runs one model instance over the whole batched buffer.**
- It sees a buffer of `views=B` frames and feeds all B to the model in one invocation. The model must therefore be **compiled for batch dimension B** (e.g. `..._batch_4.tflite` for B=4). A batch-1 model given a B-frame buffer forces the delegate to instantiate the graph B times on the HTP → memory blows up → `error 6020 / Failed to prepare graph`. The batch model is not an optimization, it is what makes one instance able to consume the batched buffer.
- Each `qtimltflite` you create is a separate HTP graph (you have one per group, so G of them). Multiple graphs on the *same* HTP core also collide on memory (6020). Spread your G groups across the available cores round-robin. **First find how many cores the device has** — count the `/dev/fastrpc-cdsp*` nodes (`cdsp` = core 0, `cdsp1` = core 1, …); this device has 2. Then assign `htp_device_id = g % core_count`: with 2 cores, groups map `0→id0, 1→id1, 2→id0, …`; with 1 core you cannot spread, so keep the batch groups few (fewer, larger batches) to stay within that single core's memory. `htp_performance_mode=(string)2` requests sustained-high clocks.

**`qtimldemux` — reverses `qtibatch`, routing each stacked result back out by `stream-id`.**
- It has request src pads `src_%u`, auto-numbered `0,1,2,…` in link order. For each channel in the batched tensor it reads the `stream-id` tag and pushes to the src pad whose id matches (`mldemux.c`: `find_srcpad(id)`).
- Consequence: **the k-th stream you linked into `qtibatch` comes out of the k-th src pad you link out of `qtimldemux`.** In→out is an index match, guaranteed by the tag, not an assumption. This is why you never name `src_N` explicitly — you link the demux outputs in the same order you linked the batch inputs, and identity is preserved automatically.

**`qtivcomposer` — blends its sink pads; lower z-order drawn first (underneath).**
- Request sink pads `sink_%u`. When `zorder` isn't given, a pad's z-order defaults to its creation index (`videocomposer.c`: `zorder = numsinkpads` at pad-add). So **link order = paint order**: earlier-linked pads render underneath, later ones on top.
- Each stream needs two pads at the *same* `position`/`dimensions`: the decoded video underneath and the transparent detection overlay on top. Because paint order follows link order, you get correct layering for free if you link all passthroughs first and all overlays second.

**Why unqualified `mixer.` and why declare it last.** In gst-launch, `elem.` (no pad name) requests the next pad in link order. So the sequence in which `mixer.` appears in the script fixes the sink-pad numbering — you don't fight it, you exploit it: link the N passthroughs first → they take `sink_0..N-1`; link the N overlays next → they take `sink_N..2N-1`. Declaring `qtivcomposer name=mixer` *after* all those links means every pad exists before you attach `sink_k::position` properties to it. Declaring the composer first (the earlier failing approach) forces you to hand-assign `mixer.sink_2i` / `sink_2i+1` and keep two interleaved numbering schemes in your head — brittle, and it broke. Letting link order drive numbering is the systematic version.

**Construction procedure for any N streams, batch size B (⇒ G = ⌈N/B⌉ groups):**
1. Declare G `qtimltflite name=infer<g>` at the top (g = 0..G-1), `htp_device_id` cycling `0,1,0,1,…` across the G groups.
2. Emit N decode chains, each ending `tee name=t<i>`: `filesrc ! qtdemux ! queue ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t<i>`. Keep the `queue` between `qtdemux` and `h264parse` (the reference C app does — it buffers `qtdemux`'s dynamically-added pad; with N demuxers prerolling at once, omitting it can wedge preroll). Insert `qtivtransform ! video/x-raw,format=NV12,width=<W>,height=<H>` before the tee only if you need to downscale.
3. For each group g, for its B streams in order: `t<i>. ! video/x-raw,format=NV12 ! mixer.` (passthrough) then `t<i>. ! ... ! batch<g>.` (AI). Then the group's batch chain: `qtibatch name=batch<g> ! queue ! qtimlvconverter ! queue ! infer<g>. infer<g>. ! queue ! qtimldemux name=demux<g>`.
4. For each stream in order, one demux output: `demux<g>. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer.`
   Two non-obvious caps details here, both load-bearing:
   - **Omit the capsfilter entirely — do not pin `format=` or `width=`/`height=`.** `qtimlpostprocess` renders its boxes/labels onto a transparent-alpha canvas and its src caps are `video/x-raw,{RGBA,RGBx}` — a pinned `format=BGRA` capsfilter fails to link, and even `format=RGBA` fails differently (`Fixated width in filter caps is not supported with current post-process type!`) if `width=`/`height=` are pinned alongside it. Leave the branch caps unset; it negotiates correctly and the alpha channel is preserved so only the boxes paint and the video underneath shows through. Do not pin `format=NV12` either — that gives an opaque tile that hides the video.
   - **The composer sink-pad `dimensions` sizes the tile, not a capsfilter.** With no capsfilter after `qtimlpostprocess`, the overlay negotiates to its native rendered size and the composer's `sink_(N+i)::dimensions` scales it onto the passthrough tile (e.g. 480×270) — do not try to pre-size it to the model's inference resolution (e.g. 640×640) via a capsfilter; that reintroduces the pinning failure above.
   - `results=10` caps detections per frame; without it the module may not bound the count.
5. Declare `qtivcomposer name=mixer` last. Pads `sink_0..N-1` are the passthroughs, `sink_N..2N-1` the overlays; give overlay `sink_(N+i)` the *same* position/dimensions as passthrough `sink_i`. End with `! video/x-raw,format=NV12 ! queue ! waylandsink sync=false fullscreen=true`. Use **`sync=false`** on the sink: with N streams converging through one composer after ~10 s/group of HTP preroll, buffers arrive well behind their nominal PTS; `sync=true` would make the sink drop every late frame and you get a frozen or black display. `sync=false` renders frames as they arrive. (The `video/x-raw,format=NV12` caps before the sink give the composer a single fixed output format to converge N differently-sized inputs onto — without it caps negotiation across 2N pads can fail.)

**Two operational facts** (not structural, but the run fails without them): prefix the command with `ulimit -n 10000` (N decoders exhaust the default fd limit), and allow ~10 s of HTP graph-prepare per group before PLAYING (G=3 ⇒ ~30 s), so never cap the run at 30 s.

### Verified working template (12 streams, 3 groups × 4, hardware confirmed)

```bash
ulimit -n 10000 && gst-launch-1.0 -e --gst-debug=2 \
  qtimltflite name=infer0 model=<MODEL_BATCH_4> \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)0,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimltflite name=infer1 model=<MODEL_BATCH_4> \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)1,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimltflite name=infer2 model=<MODEL_BATCH_4> \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)0,htp_performance_mode=(string)2,log_level=(string)1;" \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t0 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t1 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t2 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t3 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t4 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t5 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t6 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t7 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t8 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t9 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t10 \
  filesrc location=<INPUT> ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t11 \
  t0.  ! video/x-raw,format=NV12 ! mixer. \
  t0.  ! video/x-raw,format=NV12 ! batch0. \
  t1.  ! video/x-raw,format=NV12 ! mixer. \
  t1.  ! video/x-raw,format=NV12 ! batch0. \
  t2.  ! video/x-raw,format=NV12 ! mixer. \
  t2.  ! video/x-raw,format=NV12 ! batch0. \
  t3.  ! video/x-raw,format=NV12 ! mixer. \
  t3.  ! video/x-raw,format=NV12 ! batch0. \
  t4.  ! video/x-raw,format=NV12 ! mixer. \
  t4.  ! video/x-raw,format=NV12 ! batch1. \
  t5.  ! video/x-raw,format=NV12 ! mixer. \
  t5.  ! video/x-raw,format=NV12 ! batch1. \
  t6.  ! video/x-raw,format=NV12 ! mixer. \
  t6.  ! video/x-raw,format=NV12 ! batch1. \
  t7.  ! video/x-raw,format=NV12 ! mixer. \
  t7.  ! video/x-raw,format=NV12 ! batch1. \
  t8.  ! video/x-raw,format=NV12 ! mixer. \
  t8.  ! video/x-raw,format=NV12 ! batch2. \
  t9.  ! video/x-raw,format=NV12 ! mixer. \
  t9.  ! video/x-raw,format=NV12 ! batch2. \
  t10. ! video/x-raw,format=NV12 ! mixer. \
  t10. ! video/x-raw,format=NV12 ! batch2. \
  t11. ! video/x-raw,format=NV12 ! mixer. \
  t11. ! video/x-raw,format=NV12 ! batch2. \
  qtibatch name=batch0 ! queue ! qtimlvconverter ! queue ! infer0. \
  infer0. ! queue ! qtimldemux name=demux0 \
  qtibatch name=batch1 ! queue ! qtimlvconverter ! queue ! infer1. \
  infer1. ! queue ! qtimldemux name=demux1 \
  qtibatch name=batch2 ! queue ! qtimlvconverter ! queue ! infer2. \
  infer2. ! queue ! qtimldemux name=demux2 \
  demux0. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=<MODULE> labels=<LABELS> settings=<SETTINGS> ! queue ! mixer. \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"      sink_0::dimensions="<W,H>" \
    sink_1::position="<W,0>"      sink_1::dimensions="<W,H>" \
    sink_2::position="<2W,0>"     sink_2::dimensions="<W,H>" \
    sink_3::position="<3W,0>"     sink_3::dimensions="<W,H>" \
    sink_4::position="<0,H>"      sink_4::dimensions="<W,H>" \
    sink_5::position="<W,H>"      sink_5::dimensions="<W,H>" \
    sink_6::position="<2W,H>"     sink_6::dimensions="<W,H>" \
    sink_7::position="<3W,H>"     sink_7::dimensions="<W,H>" \
    sink_8::position="<0,2H>"     sink_8::dimensions="<W,H>" \
    sink_9::position="<W,2H>"     sink_9::dimensions="<W,H>" \
    sink_10::position="<2W,2H>"   sink_10::dimensions="<W,H>" \
    sink_11::position="<3W,2H>"   sink_11::dimensions="<W,H>" \
    sink_12::position="<0,0>"     sink_12::dimensions="<W,H>" \
    sink_13::position="<W,0>"     sink_13::dimensions="<W,H>" \
    sink_14::position="<2W,0>"    sink_14::dimensions="<W,H>" \
    sink_15::position="<3W,0>"    sink_15::dimensions="<W,H>" \
    sink_16::position="<0,H>"     sink_16::dimensions="<W,H>" \
    sink_17::position="<W,H>"     sink_17::dimensions="<W,H>" \
    sink_18::position="<2W,H>"    sink_18::dimensions="<W,H>" \
    sink_19::position="<3W,H>"    sink_19::dimensions="<W,H>" \
    sink_20::position="<0,2H>"    sink_20::dimensions="<W,H>" \
    sink_21::position="<W,2H>"    sink_21::dimensions="<W,H>" \
    sink_22::position="<2W,2H>"   sink_22::dimensions="<W,H>" \
    sink_23::position="<3W,2H>"   sink_23::dimensions="<W,H>" ! \
  video/x-raw,format=NV12 ! queue ! waylandsink sync=false fullscreen=true
```

**If it fails, map the symptom back to the mechanism:**

| Symptom | Cause (see the element-behavior section above) |
|---|---|
| `error 6020 / Failed to prepare graph` at preroll | batch-1 model given a batched buffer (need `..._batch_N.tflite`), or multiple `qtimltflite` graphs on one HTP core (spread via `htp_device_id`) |
| Pipeline stalls after preroll, one group never produces frames | a `qtibatch` sink pad was requested but its stream never arrives — `qtibatch` waits for all linked pads |
| Detections land on the wrong tile / swapped streams | demux outputs linked in a different order than the batch inputs — in→out is index-matched, so keep the order identical |
| Tiles show video but no boxes, or boxes with no video | the two `mixer.` pads for a stream aren't at the same position, or overlay was linked *before* its passthrough (paint order = link order) |
| Overlay tile is opaque / hides the video underneath | pinned `format=NV12` on the `qtimlpostprocess` output — omit the capsfilter entirely so alpha is preserved and only the boxes paint |
| `could not link ... can't handle caps video/x-raw, format=BGRA` after qtimlpostprocess | a pinned `format=BGRA` capsfilter — this build's src caps are `{RGBA,RGBx}`, not `BGRA`. Don't relabel to `format=RGBA` either if width/height are also pinned — that fails differently (`Fixated width in filter caps is not supported with current post-process type!`). Remove the capsfilter entirely. |
| Boxes misplaced or clipped within a tile | postprocess output caps sized to the model input (e.g. 640×640) instead of the composer tile (e.g. 480×270) |
| Display frozen or black despite PLAYING | `sync=true` on `waylandsink` drops late frames after the long batch preroll — use `sync=false` |
| `could not link ... to mixer` at parse time | used explicit `mixer.sink_N` — use unqualified `mixer.` and let link order assign pads |
| Black screen / `Failed to connect to wayland display` | wrong `WAYLAND_DISPLAY`; the deploy script sets it, or export it manually |
