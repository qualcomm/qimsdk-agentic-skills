#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Two-stage detection-classification daisy-chain Python app.

Stage 1 (full-frame): YOLOX object detection.
Stage 2 (ROI-based):  MobileNet classification on each Stage-1 detection.

Both stages run on HTP/NPU through the TFLite external delegate. Detection
boxes and classification labels are both overlaid on the original video.
"""

import os

from qimsdk import Element, Pipeline, VideoFilter, TextFilter


#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> q_dec -> [vf] -> split1 (tee)
#      split1 -> metamux_1
#      split1 -> q_stage1 -> stage_01_preproc -> stage_01_inference -> stage_01_postproc
#              -> [mlf1] -> metamux_1
#      metamux_1 -> split2 (tee)
#        split2 -> metamux_2
#        split2 -> q_stage2 -> stage_02_preproc -> stage_02_inference -> stage_02_postproc
#                -> [mlf2] -> metamux_2
#      metamux_2 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware
#  decoder, runs YOLOX full-frame object detection whose detections are
#  merged back into the frame metadata, then re-tees the merged stream into
#  a second-stage MobileNet classifier that runs on the Stage-1 detection
#  ROIs, merges the classification metadata in turn, and overlays both the
#  detection boxes and classification labels before displaying the result
#  through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/15s.mp4")
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
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Required queue immediately after hardware decode.
    q_dec = Element("queue", "q_dec")

    # Restricts the decoded stream to NV12 before it reaches the first tee.
    vf = VideoFilter().format("NV12")

    # Splits decoded frames into the metadata-passthrough and Stage-1
    # detection ML branches.
    split1 = Element("tee", "split1")

    # Queues frames from the first tee into the Stage-1 detection branch.
    q_stage1 = Element("queue", "q_stage1")

    # Converts full frames into the YOLOX model input tensor format.
    stage_01_preproc = (
        Element("qtimlvconverter", "stage_01_preproc")
        .set("mode", "image-batch-non-cumulative")
    )

    # Runs YOLOX object detection on the HTP/NPU via the TFLite external
    # delegate.
    stage_01_inference = (
        Element("qtimltflite", "stage_01_inference")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/yolox_w8a8.tflite")
    )

    # Decodes YOLOX detection output into bounding boxes/labels.
    #
    # YOLOX uses the yolov8 postprocess module. Confidence threshold is set
    # via the canonical "confidence" settings key.
    stage_01_postproc = (
        Element("qtimlpostprocess", "stage_01_postproc")
        .set("module", "yolov8")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/yolov8.json")
        .set("settings", "{\"confidence\": 51.0}")
    )

    # Restricts the Stage-1 postprocess output to a text metadata stream.
    mlf1 = TextFilter()

    # Merges Stage-1 detection metadata with the original video frames.
    metamux1 = Element("qtimetamux", "metamux_1")

    # Re-splits the merged stream into the metadata-passthrough and Stage-2
    # classification ML branches.
    split2 = Element("tee", "split2")

    # Queues frames from the second tee into the Stage-2 classification
    # branch.
    q_stage2 = Element("queue", "q_stage2")

    # Converts Stage-1 detection ROIs into the MobileNet classifier input
    # tensor format.
    stage_02_preproc = (
        Element("qtimlvconverter", "stage_02_preproc")
        .set("mode", "roi-batch-cumulative")
    )

    # Runs MobileNet classification on each Stage-1 ROI on the HTP/NPU via
    # the TFLite external delegate.
    stage_02_inference = (
        Element("qtimltflite", "stage_02_inference")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite")
    )

    # Decodes MobileNet classification output into class labels.
    #
    # mobilenet_v2 w8a8 uses the mobilenet postprocess module. Confidence
    # threshold is set via the canonical "confidence" settings key.
    stage_02_postproc = (
        Element("qtimlpostprocess", "stage_02_postproc")
        .set("module", "mobilenet")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/mobilenet.json")
        .set("settings", "{\"confidence\": 51.0}")
    )

    # Restricts the Stage-2 postprocess output to a text metadata stream.
    mlf2 = TextFilter()

    # Merges Stage-2 classification metadata with the Stage-1 merged
    # stream.
    metamux2 = Element("qtimetamux", "metamux_2")

    # Renders both detection boxes and classification labels over the video
    # frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream fullscreen on Wayland display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking branches each tee into a metadata-passthrough path
    # and an ML path, and merges them back at each metamux in turn.
    pipeline = Pipeline("daisychain-detection-classification")
    pipeline.add(src)
    pipeline.add(demux)
    pipeline.add(parse)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(split1)
    pipeline.add(q_stage1)
    pipeline.add(stage_01_preproc)
    pipeline.add(stage_01_inference)
    pipeline.add(stage_01_postproc)
    pipeline.add_stream_filter("mlf1", mlf1)
    pipeline.add(metamux1)
    pipeline.add(split2)
    pipeline.add(q_stage2)
    pipeline.add(stage_02_preproc)
    pipeline.add(stage_02_inference)
    pipeline.add(stage_02_postproc)
    pipeline.add_stream_filter("mlf2", mlf2)
    pipeline.add(metamux2)
    pipeline.add(overlay)
    pipeline.add(display)

    pipeline.link("src", "demux", "parse", "decoder", "q_dec", "vf", "split1")
    pipeline.link("split1", "metamux_1")
    pipeline.link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
                  "stage_01_postproc", "mlf1", "metamux_1")
    pipeline.link("metamux_1", "split2")
    pipeline.link("split2", "metamux_2")
    pipeline.link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference",
                  "stage_02_postproc", "mlf2", "metamux_2")
    pipeline.link("metamux_2", "overlay", "display")

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
