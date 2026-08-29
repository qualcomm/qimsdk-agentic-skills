#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear


from qimsdk import Element, Pipeline, VideoFilter
import os

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [videofilter] -> mlbin1 -> mlbin2 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs person/foot detection followed by PPE detection through two chained ML
#  bins, overlays detected objects, and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", f"{os.environ['HOME']}/media/ppe_video.mp4")
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

    # Restricts the decoded stream to NV12 before it reaches the ML bins.
    vf = VideoFilter().format("NV12")

    # Executes the ML model and attaches the results to the corresponding video frame.
    #
    # Configures the model, the hardware that executes it (delegate),
    # as well as the postprocessing algorithm and the label file.
    mlbin1 = (
        Element("qtimlvideotflitebin", "mlbin1")
        .set("inference-delegate", "external")
        .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("inference-model", f"{os.environ['HOME']}/models/foot_track_net-person-foot-detection-w8a8.tflite")
        .set("postprocess-module", "qpd")
        .set("postprocess-labels", f"{os.environ['HOME']}/labels/foot_track_net.json")
        .set("postprocess-settings", f"{os.environ['HOME']}/labels/foot_track_net_settings.json")
    )

    # Executes the ML model and attaches the results to the corresponding video frame.
    #
    # Configures the model, the hardware that executes it (delegate),
    # as well as the postprocessing algorithm and the label file.
    mlbin2 = (
        Element("qtimlvideotflitebin", "mlbin2")
        .set("preprocess-mode", "roi-batch-cumulative")
        .set("inference-delegate", "external")
        .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("inference-model", f"{os.environ['HOME']}/models/gear_guard_net-ppe-detection-w8a8.tflite")
        .set("postprocess-module", "yolov8")
        .set("postprocess-labels", f"{os.environ['HOME']}/labels/gear_guard_net.json")
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
        Pipeline("ppe-video-ml")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(mlbin1)
        .add(mlbin2)
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
