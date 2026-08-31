#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# Run in separate consoles or combine with &
gst-launch-1.0 -e -v \
  filesrc location=$HOME/media/video.mp4 ! \
  qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink &

gst-launch-1.0 -e -v \
  filesrc location=$HOME/media/video.mp4 ! \
  qtdemux ! queue ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink
``
