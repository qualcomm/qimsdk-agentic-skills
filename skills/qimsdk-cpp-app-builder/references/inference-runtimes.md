# Inference Runtimes

## Use This Reference For

- Selecting the correct inference element for a requested runtime
- Getting exact property names, enum nicks, and default values for each runtime
- Understanding model format requirements and backend library paths
- Swapping runtimes in an existing pipeline
- C++ SDK element property equivalents via `Element::set()`

## The Five Inference Elements

All four elements occupy the same position in the pipeline:

```
qtimlvconverter → <inference element> → qtimlpostprocess
```

In C++ SDK default explicit style, create `qti::Element` objects for `qtimlvconverter`, the selected inference element, and `qtimlpostprocess`, configure them with `.set(...)`, then add them to `qti::Pipeline`.

All four accept `neural-network/tensors` on their sink pad and produce `neural-network/tensors` on
their source pad. **Postprocess module selection (`module=yolov8`, `module=qpd`, etc.) is
runtime-agnostic** — the same module names work regardless of which inference element produced the
tensors. `qtimlpostprocess` reads tensor metadata, not the identity of the upstream inference element.

## Runtime Comparison

| Runtime | Element | Model format | Primary backend | Availability |
|---------|---------|-------------|----------------|-------------|
| TFLite / LiteRT | `qtimltflite` | `.tflite` | GPU, HTP (via external delegate) | Available |
| SNPE | `qtimlsnpe` | `.dlc` | DSP (Hexagon), GPU, CPU, AIP | Available |
| QNN | `qtimlqnn` | `.so` or `.bin` | HTP/NPU, GPU, CPU | Available |
| QAIRT | `qtimlqairt` | `.dlc` or cached context `.bin` | HTP, GPU, CPU | Verify target plugin availability |
| ONNX Runtime | `qtimlonnx` | `.onnx` | CPU, QNN (HTP/NPU) | **Not in current build** |

## When to Use Each Runtime

- **qtimltflite** — user has a `.tflite` model and wants TFLite execution; or wants HTP via QNN TFLite external delegate
- **qtimlsnpe** — user has a `.dlc` model (converted via Qualcomm SNPE SDK); DSP delegate gives best latency for many models
- **qtimlqnn** — user has a QNN-compiled `.so` model or cached `.bin`; gives direct access to HTP/NPU without TFLite layer
- **qtimlqairt** — user explicitly requests QAIRT or provides a QAIRT/SNPE `.dlc` container or cached context `.bin`; verify target plugin availability
- **qtimlonnx** — user has a `.onnx` model; pipeline structure is correct but runtime is not yet in the current SDK build

---

## qtimltflite — Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | — | Path to `.tflite` model file |
| `delegate` | enum | `none` | Execution backend. Nicks: `none` (CPU), `gpu` (Adreno GPU), `nnapi`, `hexagon`, `xnnpack`, `external` |
| `external-delegate-path` | string | — | Path to external delegate shared library. Required when `delegate=external`. Example: `libQnnTFLiteDelegate.so` |
| `external-delegate-options` | string | — | Options string for external delegate. For QNN HTP: `"QNNExternalDelegate,backend_type=htp,log_level=(string)1;"` |
| `priority` | enum | `normal` | Inference execution priority |
| `threads` | uint | 1 | Number of CPU threads (CPU delegate only) |

### TFLite HTP pattern (external delegate to QNN HTP)

gst-launch:
```
qtimltflite model=<model.tflite> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
```

C++ SDK (`Element::set()` style):
```cpp
element.set("delegate", "external");
element.set("external-delegate-path", "libQnnTFLiteDelegate.so");
element.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
element.set("model", "<MODEL_PATH>");
```

Or inline in `.add()`:
```cpp
pipeline.add("qtimltflite", "infer",
     "delegate", "external",
     "external-delegate-path", "libQnnTFLiteDelegate.so",
     "external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
     "model", "<MODEL_PATH>")
```

### TFLite GPU pattern

gst-launch: `qtimltflite model=<model.tflite> delegate=gpu`

C++ SDK: `element.set("delegate", "gpu"); element.set("model", "<MODEL_PATH>");`

---

## ML-bin TFLite delegate (C++ SDK `inference-*` properties)

When using the fused ML bin (`qtimlvideotflitebin` / `qti::MLVideoTFLiteBin`), delegate/backend selection uses `inference-*`-prefixed properties instead of bare property names:

| ML-bin property | Equivalent bare element property |
|---|---|
| `inference-delegate` | `delegate` |
| `inference-external-delegate-path` | `external-delegate-path` |
| `inference-external-delegate-options` | `external-delegate-options` |
| `inference-model` | `model` |

Valid `inference-delegate` values: `"gpu"` or `"external"`. When using `"external"`, always set `inference-external-delegate-path` and `inference-external-delegate-options`.

```cpp
element.set("inference-delegate", "external");
element.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
element.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
element.set("inference-model", HOME_PATH + "/models/<MODEL_FILE>.tflite");
```

Rule: If delegate is `external`, always include the paired external delegate path/options properties. For HTP/NPU, use `QNNExternalDelegate,backend_type=htp,log_level=(string)1;` unless the user provides exact delegate options. For secondary ROI inference stages where the user asks for high-performance HTP/NPU daisy-chain behavior, or for high-concurrency parallel HTP/NPU workloads with multiple simultaneous inference branches, use `QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;`. Never use `QNNExecurorBackend:HTP`, `QNNExecutorBackend:HTP`, `QNNExternalDelegateBackend:HTP`, or colon-separated QNN delegate option strings. If delegate is `gpu`, do not emit external-delegate properties unless user explicitly asks.

For ML-bin custom preprocess, use `set_preprocess_handler(callback)` and set `"preprocess-engine", "none"` when overriding the built-in preprocessor.

---

## qtimlsnpe — Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | — | Path to `.dlc` model file |
| `delegate` | enum | `none` | Execution backend |
| `performance-profile` | enum | `default` | Performance/power tradeoff |
| `profiling-level` | enum | `off` | Runtime diagnostics verbosity |
| `priority` | enum | `normal` | Execution priority |
| `layers` | GstValueArray of strings | — | Output layer names. Mutually exclusive with `tensors`. |
| `tensors` | GstValueArray of strings | — | Output tensor names. Mutually exclusive with `layers`. |

**`delegate` enum nicks:**

| Nick | Target |
|------|--------|
| `none` | CPU |
| `dsp` | Hexagon DSP (recommended for latency on QCS6490/IQ devices) |
| `gpu` | Adreno GPU |
| `aip` | Snapdragon AIX + HVX |

**`performance-profile` enum nicks:**

| Nick | Description |
|------|-------------|
| `default` | Standard mode |
| `balanced` | Balance between performance and power |
| `high-performance` | Maximum performance |
| `power-saver` | Low power mode |
| `system-settings` | Use system settings |
| `sustained-high-performance` | High performance maintained over time |
| `burst` | Maximum burst performance |
| `low-power-saver` | Lower clock than power-saver |
| `high-power-saver` | Higher clock than power-saver with better performance |
| `low-balanced` | Lower balanced mode |

**`profiling-level` enum nicks:** `off`, `basic`, `detailed` (per-layer statistics), `moderate`

**`priority` enum nicks:** `normal`, `high`, `low`

**`layers` vs `tensors`:**
- Both are optional filters. Default empty means "emit every model output tensor in native order."
- Use a filter only when there is positive evidence that the model emits extra/debug outputs or a non-default output order that the downstream `qtimlpostprocess module` does not already expect.
- They are mutually exclusive — setting one clears the other.
- Use `layers` if your model identifies outputs by layer name; use `tensors` if it identifies outputs by tensor name.
- If unknown, omit both. Never invent `tensors="<OUTPUT_TENSOR>"`.

**In gst-launch, GstValueArray is written as a comma-separated list in angle brackets:**
```
tensors="<output_tensor_name>"
layers="<layer_name_0>,<layer_name_1>"
```

### SNPE DSP pattern

gst-launch: `qtimlsnpe model=<model.dlc> delegate=dsp`

C++ SDK:
```cpp
pipeline.add("qtimlsnpe", "infer",
     "delegate", "dsp",
     "model", "<MODEL_PATH>")
```

### SNPE DSP with performance profile

gst-launch: `qtimlsnpe model=<model.dlc> delegate=dsp performance-profile=sustained-high-performance`

### SNPE GPU pattern

gst-launch: `qtimlsnpe model=<model.dlc> delegate=gpu`

### SNPE CPU pattern

gst-launch: `qtimlsnpe model=<model.dlc> delegate=none`

---

## qtimlqnn — Full Property Reference

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | — | Path to `.so` QNN model shared library or `.bin` cached binary |
| `backend` | string | `/usr/lib/libQnnCpu.so` | Path to QNN backend shared library |
| `system` | string | `/usr/lib/libQnnSystem.so` | Path to QNN system shared library |
| `backend-device-id` | uint | `0` | Backend device index (for multi-CDSP devices) |
| `tensors` | GstValueArray of strings | empty | Optional output tensor filter, order-preserving. Empty emits all model outputs. |

**Backend library paths by hardware target:**

| Target | `backend` value |
|--------|----------------|
| NPU / HTP (recommended for inference) | `/usr/lib/libQnnHtp.so` |
| Adreno GPU | `/usr/lib/libQnnGpu.so` |
| CPU | `/usr/lib/libQnnCpu.so` (default) |

### QNN HTP/NPU pattern (recommended)

gst-launch: `qtimlqnn model=<model.so> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so`

C++ SDK:
```cpp
pipeline.add("qtimlqnn", "infer",
     "model", "<MODEL_PATH>",
     "backend", "/usr/lib/libQnnHtp.so",
     "system", "/usr/lib/libQnnSystem.so")
```

### QNN GPU pattern

gst-launch: `qtimlqnn model=<model.so> backend=/usr/lib/libQnnGpu.so system=/usr/lib/libQnnSystem.so`

### QNN CPU pattern

gst-launch: `qtimlqnn model=<model.so> backend=/usr/lib/libQnnCpu.so system=/usr/lib/libQnnSystem.so`

### QNN cached binary pattern

gst-launch: `qtimlqnn model=<model.bin> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so`

---

## qtimlqairt — QAIRT / cached-context inference

Use `qtimlqairt` only when QAIRT is explicitly requested or model evidence
requires a `.dlc` container or cached context `.bin`. The refreshed C++ source
has no dedicated QAIRT wrapper; construct it as a generic `qti::Element` only
after target plugin availability is confirmed.

```cpp
qti::Element infer("qtimlqairt", "infer");
infer.set("model", "<QAIRT_DLC_OR_CONTEXT_BIN>");
infer.set("backend", "libQairtHtp.so");
```

Supported catalog properties are `model`, `backend`, `priority`, optional
`layers`/`tensors`, and `qos`. `backend` is the QAIRT backend shared-library name
(`libQairtHtp.so` for HTP, `libQairtGpu.so` for GPU, `libQairtCpu.so` for CPU); it
replaces the removed `delegate` enum and is required (non-null) for GPU/HTP. Omit
output filters unless model evidence requires
known outputs. Keep the normal tensor topology:

`qtimlvconverter -> qtimlqairt -> qtimlpostprocess`

Do not silently substitute QAIRT for TFLite, QNN, SNPE, or ONNX. Do not invent a
C++-specific QAIRT wrapper or property name.

## qtimlonnx — Full Property Reference

> **Build status:** `qtimlonnx` is not available in the current SDK build. The pipeline syntax is correct and should work when the plugin is released, but it cannot be tested on device. Always include a note in the generated artifact README when using this runtime.

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `model` | string | — | Path to `.onnx` model file |
| `execution-provider` | enum | `cpu` | Execution backend |
| `backend-path` | string | — | Path to QNN backend lib. Required when `execution-provider=qnn`. Example: `/usr/lib/libQnnHtp.so` |
| `htp-performance-mode` | enum | `default` | HTP performance mode. Only applies when `execution-provider=qnn` |
| `optimization-level` | enum | `extended` | ONNX Runtime graph optimization level |
| `threads` | uint | — | Number of threads. Only applies for CPU provider |

**`execution-provider` enum nicks:**

| Nick | Target |
|------|--------|
| `cpu` | CPU execution |
| `qnn` | Qualcomm AI accelerator / HTP/NPU via QNN |

**`htp-performance-mode` enum nicks (when execution-provider=qnn):**

| Nick | Description |
|------|-------------|
| `default` | Default mode |
| `burst` | Maximum performance |
| `balanced` | Balance performance/power |
| `low-balanced` | Lower balanced mode |
| `high-performance` | High performance |
| `extreme-power` | Extreme power mode |
| `low-power` | Low power mode |
| `sustained-high-performance` | Sustained high performance |

**`optimization-level` enum nicks:** `disable-all`, `basic`, `extended` (default), `all`

**Layout detection:** `qtimlonnx` automatically reads NCHW/NHWC layout from the ONNX model graph and
adds `layout=nchw` to src caps for 4-D tensors when needed.

### ONNX CPU pattern

gst-launch: `qtimlonnx model=<model.onnx> execution-provider=cpu`

### ONNX QNN/HTP pattern

gst-launch: `qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so`

### ONNX QNN/HTP with performance mode

gst-launch:
```
qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so \
  htp-performance-mode=sustained-high-performance
```

---

## Runtime Swap Pattern

To swap runtimes in the same pipeline, replace only the inference element and its properties.
Everything else — source, preprocess (`qtimlvconverter`), postprocess (`qtimlpostprocess`), overlay,
sink — stays identical.

**Example: single-stream detection, file source → display**

TFLite HTP (gst-launch):
```
qtimlvconverter ! \
qtimltflite model=<model.tflite> delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

SNPE DSP (same pipeline, swap inference stage only):
```
qtimlvconverter ! \
qtimlsnpe model=<model.dlc> delegate=dsp ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

QNN HTP:
```
qtimlvconverter ! \
qtimlqnn model=<model.so> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

ONNX QNN (not in current build):
```
qtimlvconverter ! \
qtimlonnx model=<model.onnx> execution-provider=qnn backend-path=/usr/lib/libQnnHtp.so ! \
qtimlpostprocess module=yolov8 labels=<labels.json>
```

---

## Clarification Rule

If the user does not specify a runtime, ask before generating:

> "Which inference runtime should the pipeline use? Options: TFLite (`.tflite`), SNPE (`.dlc`), QNN (`.so`/`.bin`), or ONNX (`.onnx`, note: not in current build)."

Backend/delegate selection changes the element, properties, and model format — generating without
knowing the runtime produces structurally wrong code.

---

## Known Constraints

- `qtimlonnx` is not in the current SDK build. Always add a README note when generating ONNX pipelines.
- `qtimlsnpe` `layers` and `tensors` are mutually exclusive optional filters. **Property names are exact and fail silently with wrong names:** use `tensors` (NOT `output-tensors`, NOT `tensor-names`, NOT `output-layers`); delegate nicks are lowercase (`dsp`, `gpu`, `none`, `aip`). See "Tensor Filter Decision Rule" below before adding either filter.
- `qtimlqnn` `backend` and `system` default to CPU. For NPU inference, `backend=/usr/lib/libQnnHtp.so` must be set explicitly. Always set both. **Property names are exact:** use `tensors` (NOT `output-tensors`, NOT `tensor-names`); `backend` (NOT `backend-path`); `system` (NOT `system-lib`, NOT `backend-extra`). `tensors` is optional; see the decision rule below before adding it.
- All four inference elements occupy the same pipeline position and have identical caps — they are true drop-in replacements.
- **Camera source element:** Use the documented default ISP camera source from `references/plugin-catalog.md`; use the user-requested source when explicitly provided.

---

## Tensor Filter Decision Rule

`qtimlqnn.tensors` and `qtimlsnpe.tensors`/`layers` are optional output filters and orderers, not required mappings. Default empty means "emit every model output tensor in native order." The downstream `qtimlpostprocess module=<name>` has its own expectation of input tensor count, shape, and order; add a filter only when the model's native outputs do not already match that expectation.

Default to omitting `tensors`/`layers`. Add one only with positive evidence:

- the model's conversion docs or metadata show extra/debug tensors beyond what the module expects
- the model docs specify a non-default output order that the module does not already assume

Never invent placeholder tensor names such as `<OUTPUT_TENSOR>`. If a mismatch is suspected but exact tensor names are unknown, ask for the model conversion docs or state the unresolved requirement in README instead of generating an unfillable property.

Known corpus values below can be used only when the requested model family and runtime match:

| Model family | Runtime | Tensor names |
|---|---|---|
| YOLOX, YOLOv8 detection | SNPE (DSP or GPU) | `tensors="<boxes,scores,class_idx>"` |
| YOLOX, YOLOv8 detection | QNN (HTP or GPU) | `tensors="<boxes,scores,class_idx>"` |
| InceptionV3 classification | SNPE GPU | `tensors="<class_logits>"` |

For undocumented modules, default to omitting the filter but state in README assumptions that the model output order/filtering is unverified.

---

## Performance Profile Recommendations (SNPE)

Use these as defaults when the user mentions a deployment goal:

| Use case | Recommended profile |
|----------|-------------------|
| Continuous real-time streaming | `sustained-high-performance` |
| Latency-critical single-shot | `burst` |
| General real-time | `high-performance` |
| Battery / power constrained | `power-saver` or `low-power-saver` |
| No specific requirement | `default` (omit property) |

---

## SNPE vs QNN — When to Use Each

| Situation | Use |
|-----------|-----|
| User has an existing `.dlc` model file | SNPE (`qtimlsnpe`) |
| User has a `.so` or `.bin` QNN model | QNN (`qtimlqnn`) |
| New development, no existing model | QNN is the forward path |
| User explicitly asks for SNPE SDK | SNPE |

**Key difference:** SNPE uses DLC format (converted via Qualcomm SNPE SDK). QNN uses `.so`/`.bin` compiled via QAIRT SDK. Both target Hexagon HTP/NPU. QNN is the recommended path for new development.

**SNPE delegate is self-contained** — the `delegate` enum selects the hardware target directly. No backend library path is needed (unlike QNN's `backend` property).

---

## File Sink Output Pattern

When the user does not have a display (no waylandsink), encode the annotated output to MP4:

```
qtivoverlay ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! \
mp4mux ! filesink location=<OUTPUT>.mp4
```

This replaces `waylandsink fullscreen=true sync=true` at the end of the pipeline. The full path is:
```
qtimetamux → qtivoverlay → v4l2h264enc capture-io-mode=4 output-io-mode=4 → h264parse → mp4mux → filesink location=/home/ubuntu/Downloads/qimsdk_samples/media/output/<filename>.mp4
```

**For SNPE GPU classification (Topology B — qtivcomposer):**
The SNPE GPU classification example uses `qtivcomposer` instead of `qtimetamux/qtivoverlay`. For file output:
```
qtivcomposer ! video/x-raw,format=NV12 ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! \
mp4mux ! filesink location=<OUTPUT>.mp4
```
