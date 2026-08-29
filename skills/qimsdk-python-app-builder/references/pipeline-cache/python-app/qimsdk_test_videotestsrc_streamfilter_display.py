#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Test video test source with stream filter."""

from qimsdk import Element, Pipeline, StreamFilter

#  Example pipeline:
#
#    src -> [vf1] -> transform -> [vf2] -> display
#
#  The pipeline generates synthetic test video frames, applies a caps-string-based
#  stream filter, rotates the frames, applies a second caps-string-based stream
#  filter, and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Generates synthetic test video frames.
    src = (
        Element("videotestsrc", "src")
        .set("pattern", "ball")
    )

    # Restricts the generated stream to NV12 at 1920x1080, 30fps.
    vf1 = StreamFilter("video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1")

    # Applies geometric transforms to video frames.
    transform = (
        Element("qtivtransform", "transform")
        .set("rotate", "180")
    )

    # Restricts the transformed stream to 1280x720.
    vf2 = StreamFilter("video/x-raw,width=1280,height=720")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("video-pipeline-streamfilter-string")
        .add(src)
        .add_stream_filter("vf1", vf1)
        .add(transform)
        .add_stream_filter("vf2", vf2)
        .add(display)
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
