#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qtivcomposer name=comp \
    sink_0::position="<0, 270>" sink_0::dimensions="<960, 540>" \
    sink_1::position="<960, 270>" sink_1::dimensions="<960, 540>" ! \
  video/x-raw,format=NV12 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=5 ! h264parse ! mp4mux ! \
  filesink location=/root/media/output/two_stream_obj_detect_out.mp4 \
  qtiqmmfsrc camera=0 name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
  tee name=t_src \
  t_src. ! queue ! comp.sink_0 \
  t_src. ! queue ! tee name=t \
  t. ! queue ! qtivtransform ! video/x-raw,format=NV12 ! obj_mux. \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimltflite model=/root/models/yolox_w8a8.tflite delegate=external \
    external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
  qtimlpostprocess module=yolov8 labels=/root/labels/yolov8.json \
    settings="{\"confidence\": 51.0}" bbox-stabilization=true ! \
  text/x-raw ! queue ! qtimetamux name=obj_mux \
  obj_mux. ! queue ! qtivoverlay ! queue ! comp.sink_1
