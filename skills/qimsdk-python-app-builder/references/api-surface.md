# API Surface

## Public Imports

Use imports from `qimsdk`, not internal `qimsdk._*` modules.

Core exports:

- `Pipeline`
- `Element`, `Port`
- `Buffer`
- `AppSrc`, `AppSink`, `CamSrc`
- `MLVConverter`, `MLVideoBlit`
- `MLPostprocess`
- `MLVideoTFLiteBin`, `MLVideoONNXBin`, `MLVideoQNNBin`, `MLVideoSNPEBin`
- `StreamFilter`, `VideoFilter`, `ImageFilter`, `H264Filter`, `TensorFilter`, `TextFilter`, `AudioFilter`
- `VideoFormat`, `AudioFormat`, `AudioLayout`
- `ImsdkLogLevel`, `ImsdkGstLogMode`, `SetImsdkLogLevel`, `SetImsdkGstLogMode`
- Logging modes: `ImsdkGstLogMode.ImsdkLog` (default) and `ImsdkGstLogMode.GstLog`
- Logging levels: `ImsdkLogLevel.Error`, `Warning`, `Info`, `Debug` (default for generated apps)
- `ImageClassifications`, `AudioClassifications`, `ObjectDetections`, `Poses`, `DepthMaps`, `Segmentations`, `Tensors`
- `ImsdkError`, `GstError`, `PipelineError`

## Pipeline

Valid methods:

- `Pipeline(name)`
- `.add(factory_or_element, name=None, *args, **props)`
- `.add_stream_filter(name, filter)` for generated apps
- `.link(*names)`
- `.get(name, as_type=None)`
- `.start()`
- `.wait()`
- `.stop()`
- `.prepare()`
- `.activate()`
- `.deactivate()`
- `.execute()`
- `.eos(enabled: bool)`
- `.print()`
- `.generate_graph(filename)`
- `Pipeline.from_yaml(name, yaml_path)`

`Pipeline.add(...)` accepts either:

- an explicit `Element` or wrapper object
- a factory string plus element name and properties

Default generated apps should use explicit objects.

`Pipeline.get(name, as_type=None)` returns a plain `Element` wrapper by default. Pass a wrapper class as `as_type` to get a typed wrapper around the same underlying element:

```python
cam = pipeline.get("source", CamSrc)
cam.image_capture()
```

`.prepare()` moves the pipeline to PAUSED, `.activate()` is equivalent to `.start()`, and `.deactivate()` moves an already-started pipeline back to PAUSED. `.print()` logs the resolved topology through IMSDK logging, and `.generate_graph(filename)` writes a draw.io XML graph. Default generated apps still use `pipeline.execute()` unless the user asks for staged lifecycle or debug graph output.

## Element

Valid generic element shape:

```python
element = Element("factory", "name")
element.set("property", value)
element.set(other_property=value)
pipeline.add(element)
```

Valid methods:

- `.set(*property_value_pairs, **props)`
- `.get_raw()`
- `.link(downstream, src_pad="src", sink_pad="sink")`
- `.input(...)`
- `.output(...)`
- `.connect_signal(signal_name, callback, *user_args) -> int`
- `.disconnect_signal(handler_id) -> Element`
- `.sync()`
- `.stop()`

Do not invent element methods.

`Element.connect_signal(signal_name, callback, *user_args)` connects a generic
GObject signal on the underlying element and returns an integer handler id;
`Element.disconnect_signal(handler_id)` disconnects it. Use these for real GObject
element signals not covered by a typed wrapper callback (for example a runtime
camera/action signal on a generic element). They are the preferred form over the
`get_raw().connect(...)` escape hatch. Do NOT use them as a replacement for the
dedicated AppSrc/AppSink wrapper callbacks (`set_buffer_producer`,
`set_buffer_consumer`, `set_preroll_handler`, `set_eos_handler`,
`set_enough_handler`) — those remain the correct API for buffer boundaries.

`Element.get_raw()` returns the underlying PyGObject `GstElement`. Use it when a generic GStreamer element property expects another `GstElement` object rather than a string, for example `fpsdisplaysink`'s object-valued `video-sink` property:

```python
wayland_sink = Element("waylandsink", "wayland_sink")
fps_sink = Element("fpsdisplaysink", "fps_sink").set(
    "video-sink", wayland_sink.get_raw()
)
```

Add and link `fps_sink`; do not link `wayland_sink` separately as a second terminal sink. Generic non-QIM GStreamer factories are supported through `Element` when their properties and object-valued arguments are supplied with the correct PyGObject types.

`Element.input(id_or_name)` / `.output(id_or_name)` return a `Port` for pad-level property access. Use this for request-pad/input-pad properties such as `qtivcomposer` sink alpha/position/dimensions:

```python
composer = pipeline.get("composer")
composer.input(1).set("alpha", 0.5)
composer.input(1).set("position", [960, 0])
composer.input(1).set("dimensions", [960, 540])
```

Do not set pad properties as element-level properties. For `qtivcomposer`, use
the pad properties `position` and `dimensions`; do not invent scalar pad
properties such as `x`, `y`, `width`, or `height`.

**Pass geometry as Python lists — `[x, y]` and `[w, h]` — never gst-array
strings.** `composer.input(1).set("position", [960, 0])` is correct;
`.set("position", "<960, 0>")` is NOT (the gst-array string form fails to apply
as a pad property and deadlocks composer preroll — verified on device). Likewise
never set `sink_1::position`/`sink_1::dimensions` as element-level properties on
the composer — that raises `Unknown property 'sink-1::position'`. Only the
`.input(N).set("position"/"dimensions", [...])` pad form works.

## Filters

Use filter objects with `Pipeline.add_stream_filter(...)`, never `Pipeline.add(...)`.

Generated apps should always use the named form. Do not use the one-argument form, because multiple filters of the same type can collide on default element names such as `textfilter`.

If the filter appears in `pipeline.link(...)`, the link name must match the `add_stream_filter(...)` name.

Video:

```python
vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)
pipeline.add_stream_filter("vf", vf)

mlf1 = TextFilter()
mlf2 = TextFilter()
pipeline.add_stream_filter("mlf1", mlf1)
pipeline.add_stream_filter("mlf2", mlf2)
```

Rules:

- use `.resolution(width, height)`, not `.width()` or `.height()`
- use `.framerate(fps)` or `.framerate(num, den)`
- use `TextFilter()` for `text/x-raw` metadata branches
- use `TensorFilter()` for tensor metadata/tensor dump patterns
- use `H264Filter()` for compressed H.264 stream constraints

Additional filter helpers confirmed by the SDK:

- `VideoFilter.colorimetry(value)`, `.range(value)`, `.interlace(mode)`, `.pixel_aspect_ratio(num, den=1)`
- `H264Filter.profile(profile)`, `.level(level)`, `.stream_format(fmt)`, `.alignment(value)`, `.codec_data(value)`, `.set(key, value)`
- `TensorFilter.Type`, `.type(t)`, `.dimensions(*dims)`; `.dimensions(...)` also accepts multi-tensor dimension lists
- `TextFilter.add(expr)`
- `AudioFilter.format(fmt)`, `.channels(n)`, `.rate(hz)`, `.layout(layout)` using string values or `AudioFormat` / `AudioLayout`

## Wrappers

Use wrappers when the request needs wrapper-specific behavior:

- `AppSrc` for Python-produced buffers
- `AppSink` for Python-consumed buffers
- `CamSrc` for camera convenience behavior
- `MLVConverter` for custom preprocess handlers on discrete AI pipelines
- `MLPostprocess` for custom postprocess handlers
- `MLVideo*Bin` wrappers for ML-bin preprocess/postprocess handler access

Do not use wrapper-specific APIs on generic `Element` objects.

## AppSrc/AppSink Callback APIs

Use these wrapper APIs for Python buffer boundaries:

```python
appsrc.set_buffer_producer(producer)
appsrc.set_enough_handler(on_enough)
appsink.set_buffer_consumer(consumer)
appsink.set_preroll_handler(on_preroll)
appsink.set_eos_handler(on_eos)
```

`AppSrc` and `AppSink` do not have a generic `set_handler(...)`. For a raw GStreamer `need-data` callback on `AppSrc`, use `appsrc.connect_signal("need-data", handler)` (or the equivalent `appsrc.get_raw().connect("need-data", handler)`) instead of inventing a wrapper method.

Additional `AppSrc` methods:

- `AppSrc.Format`: `DEFAULT`, `BYTES`, `TIME`, `BUFFERS`, `PERCENT`
- `.set_caps(caps)` accepts a stream filter, caps string, or raw caps object
- `.push_buffer(buffer: Buffer) -> bool` — consumes the wrapper (`take_gst_buffer()`); to relay a buffer received in an `AppSink` consumer, push a copy: `AppSrc.push_buffer(Buffer(gst_buffer=received.take_gst_buffer().copy()))`.
- `.end_of_stream()`
- For a resident/relay `AppSrc` (e.g. cross-pipeline event recording), set element properties `format=3` (GST_FORMAT_TIME), `is-live=True`, and `do-timestamp=True`, and give it **fully-fixed** caps (`VideoFilter().format("NV12").resolution(W,H).framerate(FPS)`). `is-live` lets the recording pipeline finish preroll without data so it can be held PLAYING and fed only when gated on. See the event-recording pattern in `ai-pipeline-patterns.md`.

`Buffer` wraps a `GstBuffer` for reading, writing, and timestamping data. Confirmed helpers include `.data()`, `.size()`, `.set_pts(...)`, `.set_dts(...)`, `.set_duration(...)`, `.pts`, `.dts`, `.duration`, `.is_writable()`, `.is_readonly()`, and `.valid()`. **`.data()` maps the buffer for reading and returns a `memoryview` of the payload bytes** (or `None`) — read a metadata/text payload in an `AppSink` consumer with `raw = bytes(buf.data()); text = raw.split(b"\x00", 1)[0].decode("utf-8", "ignore")`. Construct a wrapper around an existing GstBuffer with `Buffer(gst_buffer=<gstbuffer>)`.

## Camera Helpers

`CamSrc` wraps a `qticamsrc`-style camera source and adds capture helpers:

- `CamSrc.CaptureMode.STILL`
- `CamSrc.CaptureMode.BURST`
- `.capture(mode=CaptureMode.STILL, count=1, metadata=None)`
- `.image_capture(*args, **kwargs)`
- `.cancel_capture()`

Use `pipeline.get("source", CamSrc)` to obtain a typed wrapper around an already-added camera source element.

## Custom Preprocess Callback Form

Use custom preprocess only when requested. The public discrete wrapper is:

```python
preprocess = (
    MLVConverter("preprocessing")
    .set(engine="none")
    .set_handler(preprocess_callback)
)
```

ML-bin wrappers expose:

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set("preprocess-engine", "none")
mlbin.set_preprocess_handler(preprocess_callback)
```

Supported callback signature:

```python
def preprocess_callback(blits, outmlframe) -> bool:
    ...
```

Facts:

- `blits` is a list of `MLVideoBlit` wrappers.
- `MLVideoBlit` exposes `destination`, `alpha`, `info`, `planes()`, and `unmap()`.
- `outmlframe` is the writable ML frame passed by `qtimlvconverter`.
- Emit explicit `.set(engine="none")` before `MLVConverter.set_handler(...)` so the custom callback owns tensor production.
- Emit explicit `"preprocess-engine", "none"` before ML-bin `.set_preprocess_handler(...)`.
- Do not tell generated callbacks to call `blit.unmap()` manually; qimsdk unmaps `MLVideoBlit` entries after the callback returns. Do not retain plane memoryviews after callback return.

If the user did not provide exact model input layout, quantization, color conversion, scaling, and normalization requirements, generate a TODO placeholder instead of inventing tensor conversion logic.

## Custom Postprocess Callback Forms

Supported callback signatures:

```python
def callback(mlframe, mlparams, results: ObjectDetections):
    ...
```

```python
def callback(mlpostprocess, mlframe, mlparams, results: ObjectDetections):
    ...
```

The output parameter must be annotated with a public marker type:

- `ImageClassifications`
- `AudioClassifications`
- `ObjectDetections`
- `Poses`
- `DepthMaps`
- `Segmentations`
- `Tensors`

`MLPostprocess.set_handler(callback, kind=None)` infers `kind` from the annotation for discrete postprocess wrappers. ML-bin wrappers use `.set_postprocess_handler(callback, kind=None)`. If annotations are unavailable, pass an explicit supported `kind`.

## Lifecycle

Default:

```python
pipeline.execute()
```

Use staged lifecycle only when requested:

```python
pipeline.start()
pipeline.wait()
pipeline.stop()
```

For file/mux outputs:

```python
pipeline.eos(True)
pipeline.execute()
```
