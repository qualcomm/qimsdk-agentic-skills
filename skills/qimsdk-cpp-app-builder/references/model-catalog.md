# Model Catalog

## Purpose

Per-model lookup table for named AI Hub models. When a model name or `.tflite` filename is identifiable in the request, look up the model here before filling in any pipeline property. Values in this table take priority over generic family heuristics in `SKILL.md`.

If a model is not in this table, proceed without it — fall through to the generic delegate rules and ask for labels/settings as needed.

## How to look up

Match by display name (case-insensitive) OR by filename (strip path prefix if present, match the base filename). A single model row covers both precision variants — check which filename(s) exist to know which precisions are available.

**What the catalog resolves vs what stays as a placeholder:**
The catalog resolves structural decisions — `module=`, `delegate=`, and whether `settings=` should be omitted, inline JSON, or a file path. File path values (`labels=`, `settings=` filename, `model=`) always stay as `<LABELS_PATH>` / `<SETTINGS_PATH>` / `<MODEL_PATH>` unless the user explicitly provided them in the prompt. The catalog's Labels file and Settings columns tell you which placeholder to use and what format to expect, but the user supplies the actual paths.

**Multi-value module cells:** Some rows use `float: X / w8a8: Y` notation (e.g. `float: mobilenet-softmax / w8a8: mobilenet`). Select the value that matches the precision variant the user named or that is implied by the filename suffix (`_float` → float, `_w8a8` → w8a8). If precision cannot be determined, ask the user before selecting the module.

**Settings column values:**
- `none` — omit the `settings=` property entirely
- `json` — use inline JSON format with `<SETTINGS_PATH>` placeholder, or user-supplied value if provided
- `file:<filename>` — use a file path: `settings=<SETTINGS_PATH>`; this is mandatory (do not omit), but use the placeholder unless the user gave an explicit path

**Delegate column scope:** Delegate values apply only when the inference element is `qtimltflite`. For SNPE (`qtimlsnpe`), QNN (`qtimlqnn`), or ONNX (`qtimlonnx`) runtimes, skip the Delegate column entirely and apply the normal runtime-specific backend/delegate rules.

**Multi-filename cells:** The HRNetPose W8A8 cell contains two comma-separated filenames (`foot_track_net_w8a8.tflite, hrnet_pose_w8a8.tflite`). A match on either filename identifies the row.

**Notes column overrides:** When the Notes column explicitly marks a field as mandatory (e.g. "settings and results are mandatory"), treat those values as pre-resolved from the catalog — they override the general "optional unless user asks" default in `generation-rules.md`.

---

## Object Detection

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| Yolo-V5 | yolov5_float.tflite | — | yolov8 | yolov8.json | json | gpu | — | — |
| Person-Foot-Detection | person_foot_detection_float.tflite | person_foot_detection_w8a8.tflite | qpd | foot_track_net.json | file:foot_track_net_settings.json | gpu | external | requires companion settings file foot_track_net_settings.json |
| Yolo-X | — | yolox_w8a8.tflite | yolov8 | yolov8.json | none | — | external | — |
| YOLOv11-Detection | yolov11_det_float.tflite | yolov11_det_w8a8.tflite | yolov8 | yolov8.json | none | gpu | external | — |
| Yolo-v7 | yolov7_float.tflite | yolov7_w8a8.tflite | yolov8 | yolov8.json | none | gpu | external | — |
| YOLOv8-Detection | yolov8_det_float.tflite | yolov8_det_w8a8.tflite | yolov8 | yolov8.json | none | gpu | external | — |
| YOLOv8-Detection (SNPE) | yolov8_det_quantized.dlc | — | yolov8 | yolov8.json | none | — | — | SNPE runtime (`qtimlsnpe`): this DLC bakes NMS in and exports 3 separate output tensors — requires `tensors="<boxes,scores,class_idx>"` (confirmed on-device); without it `qtimlsnpe` forwards only the first tensor and `qtimlpostprocess module=yolov8` fails caps negotiation |
| YOLOv10-Detection | yolov10_detection_float.tflite | yolov10_detection_w8a8.tflite | yolov8 | yolov8.json | none | gpu | external | — |
| DETR-ResNet101 | detr_resnet101_float.tflite | — | yolov8 | yolov8.json | json | external | — | float32 uses external delegate |
| DETR-ResNet50 | detr_resnet50_float.tflite | — | yolov8 | yolov8.json | none | external | — | float32 uses external delegate |
| Conditional-DETR-ResNet50 | conditional_detr_resnet50_float.tflite | — | yolov8 | coco_labels.json | json | external | — | float32 uses external delegate |
| RF-DETR | rf_detr_float.tflite | — | yolov8 | coco_labels.json | json | external | — | float32 uses external delegate |
| RTMDet | rtmdet_float.tflite | — | yolov8 | yolov8.json | json | external | — | float32 uses external delegate |
| Yolo-R | yolor_float.tflite | — | yolov8 | yolov8.json | none | gpu | — | — |

## Image Classification

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| ResNeXt101 | resnext101_float.tflite | resnext101_w8a8.tflite | mobilenet-softmax | mobilenet.json | json | gpu | external | — |
| VIT | — | vit_w8a8.tflite | mobilenet-softmax | mobilenet.json | json | — | external | — |
| EfficientViT-b2-cls | efficientvit_b2_cls_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| EfficientViT-l2-cls | efficientvit_l2_cls_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| EfficientNet-B0 | efficientnet_b0_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| ConvNext-Base | convnext_base_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| ConvNext-Tiny | convnext_tiny_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| EfficientNet-V2-s | efficientnet_v2_s_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| Beit | beit_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| MNASNet05 | mnasnet05_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| DenseNet-121 | densenet_121_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| GoogLeNet | googlenet_float.tflite | googlenet_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| Resnet101 | resnet101_float.tflite | resnet101_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| Resnet50 | resnet50_float.tflite | resnet50_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| Inception-v3 | inception_v3_float.tflite | inception_v3_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| Inception-v3 (SNPE) | inception_v3.dlc | — | mobilenet | classification.txt | none | — | — | SNPE runtime (`qtimlsnpe`): labels file is `classification.txt` (plain text, 1000 ImageNet classes) — **not** `mobilenet.json`. No `tensors=` needed; single native output tensor. Use `delegate=dsp`. When used as an ROI classifier, set `qtimlvconverter mode=roi-batch-cumulative`. |
| Inception-v3 (ONNX) | inception_v3.onnx | — | mobilenet | classification.txt | none | — | — | ONNX Runtime (`qtimlonnx`): labels file is `classification.txt` (plain text, 1000 ImageNet classes) — **not** `mobilenet.json`. Use `execution-provider=cpu` (built into the ONNX Runtime core, portable; the QNN/HTP provider is a separate device install) and `optimization-level=disable-all` (keeps `Transpose` nodes intact for `qtimlonnx` NCHW/NHWC layout/caps negotiation). `qtimlonnx` has no `tensors=` property, so the native ONNX output order must satisfy the `module=mobilenet` contract directly. CPU inference runs below realtime, so a file-to-file run must reach natural EOS for `mp4mux` to finalize a playable file. |
| MobileNet-v2 | mobilenet_v2_float.tflite | mobilenet_v2_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| MobileNet-v3-Large | mobilenet_v3_large_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| RegNet | regnet_float.tflite | regnet_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| ResNet18 | resnet18_float.tflite | resnet18_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| ResNeXt50 | resnext50_float.tflite | resnext50_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| Shufflenet-v2 | shufflenet_v2_float.tflite | shufflenet_v2_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| SqueezeNet-1.1 | squeezenet_1_1_float.tflite | squeezenet_1_1_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| WideResNet50 | wideresnet50_float.tflite | wideresnet50_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| EfficientNet-B4 | efficientnet_b4_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| DLA-102-X | dla_102_x_float.tflite | dla_102_x_w8a8.tflite | float: mobilenet-softmax / w8a8: mobilenet | mobilenet.json | json | gpu | external | — |
| MobileNet-v3-Small | mobilenet_v3_small_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| EfficientFormer | efficientformer_float.tflite | — | mobilenet-softmax | mobilenet.json | json | gpu | — | — |
| GPUNet | gpunet_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| Sequencer2D | sequencer2d_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| LeViT | levit_float.tflite | — | mobilenet | mobilenet.json | json | external | — | float32 uses external delegate |
| Mobile-VIT | mobile_vit_float.tflite | — | mobilenet | mobilenet.json | json | gpu | — | — |
| NASNet | nasnet_float.tflite | — | mobilenet-softmax | mobilenet.json | json | external | — | float32 uses external delegate |

## Semantic Segmentation

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| DeepLabV3-Plus-MobileNet | — | deeplabv3_plus_mobilenet_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| FCN-ResNet50 | — | fcn_resnet50_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| FFNet-122NS-LowRes | — | ffnet_122ns_lowres_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| FFNet-78S-LowRes | — | ffnet_78s_lowres_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| PidNet | — | pidnet_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| DDRNet23-Slim | ddrnet23_slim_float.tflite | — | deeplab-argmax | dv3-argmax.json | none | gpu | — | — |
| FFNet-40S | — | ffnet_40s_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| FFNet-54S | — | ffnet_54s_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| FFNet-78S | — | ffnet_78s_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| Segformer-Base | — | segformer_base_w8a8.tflite | deeplab-argmax | segformer.json | none | — | external | uses segformer.json, not dv3-argmax.json |
| SINet | sinet_float.tflite | — | deeplab-argmax | dv3-argmax.json | none | gpu | — | — |
| PSPNet | — | pspnet_w8a8.tflite | deeplab-argmax | dv3-argmax.json | none | — | external | — |
| DeepLabV3-Plus-MobileNet (SNPE) | deeplabv3_plus_mobilenet.dlc | — | deeplab-argmax | deeplab-labels.txt | none | — | — | SNPE runtime (`qtimlsnpe`): labels file is `deeplab-labels.txt` (plain text, 21 PASCAL VOC classes) — **not** `dv3-argmax.json`. No `tensors=` needed; single native output tensor. Use `delegate=dsp`. Direct-to-composer topology for RGBA mask output — do not route through text-metadata mux. |

## Pose Estimation

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| HRNetPose | — | foot_track_net_w8a8.tflite, hrnet_pose_w8a8.tflite | stage1: qpd / stage2: hrnet | stage1: foot_track_net.json / stage2: hrnet.json | file:foot_track_net_settings.json (stage1), file:hrnet_settings.json (stage2) | — | external | **Top-down.** Full-frame mode assumes the subject fills most of the frame; on a small or distant subject it produces low-confidence, spatially incoherent keypoints that a correctly-configured confidence threshold drops, so the pipeline runs with no errors but draws no overlay. Use the detector-cropped ROI cascade whenever the subject may be small or distant in frame; use full-frame only when the subject is known to dominate the frame. Build the topology the request describes rather than defaulting to one. If building the two-stage cascade, settings and results are mandatory: stage1 results=10, stage2 results=2. |
| HRNetPose (SNPE) | hrnet_pose.dlc | — | hrnet | hrnet.json | file:hrnet_settings.json (mandatory) | — | — | SNPE runtime (`qtimlsnpe`): labels file is `hrnet.json` (mandatory — required by `qtimlpostprocess module=hrnet`). `settings=` mandatory. No `tensors=` needed; single native output tensor. Use `delegate=dsp`. When consuming ROIs from a preceding detector, use `qtimlvconverter mode=roi-batch-cumulative` and `results=2`. |

## Depth Estimation

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| Depth-Anything | depth_anything_float.tflite | — | midas-v2 | monodepth.json | none | gpu | — | — |
| Depth-Anything-V2 | depth_anything_v2_float.tflite | — | midas-v2 | monodepth.json | none | gpu | — | — |

## Super Resolution

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| QuickSRNetLarge | quicksrnetlarge_float.tflite | quicksrnetlarge_w8a8.tflite | srnet | — | none | gpu | external | — |
| QuickSRNetMedium | quicksrnetmedium_float.tflite | quicksrnetmedium_w8a8.tflite | srnet | — | none | gpu | external | — |
| QuickSRNetSmall | quicksrnetsmall_float.tflite | quicksrnetsmall_w8a8.tflite | srnet | — | none | gpu | external | — |
| XLSR | xlsr_float.tflite | xlsr_w8a8.tflite | srnet | — | none | gpu | external | — |
| SESR-M5 | sesr_m5_float.tflite | sesr_m5_w8a8.tflite | srnet | — | none | gpu | external | — |
| Real-ESRGAN-General-x4v3 | real_esrgan_general_x4v3_float.tflite | real_esrgan_general_x4v3_w8a8.tflite | srnet | — | none | gpu | external | — |
| Real-ESRGAN-x4plus | real_esrgan_x4plus_float.tflite | real_esrgan_x4plus_w8a8.tflite | srnet | — | none | gpu | external | — |
| ESRGAN | esrgan_float.tflite | — | srnet | — | none | gpu | — | — |

## Audio Classification

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| YAMNET | yamnet_float.tflite | — | yamnet | yamnet.json | json | none | — | audio pipeline; uses qtimlaconverter with LMFE params instead of qtimlvconverter |

## Face Detection / Recognition

Three distinct models cover the face-detection/recognition daisy chain. All three are w8a8-only on device (no float TFLite variant shipped) and use the `external` (HTP) delegate on `qtimltflite`. Each also ships an SNPE `.dlc` build that runs on `qtimlsnpe delegate=dsp` instead — see the SNPE-tagged rows below for the per-model `tensors=` behavior, which is NOT uniform across the three stages. `results=6` is used for all three stages in the documented face-detection / face-recognition chain.

Face recognition requests require the full 3-stage daisy-chain whenever the request asks for face recognition/identification, or names any one of the three models below — the other two are implied even if unnamed, since these models only function as a chain. Plain face detection (only `face_det_lite`, no recognition language) is a single-stage flow. File names in the table below (`face_detection.json`, `face_recognition.json`, etc.) are conventional names, not required strings — match whatever filenames the user provides to the stage their content/pairing indicates.

**Verify the module assignment against the actual deployed model, not just its reference/display name.** A model export can ship variants with different output heads under the same display name or filename pattern (a different tensor count/shape than the one this table assumes). Before committing a `qtimlpostprocess module=` choice for Stage 3 (or any model row below), confirm the on-device model's real output tensor count/shape — via `gst-inspect-1.0 qtimlpostprocess`'s documented per-module tensor contract, exported model metadata, or direct tensor inspection — actually matches that module's contract. If it does not (its real output tensor count/shape differs from what the module expects), the model is a different output type than this table assumes and needs custom output decoding instead of a built-in `qtimlpostprocess` module, regardless of how closely its name matches a cataloged row.

| Model | Float32 filename | W8A8 filename | Module | Labels file | Settings | Delegate (float) | Delegate (w8a8) | Notes |
|---|---|---|---|---|---|---|---|---|
| Lightweight-Face-Detection (face_det_lite) | — | face_det_lite_quantized.tflite / face_det_lite-w8a8.tflite | qfd | face_detection.json | json (inline `{"confidence": <N>}`) | — | external | Stage 1 of face recognition chain; also used for standalone face detection. Output feeds a face-ROI bounding box downstream. |
| Lightweight-Face-Detection (face_det_lite, SNPE) | face_det_lite.dlc | — | qfd | face_detection.json | json (inline `{"confidence": <N>}`) | dsp | — | SNPE runtime (`qtimlsnpe`): this DLC exports 3 separate named output tensors (`heatmap`/NCHW 1ch, `bbox`/NCHW 4ch, `landmark`/NCHW 10ch, per the DLC's embedded `output_layout` metadata and `gst-inspect-1.0 qtimlpostprocess` module=qfd's documented 1/4/10-channel tensor contract) — requires a `tensors=` list of these 3 names in this order; without it `qtimlsnpe` forwards only the first tensor and `qtimlpostprocess module=qfd` fails caps negotiation. The array-property form follows the same syntax as the verified YOLOv8-SNPE row above. Used both for standalone face detection and as Stage 1 when the full recognition chain runs on SNPE — see the chain-wide SNPE note below. |
| Facial-Landmark-Detection (facemap_3dmm, SNPE) | facemap_3dmm.dlc | — | lite-3dmm | — | file:facemap_3dmm_settings.json (mandatory) | dsp | — | SNPE runtime (`qtimlsnpe`): this DLC exports its landmark/pose result as a single native output tensor, so unlike the Stage 1 SNPE row above, **no `tensors=` property is set or needed** — `qtimlsnpe` forwards the one tensor by default and `qtimlpostprocess module=lite-3dmm` negotiates caps without an explicit filter. Stage 2 of face recognition chain (3DMM face landmark/pose on the Stage 1 ROI). No labels file — landmark output only. **DEVICE PREREQUISITE:** requires companion binary data files `blendShape.bin`, `meanFace.bin`, `shapeBasis.bin` under `/etc/data/` (paths hard-coded in `facemap_3dmm_settings.json`). If absent, stage 2 fails `Failed to open /etc/data/meanFace.bin` → `Failed to load meanface` and the pipeline exits. Providing these device data files is the user's responsibility — note this device prerequisite in the artifact README. A module's `settings=<path>` property can reference further file paths inside its own JSON content that are invisible from the generating application's source; read the settings file's actual contents, not just the app code that passes its path, to find the true device-file prerequisites. |
| Facial-Attribute-Detection (face_attrib_net) | — | face_attrib_net_quantized.tflite / Facial-Attribute-Detection_w8a8.tflite | qfr (verify first — see note above) | face_recognition.json | file:face_recognition_settings.json (mandatory) | — | external | Commonly ships as Stage 3 of the face recognition chain (face embedding/recognition on the Stage 2 output), using `qfr`/`qfr-softmax`. However, builds sharing this reference name/filename pattern have also been observed exporting a different output contract (a different tensor count/shape) rather than the multi-tensor embedding — one that `qfr`/`qfr-softmax` cannot decode no matter what `labels`/`settings` are supplied. Confirm the deployed model's real output tensor shape before assuming `qfr` applies; if it does not, treat the model as a different output type and use custom output decoding instead. |
| Facial-Attribute-Detection (face_attrib_net, SNPE) | Facial-Attribute-Detection_w8a8.dlc | — | qfr | face_recognition.json | file:face_recognition_settings.json (mandatory) | dsp | — | SNPE runtime (`qtimlsnpe`): the deployed multi-head recognition DLC exports SIX output heads — `id_feature`[1,512], `liveness_feature`[1,32], `eye_closeness`[1,2], `glasses`[1,2], `mask`[1,2], `sunglasses`[1,2] — exactly `qfr`'s 6-FLOAT32-tensor contract. But SNPE's DEFAULT output set is only the last leaf (`sunglasses`[1,2]), so this stage **requires** a `tensors=` list naming all six in that order (`{"id_feature", "liveness_feature", "eye_closeness", "glasses", "mask", "sunglasses"}`) to present them on the src pad — unlike the Stage 2 SNPE row, which needs no filter. Without the list only one tensor is offered against `qfr`'s six and caps negotiation fails. Keep `labels=face_recognition.json`, `settings=face_recognition_settings.json`, `results=6`. **Secondary caution:** a *different* build under the same display name/filename pattern may instead export a different output contract (a different tensor count/shape) that no `qfr`/`qfr-softmax` can decode; if the deployed `.dlc` is that different shape, use the `MLPostprocess`/`set_handler(...)` custom postprocess pattern (`ml-and-postprocess.md`) in place of `qtimlpostprocess module=qfr`. Confirm which output shape the deployed model has before wiring. |
| Lightweight-Face-Detection (face_det_lite, QNN cached binary) | — | face_det_lite_w8a8.bin | qfd | face_detection.json | json (inline `{"confidence": <N>}`) | — | — | QNN runtime (`qtimlqnn`): native cached-context `.bin` on the HTP/NPU backend — set `backend=/usr/lib/libQnnHtp.so` and `system=/usr/lib/libQnnSystem.so` (no `delegate` property; `qtimlqnn` selects the NPU via `backend`/`system`). Exports the same 3 separate named output tensors as the SNPE row above (`heatmap`/1ch, `bbox`/4ch, `landmark`/10ch), so requires a `tensors=` list of these 3 names in this order (`{"heatmap", "bbox", "landmark"}`); without it `qtimlqnn`'s default output set does not present all three and `qtimlpostprocess module=qfd` fails caps negotiation. `qtimlqnn` is available only in the `qcom-multimedia-proprietary-image` build. See the chain-wide QNN note below. |
| Facial-Landmark-Detection (facemap_3dmm, QNN cached binary) | — | facemap_3dmm_w8a8.bin | lite-3dmm | — | file:facemap_3dmm_settings.json (mandatory) | — | — | QNN runtime (`qtimlqnn`): native cached-context `.bin` (`backend=/usr/lib/libQnnHtp.so`, `system=/usr/lib/libQnnSystem.so`). Exports its landmark/pose result as a single native output tensor, so like the SNPE Stage 2 row **no `tensors=` property is set or needed**. Same `/etc/data/*.bin` device prerequisite as the SNPE row (`blendShape.bin`, `meanFace.bin`, `shapeBasis.bin`, hard-coded inside `facemap_3dmm_settings.json`, independent of runtime). |
| Facial-Attribute-Detection (face_attrib_net, QNN cached binary) | — | Facial-Attribute-Detection_w8a8.bin | qfr | face_recognition.json | file:face_recognition_settings.json (mandatory) | — | — | QNN runtime (`qtimlqnn`): the deployed multi-head recognition `.bin` exports the SAME six output heads as the SNPE row — `id_feature`[1,512], `liveness_feature`[1,32], `eye_closeness`[1,2], `glasses`[1,2], `mask`[1,2], `sunglasses`[1,2] — exactly `qfr`'s 6-FLOAT32-tensor contract. But QNN's DEFAULT output set presents only the last leaf (`sunglasses`[1,2]), so this stage **requires** a `tensors=` list naming all six in that order (`{"id_feature", "liveness_feature", "eye_closeness", "glasses", "mask", "sunglasses"}`) — identical fix to the SNPE row, just on `qtimlqnn`. Set `backend=/usr/lib/libQnnHtp.so` and `system=/usr/lib/libQnnSystem.so` (no `delegate`). Keep `labels=face_recognition.json`, `settings=face_recognition_settings.json`, `results=6`. **Secondary caution:** as with the SNPE row, a *different* build under the same name/filename pattern may export a different output contract that `qfr` cannot decode; confirm the deployed `.bin`'s output shape before wiring, and if it differs use the `MLPostprocess`/`set_handler(...)` custom postprocess pattern (`ml-and-postprocess.md`). |

**Chain-wide SNPE variant:** all three stages can run uniformly on `qtimlsnpe delegate=dsp` instead of the `qtimltflite delegate=external` default shown in Pattern A4 — swap each stage's `qtimltflite ... delegate=external ...` block for `qtimlsnpe model=<dlc> delegate=dsp`, keep every other property (`module=`, `labels=`, `settings=`, `results=6`, `qtimlvconverter mode=`) and the topology unchanged. For this chain, **Stage 1 and Stage 3 each require a `tensors=` filter** (Stage 1 for its 3 split outputs, Stage 3 for the 6 multi-head outputs — see each row above), while **Stage 2 (`lite-3dmm`) needs none**. The `tensors=` requirement is per-model, not per-runtime — do not assume all three SNPE `.dlc` stages need (or don't need) a `tensors=` filter just because one does; check each model's own row.

**Chain-wide QNN cached-binary variant:** the same three stages also run uniformly on `qtimlqnn` with native cached-context `.bin` models (the `qtimlqnn`/`.bin` sibling of the SNPE chain) — swap each stage's inference block for `qtimlqnn model=<bin> backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so`, keeping every other property (`module=`, `labels=`, `settings=`, `results=6`, `qtimlvconverter mode=`) and the topology unchanged. `qtimlqnn` has **no `delegate` property** — it selects the HTP/NPU via `backend`/`system`, unlike `qtimlsnpe delegate=dsp`. The same per-model `tensors=` rule applies: **Stage 1 and Stage 3 each require a `tensors=` filter** (same names/order as the SNPE rows), while **Stage 2 (`lite-3dmm`) needs none**. `qtimlqnn` is available only in the `qcom-multimedia-proprietary-image` build.

**Topology:** use the C++ AI/daisy-chain references for the full 3-stage pipeline shape. Do not duplicate topology rules here — this table resolves only the per-model module/delegate facts.
