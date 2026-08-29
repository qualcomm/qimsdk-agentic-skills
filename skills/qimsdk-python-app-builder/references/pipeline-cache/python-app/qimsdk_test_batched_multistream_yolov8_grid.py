#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""12-stream batched YOLOv8 inference (batch-4 model) from MP4 on Wayland.

12 independent decode chains feed 3 batch groups of 4 streams each.
Each batch group: 4x qtivtransform(NV12) -> qtibatch -> qtimlvconverter
                  -> qtimltflite(batch-4, HTP) -> qtimldemux
                  -> 4x qtimlpostprocess(yolov8) -> VideoFilter(RGBA)
                  -> 4x qtivcomposer detection layers.

Grid layout (1920x1080 canvas, 480x360 per tile, 4 cols x 3 rows):
  [ 0][ 1][ 2][ 3]   <- batch group 0
  [ 4][ 5][ 6][ 7]   <- batch group 1
  [ 8][ 9][10][11]   <- batch group 2

Each stream contributes two composer inputs at the same tile position:
  even pad: original NV12 video
  odd pad:  RGBA YOLOv8 detection layer, alpha-blended over the video
"""

import os
import resource

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    VideoFilter,
)

HOME = os.environ["HOME"]
INPUT_FILE  = f"{HOME}/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4"
MODEL_PATH  = f"{HOME}/Downloads/qimsdk_samples/models/yolov8_det_quantized_batch_4.tflite"
LABELS_PATH = f"{HOME}/Downloads/qimsdk_samples/labels/yolov8.json"

NUM_STREAMS  = 12
BATCH_SIZE   = 4
NUM_GROUPS   = NUM_STREAMS // BATCH_SIZE   # 3

TILE_W = 480   # 1920 / 4
TILE_H = 360   # 1080 / 3
COLS   = 4

DUAL_HTP = os.path.exists("/dev/fastrpc-cdsp1")
HTP_DEVICE_COUNT = 2 if DUAL_HTP else 1


def _delegate_options(group_idx: int) -> str:
    device_id = group_idx % HTP_DEVICE_COUNT
    return (
        "QNNExternalDelegate,backend_type=htp,"
        f"htp_device_id=(string){device_id},"
        "htp_performance_mode=(string)2,log_level=(string)1;"
    )


def _build_decode_stream(pipeline: Pipeline, i: int) -> str:
    """Build one decode chain and return its composer passthrough queue.

    The decoded NV12 stream is copied into a private passthrough branch before
    the composer. The separate batch branch feeds qtibatch.
    """
    source  = Element("filesrc",   f"source_{i}").set("location", INPUT_FILE)
    demux   = Element("qtdemux",   f"demux_{i}")
    parser  = Element("h264parse", f"parser_{i}")
    decoder = (
        Element("v4l2h264dec", f"decoder_{i}")
        .set("capture-io-mode", 4)
        .set("output-io-mode",  4)
    )
    q_dec = Element("queue", f"q_dec_{i}")
    vf    = VideoFilter().format("NV12")

    # qtivtransform provides a private DMA buffer per stream before batching.
    transform = Element("qtivtransform", f"transform_{i}")
    vf2       = VideoFilter().format("NV12")
    split     = Element("tee", f"split_{i}")

    # The composer holds buffers for synchronization, so copy the passthrough
    # branch again before it is submitted as the lower tile layer.
    q_pass       = Element("queue", f"q_pass_{i}")
    pass_copy    = Element("qtivtransform", f"pass_copy_{i}")
    pass_vf      = VideoFilter().format("NV12")
    q_pass_comp  = Element("queue", f"q_pass_comp_{i}")

    # The batch branch has its own queue before the request pad on qtibatch.
    q_batch = Element("queue", f"q_batch_{i}")

    pipeline.add(source).add(demux).add(parser).add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter(f"vf_{i}", vf)
    pipeline.add(transform)
    pipeline.add_stream_filter(f"vf2_{i}", vf2)
    pipeline.add(split)
    pipeline.add(q_pass).add(pass_copy)
    pipeline.add_stream_filter(f"pass_vf_{i}", pass_vf)
    pipeline.add(q_pass_comp)
    pipeline.add(q_batch)

    pipeline.link(
        f"source_{i}", f"demux_{i}", f"parser_{i}", f"decoder_{i}",
        f"q_dec_{i}", f"vf_{i}", f"transform_{i}", f"vf2_{i}",
        f"split_{i}",
    )

    # Original-video branch for the lower composer layer.
    pipeline.link(
        f"split_{i}", f"q_pass_{i}", f"pass_copy_{i}",
        f"pass_vf_{i}", f"q_pass_comp_{i}",
    )

    # AI branch is connected to the group's qtibatch below.
    pipeline.link(f"split_{i}", f"q_batch_{i}")

    return f"q_pass_comp_{i}"


def _build_batch_group(pipeline: Pipeline, group_idx: int,
                       stream_ids: list) -> None:
    """Build one batch group: 4 streams -> shared inference -> demux.

    Each demux output goes to its stream's YOLOv8 postprocess and RGBA
    detection layer. The layer is connected to the composer in a second pass.
    """

    # qtibatch aggregates 4 NV12 streams into one batched buffer.
    batch = Element("qtibatch", f"batch_{group_idx}")

    # Shared qtimlvconverter + batch-4 qtimltflite + qtimldemux.
    pre   = Element("qtimlvconverter", f"pre_{group_idx}")
    infer = (
        Element("qtimltflite", f"infer_{group_idx}")
        .set("model",    MODEL_PATH)
        .set("delegate", "external")
        .set("external-delegate-path",    "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", _delegate_options(group_idx))
    )
    demux_ml = Element("qtimldemux", f"mldemux_{group_idx}")

    pipeline.add(batch).add(pre).add(infer).add(demux_ml)

    # Wire each stream's AI branch into a distinct qtibatch sink pad.
    for stream_i in stream_ids:
        pipeline.link(f"q_batch_{stream_i}", f"batch_{group_idx}")

    # qtibatch -> shared converter -> inference -> demux.
    pipeline.link(
        f"batch_{group_idx}", f"pre_{group_idx}",
        f"infer_{group_idx}", f"mldemux_{group_idx}",
    )

    # Per-stream postprocess + RGBA detection layer off the demux outputs.
    for stream_i in stream_ids:
        post = (
            Element("qtimlpostprocess", f"post_{stream_i}")
            .set("module",   "yolov8")
            .set("labels",   LABELS_PATH)
            .set("settings", '{"confidence": 51.0}')
        )
        # RGBA output is required; leave resolution unpinned so composer pad
        # dimensions perform the tile scaling.
        render_vf = VideoFilter().format("RGBA")
        q_comp    = Element("queue", f"q_comp_{stream_i}")

        pipeline.add(post)
        pipeline.add_stream_filter(f"render_vf_{stream_i}", render_vf)
        pipeline.add(q_comp)

        # mldemux src_N -> postprocess -> RGBA -> detection composer layer.
        pipeline.link(
            f"mldemux_{group_idx}", f"post_{stream_i}",
            f"render_vf_{stream_i}", f"q_comp_{stream_i}",
        )


def create_and_execute_pipeline() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (10000, 10000))
    except (OSError, ValueError) as exc:
        print(f"Warning: failed to raise fd limit: {exc}")

    pipeline = Pipeline("multibatch-yolov8-12stream")

    # Grid composer and display.
    composer = Element("qtivcomposer", "composer")
    # Large multistream grid: sync=False avoids clock stalls.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync",       False)
    )
    pipeline.add(composer)
    pipeline.add(display)

    # Build 12 independent decode chains and retain each original-video feed.
    passthrough_feeds = []
    for i in range(NUM_STREAMS):
        passthrough_feeds.append(_build_decode_stream(pipeline, i))

    # Build 3 batch groups (4 streams each).
    for g in range(NUM_GROUPS):
        stream_ids = list(range(g * BATCH_SIZE, (g + 1) * BATCH_SIZE))
        _build_batch_group(pipeline, g, stream_ids)

    # Each stream uses two composer inputs at the same tile position:
    # even pad = original video, odd pad = RGBA detection layer.
    for i in range(NUM_STREAMS):
        col = i % COLS
        row = i // COLS
        x = col * TILE_W
        y = row * TILE_H
        passthrough_pad = 2 * i
        detection_pad = passthrough_pad + 1

        pipeline.link(passthrough_feeds[i], "composer")
        composer.input(passthrough_pad).set("position", [x, y])
        composer.input(passthrough_pad).set("dimensions", [TILE_W, TILE_H])

        pipeline.link(f"q_comp_{i}", "composer")
        composer.input(detection_pad).set("position", [x, y])
        composer.input(detection_pad).set("dimensions", [TILE_W, TILE_H])
        composer.input(detection_pad).set("alpha", 0.5)

    pipeline.link("composer", "display")

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
