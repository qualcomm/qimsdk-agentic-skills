Create a QIM SDK `gst-launch-1.0` command for a 2-stream face detection pipeline.

## Pipeline Behavior
- Decode 2 MP4 file streams using Qualcomm hardware decoder
- Run Face Detection Lite inference on full frames of each stream using NPU
- Overlay bounding boxes on detected faces per stream
- Composite all streams into a single output view and render to display

## Configuration
- Input:   2 instances of /etc/mahendra/gesture_sample.mp4
- Model:   /etc/mahendra/face_det_lite_w8a8.tflite
- Labels:  /etc/mahendra/face_detection.json
- Backend: QNN engine, DSP
- Confidence: 51.0
- Composition: side-by-side on Wayland display
