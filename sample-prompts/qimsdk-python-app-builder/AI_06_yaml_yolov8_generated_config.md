Create a QIM SDK Python app and YAML config for a YOLOv8 object detection pipeline.

## Pipeline Behavior
- Generate the YAML config file as part of the artifact
- Load pipeline construction from the generated YAML file
- Run YOLOv8 object detection using TFLite external delegate on HTP/NPU
- Overlay detections and render to display

## Configuration
- YAML path: /root/configs/yolov8_camera_overlay.yaml
- Output: Wayland display, fullscreen, sync enabled

## Artifact Expectation
- Generate `main.py`
- Generate `README.md`
- Generate the YAML file using the basename `yolov8_camera_overlay.yaml`
- Include run steps that copy the generated YAML to `/root/configs/yolov8_camera_overlay.yaml` before running the app
