#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Super resolution side-by-side comparison Python app."""

import os

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    VideoFilter,
)

#  Example pipeline:
#
#    filesrc -> qtdemux -> h264parse -> v4l2h264dec -> queue -> NV12 VideoFilter -> tee
#      tee passthrough branch -> queue -> qtivcomposer sink_0 (left)
#      tee SR branch -> qtimlvconverter -> queue -> qtimltflite (external/HTP)
#                     -> queue -> qtimlpostprocess(module=srnet) -> RGBA VideoFilter -> queue -> qtivcomposer sink_1 (right)
#    qtivcomposer -> waylandsink
#
#  Decodes an MP4/H.264 file through the hardware decoder, splits the decoded
#  stream into an unmodified passthrough branch and an AI super-resolution
#  branch running QuickSRNet on the HTP/NPU, and composes the original frame
#  (left) alongside the upscaled frame (right) on a Wayland display.

INPUT_FILE = os.path.join(
    os.environ["HOME"], "Downloads", "qimsdk_samples", "media",
    "Draw_1080p_180s_30FPS.mp4",
)
MODEL_PATH = os.path.join(
    os.environ["HOME"], "Downloads", "qimsdk_samples", "models",
    "quicksrnetlarge_w8a8.tflite",
)


def create_and_execute_pipeline() -> None:

    # Reads the input MP4 file as raw bytes.
    source = Element("filesrc", "source").set("location", INPUT_FILE)

    # Extracts elementary streams from the MP4 container.
    demux = Element("qtdemux", "demux")

    # Prepares the H.264 bitstream for the decoder.
    parser = Element("h264parse", "parser")

    # Decodes the compressed H.264 stream into raw video frames using the
    # Qualcomm hardware decoder. DMA I/O modes avoid unnecessary buffer copies.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode to decouple the decoder thread.
    q_dec = Element("queue", "q_dec")

    # Normalize the decoded stream to NV12 before branching.
    vf = VideoFilter().format("NV12")

    # Splits the normalized stream into a passthrough branch and an AI
    # super-resolution branch.
    split = Element("tee", "split")

    # Passthrough branch: unmodified original frame feeding the left composer pane.
    q_passthrough = Element("queue", "q_passthrough")

    # AI branch: converts NV12 frames into the tensor layout QuickSRNet expects.
    pre = Element("qtimlvconverter", "pre")

    q_infer = Element("queue", "q_infer")

    # Runs QuickSRNetLarge super-resolution inference on the HTP/NPU via the
    # TFLite external delegate.
    infer = (
        Element("qtimltflite", "infer")
        .set("model", MODEL_PATH)
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set(
            "external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
        )
    )

    q_post = Element("queue", "q_post")

    # Decodes the super-resolution model output back into an upscaled video frame.
    post = Element("qtimlpostprocess", "post").set("module", "srnet")

    # Super-resolution postprocess emits video/x-raw {RGBA, RGBx}; use RGBA before
    # the composer. (RGB is NOT in qtimlpostprocess SRC caps and fails to link.)
    render_filter = VideoFilter().format("RGBA")

    q_sr = Element("queue", "q_sr")

    # Composes the original frame (left) and the upscaled frame (right)
    # side-by-side into a single canvas.
    composer = Element("qtivcomposer", "composer")

    # Renders the composed side-by-side stream on the Wayland display, fullscreen.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    pipeline = Pipeline("super-resolution-side-by-side")
    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(split)
    pipeline.add(q_passthrough)
    pipeline.add(pre)
    pipeline.add(q_infer)
    pipeline.add(infer)
    pipeline.add(q_post)
    pipeline.add(post)
    pipeline.add_stream_filter("render_filter", render_filter)
    pipeline.add(q_sr)
    pipeline.add(composer)
    pipeline.add(display)

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split")
    pipeline.link("split", "q_passthrough", "composer")
    pipeline.link(
        "split", "pre", "q_infer", "infer", "q_post", "post",
        "render_filter", "q_sr", "composer",
    )
    pipeline.link("composer", "display")

    # Position the original (left) and upscaled (right) frames on the canvas.
    composer.input(0).set("position", [0, 0])
    composer.input(0).set("dimensions", [960, 1080])
    composer.input(1).set("position", [960, 0])
    composer.input(1).set("dimensions", [960, 1080])

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
