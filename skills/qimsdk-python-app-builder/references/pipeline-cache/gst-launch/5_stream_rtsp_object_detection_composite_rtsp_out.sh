#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

#
# 5-stream H.265 RTSP input → YOLOv8 object detection → 3×2 grid composite → RTSP re-stream
#
# Each stream: rtspsrc (H.265) → v4l2h265dec → tee
#   raw branch  → qtivcomposer (background tile)
#   AI  branch  → qtimlvconverter → qtimltflite → qtimlpostprocess → qtivcomposer (unpinned video/x-raw overlay)
#
# Composer: 10 sink pads (2 per stream), 3×2 grid at 1920×720, 640×360 cells
# Output:   v4l2h264enc → h264parse → qtirtspbin (port 8900, /live)

gst-launch-1.0 -e --gst-debug=2 \
qtimlvconverter name=s1_pre \
qtimltflite name=s1_infer model=$HOME/models/yolov8_det_quantized.tflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=s1_post module=yolov8 labels=$HOME/labels/yolov8.json bbox-stabilization=true \
qtimlvconverter name=s2_pre \
qtimltflite name=s2_infer model=$HOME/models/yolov8_det_quantized.tflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=s2_post module=yolov8 labels=$HOME/labels/yolov8.json bbox-stabilization=true \
qtimlvconverter name=s3_pre \
qtimltflite name=s3_infer model=$HOME/models/yolov8_det_quantized.tflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=s3_post module=yolov8 labels=$HOME/labels/yolov8.json bbox-stabilization=true \
qtimlvconverter name=s4_pre \
qtimltflite name=s4_infer model=$HOME/models/yolov8_det_quantized.tflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=s4_post module=yolov8 labels=$HOME/labels/yolov8.json bbox-stabilization=true \
qtimlvconverter name=s5_pre \
qtimltflite name=s5_infer model=$HOME/models/yolov8_det_quantized.tflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=s5_post module=yolov8 labels=$HOME/labels/yolov8.json bbox-stabilization=true \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<640, 360>" \
  sink_1::position="<0, 0>" sink_1::dimensions="<640, 360>" \
  sink_2::position="<640, 0>" sink_2::dimensions="<640, 360>" \
  sink_3::position="<640, 0>" sink_3::dimensions="<640, 360>" \
  sink_4::position="<1280, 0>" sink_4::dimensions="<640, 360>" \
  sink_5::position="<1280, 0>" sink_5::dimensions="<640, 360>" \
  sink_6::position="<0, 360>" sink_6::dimensions="<640, 360>" \
  sink_7::position="<0, 360>" sink_7::dimensions="<640, 360>" \
  sink_8::position="<640, 360>" sink_8::dimensions="<640, 360>" \
  sink_9::position="<640, 360>" sink_9::dimensions="<640, 360>" ! \
video/x-raw,format=NV12 ! \
v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse config-interval=-1 ! \
qtirtspbin port=8900 mpoint=/live address=0.0.0.0 \
rtspsrc location=rtsp://<RTSP_USER>:<RTSP_PASS>@<CAMERA_IP_1>:554/Streaming/Channels/101 latency=200 ! rtph265depay ! h265parse ! \
v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
tee name=t1 \
t1. ! queue ! comp. \
t1. ! queue ! s1_pre. s1_pre. ! queue ! s1_infer. s1_infer. ! queue ! s1_post. s1_post. ! video/x-raw ! queue ! comp. \
rtspsrc location=rtsp://<RTSP_USER>:<RTSP_PASS>@<CAMERA_IP_2>:554/Streaming/Channels/101 latency=200 ! rtph265depay ! h265parse ! \
v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
tee name=t2 \
t2. ! queue ! comp. \
t2. ! queue ! s2_pre. s2_pre. ! queue ! s2_infer. s2_infer. ! queue ! s2_post. s2_post. ! video/x-raw ! queue ! comp. \
rtspsrc location=rtsp://<RTSP_USER>:<RTSP_PASS>@<CAMERA_IP_3>:554/Streaming/Channels/101 latency=200 ! rtph265depay ! h265parse ! \
v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
tee name=t3 \
t3. ! queue ! comp. \
t3. ! queue ! s3_pre. s3_pre. ! queue ! s3_infer. s3_infer. ! queue ! s3_post. s3_post. ! video/x-raw ! queue ! comp. \
rtspsrc location=rtsp://<RTSP_USER>:<RTSP_PASS>@<CAMERA_IP_4>:554/Streaming/Channels/101 latency=200 ! rtph265depay ! h265parse ! \
v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
tee name=t4 \
t4. ! queue ! comp. \
t4. ! queue ! s4_pre. s4_pre. ! queue ! s4_infer. s4_infer. ! queue ! s4_post. s4_post. ! video/x-raw ! queue ! comp. \
rtspsrc location=rtsp://<RTSP_USER>:<RTSP_PASS>@<CAMERA_IP_5>:554/Streaming/Channels/101 latency=200 ! rtph265depay ! h265parse ! \
v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t5 \
t5. ! queue ! comp. \
t5. ! queue ! s5_pre. s5_pre. ! queue ! s5_infer. s5_infer. ! queue ! s5_post. s5_post. ! video/x-raw ! queue ! comp.
