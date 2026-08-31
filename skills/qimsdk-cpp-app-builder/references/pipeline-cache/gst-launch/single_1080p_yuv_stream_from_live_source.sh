#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 \
  qticamsrc name=camsrc ! \
  video/x-raw,width=1920,height=1080,framerate=30/1 ! \
  waylandsink fullscreen=true
