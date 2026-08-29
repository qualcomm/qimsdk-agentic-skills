#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Camera YOLO pipeline test."""

import os

from qimsdk import Element, Pipeline, TextFilter, VideoFilter

#  Example pipeline:
#
#    source -> [videostream] -> tee name=split
#      split. -> qtimetamux
#      split. -> q1 -> qtimlvconverter -> q2 -> qtimltflite -> q4
#             -> qtimlpostprocess -> [mlf:text] -> qtimetamux -> q5 -> qtivoverlay -> waylandsink
#
#  The pipeline reads camera frames, runs YOLOv8 inference and postprocessing,
#  overlays detected objects, and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = (
        Element("qtiqmmfsrc", "source")
        .set("camera", 0)
    )

    # Restricts the camera stream to NV12/1080p/30fps.
    videostream = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Splits decoded frames into display and ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Converts raw video frames into model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Executes the ML model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{os.environ['HOME']}/models/yolov8_det_quantized.tflite")
    )

    # Queues data between pipeline stages.
    q4 = Element("queue", "q4")

    # Decodes model output tensors into metadata for downstream overlay.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("results", 5)
        .set("module", "yolov8")
        .set("labels", f"{os.environ['HOME']}/labels/yolov8.json")
        .set("settings", '{"confidence": 70.0}')
    )

    # Stream filter marking the ML branch output as text metadata.
    mlf = TextFilter()

    # Merges metadata produced by the ML branch with original video frames.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Queues data between pipeline stages.
    q5 = Element("queue", "q5")

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
    # Explicit linking is applied.
    pipeline = (
        Pipeline("ml-cam-pipeline")
        .add(source)
        .add_stream_filter("videostream", videostream)
        .add(split)
        .add(q1)
        .add(q2)
        .add(preprocessing)
        .add(inferencing)
        .add(q4)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(mlmuxer)
        .add(q5)
        .add(overlay)
        .add(display)
        .link("split", "mlmuxer")
        .link("source", "videostream", "split", "q1", "preprocessing", "q2", "inferencing", "q4", "postprocessing", "mlf", "mlmuxer", "q5", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
