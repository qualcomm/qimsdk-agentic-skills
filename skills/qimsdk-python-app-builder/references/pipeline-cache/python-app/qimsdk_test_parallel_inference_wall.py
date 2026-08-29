#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Parallel inferencing Python app: one decode, four AI branches, 2x2 grid display.

A single MP4 file is decoded once with the Qualcomm hardware decoder and teed
into four independent AI branches that each run a different model:

  - Branch A: image classification (Inception-v3, module=mobilenet)
  - Branch B: pose estimation (HRNet, module=hrnet)
  - Branch C: object detection (YOLOX, module=yolov8)
  - Branch D: semantic segmentation (DeepLabV3+, module=deeplab-argmax)

Branches A-C overlay their metadata with qtimetamux + qtivoverlay (Topology A).
Branch D renders an RGBA mask that is alpha-blended over its own passthrough
video with a dedicated qtivcomposer (Topology B), since segmentation output is
video-only. All four branches then feed one fixed-position top-level
qtivcomposer that tiles them into a 2x2 grid on a single display sink.
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

# Branch A - Image classification (Inception-v3 w8a8)
CLASS_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/inception_v3_w8a8.tflite"
CLASS_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/mobilenet.json"

# Branch B - Pose estimation (HRNet w8a8)
POSE_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/hrnetpose_w8a8.tflite"
POSE_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet.json"
POSE_SETTINGS = f"{HOME}/Downloads/qimsdk_samples/labels/hrnet_settings.json"

# Branch C - Object detection (YOLOX w8a8)
DET_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/yolox_w8a8.tflite"
DET_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/yolov8.json"

# Branch D - Semantic segmentation (DeepLabV3-Plus-MobileNet w8a8)
SEG_MODEL = f"{HOME}/Downloads/qimsdk_samples/models/deeplabv3_plus_mobilenet_w8a8.tflite"
SEG_LABELS = f"{HOME}/Downloads/qimsdk_samples/labels/dv3-argmax.json"
SEG_ALPHA = 0.5

# HTP/NPU external delegate shared by all four TFLite inference stages. Since
# all four branches run simultaneously (high-concurrency parallel HTP/NPU
# workload), htp_performance_mode=2 is set on every branch's delegate options.
DELEGATE_PATH = "libQnnTFLiteDelegate.so"
DELEGATE_OPTIONS = (
    "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;"
)

# 2x2 grid geometry on a 1920x1080 canvas.
TILE_W, TILE_H = 960, 540
GRID_POSITIONS = [(0, 0), (TILE_W, 0), (0, TILE_H), (TILE_W, TILE_H)]


def create_and_execute_pipeline() -> None:

    # --- Single decode, shared by all four branches ---

    # Reads the encoded MP4 file from disk.
    source = Element("filesrc", "source").set("location", INPUT_FILE)

    # Demuxes the MP4 container to extract the H.264 elementary stream.
    demux = Element("qtdemux", "demux")

    # Parses the H.264 stream for the hardware decoder.
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4, "output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required to decouple the decoder.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before the single input is teed out.
    # Pin the resolution (matches the working 06_object_detection app): without a
    # defined frame size the detection bbox overlay coordinates scale wrong and the
    # box blows up to the whole tile.
    vf = VideoFilter().format("NV12").resolution(1920, 1080)

    # Single-input tee shared by all four AI branches (one decode, four models).
    main_tee = Element("tee", "main_tee")

    pipeline = Pipeline("parallel-inferencing-grid")
    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(main_tee)

    # --- Branch A: image classification (quadrant 0, top-left) ---

    q_a_pass = Element("queue", "q_a_pass")
    # Force sole buffer ownership before the overlay/mux writes in-place. The
    # parallel seg_mix + top-level grid qtivcomposers hold the tee buffers for
    # stream sync (refcount > 1), so without a copy the overlay draws nothing.
    tf_a = Element("qtivtransform", "tf_a")
    vf_a_pass = VideoFilter().format("NV12")
    q_a_ai = Element("queue", "q_a_ai")
    pre_a = Element("qtimlvconverter", "class_pre")
    infer_a = (
        Element("qtimltflite", "class_infer")
        .set("model", CLASS_MODEL)
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
    )
    post_a = (
        Element("qtimlpostprocess", "class_post")
        .set("module", "mobilenet")
        .set("labels", CLASS_LABELS)
        .set("settings", '{"confidence": 51.0}')
        .set("results", 5)
    )
    mlf_a = TextFilter()
    mux_a = Element("qtimetamux", "class_mux")
    overlay_a = Element("qtivoverlay", "class_overlay")
    q_a_out = Element("queue", "q_a_out")

    pipeline.add(q_a_pass)
    pipeline.add(tf_a)
    pipeline.add_stream_filter("vf_a_pass", vf_a_pass)
    pipeline.add(q_a_ai)
    pipeline.add(pre_a)
    pipeline.add(infer_a)
    pipeline.add(post_a)
    pipeline.add_stream_filter("mlf_a", mlf_a)
    pipeline.add(mux_a)
    pipeline.add(overlay_a)
    pipeline.add(q_a_out)

    # --- Branch B: pose estimation (quadrant 1, top-right) ---

    q_b_pass = Element("queue", "q_b_pass")
    tf_b = Element("qtivtransform", "tf_b")
    vf_b_pass = VideoFilter().format("NV12")
    q_b_ai = Element("queue", "q_b_ai")
    pre_b = Element("qtimlvconverter", "pose_pre")
    infer_b = (
        Element("qtimltflite", "pose_infer")
        .set("model", POSE_MODEL)
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
    )
    post_b = (
        Element("qtimlpostprocess", "pose_post")
        .set("module", "hrnet")
        .set("labels", POSE_LABELS)
        .set("settings", POSE_SETTINGS)
        .set("results", 2)
    )
    mlf_b = TextFilter()
    mux_b = Element("qtimetamux", "pose_mux")
    overlay_b = Element("qtivoverlay", "pose_overlay")
    q_b_out = Element("queue", "q_b_out")

    pipeline.add(q_b_pass)
    pipeline.add(tf_b)
    pipeline.add_stream_filter("vf_b_pass", vf_b_pass)
    pipeline.add(q_b_ai)
    pipeline.add(pre_b)
    pipeline.add(infer_b)
    pipeline.add(post_b)
    pipeline.add_stream_filter("mlf_b", mlf_b)
    pipeline.add(mux_b)
    pipeline.add(overlay_b)
    pipeline.add(q_b_out)

    # --- Branch C: object detection (quadrant 2, bottom-left) ---

    q_c_pass = Element("queue", "q_c_pass")
    tf_c = Element("qtivtransform", "tf_c")
    vf_c_pass = VideoFilter().format("NV12")
    q_c_ai = Element("queue", "q_c_ai")
    pre_c = Element("qtimlvconverter", "det_pre")
    infer_c = (
        Element("qtimltflite", "det_infer")
        .set("model", DET_MODEL)
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
    )
    post_c = (
        Element("qtimlpostprocess", "det_post")
        .set("module", "yolov8")
        .set("labels", DET_LABELS)
        .set("settings", '{"confidence": 51.0}')
    )
    mlf_c = TextFilter()
    mux_c = Element("qtimetamux", "det_mux")
    overlay_c = Element("qtivoverlay", "det_overlay")
    # Size the detection tile to the grid quadrant BEFORE the grid composer.
    # qtivoverlay draws boxes in the frame it outputs; if the grid rescales that
    # frame afterwards the box geometry is distorted (box blows up). Fixing the
    # tile size here (as the segmentation branch does via seg_mix_vf) keeps boxes
    # correctly scaled. Pose/classification overlays are unaffected, but sizing
    # all three uniformly is cleanest.
    vf_c_out = VideoFilter().format("NV12").resolution(TILE_W, TILE_H)
    q_c_out = Element("queue", "q_c_out")

    pipeline.add(q_c_pass)
    pipeline.add(tf_c)
    pipeline.add_stream_filter("vf_c_pass", vf_c_pass)
    pipeline.add(q_c_ai)
    pipeline.add(pre_c)
    pipeline.add(infer_c)
    pipeline.add(post_c)
    pipeline.add_stream_filter("mlf_c", mlf_c)
    pipeline.add(mux_c)
    pipeline.add(overlay_c)
    pipeline.add_stream_filter("vf_c_out", vf_c_out)
    pipeline.add(q_c_out)

    # --- Branch D: semantic segmentation (quadrant 3, bottom-right) ---
    #
    # Segmentation renders an RGBA mask, not text metadata, so it uses the
    # direct-to-composer topology instead of qtimetamux/qtivoverlay: a local
    # two-pad qtivcomposer alpha-blends the rendered mask over its own
    # passthrough video before this branch's tile is handed to the grid.

    q_d_pass = Element("queue", "q_d_pass")
    q_d_ai = Element("queue", "q_d_ai")
    pre_d = Element("qtimlvconverter", "seg_pre")
    infer_d = (
        Element("qtimltflite", "seg_infer")
        .set("model", SEG_MODEL)
        .set("delegate", "external")
        .set("external-delegate-path", DELEGATE_PATH)
        .set("external-delegate-options", DELEGATE_OPTIONS)
    )
    post_d = (
        Element("qtimlpostprocess", "seg_post")
        .set("module", "deeplab-argmax")
        .set("labels", SEG_LABELS)
    )
    # Constrains the rendered mask before the local alpha-blend composer.
    # qtimlpostprocess only emits {RGBA, RGBx} (BGRA fails to link); leave size
    # unpinned (pinning it makes postproc caps fixation fail) — the seg_mix
    # composer scales the mask to match its passthrough pad.
    seg_mask_vf = VideoFilter().format("RGBA")
    q_d_mask = Element("queue", "q_d_mask")
    seg_mix = Element("qtivcomposer", "seg_mix")
    # Normalizes the alpha-blended segmentation tile back to NV12 before it
    # is fed into the top-level grid composer.
    seg_mix_vf = VideoFilter().format("NV12")
    q_d_out = Element("queue", "q_d_out")

    pipeline.add(q_d_pass)
    pipeline.add(q_d_ai)
    pipeline.add(pre_d)
    pipeline.add(infer_d)
    pipeline.add(post_d)
    pipeline.add_stream_filter("seg_mask_vf", seg_mask_vf)
    pipeline.add(q_d_mask)
    pipeline.add(seg_mix)
    pipeline.add_stream_filter("seg_mix_vf", seg_mix_vf)
    pipeline.add(q_d_out)

    # --- Top-level 2x2 grid composer and display ---

    grid = Element("qtivcomposer", "grid")
    q_grid_out = Element("queue", "q_grid_out")
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", False)
    )

    pipeline.add(grid)
    pipeline.add(q_grid_out)
    pipeline.add(display)

    # --- Linking ---

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "main_tee")

    # Branch A: passthrough + AI leg merge through qtimetamux, then overlay.
    # Passthrough goes through qtivtransform (buffer copy) so the overlay owns it.
    pipeline.link("main_tee", "q_a_pass", "tf_a", "vf_a_pass", "class_mux")
    pipeline.link("main_tee", "q_a_ai", "class_pre", "class_infer", "class_post", "mlf_a", "class_mux")
    pipeline.link("class_mux", "class_overlay", "q_a_out")

    # Branch B: passthrough + AI leg merge through qtimetamux, then overlay.
    pipeline.link("main_tee", "q_b_pass", "tf_b", "vf_b_pass", "pose_mux")
    pipeline.link("main_tee", "q_b_ai", "pose_pre", "pose_infer", "pose_post", "mlf_b", "pose_mux")
    pipeline.link("pose_mux", "pose_overlay", "q_b_out")

    # Branch C: passthrough + AI leg merge through qtimetamux, then overlay.
    pipeline.link("main_tee", "q_c_pass", "tf_c", "vf_c_pass", "det_mux")
    pipeline.link("main_tee", "q_c_ai", "det_pre", "det_infer", "det_post", "mlf_c", "det_mux")
    pipeline.link("det_mux", "det_overlay", "vf_c_out", "q_c_out")

    # Branch D: passthrough + rendered mask alpha-blend directly on seg_mix.
    pipeline.link("main_tee", "q_d_pass", "seg_mix")
    pipeline.link("main_tee", "q_d_ai", "seg_pre", "seg_infer", "seg_post", "seg_mask_vf", "q_d_mask", "seg_mix")
    pipeline.link("seg_mix", "seg_mix_vf", "q_d_out")

    # Fixed 2x2 grid: quadrant order is class(0,0), pose(1,0), det(0,1), seg(1,1).
    pipeline.link("q_a_out", "grid")
    pipeline.link("q_b_out", "grid")
    pipeline.link("q_c_out", "grid")
    pipeline.link("q_d_out", "grid")
    pipeline.link("grid", "q_grid_out", "display")

    # Alpha-blends the segmentation mask (sink_1) over its passthrough video
    # (sink_0) on the local seg_mix composer.
    seg_mix_el = pipeline.get("seg_mix")
    seg_mix_el.input(1).set("alpha", SEG_ALPHA)

    # Places each branch's tile at its fixed quadrant on the top-level grid.
    grid_el = pipeline.get("grid")
    for pad_index, (x, y) in enumerate(GRID_POSITIONS):
        grid_el.input(pad_index).set("position", [x, y])
        grid_el.input(pad_index).set("dimensions", [TILE_W, TILE_H])

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
