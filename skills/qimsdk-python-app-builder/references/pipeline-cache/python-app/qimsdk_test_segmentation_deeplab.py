#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Single-stream DeepLabV3+ semantic segmentation Python app."""

import os

from qimsdk import Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> demux -> parser -> decoder -> q_dec -> [NV12 vf] -> tee name=split
#      split. -> q_video -> composer.sink_0 (raw passthrough)
#      split. -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> postprocessing -> [RGBA vf] -> q4 -> composer.sink_1 (mask)
#    composer (alpha-blend) -> display
#
#  The pipeline decodes an MP4/H.264 file with the hardware decoder, runs
#  DeepLabV3+ semantic segmentation on full frames via HTP/NPU, and
#  alpha-blends the rendered segmentation mask over the original video
#  using qtivcomposer before rendering fullscreen on display.


def create_and_execute_pipeline() -> None:

    # Reads the encoded MP4 file from disk.
    source = (
        Element("filesrc", "source")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4")
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

    # Splits frames into a raw passthrough branch and a segmentation branch.
    split = Element("tee", "split")

    # Queues the raw passthrough branch into the composer.
    q_video = Element("queue", "q_video")

    # Queues frames from the tee into the AI branch.
    q1 = Element("queue", "q1")

    # Converts raw video frames into the model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Runs DeepLabV3+ segmentation on the HTP/NPU via the TFLite external delegate.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/deeplabv3_plus_mobilenet_w8a8.tflite")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    )

    # Queues tensor outputs between inference and postprocess.
    q3 = Element("queue", "q3")

    # Decodes segmentation tensor output into a rendered mask.
    # deeplabv3_plus_mobilenet_w8a8 uses module="deeplab-argmax" per model catalog.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "deeplab-argmax")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/dv3-argmax.json")
    )

    # Segmentation is rendered to an RGBA video mask before the composer.
    # qtimlpostprocess only emits {RGBA, RGBx} (BGRA fails to link), and pinning
    # width/height makes postproc caps fixation fail — leave size unpinned and let
    # the composer alpha-blend scale the mask over the passthrough video.
    render_filter = VideoFilter().format("RGBA")

    # Queues the rendered mask branch into the composer.
    q4 = Element("queue", "q4")

    # Alpha-blends the segmentation mask (sink_1) over the raw passthrough
    # video (sink_0). alpha=0.5 blends mask and source evenly.
    composer = Element("qtivcomposer", "composer")

    # Renders the blended video stream fullscreen on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("segmentation-pipeline")
        .add(source)
        .add(demux)
        .add(parser)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("videostream", videostream)
        .add(split)
        .add(q_video)
        .add(q1)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(q3)
        .add(postprocessing)
        .add_stream_filter("render_filter", render_filter)
        .add(q4)
        .add(composer)
        .add(display)
        .link("source", "demux", "parser", "decoder", "q_dec", "videostream", "split")
        .link("split", "q_video", "composer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q3",
              "postprocessing", "render_filter", "q4", "composer")
        .link("composer", "display")
    )

    # Blend the segmentation mask (sink_1) over the raw video (sink_0).
    composer.input(1).set("alpha", 0.5)

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
