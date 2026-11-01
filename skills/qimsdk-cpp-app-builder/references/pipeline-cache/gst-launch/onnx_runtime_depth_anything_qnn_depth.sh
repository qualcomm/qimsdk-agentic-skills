#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/ppe_video.mp4}"
MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/depth_anything_v2.onnx}"
LABELS_PATH="${LABELS_PATH:-$HOME/Downloads/qimsdk_samples/labels/monodepth.json}"

gst-launch-1.0 -v \
  filesrc location="$INPUT_PATH" ! qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! tee name=split \
  split. ! queue ! qtivcomposer name=mixer ! queue ! waylandsink fullscreen=true \
  split. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" htp-performance-mode=burst ! queue ! \
  qtimlpostprocess module=midas-v2 labels="$LABELS_PATH" ! video/x-raw,format=RGBA ! queue ! mixer.
