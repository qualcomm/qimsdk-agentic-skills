Create a QIM SDK C++ app for object detection with a TFLite ML-bin custom preprocess placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Keep built-in YOLOv8 postprocess inside the ML-bin
- Overlay detections and render to display

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Inference model: /root/Downloads/qimsdk_samples/models/yolov8_det_quantized.tflite
- Inference delegate: external
- External delegate path: libQnnTFLiteDelegate.so
- External delegate options: QNNExternalDelegate,backend_type=htp,log_level=(string)1
- Postprocess module: yolov8
- Postprocess labels: /root/Downloads/qimsdk_samples/labels/yolov8.json
- Output: Wayland display, fullscreen, sync enabled

## Custom Preprocess Requirement
- Include TODO comments for source frame access, tensor shape, resize/letterbox, channel order, normalization, quantization, and tensor write
- Return failure in the placeholder unless real tensor write logic is implemented
- Add a comment explaining that after writing valid output tensors, the callback must report success so output is emitted downstream
- Keep custom preprocessing separate from custom postprocessing
