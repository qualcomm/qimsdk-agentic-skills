#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Transport stream demux sample that exercises H264Filter."""

from qimsdk import Element, Pipeline, VideoFilter
import os

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [videofilter] -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", f"{os.environ['HOME']}/media/video.mp4")
    )

    # Extracts elementary streams from the MP4 container.
    demux = Element("qtdemux", "demux")

    # Prepares the H.264 bitstream for the decoder.
    parse = Element("h264parse", "parse")

    # Decodes the compressed H.264 stream into raw video frames.
    #
    # The I/O mode is configured to enforce DMA buffer usage,
    # avoiding unnecessary buffer copies.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("output-io-mode", 4)
        .set("capture-io-mode", 4)
    )

    # Restricts the decoded stream to NV12 before it reaches the display.
    videofilter = VideoFilter().format("NV12")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("video-pipeline")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("videofilter", videofilter)
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
