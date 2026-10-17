#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4}"
MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.onnx}"
LABELS_PATH="${LABELS_PATH:-$HOME/Downloads/qimsdk_samples/labels/yolov8.json}"

gst-launch-1.0 -v --gst-debug=2 \
  qtivcomposer name=mixer \
    sink_0::position="<0, 0>" sink_0::dimensions="<640, 360>" \
    sink_1::position="<640, 0>" sink_1::dimensions="<640, 360>" \
    sink_2::position="<0, 360>" sink_2::dimensions="<640, 360>" \
    sink_3::position="<640, 360>" sink_3::dimensions="<640, 360>" \
    sink_4::position="<0, 0>" sink_4::dimensions="<640, 360>" sink_4::alpha=0.5 \
    sink_5::position="<640, 0>" sink_5::dimensions="<640, 360>" sink_5::alpha=0.5 \
    sink_6::position="<0, 360>" sink_6::dimensions="<640, 360>" sink_6::alpha=0.5 \
    sink_7::position="<640, 360>" sink_7::dimensions="<640, 360>" sink_7::alpha=0.5 \
    ! queue ! waylandsink sync=false fullscreen=true \
  filesrc location="$INPUT_PATH" ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! tee name=split1 ! queue ! mixer. \
  filesrc location="$INPUT_PATH" ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! tee name=split2 ! queue ! mixer. \
  filesrc location="$INPUT_PATH" ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! tee name=split3 ! queue ! mixer. \
  filesrc location="$INPUT_PATH" ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! tee name=split4 ! queue ! mixer. \
  split1. ! queue ! qtimlvconverter ! queue ! qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! qtimlpostprocess module=yolov8 labels="$LABELS_PATH" ! video/x-raw,format=RGBA,width=640,height=360 ! queue ! mixer. \
  split2. ! queue ! qtimlvconverter ! queue ! qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! qtimlpostprocess module=yolov8 labels="$LABELS_PATH" ! video/x-raw,format=RGBA,width=640,height=360 ! queue ! mixer. \
  split3. ! queue ! qtimlvconverter ! queue ! qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! qtimlpostprocess module=yolov8 labels="$LABELS_PATH" ! video/x-raw,format=RGBA,width=640,height=360 ! queue ! mixer. \
  split4. ! queue ! qtimlvconverter ! queue ! qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! qtimlpostprocess module=yolov8 labels="$LABELS_PATH" ! video/x-raw,format=RGBA,width=640,height=360 ! queue ! mixer.
