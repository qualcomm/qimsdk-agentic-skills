#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear


from qimsdk import Element, Pipeline, TextFilter, VideoFilter
import os

#  Example pipeline:
#
#    source -> [videostream] -> split1 -> q2 -> stage_01_preprocessing -> q3 -> stage_01_inferencing -> q1 -> stage_01_postprocessing -> [mlf_01] -> mlmuxer1 -> q11 -> metatransform -> split2 -> q5 -> stage_02_preprocessing -> q6 -> stage_02_inferencing -> split3 -> q7 -> stage_02_1_postprocessing -> [mlf_02] -> mlmuxer2 -> q12 -> overlay -> display
#                                                                                                                        \-> q8 -> stage_02_2_postprocessing -> q9 -> stage_03_1_inferencing -> q10 -> stage_03_2_inferencing -> q4 -> stage_03_postprocessing -> [mlf_03] -> mlmuxer2
#
#  The pipeline reads camera frames, runs ML inference and postprocessing,
#  and displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    source = Element("qtiqmmfsrc", "source")

    # Restricts the camera stream to NV12/1080p/30fps.
    videostream = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Splits decoded frames into display and ML branches.
    split1 = Element("tee", "split1")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Converts raw video frames into model input tensor format.
    stage_01_preprocessing = Element("qtimlvconverter", "stage_01_preprocessing")

    # Queues data between pipeline stages.
    q3 = Element("queue", "q3")

    # Executes the ML model and attaches tensor outputs to each frame.
    stage_01_inferencing = (
        Element("qtimltflite", "stage_01_inferencing")
        .set("delegate", "gpu")
        .set("model", f"{os.environ['HOME']}/models/palm_detection_full.tflite")
    )

    # Decodes model output tensors into metadata for downstream overlay.
    stage_01_postprocessing = (
        Element("qtimlpostprocess", "stage_01_postprocessing")
        .set("module", "palmd")
        .set("labels", f"{os.environ['HOME']}/labels/palmd_labels.json")
        .set("settings", f"{os.environ['HOME']}/labels/palmd_settings.json")
    )

    # Stream filter marking the palm-detection ML branch output as text metadata.
    mlf_01 = TextFilter()

    # Merges metadata produced by the ML branch with original video frames.
    mlmuxer1 = Element("qtimetamux", "mlmuxer1")

    # Queues data between pipeline stages.
    q11 = Element("queue", "q11")

    # Transforms metadata for downstream processing stages.
    metatransform = (
        Element("qtimetatransform", "metatransform")
        .set("module", "roi-palmd")
    )

    # Splits decoded frames into display and ML branches.
    split2 = Element("tee", "split2")

    # Queues frames from tee into the ML branch.
    q4 = Element("queue", "q4")

    # Queues data between pipeline stages.
    q5 = Element("queue", "q5")

    # Merges metadata produced by the ML branch with original video frames.
    mlmuxer2 = Element("qtimetamux", "mlmuxer2")

    # Queues data between pipeline stages.
    q12 = Element("queue", "q12")

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("sync", False)
        .set("fullscreen", True)
    )

    # Converts raw video frames into model input tensor format.
    stage_02_preprocessing = (
        Element("qtimlvconverter", "stage_02_preprocessing")
        .set("mode", "roi-batch-cumulative")
    )

    # Queues data between pipeline stages.
    q6 = Element("queue", "q6")

    # Executes the ML model and attaches tensor outputs to each frame.
    stage_02_inferencing = (
        Element("qtimltflite", "stage_02_inferencing")
        .set("delegate", "xnnpack")
        .set("model", f"{os.environ['HOME']}/models/hand_landmark_full.tflite")
    )

    # Splits decoded frames into display and ML branches.
    split3 = Element("tee", "split3")

    # Queues data between pipeline stages.
    q7 = Element("queue", "q7")

    # Queues data between pipeline stages.
    q8 = Element("queue", "q8")

    # Queues data between pipeline stages.
    q9 = Element("queue", "q9")

    # Queues data between pipeline stages.
    q10 = Element("queue", "q10")

    # Decodes model output tensors into metadata for downstream overlay.
    stage_02_1_postprocessing = (
        Element("qtimlpostprocess", "stage_02_1_postprocessing")
        .set("module", "hlandmark")
        .set("labels", f"{os.environ['HOME']}/labels/hlandmarks.json")
        .set("settings", f"{os.environ['HOME']}/labels/hlandmark_settings.json")
    )

    # Stream filter marking the hand-landmark ML branch output as text metadata.
    mlf_02 = TextFilter()

    # Decodes model output tensors into metadata for downstream overlay.
    stage_02_2_postprocessing = (
        Element("qtimlpostprocess", "stage_02_2_postprocessing")
        .set("module", "tensor")
    )

    # Executes the ML model and attaches tensor outputs to each frame.
    stage_03_1_inferencing = (
        Element("qtimltflite", "stage_03_1_inferencing")
        .set("delegate", "gpu")
        .set("model", f"{os.environ['HOME']}/models/gesture_embedder.tflite")
    )

    # Executes the ML model and attaches tensor outputs to each frame.
    stage_03_2_inferencing = (
        Element("qtimltflite", "stage_03_2_inferencing")
        .set("delegate", "gpu")
        .set("model", f"{os.environ['HOME']}/models/canned_gesture_classifier.tflite")
    )

    # Decodes model output tensors into metadata for downstream overlay.
    stage_03_postprocessing = (
        Element("qtimlpostprocess", "stage_03_postprocessing")
        .set("module", "mobilenet")
        .set("labels", f"{os.environ['HOME']}/labels/gesture_rec.json")
    )

    # Stream filter marking the gesture-classification ML branch output as text metadata.
    mlf_03 = TextFilter()

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("gesture-recognition")
        .add(source)
        .add_stream_filter("videostream", videostream)
        .add(split1)

        .add(q1)
        .add(q2)
        .add(stage_01_preprocessing)
        .add(q3)
        .add(stage_01_inferencing)
        .add(stage_01_postprocessing)
        .add_stream_filter("mlf_01", mlf_01)
        .add(mlmuxer1)
        .add(q11)
        .add(metatransform)
        .add(split2)

        .add(q4)
        .add(q5)
        .add(mlmuxer2)
        .add(q12)
        .add(overlay)
        .add(display)
        .add(stage_02_preprocessing)
        .add(q6)
        .add(stage_02_inferencing)
        .add(split3)
        .add(q7)
        .add(q8)
        .add(q9)
        .add(q10)
        .add(stage_02_1_postprocessing)
        .add_stream_filter("mlf_02", mlf_02)
        .add(stage_02_2_postprocessing)
        .add(stage_03_1_inferencing)
        .add(stage_03_2_inferencing)
        .add(stage_03_postprocessing)
        .add_stream_filter("mlf_03", mlf_03)

        .link("source", "videostream", "split1")
        .link("split1", "mlmuxer1", "q11", "metatransform", "split2")
        .link("split1", "q2", "stage_01_preprocessing", "q3", "stage_01_inferencing", "q1", "stage_01_postprocessing", "mlf_01", "mlmuxer1")
        .link("split2", "mlmuxer2", "q12", "overlay", "display")
        .link("split2", "q5", "stage_02_preprocessing", "q6", "stage_02_inferencing", "split3")
        .link("split3", "q7", "stage_02_1_postprocessing", "mlf_02", "mlmuxer2")
        .link("split3", "q8", "stage_02_2_postprocessing", "q9", "stage_03_1_inferencing", "q10", "stage_03_2_inferencing", "q4", "stage_03_postprocessing", "mlf_03", "mlmuxer2")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
