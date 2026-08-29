# Pipeline Construction

## Default: Explicit Construction

Generated apps should create objects first, configure them, then add them to the pipeline.

```python
from qimsdk import (
    Pipeline,
    Element,
    VideoFilter,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
)


def create_and_execute_pipeline() -> None:
    source = Element("filesrc", "source")
    source.set("location", "<INPUT_FILE>")

    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")

    decoder = Element("v4l2h264dec", "decoder")
    decoder.set("capture-io-mode", 4)
    decoder.set("output-io-mode", 4)

    q_dec = Element("queue", "q_dec")
    video_filter = VideoFilter().format("NV12")

    display = Element("waylandsink", "display")
    display.set("fullscreen", True)
    display.set("sync", True)

    pipeline = Pipeline("video-pipeline")
    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", video_filter)
    pipeline.add(display)
    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "display")
    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
```

Use section comments in larger apps:

- source/decode
- preprocessing
- inference
- postprocess
- metadata merge
- overlay/output

## Supported: Implicit Construction

Use this style only when the user asks for implicit/fluent/chained style or when editing an existing implicit app.

```python
def create_and_execute_pipeline() -> None:
    pipeline = (
        Pipeline("video-pipeline")
        .add("filesrc", "source", "location", "<INPUT_FILE>")
        .add("qtdemux", "demux")
        .add("h264parse", "parser")
        .add("v4l2h264dec", "decoder", "capture-io-mode", 4, "output-io-mode", 4)
        .add("queue", "q_dec")
        .add_stream_filter("vf", VideoFilter().format("NV12"))
        .add("waylandsink", "display", "fullscreen", True, "sync", True)
    )
    pipeline.execute()


def main() -> None:
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
```

## Style Preservation for Edits

- If the existing app is explicit, keep it explicit.
- If the existing app is implicit, keep it implicit.
- Convert style only when the user asks.
- Do not mix styles unless explicitly requested.

## Linking

Use `pipeline.link(...)` when branch boundaries, demux, mux, tee, or named stages must be clear.

Simple linear chains can rely on insertion-order auto-linking, but explicit links are easier to review and preferred for generated examples.

Every name in `pipeline.link(...)` must correspond to an element or stream filter added earlier.

**Each `tee` branch gets exactly one complete `pipeline.link(...)` chain to its consumer.** Do not also link the tee directly to a downstream element (e.g. a mux) when a separate queued chain already terminates at that same element — each `pipeline.link(...)` call to a `tee` requests its own pad, so a redundant direct link alongside a queued link claims a second pad and leaves the queued branch effectively unlinked/starved, producing confusing downstream symptoms (encoder errors, missing metadata) that look unrelated to linking. Before adding a link from a tee, check whether that consumer already has a queued chain from the same tee.

**A `qtivcomposer`'s configured `input(N)` pad geometry must exactly match the number of branches actually linked into it** — this is an invariant to re-derive after every topology change, not a fixed count carried over from an earlier draft. A stale `input(N)` call for a pad that is no longer linked fails at graph-construction time before `PLAYING`. This commonly happens when a branch that used to feed the composer as two raw pads (e.g. a passthrough tile plus a separately rendered mask tile) is refactored to pre-compose those two into one finished tile through a local `qtivcomposer` first — the top-level composer then needs one fewer configured pad than before the refactor.

## Generated App Shape

Default full app:

- shebang
- short module docstring
- imports from public `qimsdk`
- constants or placeholders for paths
- optional custom preprocess/postprocess callback functions
- `create_and_execute_pipeline(...)`
- `main() -> None`
- explicit element/filter construction
- pipeline add/link/run
- `if __name__ == "__main__": main()`

Put pipeline construction and `pipeline.execute()` in `create_and_execute_pipeline(...)`. Keep `main()` for logging setup, CLI argument parsing, and calling `create_and_execute_pipeline(...)`.

## YAML Mode

Use YAML mode only when the user asks for config-driven pipeline construction or provides a YAML file.

```python
from qimsdk import Pipeline

pipeline = Pipeline.from_yaml("pipeline-name", "<PIPELINE_YAML>")
pipeline.execute()
```

Rules:

- Do not default to YAML for normal app generation.
- Do not convert direct Python construction to YAML unless requested.
- If editing YAML-driven apps, preserve YAML mode.
- Support both YAML situations:
  - user asks for a YAML-driven app but does not say the YAML already exists: generate `main.py`, `README.md`, and the YAML config file.
  - user explicitly says the YAML already exists or is externally provided: generate only the loader app and README; include the exact README phrase `External YAML provided by user`.
- For generated YAML configs, use the SDK parser schema: top-level `pipeline:`, then `elements:` and `links:`.
- In `elements:`, every item must have `type:` and `name:`. For normal elements, `type:` is the element factory (`type: qticamsrc`, `type: tee`, `type: qtimltflite`); never use `factory:`.
- Put element properties as flat keys beside `type:` and `name:`; never wrap them in `properties:`.
- For generated YAML stream filters, use `type: filter` plus a `video:`, `text:`, `tensor:`, `image:`, `h264:`, `audio:`, or `caps:` block.
- Keep unknown YAML keys as placeholders or ask when they change topology.
