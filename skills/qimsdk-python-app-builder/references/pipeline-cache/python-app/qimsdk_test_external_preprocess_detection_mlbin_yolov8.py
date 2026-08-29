#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom object-detection preprocess example using MLVideoTFLiteBin (native
YOLOv8 postprocess via the bin's own postprocess-module). Equivalent to
qimsdk_test_external_preprocess_detection_yolov8.py, but built on
qtimlvideotflitebin's combined preprocess+inference+postprocess bin instead
of assembling tee/queue/inferencing/postprocessing as separate elements."""

import os

from typing import List

import numpy as np

from qimsdk import Element, MLVideoTFLiteBin, Pipeline, VideoFilter

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> mlbin -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  and runs YOLOv8 object detection inside qtimlvideotflitebin using an externally
#  supplied preprocess callback (NV12 -> quantized NHWC int8) in place of the
#  bin's built-in preprocessing, then overlays the detections and displays the
#  result through Wayland.


# Cache of (src_w, src_h, dst_w, dst_h) -> (src_y_idx, src_x_idx, uv_y_idx,
# uv_x_idx) gather indices. These only depend on frame/tensor geometry, which
# is constant for the life of the pipeline, so building them once and reusing
# them avoids re-deriving the same arrays on every frame.
_scale_idx_cache: dict = {}


def _scale_indices(src_w: int, src_h: int, dst_w: int, dst_h: int):
    key = (src_w, src_h, dst_w, dst_h)
    idx = _scale_idx_cache.get(key)
    if idx is None:
        src_y_idx = np.minimum(src_h - 1, (np.arange(dst_h) * src_h) // dst_h)
        src_x_idx = np.minimum(src_w - 1, (np.arange(dst_w) * src_w) // dst_w)
        uv_y_idx = src_y_idx // 2
        # NV12 interleaves U and V at half resolution, so a chroma pair starts
        # at an even column. On an odd-width frame the last column's pair would
        # read past the visible row, so the V index is clamped below.
        uv_x_idx = (src_x_idx // 2) * 2
        idx = (src_y_idx, src_x_idx, uv_y_idx, uv_x_idx)
        _scale_idx_cache[key] = idx
    return idx


def convert_nv12_to_nhwc_i8(planes: List[memoryview], info, destination, tensor: np.ndarray) -> bool:
    # Converts one NV12 blit into the model's [1, H, W, 3] int8 input tensor.
    #
    # yolov8_det_quantized.tflite uses asymmetric int8 quantization with a
    # zero-point of 128, so each [0, 255] RGB sample is rebased by -128 into
    # [-128, 127] rather than normalized to a float range.
    #
    # Writes directly into `tensor` (a writable, in-place view onto the bin's
    # internal preprocess output GstMLFrame). The SDK re-validates the block
    # after the callback returns, so the tensor must be filled in place.
    #
    # Nearest-neighbor scaling keeps the example dependency-free. A model
    # trained with bilinear or letterbox preprocessing will lose some accuracy
    # here; match the model's training-time resize for best results.

    width, height = info.width, info.height

    if tensor.ndim != 4 or tensor.shape[0] != 1 or tensor.shape[3] != 3 or len(planes) < 2:
        print(
            f"preprocess failed: invalid input/output contract "
            f"| tensor.shape={tensor.shape} | planes={len(planes)} "
            f"| image.wh={width}x{height}"
        )
        return False

    # The C++ example checks MLTensorType explicitly; the NumPy view carries
    # the same information, and a mismatch here would silently write values
    # the model cannot dequantize.
    if tensor.dtype != np.int8:
        print(f"preprocess failed: expected int8 tensor | dtype={tensor.dtype}")
        return False

    out_h, out_w = tensor.shape[1], tensor.shape[2]
    if out_h == 0 or out_w == 0:
        print(f"preprocess failed: zero output size | out_wh={out_w}x{out_h}")
        return False

    y_stride = info.stride[0]
    uv_stride = info.stride[1]
    y_plane = np.frombuffer(planes[0], dtype=np.uint8).reshape(-1, y_stride)[:height, :width]
    uv_plane = np.frombuffer(planes[1], dtype=np.uint8).reshape(-1, uv_stride)[: (height + 1) // 2, :width]

    dst_x, dst_y, dst_w, dst_h = 0, 0, out_w, out_h
    if destination is not None:
        dst_x = max(0, destination.x)
        dst_y = max(0, destination.y)
        dst_w = max(1, min(destination.w, out_w - dst_x))
        dst_h = max(1, min(destination.h, out_h - dst_y))

    src_y_idx, src_x_idx, uv_y_idx, uv_x_idx = _scale_indices(width, height, dst_w, dst_h)

    # Row-select then column-select instead of a single np.ix_() outer-product
    # gather - equivalent result, but avoids materializing the full 2D index
    # grid np.ix_() builds, which dominated per-frame cost (measured ~20ms+ of
    # a ~27ms callback on-device for a 1920x1080 -> 640x640 conversion).
    y_sampled = y_plane[src_y_idx, :][:, src_x_idx].astype(np.int32)
    uv_rows = uv_plane[uv_y_idx, :]
    u_sampled = uv_rows[:, uv_x_idx].astype(np.int32)
    v_sampled = uv_rows[:, np.minimum(uv_x_idx + 1, width - 1)].astype(np.int32)

    # BT.601 limited-range YUV to full-range RGB, in fixed point (8-bit shift).
    #
    # In-place int32 arithmetic (needed range: values exceed int16 in the
    # unclamped intermediate sums, so int16 silently wraps around here).
    # y_sampled is reused as c_scaled for all three channels below.
    np.subtract(y_sampled, 16, out=y_sampled)
    np.maximum(y_sampled, 0, out=y_sampled)
    np.multiply(y_sampled, 298, out=y_sampled)

    np.subtract(u_sampled, 128, out=u_sampled)  # d
    np.subtract(v_sampled, 128, out=v_sampled)  # e

    # Letterbox padding: fill with the quantized zero-point (-128, i.e. RGB 0)
    # only when the blit leaves part of the destination uncovered. On full
    # coverage every pixel is overwritten below, so the fill would be wasted.
    if dst_w != out_w or dst_h != out_h:
        tensor.fill(-128)
    dst = tensor[0, dst_y:dst_y + dst_h, dst_x:dst_x + dst_w, :]

    r = v_sampled * 409
    r += y_sampled
    r += 128
    r >>= 8
    np.clip(r, 0, 255, out=r)
    dst[:, :, 0] = r - 128

    g = u_sampled * -100
    g -= v_sampled * 208
    g += y_sampled
    g += 128
    g >>= 8
    np.clip(g, 0, 255, out=g)
    dst[:, :, 1] = g - 128

    b = u_sampled * 516
    b += y_sampled
    b += 128
    b >>= 8
    np.clip(b, 0, 255, out=b)
    dst[:, :, 2] = b - 128

    return True


def preprocess_callback(blits, output) -> bool:
    print(
        f"[external-preprocess][detection] called: "
        f"blits={len(blits)}, tensors={output.info.n_tensors}"
    )

    if not blits or output.info.n_tensors == 0:
        print(
            f"preprocess failed: empty blits or tensors "
            f"| blits={len(blits)} | tensors={output.info.n_tensors}"
        )
        return False

    # Only the first blit is converted; this example assumes the single-frame
    # batching that the bin's preprocess uses by default.
    first_blit = blits[0]
    info = first_blit.info
    if info is None:
        print("preprocess failed: blit has no video info")
        return False

    planes = first_blit.planes()
    if not planes:
        print(
            f"preprocess failed: image has no planes "
            f"| wh={info.width}x{info.height}"
        )
        return False

    tensor = output.get_tensor(0)
    if tensor is None:
        print("preprocess failed: output tensor 0 is unavailable")
        return False

    ok = convert_nv12_to_nhwc_i8(
        planes, info, first_blit.destination, tensor
    )
    if not ok:
        print("preprocess failed: conversion routine failed")
    return ok


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", f"{os.environ['HOME']}/media/video.mp4")
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

    # Restricts the decoded stream to NV12 before it reaches the ML bin.
    vf = VideoFilter().format("NV12")

    # Runs YOLOv8 inference and postprocessing, with preprocessing delegated
    # to the external preprocess_callback instead of the bin's own converter.
    #
    # Configures the model, the hardware that executes it (delegate),
    # as well as the postprocessing algorithm and the label file.
    #
    # No explicit preprocess-engine=none is needed here: set_preprocess_handler()
    # sets engine=none on the bin's internal mlpreprocess element. The C++
    # example sets the property directly because its set_preprocess_handler()
    # does not.
    mlbin = (
        MLVideoTFLiteBin("mlbin")
        .set("inference-delegate", "external")
        .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("inference-model", f"{os.environ['HOME']}/models/yolov8_det_quantized.tflite")
        .set("postprocess-module", "yolov8")
        .set("postprocess-labels", f"{os.environ['HOME']}/labels/yolov8.json")
        .set_preprocess_handler(preprocess_callback)
    )

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Linking is implicit and follows the order in which elements are added.
    pipeline = (
        Pipeline("ml-external-preprocess-detection-mlbin")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
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
