#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e -v \
  filesrc location=/root/media/Bunny_1MB.webm ! \
  matroskademux ! queue ! \
  v4l2vp9dec capture-io-mode=4 output-io-mode=4 ! \
  video/x-raw,format=NV12 ! queue ! waylandsink fullscreen=true sync=true
