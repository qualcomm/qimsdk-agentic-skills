#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.onnx}"
LABELS_PATH="${LABELS_PATH:-$HOME/Downloads/qimsdk_samples/labels/yolov8.json}"

gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc ! video/x-raw,width=1920,height=1080,format=NV12,framerate=30/1 ! queue ! tee name=t \
  t. ! queue ! qtimetamux name=metamux ! queue ! qtivoverlay ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! \
  qtimlpostprocess results=10 module=yolov8 labels="$LABELS_PATH" ! video/x-raw ! queue ! metamux.
