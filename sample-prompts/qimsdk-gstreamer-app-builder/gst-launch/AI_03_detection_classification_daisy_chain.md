Create a QIM SDK `gst-launch-1.0` command for a two-stage detection-classification daisy-chain pipeline.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Stage 1: Run YOLOX object detection on full frames using NPU
- Stage 2: Run MobileNet classification on detected object ROIs from Stage 1 using NPU
- Overlay both detection bounding boxes and classification labels and render to display

## Configuration
- Input:   $HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen

Stage 1 — Object Detection:
- Model:      $HOME/Downloads/qimsdk_samples/models/yolox_w8a8.tflite
- Labels:     $HOME/Downloads/qimsdk_samples/labels/yolov8.json
- Confidence: 51.0

Stage 2 — Classification (ROI-based):
- Model:      $HOME/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite
- Labels:     $HOME/Downloads/qimsdk_samples/labels/mobilenet.json
- Confidence: 51.0
