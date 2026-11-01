#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Camera preview + still image capture using CamSrc and explicit links."""

import argparse
import sys
import time
import os


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
parser.add_argument("-o", "--output-config",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/image_%d.jpeg",
                    help="Output file location")
args = parser.parse_args()

input_config = args.input_config
output_config = args.output_config

from qimsdk import CamSrc, Element, ImageFilter, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> [vf] -> display
#    source -> [if] -> imagesink
#
#  The pipeline reads camera frames, displays them through Wayland, and on
#  request captures a still image to a JPEG file.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = CamSrc("source")
    source.set("camera", int(input_config))

    # Restricts the preview stream to NV12/1080p/30fps before display.
    vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("sync", False)
        .set("async", False)
        .set("fullscreen", True)
    )

    # Restricts the still capture stream to a 4K JPEG format.
    imagefilter = ImageFilter().format("JPEG").resolution(3840, 2160)

    # Writes output buffers to files.
    imagesink = (
        Element("multifilesink", "imagesink")
        .set("enable-last-sample", False)
        .set("location", output_config)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("cam-capture-pipeline")
        .add(source)
        .add_stream_filter("vf", vf)
        .add(display)
        .add_stream_filter("if", imagefilter)
        .add(imagesink)
        .link("source", "vf", "display")
        .link("source", "if", "imagesink")
    )

    pipeline.start()
    time.sleep(2)
    # image capture trigger
    cam = pipeline.get("source", CamSrc)
    cam.image_capture()
    time.sleep(2)
    pipeline.stop().wait()


def main() -> None:

    if "HOME" not in os.environ:
        raise EnvironmentError("Error: HOME environment variable is not set.")

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
