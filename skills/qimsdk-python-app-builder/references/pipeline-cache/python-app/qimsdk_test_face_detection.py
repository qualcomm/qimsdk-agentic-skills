#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Single-stream face detection pipeline test."""

import os

from qimsdk import Element, Pipeline, TextFilter, VideoFilter

#  Example pipeline (single AI stage, discrete converter/inference/postprocess):
#
#    filesrc -> qtdemux -> h264parse -> v4l2h264dec -> q_dec -> [NV12 videofilter] -> tee name=split
#      split. -> facemux
#      split. -> q1 -> [ML preprocess] -> q2 -> [ML inference] -> q3
#             -> [ML postprocess, module=qfd] -> [mlf:text] -> facemux -> q4 -> qtivoverlay -> waylandsink
#
#  The pipeline decodes an mp4 file with the hardware decoder, runs Face
#  Detection Lite inference on full frames using the HTP/NPU via the TFLite
#  external delegate, overlays detected face bounding boxes, and renders the
#  result fullscreen on a Wayland display.


def create_and_execute_pipeline() -> None:

    # Reads the input MP4 file from disk.
    source = Element("filesrc", "source").set(
        "location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/15s.mp4"
    )

    # Demuxes the MP4 container into elementary streams.
    demux = Element("qtdemux", "demux")

    # Parses the H.264 elementary stream for the hardware decoder.
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required for decoder decoupling.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before branching/AI preprocessing.
    videostream = VideoFilter().format("NV12")

    # Splits decoded frames into display and ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Converts raw video frames into model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Runs Face Detection Lite inference on the HTP/NPU via the TFLite
    # external delegate.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set(
            "external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
        )
        .set(
            "model",
            f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/face_det_lite.tflite",
        )
    )

    # Queues data between pipeline stages.
    q3 = Element("queue", "q3")

    # Decodes model output tensors into face detection metadata.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "qfd")
        .set(
            "labels",
            f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/face_det_lite.json",
        )
    )

    # Stream filter marking the ML branch output as text metadata.
    mlf = TextFilter()

    # Merges face detection metadata with the original video frames.
    facemux = Element("qtimetamux", "facemux")

    # Queues data between pipeline stages.
    q4 = Element("queue", "q4")

    # Renders face bounding box metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Renders the video stream fullscreen on the Wayland display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("face-detection-pipeline")
        .add(source)
        .add(demux)
        .add(parser)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("videostream", videostream)
        .add(split)
        .add(q1)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(q3)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(facemux)
        .add(q4)
        .add(overlay)
        .add(display)
        .link("split", "facemux")
        .link(
            "source", "demux", "parser", "decoder", "q_dec", "videostream", "split",
            "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf",
            "facemux",
        )
        .link("facemux", "q4", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
