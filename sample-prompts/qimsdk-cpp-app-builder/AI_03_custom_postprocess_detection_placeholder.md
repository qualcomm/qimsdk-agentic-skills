Create a QIM SDK C++ app for object detection with a custom C++ postprocess placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run TFLite inference using external delegate on HTP/NPU
- Generate a type-correct placeholder callback instead of using the built-in object-detection postprocess module
- Overlay detections and render to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Model:   /root/Downloads/qimsdk_samples/models/custom_detector_w8a8.tflite
- Labels:  /root/Downloads/qimsdk_samples/labels/custom_detector_labels.json
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen, sync enabled

## Custom Postprocess Requirement
- Callback output type: object detections
- Include TODO comments for tensor read, box/class/score decode, thresholding, NMS, label mapping, and output population
- Return `true` for a valid empty placeholder result or `false` only for real error paths
- Do not invent model-specific tensor decode logic
