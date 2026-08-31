#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e filesrc location=$HOME/media/<mp3_file>.mp3 ! mpegaudioparse ! mpg123audiodec ! pulsesink volume=10
