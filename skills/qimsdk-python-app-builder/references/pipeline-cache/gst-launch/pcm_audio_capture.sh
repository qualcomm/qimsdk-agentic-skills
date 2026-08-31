#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -v pulsesrc volume=10 ! audioconvert ! wavenc ! filesink location=$HOME/media/<Audio_PCM>.wav
