#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Test tensor output example."""

from qimsdk import Element, Pipeline, TensorFilter, VideoFilter
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
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
parser.add_argument("-o", "--output-config",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/tensor_520_520.rgb",
                    help="Output file location")
args = parser.parse_args()

input_config = args.input_config
output_config = args.output_config

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> ml_preprocessing -> [tf] -> sink
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  converts the decoded frames into model input tensor format, and dumps the raw
#  tensor output to a file.


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", input_config)
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

    # Restricts the decoded stream to NV12 before it reaches the ML converter.
    vf = VideoFilter().format("NV12")

    # Converts raw video frames into model input tensor format.
    ml_preprocessing = Element("qtimlvconverter", "ml_preprocessing")

    # Restricts the tensor stream to the shape and type expected by the model.
    tf = TensorFilter().type("UINT8").dimensions(1, 520, 520, 3)

    # Writes output buffers to files.
    sink = (
        Element("multifilesink", "sink")
        .set("max-files", 1)
        .set("location", output_config)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("ml-pipeline")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(ml_preprocessing)
        .add_stream_filter("tf", tf)
        .add(sink)
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
