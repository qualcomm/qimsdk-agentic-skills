Create a QIM SDK Python app that loads an existing YOLOv8 object detection pipeline from YAML.

## Pipeline Behavior
- Load pipeline construction from an externally provided YAML file
- Do not generate or modify the YAML config file
- Run YOLOv8 object detection using the topology and properties already defined in the YAML
- Overlay detections and render to display

## Configuration
- Existing YAML path: /root/configs/yolov8_camera_overlay.yaml
- Output: Wayland display, fullscreen, sync enabled

## Artifact Expectation
- Generate `main.py`
- Generate `README.md`
- Do not generate a YAML file
- README assumptions must state exactly: `External YAML provided by user`
