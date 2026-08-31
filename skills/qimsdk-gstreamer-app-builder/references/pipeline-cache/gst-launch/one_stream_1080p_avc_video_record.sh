#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video ! \
  video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! \
  queue ! h264parse ! mp4mux ! queue ! \
  filesink location=$HOME/media/mux.mp4
