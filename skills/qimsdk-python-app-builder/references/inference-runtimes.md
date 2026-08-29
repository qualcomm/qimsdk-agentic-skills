# Inference Runtimes

## Runtime Selection

Ask when the runtime cannot be inferred from the user request or existing app. Do not randomly choose between discrete inference elements and ML-bin wrappers.

Supported runtime families in the Python SDK surface:

- discrete TFLite: `qtimltflite`
- discrete QNN: `qtimlqnn`
- discrete SNPE: `qtimlsnpe`
- discrete ONNX: `qtimlonnx`
- ML-bin TFLite: `MLVideoTFLiteBin` / `qtimlvideotflitebin`
- ML-bin QNN: `MLVideoQNNBin` / `qtimlvideoqnnbin`
- ML-bin SNPE: `MLVideoSNPEBin` / `qtimlvideosnpebin`
- ML-bin ONNX: `MLVideoONNXBin` / `qtimlvideoonnxbin`

Use only runtimes available in the target build. If availability is unclear and the user asks for a specific runtime, preserve the request and document the requirement.

## Discrete TFLite

```python
infer = Element("qtimltflite", "infer")
infer.set("model", "<MODEL_PATH>")
infer.set("delegate", "external")
infer.set("external-delegate-path", "libQnnTFLiteDelegate.so")
infer.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
```

Rules:

- Valid documented delegate intents are `gpu` and `external`.
- For `external`, always set `external-delegate-path`.
- For `external`, always set `external-delegate-options`.
- For HTP/NPU, use `QNNExternalDelegate,backend_type=htp,log_level=(string)1;` unless the user provides exact delegate options.
- For secondary ROI inference stages where the user asks for high-performance HTP/NPU daisy-chain behavior, or for high-concurrency parallel HTP/NPU workloads with multiple simultaneous inference branches, use `QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;`.
- Never use `QNNExecurorBackend:HTP`, `QNNExecutorBackend:HTP`, `QNNExternalDelegateBackend:HTP`, or colon-separated QNN delegate option strings.

## ML-Bin TFLite

```python
mlbin = MLVideoTFLiteBin("mlbin")
mlbin.set("inference-model", "<MODEL_PATH>")
mlbin.set("inference-delegate", "external")
mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
mlbin.set("postprocess-module", "yolov8")
mlbin.set("postprocess-labels", "<LABELS_PATH>")
```

Rules:

- Use `inference-*` and `postprocess-*` keys.
- Do not use discrete keys such as `model`, `delegate`, `module`, or `labels` on ML-bin elements.
- Link ML-bin overlay directly in the media path: `source/decode -> VideoFilter(NV12) -> MLVideoTFLiteBin -> qtivoverlay -> display/file`, or direct cascades such as `mlbin1 -> mlbin2 -> qtivoverlay -> display`. Do not wrap ML-bin output in external `tee -> TextFilter -> qtimetamux` metadata fan-in.
- For custom preprocess on ML-bin wrappers, use `.set_preprocess_handler(callback)` after creating/configuring the wrapper.
- For custom postprocess on ML-bin wrappers, use `.set_postprocess_handler(callback)` after creating/configuring the wrapper.

## ML-Bin Daisy-Chain (ROI Cascade)

Two native ML-bins can be chained directly, without discrete `qtimlvconverter` / `qtimlpostprocess` stages, when the second bin consumes ROI metadata produced by the first bin's postprocess.

```python
mlbin1 = (
    MLVideoTFLiteBin("mlbin1")
    .set("inference-model", "<MODEL_PATH_STAGE1>")
    .set("inference-delegate", "external")
    .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
    .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    .set("postprocess-module", "qpd")
    .set("postprocess-labels", "<LABELS_PATH_STAGE1>")
)

mlbin2 = (
    MLVideoTFLiteBin("mlbin2")
    .set("preprocess-mode", "roi-batch-cumulative")
    .set("inference-model", "<MODEL_PATH_STAGE2>")
    .set("inference-delegate", "external")
    .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
    .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    .set("postprocess-module", "yolov8")
    .set("postprocess-labels", "<LABELS_PATH_STAGE2>")
)
```

Link ML-bin cascades directly: `source/decode -> VideoFilter(NV12) -> mlbin1 -> mlbin2 -> overlay -> display`. Do not insert `tee`, `TextFilter`, or `qtimetamux` between the ML-bins. Do not set `preprocess-mode` on a first-stage/full-frame ML-bin; set `preprocess-mode="roi-batch-cumulative"` only on ROI-consuming downstream bins.

## QNN

Use exact property names from the loaded source/example:

- `model`
- `backend`
- `system`
- `tensors`

For NPU/HTP inference, set both backend and system paths when required by the target environment. Do not invent `backend-path`, `system-lib`, `output-tensors`, or `tensor-names`.

## QAIRT / `qtimlqairt`

Use QAIRT only when the user explicitly requests it or provides model evidence
for a QAIRT/SNPE `.dlc` container or cached context `.bin`. The refreshed Python
source does not expose a dedicated QAIRT wrapper; construct the plugin through
`Element("qtimlqairt", ...)` only after target plugin availability is confirmed.

```python
infer = Element("qtimlqairt", "infer")
infer.set("model", "<QAIRT_DLC_OR_CONTEXT_BIN>")
infer.set("backend", "libQairtHtp.so")
```

Supported catalog properties are `model`, `backend`, `priority`, optional
`layers`/`tensors`, and `qos`. `backend` is the QAIRT backend shared-library name
(`libQairtHtp.so` for HTP, `libQairtGpu.so` for GPU, `libQairtCpu.so` for CPU); it
replaces the removed `delegate` enum and is required (non-null) for GPU/HTP. Omit
output filters unless model evidence requires
known outputs. Keep the normal tensor topology:

`qtimlvconverter -> qtimlqairt -> qtimlpostprocess`

Do not silently substitute QAIRT for TFLite, QNN, SNPE, or ONNX. Do not invent a
Python-specific QAIRT class or backend property names.

## SNPE and ONNX

Use only when explicitly requested or when the existing app already uses the runtime. Keep undocumented properties as placeholders rather than inventing them.

## Delegate Selection

Priority:

1. explicit user request
2. existing app style/runtime
3. documented model/runtime pattern
4. ask the user

Filename heuristics are allowed only when they clearly identify a documented model family. Generic names like `model.tflite` are not enough.
