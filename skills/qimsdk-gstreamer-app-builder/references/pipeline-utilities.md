# Pipeline Utilities

## Purpose

Pipeline glue rules for queues, tees, capsfilters, muxers, demuxers, batching, and cross-process utilities.

## Load When

Load when the pipeline branches, joins, batches, uses dynamic pads, needs caps negotiation, or uses utility elements.

## This File Owns

- Minimal queue policy
- Queue-before/after-inference guidance for AI branches
- tee branch isolation and fan-in element rules
- Capsfilter, parser, mux/demux, batching, and zero-copy utility usage

## This File Does Not Own

- Plugin property tables; use plugin-catalog.md
- Concrete AI/MM templates; use pattern files
- Artifact file requirements; use artifact-contract.md

---


---

## Utility Plugins and Queue Policy

# Utility Plugins and Structural Elements

## Use This Reference For

- Correct placement of structural GStreamer elements
- Avoiding deadlocks and broken branches in generated pipelines

## Queue Usage

Use `queue` only where it is structurally needed.

Required placements:

- `queue` immediately after any hardware decoder (`v4l2h264dec`, `v4l2h265dec`, `v4l2vp9dec`, `v4l2av1dec`) — applies regardless of what follows (a `tee`, a single AI/display/encode stage, anything). Whether the next element already decouples internally isn't something a pipeline definition can verify from the outside, so the safe default is to add the queue unconditionally rather than reason about the downstream element's internals.
- `queue` between NV12 caps output and `tee` — required before the branch split in AI pipelines (this is the decoder-queue above, immediately followed by the branch point, when the two coincide)
- `queue` on the AI branch after the tee pad (before `qtimlvconverter`)
- `queue` between each AI stage in the AI branch: after `qtimlvconverter`, after the inference element, and after `qtimlpostprocess` before `qtimetamux`
- immediately after dynamic-pad sources (`qtdemux`/`rtspsrc`) when app wiring links into a queue sink
- when a `qtdemux`/`rtspsrc` exposes more than one dynamic pad that runs concurrently (e.g. an MP4 with both a video track and an audio track each feeding an independent downstream path), give **each** dynamic pad its own `queue` immediately after it, plus another `queue` at each intermediate decode boundary within a branch (e.g. immediately after an audio decoder, before the next conversion stage). One concurrent branch's preroll/scheduling can otherwise block the other's, and the pipeline can stall before reaching `PLAYING` with no obvious error. Reserve the minimal direct `qtdemux -> parser` hop (no queue) for genuinely single-stream video-only paths.

**Main (passthrough) branch exception:** Do NOT insert a `queue` between the `tee` and `qtimetamux` on the passthrough/main branch. The canonical pattern is `tee name=t ! qtimetamux name=mux` with no queue in between on the passthrough side.

## Tee Usage

In AI pipelines, the canonical `tee` pattern connects the passthrough branch directly to the mux (no queue), and the AI branch via a queue:

```text
... ! queue ! tee name=t ! qtimetamux name=mux ! qtivoverlay ! <sink>
t. ! queue ! qtimlvconverter ! queue ! <infer> ! queue ! qtimlpostprocess ... ! text/x-raw ! queue ! mux.
```

- The main/passthrough branch: `tee name=t ! qtimetamux` — direct connection, no queue
- The AI branch: `t. ! queue ! ...` — queue immediately after the tee pad
- Both branches must terminate at the same `qtimetamux` instance

Rules:

- every tee output used in command must be connected
- no orphaned tee pads
- each branch consumer (e.g. a `qtimetamux` sink or a `qtivcomposer` sink) gets exactly one link chain from the tee. Do not write a second, direct `t. ! <consumer>` line for the same consumer alongside an already-queued `t. ! queue ! ... ! <consumer>` chain — each `t.`/tee reference requests a **new** tee src pad, so the duplicate line silently claims a second output pad on the consumer instead of reusing the queued branch's pad, leaving that queued branch's own consumer pad unlinked while the duplicate appears to "work." This produces confusing downstream symptoms (missing metadata, encoder errors) that look unrelated to the tee. Before adding a `t. ! <consumer>` line, check whether that same consumer already has a queued chain from the same tee.

## Tee Back-Pressure: Leaky Queue for Mixed-Speed Branches

When a `tee` feeds branches that run at different speeds (e.g. display or encode alongside slow AI inference), the tee serializes all branches at the speed of the slowest. Add a `leaky=downstream` queue on the slow branch only:

```text
tee name=t
t. ! queue ! <display or encode>                          (no leaky — must not drop frames)
t. ! queue leaky=downstream ! qtimlvconverter ! ...       (leaky — AI inference can tolerate drops)
```

Rules:
- Add `leaky=downstream` only on branches that can tolerate dropped frames (AI inference).
- Do NOT make display, encode, or file-write branches leaky — dropping frames on those branches causes visible glitches or corrupted output.
- This applies whenever branches have materially different throughput; it is not needed when all branches run at the same speed.

## Capsfilter Usage

Use `capsfilter` or inline caps where format normalization is needed:

```text
video/x-raw,format=NV12,width=<W>,height=<H>,framerate=<FPS>/1
```

## Appsink/Appsrc Usage

- `appsink` for app-side consumption
- `appsrc` for app-side injection

Keep these only when user asks for app integration.

## Utility Validation Rules

- No unconnected named pads
- No terminal branch without sink
- No sink without upstream complete path
- For single-input pipelines, reject redundant queue chains that do not serve branching or dynamic-pad decoupling — except the queue immediately after a hardware decoder (see Queue Usage above), which is required unconditionally and is never "redundant."

---

## Batching

# Batching

## Use This Reference For

- Batch-oriented preprocessing or multi-item inference flow
- ROI cumulative batching in cascaded workflows

## Typical Batching Situations

- Multiple source streams processed in parallel
- ROI batches generated from upstream detections
- Postprocess that handles nested per-batch outputs

## Required Rules

- Only introduce batching when user asks for it.
- Keep stage ownership clear: preprocess batching, infer execution, postprocess mapping.
- Ensure batch-oriented branches still rejoin correctly through metadata mux stages.

## ROI Batch Rule

For cascaded stage-2 inference on detections, use:

```text
qtimlvconverter mode=roi-batch-cumulative
```

## Anti-Patterns

- Adding batching semantics to simple single-stream requests
- Returning batch claims without stage-specific wiring
- Breaking metadata merge by introducing unmatched branch counts

---

## Zero-Copy Transport

# Zero Copy

## Use This Reference For

- Cross-process or containerized split pipelines
- FD-backed media transfer over UNIX socket boundaries

## Canonical Pattern

Producer:

```text
source/decode -> queue -> qtisocketsink socket=<SOCKET_PATH>
```

Consumer:

```text
qtisocketsrc socket=<SOCKET_PATH> -> processing/AI -> output
```

## Required Rules

- Producer and consumer must use the same socket path.
- Media caps assumptions (format/size/framerate) must be consistent across boundary.
- Keep one clear final output target in consumer pipeline.

## When Not To Use

- User asked for simple in-process single pipeline.
- No explicit requirement for process split, socket boundary, or zero-copy transport.
