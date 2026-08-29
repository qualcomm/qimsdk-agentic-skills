Create a QIM SDK Python app for pose estimation with a custom Python postprocess placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run pose inference using TFLite external delegate on HTP/NPU
- Generate a type-correct custom pose postprocess placeholder
- Overlay pose output and render to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/pose_input.mp4
- Model:   /root/Downloads/qimsdk_samples/models/custom_pose_w8a8.tflite
- Labels:  /root/Downloads/qimsdk_samples/labels/custom_pose_labels.json
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen, sync enabled

## Custom Postprocess Requirement
- Callback output type: `Poses`
- Include TODO comments for tensor read, keypoint layout, score filtering, coordinate scaling, and output population
- Do not invent keypoint layout or model-specific decode math
