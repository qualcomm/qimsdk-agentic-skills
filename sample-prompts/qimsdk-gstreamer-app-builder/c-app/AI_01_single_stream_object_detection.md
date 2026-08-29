Create a QIM SDK C app for single-stream object detection from an MP4 file, encoding the result to a file.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run YOLOX object detection on full frames using NPU
- Overlay bounding boxes and class labels on each frame
- Encode the overlaid video and save to an output MP4 file

## Configuration
- Input:      $HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Model:      $HOME/Downloads/qimsdk_samples/models/yolox_w8a8.tflite
- Labels:     $HOME/Downloads/qimsdk_samples/labels/yolov8.json
- Backend:    TFLite external delegate, HTP/NPU
- Confidence: 51.0
- Output:     encode to file at $HOME/Downloads/qimsdk_samples/media/output/obj_detect_out.mp4
