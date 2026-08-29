#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Camera preview + still image capture using CamSrc and explicit links."""

import time
import os

from qimsdk import CamSrc, Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> [vf] -> encoder -> parser -> muxer -> sink
#
#  The pipeline reads camera frames, encodes them with H.264, muxes into MP4,
#  and writes the output to disk.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = CamSrc("source")

    # Stream filters used in branch links.
    # They define specific stream characteristics from the supported options.
    vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Encodes raw video frames into H.264 stream.
    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("output-io-mode", "dmabuf-import")
        .set("capture-io-mode", "dmabuf")
    )

    # Parses H.264 bitstream for downstream muxing.
    parser = Element("h264parse", "parser")

    # Muxes encoded stream into MP4 container.
    muxer = Element("mp4mux", "muxer")

    # Writes output stream to a file.
    sink = (
        Element("filesink", "sink")
        .set("location", f"{os.environ['HOME']}/media/encoder_output.mp4")
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("cam-encoder-pipeline")
        .add(source)
        .add_stream_filter("vf", vf)
        .add(encoder)
        .add(parser)
        .add(muxer)
        .add(sink)
    )

    pipeline.eos(True).execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
