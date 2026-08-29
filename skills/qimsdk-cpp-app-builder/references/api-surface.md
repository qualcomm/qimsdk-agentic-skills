# QIMSDK C++ API Surface

Use this file as the API truth for generated C++ code.

## Core Header

- Include umbrella header: `#include <qti/qimsdk.h>`
- `qimsdk.h` pulls in every class header (`qimsdk-pipeline.h`, `qimsdk-element.h`, `qimsdk-buffer.h`, `qimsdk-appsrc.h`, `qimsdk-appsink.h`, `qimsdk-camsrc.h`, `qimsdk-mlvconverter.h`, `qimsdk-mlpostprocess.h`, `qimsdk-preprocess-base.h`, `qimsdk-postprocess-base.h`, `qimsdk-mlvideotflitebin.h`, `qimsdk-mlvideoonnxbin.h`, `qimsdk-mlvideoqnnbin.h`, `qimsdk-mlvideosnpebin.h`, `qimsdk-ml-types.h`, `qimsdk-stream-filter.h`, `qimsdk-logging.h`). Do not include per-class headers individually in generated apps — always include `<qti/qimsdk.h>`.
- The library target to link against is `qimsdk-app-builder` (a shared library). Downstream apps only need `target_link_libraries(<target> PRIVATE qimsdk-app-builder)` — GStreamer and the QTI ML/video base libraries are private/transitive dependencies of `qimsdk-app-builder` and must not be linked directly by the app.

## Pipeline API (`qimsdk-pipeline.h`)

- `qti::Pipeline(const std::string& name)`
- `qti::Pipeline(const std::string& name, const std::string& config)` — config = full YAML text
- `Pipeline& add(const std::string& factory, const std::string& unique_name, Rest&&... rest)` — variadic prop/value pairs
- `Pipeline& add(const qti::Element& element)`
- `Pipeline& add_stream_filter(const std::string& unique_name, const qti::StreamFilter& caps)`
- `Pipeline& link(Names&&... names)` — variadic name list
- Lifecycle: `prepare()`, `activate()`, `deactivate()`, `start()`, `wait()`, `stop()`, `execute()`
- `Pipeline& eos(bool enabled)` — set/clear EOS; `bool eos() const` — query EOS flag
- Element access:
  - Use `get(const std::string& unique_name)` when retrieving `qti::Element`.
  - Use `get<T>(...)` for typed wrappers that support raw-element construction (for example `qti::CamSrc`, `qti::AppSrc`, `qti::AppSink`).
  - Do not use `get<qti::Element>(...)`; use non-template `get(...)` instead.

**Linking model**: elements are linked by name string via `Pipeline::link(Names&&...)`. For a straight-line chain, omit `.link()` — the pipeline auto-links added elements in insertion order on `.start()`/`.execute()`. For branching (`tee`, `qtimetamux` request pads, demuxer dynamic pads), call `.link()` more than once starting from the same source name.

**Dynamic pads (qtdemux) need no manual callback**: for a straight-line hop through `qtdemux` into `h264parse`, just add them in insertion order — the SDK defers/completes the pad-added link internally, so no hand-written `pad-added` callback is required. This differs from the plain-C app convention where `qtdemux`/`rtspsrc` require a manually written pad-added callback.

**Error handling**: construction/link/state-transition failures throw `std::exception`-derived errors. Every generated `main()` must wrap pipeline construction/execution in `try`/`catch`:

```cpp
try {
    create_and_execute_pipeline();
} catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
}
```

Fallible/optional operations (e.g. `AppSrc::push_buffer`, `CamSrc::image_capture`, `MLParam::get<T>`, postprocess callback return value) use `bool` returns instead of exceptions.

**No built-in main loop, SIGINT, or CLI utilities.** The SDK does provide generic *element* signal connect/disconnect (`qti::Element::connect_signal(...)` / `disconnect_signal(...)`) for GObject element signals, but there is no `SIGINT`/Ctrl+C handling, no CLI arg-parsing helper, and no shared main-loop driver anywhere in the SDK. If the user wants graceful Ctrl+C shutdown or CLI argument handling, that must be hand-written — do not assume the SDK provides it, and do not silently omit it if the user asked for it.

## Element API (`qimsdk-element.h`)

- Construction: `qti::Element(const std::string& factory, const std::string& name = {})`
- Properties: `set(const char* prop, Value&& value, Rest&&... rest)` — variadic prop/value pairs; accepts `const char*`, `std::string`, `bool`, integer widths, `float`, `double`, `const qti::StreamFilter&` (for a `caps` property), and enums (cast to `int`)
- Lifecycle: `deactivate()`, `stop()`, `sync()`
- Linking: `link(Element& downstream, src_pad, sink_pad)` and `unlink(...)`
- Signals: `SignalHandlerId connect_signal(const std::string& signal_name, SignalCallback callback, void* user_data = nullptr)` and `Element& disconnect_signal(SignalHandlerId handler_id)` — typedefs `SignalHandlerId = unsigned long`, `SignalCallback = void (*)()`. Connects/disconnects a generic GObject signal on the element. Use for real element signals not covered by a typed wrapper callback; not a replacement for the AppSrc/AppSink `set_*_handler` callbacks. `connect_signal` throws on empty name / null callback / unknown signal.
- Port access: `input(unsigned int id)`, `input(const std::string& name_or_type, unsigned int id)`, `output(unsigned int id)`, `output(const std::string& name_or_type, unsigned int id)` — `Port` is the pad-level handle for setting request-pad properties, e.g. `pipeline.get("composer").input(1).set("alpha", 0.5)`

`Port::set(const char* prop, Value&& value, Rest&&... rest)` uses the same variadic prop/value shape as `Element::set(...)`. It also accepts initializer-list/vector values for array-valued pad properties, for example `port.set("dimensions", {1920, 1080})` or `port.set("position", std::vector<int>{0, 0})`.

For `qtivcomposer`, set layout with the pad properties `position` and
`dimensions`. Do not invent scalar pad properties such as `x`, `y`, `width`, or
`height`.

## Stream Filters (`qimsdk-stream-filter.h`)

Copyable (backed by `shared_ptr`, unlike the other mostly move-only classes). Always pass a unique name string and a filter instance as the two args to `add_stream_filter(...)`. Do not add stream filters with `pipeline.add(...)`; they are not pipeline elements.

Any filter referenced in `pipeline.link(...)` must use the same name string passed to `add_stream_filter(...)`.

- `qti::VideoFilter` — `.format(string|Format)`, `.resolution(w, h)`, `.framerate(num[, den])` or `.framerate(float)`, `.colorimetry(string)`, `.range(string)`, `.interlace(string)`, `.pixel_aspect_ratio(num[, den])`, `.add(expr)`
- `qti::ImageFilter` — `.format`, `.resolution`, `.framerate`, `.add` (JPEG/still-capture caps)
- `qti::H264Filter` — `.resolution`, `.framerate`, `.profile`, `.level`, `.stream_format`, `.alignment`, `.codec_data`, `.set(key, val)`, `.add`
- `qti::TensorFilter` — `Type` enum (`UINT8, UINT16, UINT32, INT8, INT16, INT32, FLOAT16, FLOAT32`); `.type(string|Type)`; `.dimensions(UInts...)` variadic, or `.dimensions(vector<int>)` / `.dimensions(vector<vector<int>>)` for multi-tensor
- `qti::TextFilter` — `.add(expr)` only; used as the metadata bus into `qtimetamux`
- `qti::AudioFilter` — `Format`/`Layout` enums; `.format`, `.channels`, `.rate`, `.layout`, `.add`
- Base constructor (raw caps-string escape hatch): `explicit StreamFilter(const std::string& caps_or_mediatype)` — use when the typed subclasses cannot express the required caps (rare)

```cpp
pipeline.add_stream_filter("vf", VideoFilter().format("NV12").resolution(1920, 1080).framerate(30));
pipeline.add_stream_filter("stage01_textfilter", TextFilter());
```

## AppSrc/AppSink/Buffer

- `qti::AppSrc`
  - `AppSrc(const std::string& name = {})`
  - `enum class Format : uint32_t { DEFAULT = 1, BYTES = 2, TIME = 3, BUFFERS = 4, PERCENT = 5 }`
  - `set(...)`, `set_buffer_producer(std::function<bool(qti::Buffer&)> producer)`, `set_enough_handler(std::function<void()> enough)`
  - `push_buffer(qti::Buffer& buffer)`, `push_buffer(qti::Buffer&& buffer)`, `end_of_stream()`
  - Callback registration uses `std::function`, not raw function pointers
- `qti::AppSink`
  - `AppSink(const std::string& name = {})`
  - `set(...)`, `set_buffer_consumer(std::function<void(qti::Buffer)> consumer)`, `set_preroll_handler(std::function<bool(qti::Buffer&&)> preroll)`, `set_eos_handler(std::function<void()> eos)`
- `qti::Buffer`
  - `Buffer()`, `Buffer(size_t size)`, `static Buffer from_readable_sample(void* gst_sample_opaque)`
  - `data()`, `size()`, `resize(size_t n)`, `set_pts(uint64_t ns)`, `set_dts(uint64_t ns)`, `set_duration(uint64_t ns)`, `pts() const`, `dts() const`, `duration() const`, `is_writable() const`, `is_readonly() const`, `valid() const`
  - `take_gst_buffer()` transfers ownership of the underlying `GstBuffer` to the caller; use only for low-level buffer ownership flows explicitly requested by the user
- Move-only. Used with `AppSrc`/`AppSink`.

`qti::AppSrc` and `qti::AppSink` do not use a generic `set_handler(...)`. Use the specific producer/consumer/preroll/EOS methods above.

## Camera Control

- `qti::CamSrc` — wraps the `qticamsrc` factory
  - `explicit CamSrc(const std::string& name = {})`
  - `enum class CaptureMode : unsigned int { kStill = 0, kBurst = 1 }`
  - `bool image_capture(unsigned int count = 1u)`
  - `bool image_capture(CaptureMode mode, unsigned int count = 1u)`
  - `bool image_capture(CaptureMode mode, unsigned int count, const std::vector<void*>& metadata_ptrs)`
  - `bool cancel_capture()`
  - Camera id and format are set via the inherited `Element::set()` (e.g. `source.set("camera", 0)`) or inline in `.add(...)` — there is no dedicated resolution/format setter on `CamSrc` itself; resolution/format/framerate are negotiated downstream via a `VideoFilter`/`ImageFilter` stream filter

## ML Wrappers and Postprocess

- `qti::MLVConverter`
  - `explicit MLVConverter(const std::string& name = {})`
  - `set(...)`
  - `set_handler(callback)` for custom preprocess callbacks
- `qti::MLPostprocess`
  - `set(...)`, `set_handler(callback)` — use `set_handler` on the discrete `MLPostprocess` wrapper; use `set_postprocess_handler` on ML bins (these are different methods on different classes)
- `qti::MLVideoTFLiteBin`, `qti::MLVideoQNNBin`, `qti::MLVideoSNPEBin`, `qti::MLVideoONNXBin`
  - All derive from `MLPreprocessBase` and `MLPostprocessBase` and share an identical shape, differing only by the wrapped factory (`qtimlvideotflitebin`, `qtimlvideoonnxbin`, `qtimlvideoqnnbin`, `qtimlvideosnpebin`)
  - Each supports `set(...)`, `set_preprocess_handler(callback)`, and `set_postprocess_handler(callback)`
  - Delegate/backend selection is via plain string property values (`inference-*`/`postprocess-*` prefix convention); there are no distinct C++ delegate-enum types per runtime
  - For multi-stage pipelines, prefer daisy-chaining fused ML bins back-to-back; set `"preprocess-mode", "roi-batch-cumulative"` on every bin after the first

## Custom Preprocess Callback

`MLPreprocessBase` defines:

```cpp
using TensorsPreprocessCallback =
    std::function<bool(const MLVideoBlits& blits, MLFrame& output)>;
```

Use this shape for custom preprocess placeholders:

```cpp
qti::TensorsPreprocessCallback preprocess_callback =
    [](const qti::MLVideoBlits& blits, qti::MLFrame& output) {
      // TODO: inspect blits.entries and output.tensors.
      // TODO: implement model-specific image-to-tensor conversion.
      // Keep return false until a valid tensor is written; after writing output, return true.
      return false;
    };
```

Facts:

- `qti::MLVConverter::set_handler(...)` registers the callback on a discrete `qtimlvconverter` wrapper.
- `qti::MLVideo*Bin::set_preprocess_handler(...)` registers the callback on an ML-bin wrapper.
- `MLVideoBlits` contains input image/blit metadata; `MLFrame` contains output tensors to fill.
- Do not invent tensor conversion logic when tensor shape, quantization, color order, normalization, and resize/letterbox policy are unknown.

## Logging API

- `qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog or GstLog)`
- `qti::SetImsdkLogLevel(qti::ImsdkLogLevel::{Error, Warning, Info, Debug})`
- Default generated apps to `qti::ImsdkGstLogMode::ImsdkLog` and `qti::ImsdkLogLevel::Debug` unless user explicitly requests a different logging mode or verbosity.
- Emit both calls at the very top of `main()`, before constructing any `Pipeline` — fixed boilerplate present in every SDK example

## Generation Rules

- Do not use methods not listed in SDK headers.
- Keep callback signatures compatible with SDK typedefs in `qimsdk-postprocess-base.h`.
- Do not apply plain-C-app rules (`gst_element_factory_make`, `gst_sample_apps_utils.h`, `GstAppContext`) — `qti::Pipeline`/`qti::Element` supersede all of that scaffolding; do not mix the two APIs in one generated app.
