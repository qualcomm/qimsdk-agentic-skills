# 02 - RTSP Input YOLOv8 Object Detection → Video + Metadata over RTSP

Create a QIM SDK `gst-launch-1.0` command for a single-stream YOLOv8 object detection pipeline that reads from an RTSP source, runs inference, and streams both the annotated video and the raw detection metadata over RTSP.

## Pipeline Behavior
- Receive an H.264 RTSP stream from a network camera
- Run YOLOv8 object detection on full frames using NPU
- Stream the overlaid video (with bounding boxes and labels) over RTSP
- Also stream the raw detection metadata (bounding box coordinates, labels, confidence scores) over RTSP so a downstream application can consume the structured results

## Configuration
- Input:      RTSP stream at rtsp://192.168.1.100:554/stream (H.264)
- Model:      $HOME/Downloads/qimsdk_samples/models/yolox_w8a8.tflite
- Labels:     $HOME/Downloads/qimsdk_samples/labels/yolov8.json
- Backend:    TFLite external delegate, HTP/NPU
- Confidence: 51.0
- Output:     RTSP server on port 8900, mount point /live, address 0.0.0.0
              - Video stream: annotated H.264 video
              - Metadata stream: raw detection metadata (bounding boxes, labels, scores)
