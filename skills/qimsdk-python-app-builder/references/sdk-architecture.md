# SDK Architecture

## Public Facade

`qimsdk/__init__.py` exports the public API. Generated apps should import from this facade.

Important modules:

- `_pipeline.py` - `Pipeline`, linking, dynamic pad handling, YAML loading, lifecycle
- `_element.py` - `Element`, `Port`, property setting, pad helpers
- `_stream_filter.py` - `VideoFilter`, `ImageFilter`, `H264Filter`, `TensorFilter`, `TextFilter`, `AudioFilter`
- `_appsrc.py` - `AppSrc` wrapper
- `_appsink.py` - `AppSink` wrapper
- `_camsrc.py` - `CamSrc` wrapper
- `_mlvconverter.py` - `MLVConverter.set_handler(...)` and `MLVideoBlit` for custom preprocess callbacks
- `_mlpostprocess.py` - `MLPostprocess.set_handler(...)`
- `_mlvideo*tbin.py` / `_mlvideo*qnnbin.py` / `_mlvideo*snpebin.py` / `_mlvideo*onnxbin.py` - ML-bin wrappers with preprocess and postprocess handler delegation
- `typing.py` - callback marker types
- `_logging.py` - public logging enums and setters

## Generation Implications

- Default to explicit object construction because it is easier to review and extend.
- Use wrapper classes only when their wrapper behavior is needed.
- Use `Element` for generic GStreamer plugin elements.
- Use stream filter classes instead of raw caps strings where the SDK provides a filter.
- Use `MLVConverter` only when custom preprocess behavior is needed.
- Use public marker types for custom postprocess callbacks.
- Use `Pipeline.from_yaml(...)` only when the user asks for YAML/config-driven construction.
