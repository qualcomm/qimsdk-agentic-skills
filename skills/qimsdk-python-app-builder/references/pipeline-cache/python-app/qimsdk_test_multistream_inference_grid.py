#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""32-stream YOLOX object detection with per-stream (non-batched) inference
composed into a dynamic grid, supporting display, MP4 file, or RTSP output."""

import math
import os
import resource

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

# --- Configuration placeholders --------------------------------------------
INPUT_FILE = os.path.expandvars("$HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4")
MODEL_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.tflite")
LABELS_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/labels/yolov8.json")
CONFIDENCE = 51.0

NUM_STREAMS = 32
MAX_GRID_DIM = 6

# "display" (default), "file", or "rtsp"
SINK_MODE = "display"
OUTPUT_FILE = "<OUTPUT_FILE>.mp4"
RTSP_ADDRESS = "0.0.0.0"
RTSP_PORT = "8900"
RTSP_MPOINT = "/live"

# Canvas the grid is composited onto; each stream gets one tile of this size.
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080

EXTERNAL_DELEGATE_PATH = "libQnnTFLiteDelegate.so"
DUAL_HTP = os.path.exists("/dev/fastrpc-cdsp1")
HTP_DEVICE_COUNT = 2 if DUAL_HTP else 1


def _grid_dims(num_streams: int) -> tuple:
    """Grid scales with stream count: ceil(sqrt(N)) per side, capped at 6x6."""
    cols = min(MAX_GRID_DIM, math.ceil(math.sqrt(num_streams)))
    rows = min(MAX_GRID_DIM, math.ceil(num_streams / cols))
    return cols, rows


def _delegate_options(stream_index: int) -> str:
    # High-concurrency parallel HTP inference: raise performance mode and
    # round-robin across HTP devices when more than one is present.
    device_id = stream_index % HTP_DEVICE_COUNT
    return (
        "QNNExternalDelegate,backend_type=htp,"
        f"htp_device_id=(string){device_id},"
        "htp_performance_mode=(string)2,log_level=(string)1;"
    )


def _build_stream(pipeline: Pipeline, index: int) -> None:
    """Build one independent decode -> per-stream YOLOX detect -> overlay branch."""
    source = Element("filesrc", f"source_{index}").set("location", INPUT_FILE)
    demux = Element("qtdemux", f"demux_{index}")
    parser = Element("h264parse", f"parser_{index}")
    decoder = Element("v4l2h264dec", f"decoder_{index}")
    decoder.set("capture-io-mode", 4, "output-io-mode", 4)
    q_dec = Element("queue", f"q_dec_{index}")
    vf = VideoFilter().format("NV12")

    split = Element("tee", f"split_{index}")
    q_video = Element("queue", f"q_video_{index}")
    q_ai = Element("queue", f"q_ai_{index}")

    # Non-batched preprocess/inference/postprocess: one qtimlvconverter and
    # one qtimltflite instance per stream (no qtibatch/qtimldemux involved).
    pre = Element("qtimlvconverter", f"pre_{index}")
    infer = Element("qtimltflite", f"infer_{index}")
    infer.set("model", MODEL_PATH)
    infer.set("delegate", "external")
    infer.set("external-delegate-path", EXTERNAL_DELEGATE_PATH)
    infer.set("external-delegate-options", _delegate_options(index))

    post = Element("qtimlpostprocess", f"post_{index}")
    post.set("module", "yolov8")
    post.set("labels", LABELS_PATH)
    post.set("settings", '{"confidence": %s}' % CONFIDENCE)

    text = TextFilter()
    metamux = Element("qtimetamux", f"metamux_{index}")
    overlay = Element("qtivoverlay", f"overlay_{index}")
    q_comp = Element("queue", f"q_comp_{index}")

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter(f"vf_{index}", vf)
    pipeline.add(split)
    pipeline.add(q_video)
    pipeline.add(q_ai)
    pipeline.add(pre)
    pipeline.add(infer)
    pipeline.add(post)
    pipeline.add_stream_filter(f"text_{index}", text)
    pipeline.add(metamux)
    pipeline.add(overlay)
    pipeline.add(q_comp)

    pipeline.link(f"source_{index}", f"demux_{index}", f"parser_{index}",
                  f"decoder_{index}", f"q_dec_{index}", f"vf_{index}", f"split_{index}")
    pipeline.link(f"split_{index}", f"q_video_{index}", f"metamux_{index}")
    pipeline.link(f"split_{index}", f"q_ai_{index}", f"pre_{index}", f"infer_{index}",
                  f"post_{index}", f"text_{index}", f"metamux_{index}")
    pipeline.link(f"metamux_{index}", f"overlay_{index}", f"q_comp_{index}")


def _build_sink(pipeline: Pipeline, composer_name: str) -> None:
    """Attach the requested output sink to the composed grid frame."""
    if SINK_MODE == "file":
        vf_out = VideoFilter().format("NV12")
        encoder = Element("v4l2h264enc", "encoder")
        encoder.set("capture-io-mode", 4, "output-io-mode", 4)
        parser_out = Element("h264parse", "parser_out").set("config-interval", 1)
        mux = Element("mp4mux", "mux")
        sink = Element("filesink", "sink").set("location", OUTPUT_FILE)

        pipeline.add_stream_filter("vf_out", vf_out)
        pipeline.add(encoder)
        pipeline.add(parser_out)
        pipeline.add(mux)
        pipeline.add(sink)
        pipeline.link(composer_name, "vf_out", "encoder", "parser_out", "mux", "sink")
        pipeline.eos(True)
    elif SINK_MODE == "rtsp":
        vf_out = VideoFilter().format("NV12")
        encoder = Element("v4l2h264enc", "encoder")
        encoder.set("capture-io-mode", 4, "output-io-mode", 4)
        parser_out = Element("h264parse", "parser_out").set("config-interval", -1)
        rtsp = Element("qtirtspbin", "rtsp")
        rtsp.set("address", RTSP_ADDRESS, "port", RTSP_PORT, "mpoint", RTSP_MPOINT)

        pipeline.add_stream_filter("vf_out", vf_out)
        pipeline.add(encoder)
        pipeline.add(parser_out)
        pipeline.add(rtsp)
        pipeline.link(composer_name, "vf_out", "encoder", "parser_out", "rtsp")
    else:
        # Large multistream composer grid: sync=False avoids clock stalls.
        display = Element("waylandsink", "display")
        display.set("fullscreen", True, "sync", False)
        pipeline.add(display)
        pipeline.link(composer_name, "display")


def create_and_execute_pipeline() -> None:
    # High stream count needs headroom for per-stream file descriptors.
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (10000, 10000))
    except (OSError, ValueError) as exc:
        print(f"Warning: failed to raise fd limit; run 'ulimit -n 10000' first: {exc}")

    pipeline = Pipeline("multistream-yolox-grid")

    cols, rows = _grid_dims(NUM_STREAMS)
    tile_width = CANVAS_WIDTH // cols
    tile_height = CANVAS_HEIGHT // rows

    composer = Element("qtivcomposer", "composer")
    pipeline.add(composer)

    for index in range(NUM_STREAMS):
        _build_stream(pipeline, index)

    for index in range(NUM_STREAMS):
        pipeline.link(f"q_comp_{index}", "composer")
        col = index % cols
        row = index // cols
        composer.input(index).set("position", [col * tile_width, row * tile_height])
        composer.input(index).set("dimensions", [tile_width, tile_height])

    _build_sink(pipeline, "composer")

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
