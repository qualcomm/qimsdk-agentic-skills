#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 --gst-debug=2 \
  qtimlvconverter name=pre_0 \
  qtimltflite name=infer_0 model=~/models/yolov8_det_quantized.tflite delegate=external \
    external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  qtimlpostprocess name=post_0 module=yolov8 labels=~/labels/yolov8.json \
  qtimetamux name=mux_0 \
  qtivoverlay name=ovl_0 \
  qtimlvconverter name=pre_1 \
  qtimltflite name=infer_1 model=~/models/yolov8_det_quantized.tflite delegate=external \
    external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  qtimlpostprocess name=post_1 module=yolov8 labels=~/labels/yolov8.json \
  qtimetamux name=mux_1 \
  qtivoverlay name=ovl_1 \
  qtimlvconverter name=pre_2 \
  qtimltflite name=infer_2 model=~/models/yolov8_det_quantized.tflite delegate=external \
    external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  qtimlpostprocess name=post_2 module=yolov8 labels=~/labels/yolov8.json \
  qtimetamux name=mux_2 \
  qtivoverlay name=ovl_2 \
  qtimlvconverter name=pre_3 \
  qtimltflite name=infer_3 model=~/models/yolov8_det_quantized.tflite delegate=external \
    external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  qtimlpostprocess name=post_3 module=yolov8 labels=~/labels/yolov8.json \
  qtimetamux name=mux_3 \
  qtivoverlay name=ovl_3 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<960, 540>" \
    sink_1::position="<960, 0>" sink_1::dimensions="<960, 540>" \
    sink_2::position="<0, 540>" sink_2::dimensions="<960, 540>" \
    sink_3::position="<960, 540>" sink_3::dimensions="<960, 540>" ! \
  queue ! waylandsink fullscreen=true sync=true \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_0 \
  t_0. ! queue ! mux_0. \
  t_0. ! queue ! pre_0. pre_0. ! queue ! infer_0. infer_0. ! queue ! \
    post_0. post_0. ! text/x-raw ! queue ! mux_0. \
  mux_0. ! queue ! ovl_0. ovl_0. ! queue ! comp.sink_0 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_1 \
  t_1. ! queue ! mux_1. \
  t_1. ! queue ! pre_1. pre_1. ! queue ! infer_1. infer_1. ! queue ! \
    post_1. post_1. ! text/x-raw ! queue ! mux_1. \
  mux_1. ! queue ! ovl_1. ovl_1. ! queue ! comp.sink_1 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_2 \
  t_2. ! queue ! mux_2. \
  t_2. ! queue ! pre_2. pre_2. ! queue ! infer_2. infer_2. ! queue ! \
    post_2. post_2. ! text/x-raw ! queue ! mux_2. \
  mux_2. ! queue ! ovl_2. ovl_2. ! queue ! comp.sink_2 \
  filesrc location=/etc/mahendra/video.mp4 ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_3 \
  t_3. ! queue ! mux_3. \
  t_3. ! queue ! pre_3. pre_3. ! queue ! infer_3. infer_3. ! queue ! \
    post_3. post_3. ! text/x-raw ! queue ! mux_3. \
  mux_3. ! queue ! ovl_3. ovl_3. ! queue ! comp.sink_3
