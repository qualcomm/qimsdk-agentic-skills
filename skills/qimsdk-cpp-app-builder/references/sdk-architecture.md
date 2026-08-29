# SDK Architecture Notes

Use this file to keep generated C++ aligned with the SDK structure in `include/` and `src/`.

## Include-Level API Surface

Primary public headers under `include/qti/`:

- `qimsdk.h` (umbrella include)
- `qimsdk-pipeline.h`
- `qimsdk-element.h`
- `qimsdk-stream-filter.h`
- `qimsdk-logging.h`
- `qimsdk-appsrc.h`
- `qimsdk-appsink.h`
- `qimsdk-camsrc.h`
- `qimsdk-mlvconverter.h`
- `qimsdk-preprocess-base.h`
- `qimsdk-mlpostprocess.h`
- `qimsdk-postprocess-base.h`
- `qimsdk-mlvideotflitebin.h`
- `qimsdk-mlvideoqnnbin.h`
- `qimsdk-mlvideosnpebin.h`
- `qimsdk-mlvideoonnxbin.h`

## Runtime Construction Model

- `qti::Pipeline` adds explicit `qti::Element`/wrapper objects and also supports factory string plus unique name.
- Element wrappers are created through registry logic in `src/qimsdk-element-registry.cc`.
- Unknown factory names fall back to generic `qti::Element`.

Implication:

- Default generated apps should create explicit `qti::Element`/wrapper objects, set properties, then add them to the pipeline.
- Generic plugins can also be added via `.add("<factory>", "<name>", ...)` when the user asks for fluent style or when preserving existing fluent code.
- Wrapper-specific behavior should use wrapper classes only when needed.

## Retrieval Model

From `qimsdk-pipeline.h` + `qimsdk-element.h`:

- `pipeline.get("<name>")` returns `qti::Element` and is safest for generic retrieval.
- Template `pipeline.get<T>("<name>")` constructs `T(raw)` and should be used only for wrapper types that support that constructor.

## Lifecycle Model

Available pipeline lifecycle methods:

- `prepare()`, `activate()`, `deactivate()`
- `start()`, `wait()`, `stop()`
- `execute()`

Generation default:

- Prefer `execute()` unless user explicitly asks for manual staged control.

## Logging Enums (Canonical)

From `qimsdk-logging.h`:

- `ImsdkGstLogMode::{GstLog, ImsdkLog}`
- `ImsdkLogLevel::{Error, Warning, Info, Debug}`

## Preprocess Callback Model

From `qimsdk-preprocess-base.h`:

- use `qti::TensorsPreprocessCallback`
- callbacks are wired through `qti::MLVConverter::set_handler(...)` or bin `set_preprocess_handler(...)`

## Postprocess Callback Model

From `qimsdk-postprocess-base.h`:

- use callback typedefs (`ObjectDetectionPostprocessCallback`, etc.)
- callbacks are wired through `qti::MLPostprocess::set_handler(...)` or bin handler APIs

## Generation Guidance

- Keep generated code aligned to the public headers above.
- Do not invent methods not present in headers.
- When uncertain on low-level behavior, keep placeholders in values, not API shape.
