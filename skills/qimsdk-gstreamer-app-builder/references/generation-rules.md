# Generation Rules

## Purpose

Global generation guardrails for QIM SDK GStreamer pipelines and C apps.

## Load When

Load for any request that generates, edits, validates, or reviews pipeline or C app artifacts.

## This File Owns

- No-invention and source-of-truth rules
- Clarification vs placeholder policy
- Scope matching and minimal-topology rules
- Cross-cutting defaults that affect every generated artifact

## This File Does Not Own

- Plugin property tables; use plugin-catalog.md
- Concrete AI or multimedia templates; use the pattern files
- Artifact file layout and verification details; use artifact-contract.md
- C API implementation details; use c-app-development.md

---

## Global Rules

- Use only instructions and facts from this skill folder (`SKILL.md` + `references/*.md`).
- Load only the references needed for the request.
- Do not invent plugin names, properties, pads, caps, model stages, module names, or Qualcomm-specific behavior.
- Treat `plugin-catalog.md` as the single source of truth for plugin, runtime, property, pad/caps, and postprocess module facts.
- Preserve user-provided model paths, label paths, settings values, backend paths, tensor names, and postprocess module names exactly as supplied. Do not reject, rename, or "correct" those runtime values only because they are not already listed in this skill.
- Match the requested scope exactly; do not add daisy-chain, multistream, zero-copy, or C app structure unless requested.
- Prefer the simplest topology that satisfies the request. Single-input pipelines should use minimal queue placement unless branching, dynamic pads, or documented isolation requires queues.
- For AI pipelines, keep stage order as source -> preprocess -> inference -> postprocess -> metadata/use.
- Ask the user when missing information changes topology or element selection. Use placeholders when only runtime values are missing.
- Generated artifact folder names must start with `qimsdk-gstreamer-`.
- If generated code changes, update the artifact README in the same pass.
- For `qticamsrc`/`qtiqmmfsrc` camera input, if the user omits resolution or framerate, use `video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1` and document the `1920x1080 @ 30fps` camera default in README assumptions.

## Multi-Stream Layout Rule

**Default source resolution:** unless the user states a source resolution, assume **1920×1080 (16:9)** for every stream in the layout — this matches the ISP camera and file-source defaults used throughout this skill (e.g. `qtiqmmfsrc`/file examples at 1920x1080). Use this assumed resolution to run the AR-fit formula below and produce concrete numbers; do not fall back to placeholders just because the user didn't state a resolution. State the assumption in the README's Assumptions section so the user can correct it if their actual source differs.

When generating a `qtivcomposer` layout, `qtivcomposer` always stretches each stream to fill its `dimensions` rectangle — it has no built-in aspect ratio mode. Apply this decision tree for any layout where streams must be rescaled.

**This rule covers uniform equal-cell grids and side-by-side layouts. For PiP and non-uniform layouts, set each cell's dimensions manually and apply the same AR-fit formula independently per cell.**

**Critical ordering — do not fix the canvas size before sizing cells.** Picking a canvas size first (e.g. assuming 1920×1080 total) and dividing evenly by `cols`/`rows` produces a cell whose aspect ratio has nothing to do with the source's — even when every stream shares the source's AR, this manufactures unnecessary letterbox bars. Always derive the cell size from the source AR first (step 1 below), then let the canvas size fall out of that — never the other way around.

**1. Choose `cell_w` and derive `cell_h` from source AR, then pick the canvas from that**
- Pick `cols`/`rows` from the grid-selection rule below.
- Pick `cell_w` (a convenient width, e.g. divide a target canvas width by `cols`, or use the source's native width directly if no target canvas size was requested).
- Derive `cell_h = round_even(cell_w * ih / iw)` using the source's aspect ratio `(iw, ih)` — stated, or the 1920×1080 default. This makes the cell itself already AR-correct for the source, so step 2's fit will need **zero padding** whenever all streams share this one source AR.
- Canvas = `cols × cell_w` wide, `rows × cell_h` tall. This is the compositor output resolution — it is a *result* of the cell size, not a starting assumption.

**2. Does the stream still need rescaling within its own cell?**
- If every stream shares the same source AR used to derive `cell_h` in step 1 → `dimensions = <cell_w, cell_h>` exactly, `pad_x = pad_y = 0`. This is the common case and needs no further math.
- If a specific stream's native AR differs from the one used to size the grid (mixed-AR multistream, or a PiP/non-uniform cell) → continue to step 3 for that stream only.

**3. Who does the scaling, when a stream's AR differs from its cell's AR**
- Use `qtivcomposer` pad `dimensions = <fit_w, fit_h>` to scale — the composer handles it in the same GPU blit.
- All other sources → same: set `dimensions = <fit_w, fit_h>` on the composer pad (not the full cell size — that would re-stretch).

**4. AR-fit formula (only for a stream whose AR differs from its cell's AR)**
Given cell `(cell_w, cell_h)` from step 1 and that stream's native resolution `(iw, ih)`:
```
scale = min(cell_w / iw,  cell_h / ih)
fit_w = round_even(iw * scale)     -- round to nearest even (hardware alignment)
fit_h = round_even(ih * scale)
pad_x = floor((cell_w - fit_w) / 2)
pad_y = floor((cell_h - fit_h) / 2)

position   = <col * cell_w + pad_x,  row * cell_h + pad_y>
dimensions = <fit_w, fit_h>
```
Do not set a custom `background` value on `qtivcomposer` — leave it at its default. This formula applies to both downscaling and upscaling. Note this only ever runs for a stream whose AR doesn't already match its cell — for the common uniform-AR case, step 2 already gave `pad_x = pad_y = 0` without needing this formula at all.

**Grid selection for N streams:** Pick the factor pair `(cols, rows)` where `cols × rows = N`, `cols ≥ rows`, and `|cols − rows|` is minimized (most-square; e.g., N=8 → 4×2, N=9 → 3×3, N=16 → 4×4). If N is prime, fall back to `cols = ceil(sqrt(N))`, `rows = ceil(N / cols)`, and omit sink pads for empty tail slots.

**Resolution stated but non-standard:** run the same steps with the stated `(iw, ih)` — always produce concrete numbers, never placeholders, since the formula only needs a resolution (assumed or stated), not any other missing information.

## Ask vs Placeholder Policy

If a model was looked up in `references/model-catalog.md` at Step 4 and a row was found, the catalog resolves structural decisions only — use those values directly without asking: `qtimlpostprocess module=`, `delegate=`, `settings` format (whether to omit or use inline JSON or file path), precision variants, and any fields marked mandatory in the Notes column (see the catalog's "Notes column overrides" rule). File path values (`labels=`, `settings=` filename, `model=`) still use placeholders unless the user explicitly provided them in the prompt. Continue to the normal Ask/Placeholder rules for everything else.

Ask before generation only when missing information changes topology, element
family, or stage wiring:

- Missing source type: file, camera, USB camera, RTSP, socket, or app source.
- Missing output target: display, MP4 file, RTSP stream, appsink, or multiple outputs.
- Missing AI stage count: single-stage, daisy-chain, multistream, or audio AI.
- Missing inference runtime family when it cannot be inferred from the prompt or model artifact: TFLite, SNPE, QNN, or ONNX.
- Missing AI task when a `qtimlpostprocess module` cannot be inferred confidently.

When asking the user to choose a postprocess module, load `plugin-catalog.md`
and list the known module choices from its Supported Module Table and relevant
Module Selection Hints. Keep the prompt compact: show the likely choices first,
then offer the full known-module list if the task is unclear. Do not invent a
module outside `plugin-catalog.md`; if the user provides a module not listed
there, use it exactly as supplied.

Use placeholders when pipeline structure is already clear and only runtime
values are missing:

- Missing model path: use `<MODEL_PATH>`.
- Missing labels path: use `<LABELS_PATH>`.
- Missing input file path: use `<INPUT_FILE>`.
- Missing output filename: use `<OUTPUT_FILE>`.
- Missing RTSP URL: use `<RTSP_URL>`.
- Missing settings file path: use `<SETTINGS_PATH>`.
- Missing threshold value: use `<CONFIDENCE>` only if the user asked for threshold tuning; otherwise omit `settings`.
- Missing segmentation output dimensions: use `<SEG_OUTPUT_WIDTH>` and `<SEG_OUTPUT_HEIGHT>`.
- "Maximum resolution" or "highest resolution" without explicit dimensions: use 3840×2160 (4K UHD). If the target device or display differs, the user must override.
- Missing runtime-specific backend paths or tensor names: use explicit placeholders when the request intentionally leaves them open.

List every placeholder in the artifact README.

## Minimal Scope Policy

- For generated `gst-launch-1.0` commands, include `--gst-debug=2` by default unless the user explicitly asks for a different debug setting.
- For generated `gst-launch-1.0` commands, include `-e` option only when the pipeline contains a video encoder.
- Use `gst-pipeline-app` only when runtime signal interaction is required, such as `qticamsrc` image pad capture.
- Do not load AI pattern references for pure multimedia requests.
- Do not load multimedia pattern references for pure AI requests unless source/output composition requires them.
- Do not duplicate plugin facts in generated artifacts; resolve plugin placement and properties from `plugin-catalog.md`.
