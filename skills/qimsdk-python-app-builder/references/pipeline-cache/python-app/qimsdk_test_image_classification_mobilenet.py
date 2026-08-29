#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Single-stream MobileNet image classification Python app."""

import os

from qimsdk import Element, Pipeline, TextFilter, VideoFilter

#  Example pipeline:
#
#    source -> demux -> parser -> decoder -> q_dec -> [NV12 vf] -> tee name=split
#      split. -> mlmuxer -> overlay -> display
#      split. -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> postprocessing -> [mlf:text] -> mlmuxer
#
#  The pipeline decodes an MP4/H.264 file with the hardware decoder, runs
#  MobileNet classification on full frames via HTP/NPU, and overlays the
#  top classification label and confidence score on the displayed video.


def create_and_execute_pipeline() -> None:

    # Reads the encoded MP4 file from disk.
    source = (
        Element("filesrc", "source")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/15s.mp4")
    )

    # Demuxes the MP4 container to extract the H.264 elementary stream.
    demux = Element("qtdemux", "demux")

    # Parses the H.264 stream for the hardware decoder.
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4, "output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required to decouple the decoder.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before branching.
    videostream = VideoFilter().format("NV12")

    # Splits frames into a display/passthrough branch and an ML branch.
    split = Element("tee", "split")

    # Queues frames from the tee into the ML branch.
    q1 = Element("queue", "q1")

    # Converts raw video frames into the model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Runs MobileNet classification on the HTP/NPU via the TFLite external delegate.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    )

    # Queues tensor outputs between inference and postprocess.
    q3 = Element("queue", "q3")

    # Decodes classification tensor output into label/confidence metadata.
    # mobilenet_v2_w8a8 (quantized) uses module="mobilenet" per model catalog.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "mobilenet")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/mobilenet.json")
        .set("settings", '{"confidence": 51.0}')
        .set("results", 5)
    )

    # Stream filter marking the ML branch output as text metadata.
    mlf = TextFilter()

    # Merges classification metadata with the original video frames.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Renders the classification label and confidence overlay on the frame.
    overlay = Element("qtivoverlay", "overlay")

    # Renders the final video stream fullscreen on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("classification-pipeline")
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
        .add(mlmuxer)
        .add(overlay)
        .add(display)
        .link("split", "mlmuxer")
        .link("source", "demux", "parser", "decoder", "q_dec", "videostream", "split",
              "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf", "mlmuxer")
        .link("mlmuxer", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
