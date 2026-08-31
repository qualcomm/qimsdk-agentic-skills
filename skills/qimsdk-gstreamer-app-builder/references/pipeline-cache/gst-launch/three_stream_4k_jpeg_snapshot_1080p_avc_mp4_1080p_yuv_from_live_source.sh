#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
      queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! \
      queue ! h264parse ! mp4mux ! queue ! filesink location=$HOME/media/video2.mp4 \
  camsrc.image_1 ! "image/jpeg,width=3840,height=2160,framerate=30/1" ! \
      multifilesink location=$HOME/media/frame%d.jpg sync=true async=false \
  camsrc. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
      waylandsink fullscreen=true async=true sync=false
