#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear


from qimsdk import Element, Pipeline, VideoFilter
import os
import argparse
import sys


class HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\n{self.prog}: error: {message}\n")
        sys.exit(2)


# Base path for sample assets (media/, models/, labels/ live under it).
#
# Defaults to the standard sample location and can be overridden by passing a
# different base path as the first command-line argument.
parser = HelpOnErrorArgumentParser(
    description="QIMSDK reference app",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-m", "--model-base-path",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples",
                    help="Base path for models and labels")
parser.add_argument("-i", "--input-config",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config


#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split -> q4 -> composer -> display
#                                             \-> q1 -> preprocessing -> q2 -> inferencing -> q3 -> postprocessing -> [mlf] -/
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  overlays detected objects, and displays the result through Wayland.


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

    # Restricts the decoded stream to NV12 before it reaches the tee.
    vf = VideoFilter().format("NV12")

    # Splits decoded frames into display and ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Converts raw video frames into model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Executes the ML model and attaches tensor outputs to each frame.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{model_base_path}/models/yolov8_det_quantized.tflite")
    )

    # Queues data between pipeline stages.
    q3 = Element("queue", "q3")

    # Decodes model output tensors into metadata for downstream overlay.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "yolov8")
        .set("labels", f"{model_base_path}/labels/yolov8.json")
    )

    # Restricts the ML branch output to RGBA/640x360 before compositing.
    mlf = VideoFilter().format("RGBA").resolution(640, 360)

    # Queues frames from tee into the composer branch.
    q4 = Element("queue", "q4")

    # Composites multiple input streams into a single output frame.
    composer = Element("qtivcomposer", "composer")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("yolov8-tee-pipeline")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(split)
        .add(q1)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(q3)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(q4)
        .add(composer)
        .add(display)

        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "q4", "composer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf", "composer")
        .link("composer", "display")
    )

    pipeline.get("composer").input(1).set("alpha", 0.5)

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
