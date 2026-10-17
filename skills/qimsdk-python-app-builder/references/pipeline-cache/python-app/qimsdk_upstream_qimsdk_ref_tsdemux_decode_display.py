#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Transport stream demux sample that exercises H264Filter."""

from qimsdk import Element, H264Filter, Pipeline, VideoFilter
import os
import argparse
import sys


class HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\n{self.prog}: error: {message}\n")
        sys.exit(2)


parser = HelpOnErrorArgumentParser(
    description="QIMSDK reference app",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-i", "--input-config",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.ts",
                    help="Input file path or pattern")
args = parser.parse_args()

input_config = args.input_config

#  Example pipeline:
#
#    src -> demux -> [vf1] -> parse -> decoder -> [vf2] -> display
#
#  The pipeline reads a sequence of transport-stream files, extracts the H.264
#  elementary stream, decodes it through the hardware decoder, and displays
#  the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads a sequence of input files as stream data.
    src = (
        Element("multifilesrc", "src")
        .set("location", input_config)
        .set("stop-index", 0)
    )

    # Extracts elementary streams from the transport stream.
    demux = Element("tsdemux", "demux")

    # Restricts the extracted stream to a fixed framerate before parsing.
    vf1 = H264Filter().framerate(30)

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
    vf2 = VideoFilter().format("NV12")

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
        .add_stream_filter("vf1", vf1)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf2", vf2)
        .add(display)
    )

    pipeline.execute()


def main() -> None:

    if "HOME" not in os.environ:
        raise EnvironmentError("Error: HOME environment variable is not set.")

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
