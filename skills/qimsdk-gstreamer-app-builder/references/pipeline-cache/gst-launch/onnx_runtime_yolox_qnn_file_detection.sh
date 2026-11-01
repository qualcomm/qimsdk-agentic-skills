#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# ONNX Runtime YOLOX detection on an MP4 file with QNN/HTP execution.
# Set INPUT_PATH, MODEL_PATH, and LABELS_PATH before running, or replace defaults.
INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/ai_demo_sample.mp4}"
MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.onnx}"
LABELS_PATH="${LABELS_PATH:-$HOME/Downloads/qimsdk_samples/labels/yolov8.json}"

gst-launch-1.0 -e --gst-debug=2 \
  filesrc location="$INPUT_PATH" ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t \
  t. ! queue ! qtimetamux name=obj_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
  t. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! \
  qtimlpostprocess module=yolov8 labels="$LABELS_PATH" settings="{\"confidence\": 51.0}" ! text/x-raw ! queue ! obj_mux.
