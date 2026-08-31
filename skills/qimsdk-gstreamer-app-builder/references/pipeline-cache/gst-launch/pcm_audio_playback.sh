#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e filesrc location=$HOME/media/<wav_file>.wav ! wavparse ! audioconvert ! pulsesink volume=10
