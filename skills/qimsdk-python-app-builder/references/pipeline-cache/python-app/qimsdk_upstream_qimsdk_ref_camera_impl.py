#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Basic camera preview using auto-linking."""

import argparse
import sys

from qimsdk import Pipeline, VideoFilter


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
                    default="0",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

input_config = args.input_config

#  Example pipeline:
#
#    source -> [videofilter] -> display
#
#  The pipeline reads camera frames and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Elements are created on the fly as they are added, and linking is
    # implicit, following the order in which elements are added.
    #
    # async=false enforce state transition to ensure the buffers are returned on time.
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    pipeline = (
        Pipeline("cam-pipeline")
        # Captures frames from the camera source.
        .add("qtiqmmfsrc", "source", "camera", int(input_config))
        # Stream filter used in the branch link.
        # It defines specific stream characteristics from the supported options.
        .add_stream_filter(
            "videofilter",
            VideoFilter().format("NV12").resolution(1920, 1080).framerate(30),
        )
        # Render video stream on display.
        .add("waylandsink", "display", "sync", False, "fullscreen", True)
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
