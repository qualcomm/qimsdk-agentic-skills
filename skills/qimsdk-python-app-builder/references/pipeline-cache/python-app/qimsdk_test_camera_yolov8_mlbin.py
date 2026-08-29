#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Camera YOLO example using ML bin."""

import os

from qimsdk import Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> [videofilter] -> qtimlvideotflitebin -> qtivoverlay -> waylandsink
#
#  The pipeline captures camera frames, runs YOLOv8 object detection with the
#  Qualcomm TFLite delegate in the ML bin, overlays detected objects, and
#  displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = (
        Element("qtiqmmfsrc", "source")
        .set("camera", 0)
    )

    # Restricts the camera stream to NV12/1080p/30fps before the ML bin.
    videofilter = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

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
        .set("sync", False)
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("mlbin-pipeline")
        .add(source)
        .add_stream_filter("videofilter", videofilter)
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
