Create a QIM SDK C app for event-triggered recording from an MP4 file — run object detection and start recording to file only when a person is detected.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run YOLOX object detection on HTP/NPU
- Display detection overlay on Wayland
- When a person is detected in the metadata, start recording to an MP4 file
- Stop recording after 150 consecutive frames with no person detected

## Configuration
- Input:      $HOME/media/Draw_1080p_180s_30FPS.mp4
- Model:      $HOME/models/yolox_w8a8.tflite
- Labels:     $HOME/labels/yolov8.json
- Backend:    TFLite external delegate, HTP/NPU
- Confidence: 51.0
- Output:     display + conditional recording to /tmp/event-output.mp4
