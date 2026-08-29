#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# 12-stream YOLOv8 object detection (single mp4 fanned to 12 decoders) on TFLite HTP/NPU,
# batched 4x3 groups, composed into a 4x3 grid on the wayland display.
# Batch pattern: 12 streams -> 3 qtibatch groups x 4 streams; one shared qtimltflite per group.
# ~10s HTP graph-prepare per group (3 groups -> ~30s) before frames appear; do not cap the run short.

ulimit -n 10000 && gst-launch-1.0 -e --gst-debug=2 \
  qtimltflite name=infer0 model=/etc/mahendra/yolov8_det_quantized_batch_4.tflite \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)0,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimltflite name=infer1 model=/etc/mahendra/yolov8_det_quantized_batch_4.tflite \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)1,htp_performance_mode=(string)2,log_level=(string)1;" \
  qtimltflite name=infer2 model=/etc/mahendra/yolov8_det_quantized_batch_4.tflite \
    delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_device_id=(string)0,htp_performance_mode=(string)2,log_level=(string)1;" \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t0 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t1 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t2 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t3 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t4 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t5 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t6 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t7 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t8 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t9 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t10 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! queue ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t11 \
  t0.  ! video/x-raw,format=NV12 ! mixer. \
  t0.  ! video/x-raw,format=NV12 ! batch0. \
  t1.  ! video/x-raw,format=NV12 ! mixer. \
  t1.  ! video/x-raw,format=NV12 ! batch0. \
  t2.  ! video/x-raw,format=NV12 ! mixer. \
  t2.  ! video/x-raw,format=NV12 ! batch0. \
  t3.  ! video/x-raw,format=NV12 ! mixer. \
  t3.  ! video/x-raw,format=NV12 ! batch0. \
  t4.  ! video/x-raw,format=NV12 ! mixer. \
  t4.  ! video/x-raw,format=NV12 ! batch1. \
  t5.  ! video/x-raw,format=NV12 ! mixer. \
  t5.  ! video/x-raw,format=NV12 ! batch1. \
  t6.  ! video/x-raw,format=NV12 ! mixer. \
  t6.  ! video/x-raw,format=NV12 ! batch1. \
  t7.  ! video/x-raw,format=NV12 ! mixer. \
  t7.  ! video/x-raw,format=NV12 ! batch1. \
  t8.  ! video/x-raw,format=NV12 ! mixer. \
  t8.  ! video/x-raw,format=NV12 ! batch2. \
  t9.  ! video/x-raw,format=NV12 ! mixer. \
  t9.  ! video/x-raw,format=NV12 ! batch2. \
  t10. ! video/x-raw,format=NV12 ! mixer. \
  t10. ! video/x-raw,format=NV12 ! batch2. \
  t11. ! video/x-raw,format=NV12 ! mixer. \
  t11. ! video/x-raw,format=NV12 ! batch2. \
  qtibatch name=batch0 ! queue ! qtimlvconverter ! queue ! infer0. \
  infer0. ! queue ! qtimldemux name=demux0 \
  qtibatch name=batch1 ! queue ! qtimlvconverter ! queue ! infer1. \
  infer1. ! queue ! qtimldemux name=demux1 \
  qtibatch name=batch2 ! queue ! qtimlvconverter ! queue ! infer2. \
  infer2. ! queue ! qtimldemux name=demux2 \
  demux0. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux0. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux1. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  demux2. ! queue ! qtimlpostprocess results=10 module=yolov8 labels=/etc/mahendra/yolov8.json settings="{\"confidence\": 51.0}" ! video/x-raw,width=480,height=270 ! queue ! mixer. \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"      sink_0::dimensions="<480,270>" \
    sink_1::position="<480,0>"    sink_1::dimensions="<480,270>" \
    sink_2::position="<960,0>"    sink_2::dimensions="<480,270>" \
    sink_3::position="<1440,0>"   sink_3::dimensions="<480,270>" \
    sink_4::position="<0,270>"    sink_4::dimensions="<480,270>" \
    sink_5::position="<480,270>"  sink_5::dimensions="<480,270>" \
    sink_6::position="<960,270>"  sink_6::dimensions="<480,270>" \
    sink_7::position="<1440,270>" sink_7::dimensions="<480,270>" \
    sink_8::position="<0,540>"    sink_8::dimensions="<480,270>" \
    sink_9::position="<480,540>"  sink_9::dimensions="<480,270>" \
    sink_10::position="<960,540>" sink_10::dimensions="<480,270>" \
    sink_11::position="<1440,540>" sink_11::dimensions="<480,270>" \
    sink_12::position="<0,0>"      sink_12::dimensions="<480,270>" \
    sink_13::position="<480,0>"    sink_13::dimensions="<480,270>" \
    sink_14::position="<960,0>"    sink_14::dimensions="<480,270>" \
    sink_15::position="<1440,0>"   sink_15::dimensions="<480,270>" \
    sink_16::position="<0,270>"    sink_16::dimensions="<480,270>" \
    sink_17::position="<480,270>"  sink_17::dimensions="<480,270>" \
    sink_18::position="<960,270>"  sink_18::dimensions="<480,270>" \
    sink_19::position="<1440,270>" sink_19::dimensions="<480,270>" \
    sink_20::position="<0,540>"    sink_20::dimensions="<480,270>" \
    sink_21::position="<480,540>"  sink_21::dimensions="<480,270>" \
    sink_22::position="<960,540>"  sink_22::dimensions="<480,270>" \
    sink_23::position="<1440,540>" sink_23::dimensions="<480,270>" ! \
  video/x-raw,format=NV12 ! queue ! waylandsink sync=false fullscreen=true
