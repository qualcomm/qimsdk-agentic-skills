#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""ISP-camera YOLOv8 object detection using the QNN runtime directly (qtimlqnn),
overlaid on a fullscreen Wayland display."""

import os

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    TextFilter,
    VideoFilter,
)

#  Example pipeline:
#
#    source -> [vf:NV12,1920x1080@30] -> split name=split
#      split. -> mlmuxer -> overlay -> display
#      split. -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> postprocessing -> [mlf:text] -> q4 -> mlmuxer.
#
#  Captures the default ISP camera, runs YOLOv8 object detection directly on
#  the QNN runtime (qtimlqnn) against a pre-converted QNN cached-binary model
#  (no TFLite external-delegate indirection), overlays detected bounding boxes
#  and class labels, and renders fullscreen on a Wayland display.
#
#  qtimlqnn requires explicit `backend`/`system` shared-library paths and,
#  for this cached binary, an explicit `tensors` output-tensor filter naming
#  the three raw outputs the model exports (boxes/scores/class_idx) so that
#  qtimlpostprocess's yolov8 module receives them in the expected order.
#  `tensors` is a GstValueArray property — set it with a Python list, never a
#  gst-launch angle-bracket string. See `api-surface.md`'s general
#  "GstValueArray-typed properties take a Python list" rule and
#  `model-catalog.md`'s "YOLOv8-Detection (QNN cached binary)" row.

MODEL_PATH = f"{os.environ['HOME']}/models/yolov8_det_w8a8.bin"
LABELS_PATH = f"{os.environ['HOME']}/labels/yolov8.json"
QNN_BACKEND = "/usr/lib/libQnnHtp.so"
QNN_SYSTEM = "/usr/lib/libQnnSystem.so"


def create_and_execute_pipeline() -> None:

    # Captures frames from the default ISP camera.
    source = Element("qtiqmmfsrc", "source").set("camera", 0)

    # Constrains the camera stream to NV12 at a fixed resolution/framerate
    # before branching for display and AI preprocessing.
    videostream = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Splits the camera stream into a passthrough/overlay branch and an AI branch.
    split = Element("tee", "split")

    # Queues frames from the tee into the AI branch.
    q1 = Element("queue", "q1")

    # Converts raw NV12 video frames into the model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Runs YOLOv8 detection directly on the QNN runtime against a cached QNN
    # binary. `tensors` must be a Python list — this model's cached binary
    # exports three separate output tensors (boxes/scores/class_idx) that the
    # yolov8 postprocess module expects in this exact order.
    inferencing = (
        Element("qtimlqnn", "inferencing")
        .set("model", MODEL_PATH)
        .set("backend", QNN_BACKEND)
        .set("system", QNN_SYSTEM)
        .set("tensors", ["boxes", "scores", "class_idx"])
    )

    # Queues tensor outputs before postprocessing.
    q3 = Element("queue", "q3")

    # Decodes YOLOv8 model output tensors into detection metadata.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("results", 5)
        .set("module", "yolov8")
        .set("labels", LABELS_PATH)
        .set("settings", '{"confidence": 51.0}')
        .set("bbox-stabilization", True)
    )

    # Stream filter marking the AI branch output as text metadata.
    mlf = TextFilter()

    # Queues metadata before it is merged back with the video branch.
    q4 = Element("queue", "q4")

    # Merges detection metadata produced by the AI branch with the original
    # video frames from the passthrough branch.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Renders detected bounding boxes and class labels on top of the frame.
    overlay = Element("qtivoverlay", "overlay")

    # Displays the annotated stream fullscreen on the Wayland compositor.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    pipeline = Pipeline("camera-qnn-yolov8-pipeline")
    pipeline.add(source)
    pipeline.add_stream_filter("videostream", videostream)
    pipeline.add(split)
    pipeline.add(q1)
    pipeline.add(preprocessing)
    pipeline.add(q2)
    pipeline.add(inferencing)
    pipeline.add(q3)
    pipeline.add(postprocessing)
    pipeline.add_stream_filter("mlf", mlf)
    pipeline.add(q4)
    pipeline.add(mlmuxer)
    pipeline.add(overlay)
    pipeline.add(display)

    pipeline.link("source", "videostream", "split")
    pipeline.link("split", "mlmuxer")
    pipeline.link("split", "q1", "preprocessing", "q2", "inferencing", "q3",
                  "postprocessing", "mlf", "q4", "mlmuxer")
    pipeline.link("mlmuxer", "overlay", "display")

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
