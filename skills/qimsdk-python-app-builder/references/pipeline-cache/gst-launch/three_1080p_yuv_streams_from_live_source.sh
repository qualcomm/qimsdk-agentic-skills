#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e -v \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>"  sink_0::dimensions="<480,270>" \
    sink_1::position="<480,0>"  sink_1::dimensions="<480,270>" \
    sink_2::position="<960,0>"  sink_2::dimensions="<480,270>" \
  mixer. ! queue ! waylandsink fullscreen=true \
  qticamsrc name=camsrc camera=0 video_0::type=preview video_1::type=video \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! videoscale ! video/x-raw,width=480,height=270 \
           ! mixer.sink_0 \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! videoscale ! video/x-raw,width=480,height=270 \
           ! mixer.sink_1 \
  camsrc. ! queue ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
           ! videoscale ! video/x-raw,width=480,height=270 \
           ! mixer.sink_2
