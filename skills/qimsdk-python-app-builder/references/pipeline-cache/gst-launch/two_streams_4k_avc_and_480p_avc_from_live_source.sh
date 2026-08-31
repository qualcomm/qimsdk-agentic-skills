#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! video/x-raw,format=NV12,width=3840,height=2160,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=$HOME/media/video1.mp4 \
  camsrc. ! video/x-raw,format=NV12,width=640,height=480,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=$HOME/media/video2.mp4
