Create a QIM SDK C app for smart codec encoding from an MP4 file — run YOLOv8 object detection and use the results to drive adaptive bitrate encoding, while showing detection overlay on display.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run YOLOv8 object detection on HTP/NPU
- Feed detection metadata to qtismartvencbin to steer adaptive bitrate
- Show bounding box overlay on Wayland display simultaneously

## Configuration
- Input:      $HOME/media/video.mp4
- Model:      $HOME/models/yolov8_det_quantized.tflite
- Labels:     $HOME/labels/yolov8.json
- Backend:    TFLite external delegate, HTP/NPU
- Confidence: 51.0
- Output:     wayland display + smart-codec encode (fakesink drain)
