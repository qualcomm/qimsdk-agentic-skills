#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""MP4 + ML bin YOLO sample."""

from qimsdk import Element, Pipeline, VideoFilter
import os

#  Example pipeline:
#
#    filesrc -> qtdemux -> h264parse -> v4l2h264dec
#            -> qtimlvideotflitebin -> qtivoverlay -> waylandsink
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs YOLOv8 object detection with the Qualcomm TFLite delegate, overlays the
#  detected objects, and displays the result through Wayland.


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

    # Restricts the decoded stream to NV12 before it reaches the ML bin.
    vf = VideoFilter().format("NV12")

    # Executes the ML model and attaches the results to the corresponding video frame.
    #
    # Configures the model, the hardware that executes it (delegate),
    # as well as the postprocessing algorithm and the label file.
    mlbin = (
        Element("qtimlvideotflitebin", "mlbin")
        .set("inference-delegate", "external")
        .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("inference-model", f"{os.environ['HOME']}/models/yolov8_det_quantized.tflite")
        .set("postprocess-module", "yolov8")
        .set("postprocess-labels", f"{os.environ['HOME']}/labels/yolov8.json")
    )

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("mlbin-pipeline")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(mlbin)
        .add(overlay)
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
