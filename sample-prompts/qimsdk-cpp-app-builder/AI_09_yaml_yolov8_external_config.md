Create a QIM SDK C++ app that loads an existing YOLOv8 object detection pipeline from YAML.

## Pipeline Behavior
- Read an externally provided YAML file in C++
- Do not generate or modify the YAML config file
- Run YOLOv8 object detection using the topology and properties already defined in the YAML
- Overlay detections and render to display

## Configuration
- Existing YAML path: /root/configs/yolov8_camera_overlay.yaml
- Output: Wayland display, fullscreen, sync enabled

## Artifact Expectation
- Generate `main.cc`
- Generate `CMakeLists.txt`
- Generate `README.md`
- Do not generate a YAML file
- README assumptions must state exactly: `External YAML provided by user`
