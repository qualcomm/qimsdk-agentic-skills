Create a QIM SDK C++ app for an AI wall pipeline running four parallel AI inference streams composed into a 2x2 grid display.

## Pipeline Behavior
- Decode four independent instances of the same MP4 file
- Run a different AI model on each stream in parallel:
  - Stream 1: Image classification
  - Stream 2: Face detection
  - Stream 3: Semantic segmentation
  - Stream 4: Object detection
- Overlay each stream's AI results on its respective video
- Compose all four streams into a 2x2 grid and render to display

## Configuration
- Input:       /root/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4 (used for all four streams)
- Backend:     TFLite external delegate, HTP/NPU
- Composition: 2x2 grid, 960x540 each

Stream 1 — Classification:
- Model:      /root/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite
- Labels:     /root/Downloads/qimsdk_samples/labels/mobilenet.json
- Confidence: 51.0
- Results:    5

Stream 2 — Face Detection:
- Model:      /root/Downloads/qimsdk_samples/models/face_det_lite_w8a8.tflite
- Labels:     /root/Downloads/qimsdk_samples/labels/face_det_lite.json
- Results:    6

Stream 3 — Segmentation:
- Model:      /root/Downloads/qimsdk_samples/models/deeplabv3_plus_mobilenet_w8a8.tflite
- Labels:     /root/Downloads/qimsdk_samples/labels/dv3-argmax.json
- Alpha blend: 0.5

Stream 4 — Object Detection:
- Model:      /root/Downloads/qimsdk_samples/models/yolox_w8a8.tflite
- Labels:     /root/Downloads/qimsdk_samples/labels/yolov8.json
- Confidence: 51.0
