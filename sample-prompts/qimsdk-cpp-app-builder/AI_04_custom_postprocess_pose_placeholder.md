Create a QIM SDK C++ app for pose estimation with a custom C++ postprocess placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run pose inference using TFLite external delegate on HTP/NPU
- Overlay pose output and render to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/pose_input.mp4
- Model:   /root/Downloads/qimsdk_samples/models/custom_pose_w8a8.tflite
- Labels:  /root/Downloads/qimsdk_samples/labels/custom_pose_labels.json
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen, sync enabled

## Custom Postprocess Requirement
- Callback output type: poses
- Include TODO comments for tensor read, keypoint layout, score filtering, coordinate scaling, and output population
- Return `true` for a valid empty placeholder result or `false` only for real error paths
- Do not invent keypoint layout or model-specific decode math
