Create a QIM SDK Python app for a two-stage PPE daisy-chain pipeline using ML-bin style where appropriate.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Stage 1: detect persons on full frames
- Stage 2: run PPE detection on person ROIs from Stage 1
- Preserve original video with both stage outputs
- Merge metadata before overlay
- Render final overlaid video to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/ppe_video.mp4
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen, sync enabled

Stage 1 - Person Detection:
- Model:    /root/Downloads/qimsdk_samples/models/person_foot_detection_w8a8.tflite
- Labels:   /root/Downloads/qimsdk_samples/labels/foot_track_net.json
- Settings: /root/Downloads/qimsdk_samples/labels/foot_track_net_settings.json
- Module:   qpd

Stage 2 - PPE Detection:
- Model:    /root/Downloads/qimsdk_samples/models/gear_guard_net.tflite
- Labels:   /root/Downloads/qimsdk_samples/labels/gear_guard_net.json
- Settings: inline JSON `{"confidence": 50.0}`
