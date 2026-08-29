#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""HRNet pose estimation via the required two-stage cascade.

HRNet is a top-down pose model: it estimates keypoints on a *person ROI*, not a
full frame. Run single-stage on a full frame it produces no valid keypoints and
nothing overlays (verified on device). The QIM SDK model catalog, the cached
`pose_estimation.sh` reference, and the working daisychain app all run HRNet as a
two-stage cascade, so this app does the same:

Stage 1 (full frame): qtimlvconverter (image-batch-non-cumulative) -> qtimltflite
(person_foot_detection_w8a8, external/HTP) -> qtimlpostprocess (module=qpd) -> person ROIs.

Stage 2 (stage-1 ROIs): qtimlvconverter (roi-batch-cumulative) -> qtimltflite
(hrnetpose_w8a8, external/HTP) -> qtimlpostprocess (module=hrnet) -> skeleton keypoints.

Each stage's metadata is merged back with qtimetamux; a final qtivoverlay renders
the pose skeleton on the video for display.
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

INPUT_FILE = f"{HOME}/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4"

# Stage 1 — person/foot detection (provides the ROI HRNet needs).
STAGE1_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/person_foot_detection_w8a8.tflite"
STAGE1_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/foot_track_net.json"
STAGE1_SETTINGS = f"{HOME}/Downloads/qimsdk_samples/labels/foot_track_net_settings.json"

# Stage 2 — HRNet pose estimation on the stage-1 ROIs.
STAGE2_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/hrnetpose_w8a8.tflite"
STAGE2_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet.json"
STAGE2_SETTINGS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet_settings.json"

DELEGATE_PATH = "libQnnTFLiteDelegate.so"
DELEGATE_OPTIONS = "QNNExternalDelegate,backend_type=htp,log_level=(string)1;"


def create_and_execute_pipeline() -> None:

    # Source / hardware decode.
    src = Element("filesrc", "src").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parse = Element("h264parse", "parse")
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    q_dec = Element("queue", "q_dec")
    vf = VideoFilter().format("NV12")

    # --- Stage 1: person/foot detection on full frames ---
    split1 = Element("tee", "split1")
    q_stage1 = Element("queue", "q_stage1")
    stage1_preproc = (
        Element("qtimlvconverter", "stage_01_preproc")
        .set("mode", "image-batch-non-cumulative")
    )
    stage1_infer = (
        Element("qtimltflite", "stage_01_inference")
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
        .set("model", STAGE1_MODEL)
    )
    stage1_post = (
        Element("qtimlpostprocess", "stage_01_postproc")
        .set("module", "qpd")
        .set("labels", STAGE1_LABELS)
        .set("settings", STAGE1_SETTINGS)
        .set("results", 10)
    )
    mlf1 = TextFilter()
    metamux1 = Element("qtimetamux", "metamux_1")

    # --- Stage 2: HRNet pose estimation on stage-1 person ROIs ---
    split2 = Element("tee", "split2")
    q_stage2 = Element("queue", "q_stage2")
    # roi-batch-cumulative crops to the stage-1 person ROIs — this is what makes
    # the top-down HRNet model produce valid keypoints.
    stage2_preproc = (
        Element("qtimlvconverter", "stage_02_preproc")
        .set("mode", "roi-batch-cumulative")
    )
    stage2_infer = (
        Element("qtimltflite", "stage_02_inference")
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
        .set("model", STAGE2_MODEL)
    )
    stage2_post = (
        Element("qtimlpostprocess", "stage_02_postproc")
        .set("module", "hrnet")
        .set("labels", STAGE2_LABELS)
        .set("settings", STAGE2_SETTINGS)
        .set("results", 2)
    )
    mlf2 = TextFilter()
    metamux2 = Element("qtimetamux", "metamux_2")

    # Renders the pose skeleton keypoints over the video frame.
    overlay = Element("qtivoverlay", "overlay")
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    pipeline = (
        Pipeline("pose-estimation-cascade")
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
