Create a QIM SDK C++ app and YAML config for a YOLOv8 object detection pipeline.

## Pipeline Behavior
- Generate the YAML config file as part of the artifact
- Read the generated YAML file in C++
- Run YOLOv8 object detection using TFLite external delegate on HTP/NPU
- Overlay detections and render to display

## Configuration
- YAML path: /root/configs/yolov8_camera_overlay.yaml
- Output: Wayland display, fullscreen, sync enabled

## Artifact Expectation
- Generate `main.cc`
- Generate `CMakeLists.txt`
- Generate `README.md`
- Generate the YAML file using the basename `yolov8_camera_overlay.yaml`
- Include run steps that copy the generated YAML to `/root/configs/yolov8_camera_overlay.yaml` before running the binary
