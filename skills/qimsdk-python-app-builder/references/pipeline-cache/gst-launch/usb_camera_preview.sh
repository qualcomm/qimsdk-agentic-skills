#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 \
  v4l2src device=/dev/video0 ! \
  video/x-raw,format=YUY2 ! \
  qtivtransform ! video/x-raw,format=NV12 ! \
  waylandsink fullscreen=true
