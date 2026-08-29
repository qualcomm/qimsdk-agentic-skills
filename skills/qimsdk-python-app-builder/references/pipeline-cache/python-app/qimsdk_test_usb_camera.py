#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""USB camera preview using explicit linking."""

import argparse

from qimsdk import Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> transform -> [videofilter] -> display
#
#  The pipeline reads frames from a USB (V4L2) camera, rotates them, restricts
#  the stream to NV12/1080p/30fps, and displays the result through Wayland.


def create_and_execute_pipeline(device: str) -> None:

    # Captures frames from the USB camera source.
    source = (
        Element("v4l2src", "source")
        .set("device", device)
    )

    # Applies geometric transforms to video frames.
    transform = Element("qtivtransform", "transform")

    # Stream filters used in branch links.
    # They define specific stream characteristics from the supported options.
    videofilter = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Render video stream on display.
    #
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    display = (
        Element("waylandsink", "display")
        .set("sync", False)
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds elements, links them explicitly, and executes it.
    pipeline = (
        Pipeline("usb-cam-pipeline")
        .add(source)
        .add(transform)
        .add_stream_filter("videofilter", videofilter)
        .add(display)
        .link("source", "transform", "videofilter", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-d", "--device", type=str, default="/dev/video2",
        help="V4L2 device node to capture from (default: %(default)s)",
    )
    args = parser.parse_args()

    create_and_execute_pipeline(args.device)


if __name__ == "__main__":
    main()
