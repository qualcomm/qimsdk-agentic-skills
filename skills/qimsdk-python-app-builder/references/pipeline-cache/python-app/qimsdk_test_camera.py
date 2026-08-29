#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Basic camera preview using explicit linking."""

from qimsdk import Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> [videofilter] -> display
#
#  The pipeline reads camera frames and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = Element("qtiqmmfsrc", "source")

    # Stream filters used in branch links.
    # They define specific stream characteristics from the supported options.
    videofilter = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Render video stream on display.
    #
    # async=false enforce state transition to ensure the buffers are returned on time.
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    display = (
        Element("waylandsink", "display")
        .set("sync", False)
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds elements, links them explicitly, and executes it.
    pipeline = (
        Pipeline("cam-pipeline")
        .add(source)
        .add_stream_filter("videofilter", videofilter)
        .add(display)
        .link("source", "videofilter", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
