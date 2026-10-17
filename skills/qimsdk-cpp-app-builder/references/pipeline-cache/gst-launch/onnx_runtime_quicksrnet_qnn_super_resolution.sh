#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/PedestrianDetection_128x128.mp4}"
MODEL_PATH="${MODEL_PATH:-$HOME/Downloads/qimsdk_samples/models/quicksrnetlarge.onnx}"

gst-launch-1.0 -v --gst-debug=2,qtimlonnx*:7 \
  filesrc location="$INPUT_PATH" ! qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! queue ! tee name=split \
  split. ! queue ! qtivcomposer name=mixer sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! waylandsink fullscreen=true \
  split. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$MODEL_PATH" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" htp-performance-mode=burst ! queue ! \
  qtimlpostprocess module=srnet ! video/x-raw,format=RGBA ! queue ! mixer.
