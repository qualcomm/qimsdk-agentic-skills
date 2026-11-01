#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Display original and ONNX Runtime super-resolution video side by side."""

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    VideoFilter,
)

INPUT_FILE = "/etc/mahendra/super.mp4"
MODEL_PATH = "/etc/mahendra/quicksrnetmedium.onnx"
MODEL_DATA_PATH = "/etc/mahendra/quicksrnetmedium.data"

# The composer uses two 960x1080 panes: original on the left and SR output on
# the right. Adjust these only when the source display layout must change.
TILE_WIDTH = 960
TILE_HEIGHT = 1080


def create_and_execute_pipeline() -> None:
    """Create the MP4-to-Wayland ONNX Runtime super-resolution pipeline."""
    # Source and hardware H.264 decode.
    source = Element("filesrc", "source").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")
    decoder = Element("v4l2h264dec", "decoder").set(
        "capture-io-mode", 4,
        "output-io-mode", 4,
    )
    q_dec = Element("queue", "q_dec")
    nv12 = VideoFilter().format("NV12")

    # Split decoded video into original and super-resolution render paths.
    split = Element("tee", "split")
    q_original = Element("queue", "q_original")

    # This device's ONNX Runtime rejects this model with duplicate `node_conv2d`
    # node names at the default optimization level; disable graph optimization.
    q_sr = Element("queue", "q_sr")
    preprocess = Element("qtimlvconverter", "preprocess")
    q_infer = Element("queue", "q_infer")
    infer = (
        Element("qtimlonnx", "infer")
        .set("model", MODEL_PATH)
        .set("execution-provider", "cpu")
        .set("optimization-level", "disable-all")
    )
    q_post = Element("queue", "q_post")
    postprocess = Element("qtimlpostprocess", "postprocess").set("module", "srnet")

    # srnet emits rendered video; RGBA is required by qtimlpostprocess caps.
    # Do not pin its resolution: composer pad geometry sizes the output pane.
    sr_render = VideoFilter().format("RGBA")
    q_render = Element("queue", "q_render")

    # Compose original and upscaled frames into a side-by-side Wayland display.
    composer = Element("qtivcomposer", "composer")
    display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)

    pipeline = Pipeline("onnxruntime-superresolution")
    for element in (
        source,
        demux,
        parser,
        decoder,
        q_dec,
        split,
        q_original,
        q_sr,
        preprocess,
        q_infer,
        infer,
        q_post,
        postprocess,
        q_render,
        composer,
        display,
    ):
        pipeline.add(element)

    pipeline.add_stream_filter("nv12", nv12)
    pipeline.add_stream_filter("sr_render", sr_render)

    pipeline.link("source", "demux")
    pipeline.link("demux", "parser", "decoder", "q_dec", "nv12", "split")
    pipeline.link("split", "q_original", "composer")
    pipeline.link(
        "split",
        "q_sr",
        "preprocess",
        "q_infer",
        "infer",
        "q_post",
        "postprocess",
        "sr_render",
        "q_render",
        "composer",
    )
    pipeline.link("composer", "display")

    # Original is left; QuickSRNet's upscaled output is right.
    composer.input(0).set("position", [0, 0])
    composer.input(0).set("dimensions", [TILE_WIDTH, TILE_HEIGHT])
    composer.input(1).set("position", [TILE_WIDTH, 0])
    composer.input(1).set("dimensions", [TILE_WIDTH, TILE_HEIGHT])

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
