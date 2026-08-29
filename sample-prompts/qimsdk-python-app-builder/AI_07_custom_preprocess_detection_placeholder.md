Create a QIM SDK Python app for object detection with a custom Python preprocess placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run TFLite inference using external delegate on HTP/NPU
- Use built-in YOLOv8 postprocess
- Merge metadata with the original video stream, overlay detections, and render to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Model:   /root/Downloads/qimsdk_samples/models/yolov8_det_quantized.tflite
- Labels:  /root/Downloads/qimsdk_samples/labels/yolov8.json
- Backend: TFLite external delegate, HTP/NPU
- Output:  Wayland display, fullscreen, sync enabled

## Custom Preprocess Requirement
- Include TODO comments for reading source frame data, model input tensor shape, resize/letterbox policy, channel order, normalization, quantization, and writing the tensor
- Return failure in the placeholder unless real tensor write logic is implemented
- Add a comment explaining that after writing a valid output tensor, the callback must report success so output is emitted downstream
- Do not invent NV12-to-tensor conversion logic
