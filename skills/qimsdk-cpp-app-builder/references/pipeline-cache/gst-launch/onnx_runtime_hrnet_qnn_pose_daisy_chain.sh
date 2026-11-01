#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

INPUT_PATH="${INPUT_PATH:-$HOME/Downloads/qimsdk_samples/media/gesture_sample.mp4}"
DETECT_MODEL="${DETECT_MODEL:-$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.onnx}"
POSE_MODEL="${POSE_MODEL:-$HOME/Downloads/qimsdk_samples/models/hrnet_pose.onnx}"
DETECT_LABELS="${DETECT_LABELS:-$HOME/Downloads/qimsdk_samples/labels/yolov8.json}"
POSE_LABELS="${POSE_LABELS:-$HOME/Downloads/qimsdk_samples/labels/hrnet.json}"
POSE_SETTINGS="${POSE_SETTINGS:-$HOME/Downloads/qimsdk_samples/labels/hrnet_settings.json}"

gst-launch-1.0 --gst-debug=2,qtimlonnx:7 \
  filesrc location="$INPUT_PATH" ! qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! tee name=t_split_1 \
  t_split_1. ! queue ! metamux_1. \
  t_split_1. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$DETECT_MODEL" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" htp-performance-mode=burst ! queue ! \
  qtimlpostprocess results=4 module=yolov8 labels="$DETECT_LABELS" ! text/x-raw ! queue ! metamux_1. \
  qtimetamux name=metamux_1 ! queue ! qtiobjtracker algo=bytetrack ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! metamux_2. \
  t_split_2. ! queue ! qtimlvconverter ! queue ! \
  qtimlonnx model="$POSE_MODEL" execution-provider=qnn backend-path="/usr/lib/libQnnHtp.so" htp-performance-mode=burst ! queue ! \
  qtimlpostprocess results=10 module=hrnet labels="$POSE_LABELS" settings="$POSE_SETTINGS" ! text/x-raw ! queue ! metamux_2. \
  qtimetamux name=metamux_2 ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true
