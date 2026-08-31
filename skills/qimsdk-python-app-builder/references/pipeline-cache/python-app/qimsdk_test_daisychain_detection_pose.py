#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Two-stage pose estimation daisy-chain: person/foot detection -> HRNet pose.

Stage 1 (full frame): qtimlvconverter (image-batch-non-cumulative) -> qtimltflite
(person_foot_detection_w8a8, external/HTP delegate) -> qtimlpostprocess (module=qpd).

Stage 2 (ROI from stage 1): qtimlvconverter (roi-batch-cumulative) -> qtimltflite
(hrnetpose_w8a8, external/HTP delegate) -> qtimlpostprocess (module=hrnet).

Each stage's metadata is merged back into the video stream with its own
qtimetamux before the next split, and the final overlay renders both the
person/foot boxes and the pose skeleton keypoints on the display.
"""

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

HOME = os.environ["HOME"]

INPUT_FILE = f"{HOME}/Downloads/qimsdk_samples/media/15s.mp4"

STAGE1_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/person_foot_detection_w8a8.tflite"
STAGE1_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/foot_track_net.json"
STAGE1_SETTINGS = f"{HOME}/Downloads/qimsdk_samples/labels/foot_track_net_settings.json"

STAGE2_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/hrnetpose_w8a8.tflite"
STAGE2_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet.json"
STAGE2_SETTINGS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet_settings.json"

# HTP/NPU external delegate options shared by both TFLite inference stages.
DELEGATE_PATH = "libQnnTFLiteDelegate.so"
DELEGATE_OPTIONS = "QNNExternalDelegate,backend_type=htp,log_level=(string)1;"


def create_and_execute_pipeline() -> None:

    # Reads the input MP4 file as raw bytes.
    src = Element("filesrc", "src").set("location", INPUT_FILE)

    # Extracts elementary streams from the MP4 container.
    demux = Element("qtdemux", "demux")

    # Prepares the H.264 bitstream for the hardware decoder.
    parse = Element("h264parse", "parse")

    # Decodes the compressed H.264 stream into raw video frames using the
    # Qualcomm hardware decoder. DMA I/O modes avoid extra buffer copies.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode, before any filter/tee stage.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before branching into AI stages.
    vf = VideoFilter().format("NV12")

    # --- Stage 1: person/foot detection on full frames ---

    # Splits the normalized stream into a metadata-passthrough branch and the
    # stage-1 detection branch.
    split1 = Element("tee", "split1")

    # Queues frames from split1 into the stage-1 detection branch.
    q_stage1 = Element("queue", "q_stage1")

    # Converts full frames into the stage-1 detector model input tensor.
    stage1_preproc = (
        Element("qtimlvconverter", "stage_01_preproc")
        .set("mode", "image-batch-non-cumulative")
    )

    # Runs the person/foot detection model on the NPU via the TFLite external
    # (HTP) delegate.
    stage1_infer = (
        Element("qtimltflite", "stage_01_inference")
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
        .set("model", STAGE1_MODEL)
    )

    # Decodes stage-1 detection tensors into person/foot ROI metadata using
    # the built-in qpd module.
    stage1_post = (
        Element("qtimlpostprocess", "stage_01_postproc")
        .set("module", "qpd")
        .set("labels", STAGE1_LABELS)
        .set("settings", STAGE1_SETTINGS)
        .set("results", 10)
    )

    # Restricts stage-1 postprocess output to a text metadata stream.
    mlf1 = TextFilter()

    # Merges stage-1 detection metadata back into the main video stream.
    metamux1 = Element("qtimetamux", "metamux_1")

    # --- Stage 2: HRNet pose estimation on stage-1 person ROIs ---

    # Re-splits the merged stream into a metadata-passthrough branch and the
    # stage-2 pose branch.
    split2 = Element("tee", "split2")

    # Queues frames from split2 into the stage-2 pose branch.
    q_stage2 = Element("queue", "q_stage2")

    # Converts stage-1 person ROIs into the HRNet pose model input tensor,
    # accumulating ROI crops in batch form.
    stage2_preproc = (
        Element("qtimlvconverter", "stage_02_preproc")
        .set("mode", "roi-batch-cumulative")
    )

    # Runs the HRNet pose model on the NPU via the TFLite external (HTP)
    # delegate.
    stage2_infer = (
        Element("qtimltflite", "stage_02_inference")
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
        .set("model", STAGE2_MODEL)
    )

    # Decodes stage-2 pose tensors into skeleton keypoint metadata using the
    # built-in hrnet module.
    stage2_post = (
        Element("qtimlpostprocess", "stage_02_postproc")
        .set("module", "hrnet")
        .set("labels", STAGE2_LABELS)
        .set("settings", STAGE2_SETTINGS)
        .set("results", 2)
    )

    # Restricts stage-2 postprocess output to a text metadata stream.
    mlf2 = TextFilter()

    # Merges stage-2 pose metadata back into the main video stream.
    metamux2 = Element("qtimetamux", "metamux_2")

    # Renders both the detection boxes and the pose skeleton keypoints over
    # the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Renders the final overlaid stream on the display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    pipeline = (
        Pipeline("pose-estimation-daisychain")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("vf", vf)
        .add(split1)
        .add(q_stage1)
        .add(stage1_preproc)
        .add(stage1_infer)
        .add(stage1_post)
        .add_stream_filter("mlf1", mlf1)
        .add(metamux1)
        .add(split2)
        .add(q_stage2)
        .add(stage2_preproc)
        .add(stage2_infer)
        .add(stage2_post)
        .add_stream_filter("mlf2", mlf2)
        .add(metamux2)
        .add(overlay)
        .add(display)
        .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split1")
        .link("split1", "metamux_1")
        .link(
            "split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
            "stage_01_postproc", "mlf1", "metamux_1",
        )
        .link("metamux_1", "split2")
        .link("split2", "metamux_2")
        .link(
            "split2", "q_stage2", "stage_02_preproc", "stage_02_inference",
            "stage_02_postproc", "mlf2", "metamux_2",
        )
        .link("metamux_2", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
