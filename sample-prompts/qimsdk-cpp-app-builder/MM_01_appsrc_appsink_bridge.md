Create a QIM SDK C++ app with two pipelines bridged through application-managed buffers.

## Pipeline Behavior
- Pipeline 1 captures from the ISP camera
- Pipeline 1 sends 1080p frames to the application
- Application code forwards those frames into Pipeline 2
- Pipeline 2 renders the forwarded frames to Wayland display
## Configuration
- Camera: default Qualcomm camera, camera 0
- Resolution: 1920x1080
- Format: NV12
- Output: Wayland display, fullscreen, sync enabled
