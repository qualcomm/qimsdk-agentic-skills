# Multimedia Pipeline Patterns

## Decode and Display

Use:

`filesrc -> qtdemux -> h264parse -> v4l2h264dec -> queue -> VideoFilter(NV12) -> waylandsink`

Generated code should use explicit construction and comments for source/decode and display sections.

## Camera Preview

Use:

`qticamsrc camera=0 -> VideoFilter(NV12) -> waylandsink fullscreen=True sync=True`

Only add `qtivtransform` when a loaded example or plugin facts require transformation/conversion.

## USB Camera Preview

Use:

`v4l2src device=<DEVICE_NODE> -> VideoFilter(YUY2) -> qtivtransform -> VideoFilter(NV12) -> waylandsink fullscreen=True sync=True`

USB cameras can enumerate compressed and raw modes; force YUY2 before `qtivtransform`, then force only NV12 after it. Do not add resolution/framerate caps for USB preview unless the user requests them or a loaded sample explicitly requires that mode. Do not generalize this into a blanket `qtivtransform` rule for `qticamsrc` camera pipelines.

## Camera Encode to MP4

Use:

`qticamsrc -> VideoFilter(NV12) -> v4l2h264enc -> h264parse -> mp4mux -> filesink`

Call `pipeline.eos(True)`.

## JPEG / Image Sequence

Use `multifilesrc` / `multifilesink` only when the user asks for image sequence or frame file handling.

Use `ImageFilter` or `VideoFilter().format("JPEG")` only when supported by the selected example pattern.

## Composition

Use `qtivcomposer` for side-by-side, wall, or picture-in-picture composition. Keep composer property/pad details conservative and ask when layout geometry is unclear.

`qtivcomposer`, `mp4mux`, and `qtimetamux` are fan-in elements. Feed each source branch directly to the fan-in element with one queue per branch when branch scheduling is needed; do not insert a `tee` only because multiple sources feed the composer or mux.

For side-by-side requests, set composer sink geometry via the pad API using
Python lists — `pipeline.get("composer").input(0).set("position", [0, 0])`,
`.input(0).set("dimensions", [w, h])`, `.input(1).set("position", [x, 0])`, etc.
— only when the requested layout dimensions are known. Never use element-level
`sink_N::position`/`sink_N::dimensions` properties or gst-array strings like
`"<0, 0>"` (both fail on this SDK — see `api-surface.md`). If layout geometry is
missing and affects output shape, ask the user.

Do not add `qtivtransform` after `qtivcomposer` before encode. Composer output is already video output; constrain it with `VideoFilter().format("NV12")` before the encoder when needed.

## App Boundary

Use `AppSrc`/`AppSink` wrappers for Python buffer handoff. Generated examples should include short comments for producer/consumer callbacks and placeholders for app-specific buffer logic.

## Audio Capture, Playback, and AV

Use explicit GStreamer elements when the request is multimedia audio rather
than audio AI:

```text
pulsesrc -> audioconvert -> wavenc -> filesink
WAV/MP3 source -> parser/decoder -> pulsesink
video encode branch + pulsesrc audio branch -> mp4mux -> filesink
```

For AV recording, set `pulsesrc do-timestamp=True provide-clock=False`, use the
appropriate audio parser/encoder, and call `pipeline.eos(True)`. README run
steps must include `wpctl status` followed by `wpctl set-default <node_no.>`;
never invent the node number.

## RTSP Serving

When network serving is requested, feed an encoded stream directly to
`qtirtspbin`:

```python
# Use output-io-mode=5 for direct camera/AV-record input; use 4 when
# the stream was decoded from a file or RTSP source.
encoder = Element("v4l2h264enc", "encoder").set("capture-io-mode", 4)
encoder.set("output-io-mode", "<SOURCE_SPECIFIC_IO_MODE>")
parser = Element("h264parse", "parser").set("config-interval", 1)
rtsp = Element("qtirtspbin", "rtsp").set(
    "address", "0.0.0.0", "port", "8900", "mpoint", "/live"
)
```

Use unique ports for independent streams. `qtirtspbin` is a sink endpoint and
has no passthrough output. Metadata RTSP is a separate text branch and needs a
separate `qtirtspbin` instance and port.

## Multi-Stream, Grid, and Transform Routes

The same Python construction model supports dual-camera, 8/16-stream grid,
side-by-side, picture-in-picture, and display-plus-file fan-out topologies:

```text
source/decode_N -> queue -> qtivcomposer sink_N
qtivcomposer -> VideoFilter(NV12) -> display or encoder
```

Set composer tile geometry via the pad API with Python lists —
`composer.input(N).set("position", [x, y])` and `.set("dimensions", [w, h])` —
when the layout is specified (never element-level `sink_N::position` or
gst-array strings; see `api-surface.md`). For a rotate/scale/flip request, place
`qtivtransform` before the branch or output that needs the transform and set only
the catalogued `rotate`, `destination`, `flip-horizontal`, or `flip-vertical`
properties. Do not insert `qtivtransform` after `qtivcomposer` before encoding;
constrain composer output with `VideoFilter().format("NV12")` instead.

For a single source feeding multiple outputs, use `tee` plus one queue per
branch. Keep encoder placement and IO modes specific to each branch; do not
use a tee merely because multiple independent sources feed a composer.

## Camera Multi-Pad and Snapshot Requests

`Element` can construct `qticamsrc`/`qtiqmmfsrc` and set documented per-element
properties. Multi-pad, JPEG snapshot, and runtime activation requests require
request-pad/action-signal handling that is not exposed as a dedicated helper in
the refreshed Python wrappers. Preserve the requested topology and use the
public raw GStreamer object only when the user explicitly requests signal-level
control and the target SDK confirms the signal contract. Do not invent a
Python-only callback name.
