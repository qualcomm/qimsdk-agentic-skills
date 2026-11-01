#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""MP4 YOLO sample."""

from qimsdk import Pipeline, TextFilter, VideoFilter
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
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> mlmuxer
#      split -> q1 -> preprocessing -> q2 -> inferencing -> q4
#             -> postprocessing -> [mlf] -> mlmuxer -> q5 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs YOLOv8 object detection, merges the resulting metadata with the original
#  frames, overlays the detected objects, and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Elements are created on the fly as they are added, and linking is
    # implicit, following the order in which elements are added.
    #
    # async=false enforce state transition to ensure the buffers are returned on time.
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    pipeline = (
        Pipeline("ml-pipeline")
        .add("filesrc", "src", "location", input_config)
        .add("qtdemux", "demux")
        .add("h264parse", "parse")
        .add("v4l2h264dec", "decoder", "output-io-mode", 4, "capture-io-mode", 4)
        .add_stream_filter("vf", VideoFilter().format("NV12"))
        .add("tee", "split")
        .add("queue", "q1")
        .add("qtimlvconverter", "preprocessing")
        .add("queue", "q2")
        .add("qtimltflite", "inferencing",
            "delegate", "external",
            "external-delegate-path", "libQnnTFLiteDelegate.so",
            "external-delegate-options", "QNNExternalDelegate,backend_type=htp;",
            "model", f"{model_base_path}/models/yolov8_det_quantized.tflite",
        )
        .add("queue", "q4")
        .add("qtimlpostprocess", "postprocessing", "module", "yolov8", "labels", f"{model_base_path}/labels/yolov8.json")
        .add_stream_filter("mlf", TextFilter())
        .add("qtimetamux", "mlmuxer")
        .add("queue", "q5")
        .add("qtivoverlay", "overlay")
        .add("waylandsink", "display", "fullscreen", True)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "mlmuxer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q4", "postprocessing", "mlf", "mlmuxer", "q5", "overlay", "display")
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
