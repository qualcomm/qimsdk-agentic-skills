#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080 ! \
  qtivtransform rotate=180 ! \
  waylandsink fullscreen=true
