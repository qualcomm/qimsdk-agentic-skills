#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4}"
MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/deeplab_xception.onnx}"
LABELS_PATH="${LABELS_PATH:-$HOME/Downloads/qimsdk_samples/labels/dv3-argmax.json}"

gst-launch-1.0 -v --gst-debug=2,qtimlonnx*:7 \
  filesrc location="$INPUT_PATH" ! qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! tee name=split \
  split. ! queue ! qtivcomposer name=mixer ! waylandsink fullscreen=true \
  split. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" ! queue ! \
  qtimlpostprocess module=deeplab-argmax labels="$LABELS_PATH" ! video/x-raw,format=RGBA ! queue ! mixer.
