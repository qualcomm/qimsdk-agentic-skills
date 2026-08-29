Create a QIM SDK gst-launch-1.0 command for single-stream YOLOv8 object detection using SNPE DSP delegate with file input and MP4 file output.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware H.264 decoder
- Run YOLOv8 object detection on full frames using SNPE on the Hexagon DSP
- Overlay bounding boxes and labels on each frame
- Encode the annotated video and write to an MP4 file

## Configuration
- Input:      /home/ubuntu/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Runtime:    SNPE DSP delegate
- Model:      /home/ubuntu/Downloads/qimsdk_samples/models/yolox_w8a8.dlc
- Tensors:    boxes,scores,class_idx
- Labels:     /home/ubuntu/Downloads/qimsdk_samples/labels/yolov8.json
- Confidence: 70.0
- Output:     /home/ubuntu/Downloads/qimsdk_samples/media/output/snpe_dsp_detect_file.mp4
