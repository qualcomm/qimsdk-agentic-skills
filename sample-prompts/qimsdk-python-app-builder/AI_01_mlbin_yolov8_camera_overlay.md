Create a QIM SDK Python app for camera YOLOv8 object detection using the fused TFLite ML-bin path.

## Pipeline Behavior
- Capture from the default Qualcomm camera
- Use fused inference and postprocess
- Configure the inference and postprocess settings for the fused ML stage
- Overlay detections and render to display

## Configuration
- Camera: camera=0
- Inference model: /root/Downloads/qimsdk_samples/models/yolov8_det_w8a8.tflite
- Inference delegate: external
- External delegate path: libQnnTFLiteDelegate.so
- External delegate options: QNNExternalDelegate,backend_type=htp,log_level=(string)1
- Postprocess module: yolov8
- Postprocess labels: /root/Downloads/qimsdk_samples/labels/yolov8.json
- Output: Wayland display, fullscreen, sync enabled
