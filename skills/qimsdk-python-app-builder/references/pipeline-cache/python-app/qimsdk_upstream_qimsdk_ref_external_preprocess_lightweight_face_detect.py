#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom lightweight-face-detection preprocess example (native qfd postprocess)."""

import argparse
import sys
import os


class HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\n{self.prog}: error: {message}\n")
        sys.exit(2)


# Base path for sample assets (media/, models/, labels/ live under it).
#
# Defaults to the standard sample location and can be overridden by passing a
# different base path as the first command-line argument.
parser = HelpOnErrorArgumentParser(
    description="QIMSDK reference app",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-m", "--model-base-path",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples",
                    help="Base path for models and labels")
parser.add_argument("-i", "--input-config",
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/face_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

from typing import List

import numpy as np

from qimsdk import Element, Pipeline, VideoFilter, TextFilter, MLVConverter

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> q0 -> mlmuxer
#      split -> q1 -> preprocessing(custom callback) -> q2 -> inferencing -> q3
#             -> postprocessing -> [mlf] -> q4 -> mlmuxer
#      mlmuxer -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs a lightweight (quantized) face detector with an external Python
#  preprocessing callback that resizes the NV12 luma plane into a single-channel
#  grayscale tensor, feeding the native postprocessing element, overlays the
#  detected faces, and displays the result through Wayland.


# Cache of (src_w, src_h, dst_w, dst_h) -> (src_y_idx, src_x_idx) gather
# indices. These only depend on frame/tensor geometry, which is constant for
# the life of the pipeline, so building them once and reusing them avoids
# re-deriving the same arrays on every frame.
_scale_idx_cache: dict = {}


def _scale_indices(src_w: int, src_h: int, dst_w: int, dst_h: int):
    key = (src_w, src_h, dst_w, dst_h)
    idx = _scale_idx_cache.get(key)
    if idx is None:
        src_y_idx = np.minimum(src_h - 1, (np.arange(dst_h) * src_h) // dst_h)
        src_x_idx = np.minimum(src_w - 1, (np.arange(dst_w) * src_w) // dst_w)
        idx = (src_y_idx, src_x_idx)
        _scale_idx_cache[key] = idx
    return idx


def convert_nv12_to_y8(planes: List[memoryview], info, destination, tensor: np.ndarray) -> bool:
    # Converts one NV12 blit into the model's [1, H, W, 1] uint8 grayscale
    # input tensor by nearest-neighbor resizing the luma (Y) plane only.
    #
    # face_det_lite_w8a8.tflite takes raw uint8 luma, so there is no color
    # conversion and no normalization step: the Y samples are copied through
    # as-is. The NV12 chroma (UV) plane is therefore never read, and only the
    # first plane is required.
    #
    # Writes directly into `tensor` (a writable, in-place view onto the
    # preprocess element's output GstMLFrame). The SDK re-validates the block
    # after the callback returns, so the tensor must be filled in place.
    #
    # Nearest-neighbor scaling keeps the example dependency-free. A model
    # trained with bilinear or letterbox preprocessing will lose some accuracy
    # here; match the model's training-time resize for best results.

    width, height = info.width, info.height

    # Only the luma plane is consumed, so a single plane is enough. The channel
    # count is checked too: a 3-channel tensor would leave two thirds unwritten.
    if (tensor.ndim != 4 or tensor.shape[0] != 1 or tensor.shape[3] != 1
            or len(planes) < 1):
        print(
            f"preprocess failed: invalid input/output contract "
            f"| tensor.shape={tensor.shape} | planes={len(planes)} "
            f"| image.wh={width}x{height}"
        )
        return False

    # The C++ example checks MLTensorType explicitly; the NumPy view carries
    # the same information, and a mismatch here would silently write values
    # the model cannot interpret.
    if tensor.dtype != np.uint8:
        print(f"preprocess failed: expected uint8 tensor | dtype={tensor.dtype}")
        return False

    out_h, out_w = tensor.shape[1], tensor.shape[2]
    if out_h == 0 or out_w == 0:
        print(f"preprocess failed: zero output size | out_wh={out_w}x{out_h}")
        return False

    y_stride = info.stride[0]
    y_plane = np.frombuffer(planes[0], dtype=np.uint8).reshape(-1, y_stride)[:height, :width]

    dst_x, dst_y, dst_w, dst_h = 0, 0, out_w, out_h
    if destination is not None:
        dst_x = max(0, destination.x)
        dst_y = max(0, destination.y)
        dst_w = max(1, min(destination.w, out_w - dst_x))
        dst_h = max(1, min(destination.h, out_h - dst_y))

    src_y_idx, src_x_idx = _scale_indices(width, height, dst_w, dst_h)

    # Row-select then column-select instead of a single np.ix_() outer-product
    # gather - equivalent result, but avoids materializing the full 2D index
    # grid np.ix_() builds, which dominates per-frame cost.
    y_sampled = y_plane[src_y_idx, :][:, src_x_idx]

    # Letterbox padding: zero the tensor only when the blit leaves part of the
    # destination uncovered. On full coverage every pixel is overwritten below,
    # so the fill would be wasted work.
    if dst_w != out_w or dst_h != out_h:
        tensor.fill(0)
    tensor[0, dst_y:dst_y + dst_h, dst_x:dst_x + dst_w, 0] = y_sampled

    return True


def preprocess_callback(blits, output) -> bool:
    print(
        f"[external-preprocess][face-detect] called: "
        f"blits={len(blits)}, tensors={output.info.n_tensors}"
    )

    if not blits or output.info.n_tensors == 0:
        print(
            f"preprocess failed: empty blits or tensors "
            f"| blits={len(blits)} | tensors={output.info.n_tensors}"
        )
        return False

    # Only the first blit is converted; this example assumes the single-frame
    # batching that qtimlvconverter uses by default.
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

    ok = convert_nv12_to_y8(
        planes, info, first_blit.destination, tensor
    )
    if not ok:
        print("preprocess failed: conversion routine failed")
    return ok


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", input_config)
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

    # Restricts the decoded stream to NV12 before it reaches the tee.
    vf = VideoFilter().format("NV12")

    # Splits decoded frames into display and ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the display branch. Without this queue both
    # tee branches are served by one thread and the ML branch stalls display.
    q0 = Element("queue", "q0")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Converts raw video frames into model input tensor format via a
    # custom Python callback.
    #
    # engine=none disables internal preprocessing path in qtimlvconverter.
    preprocessing = (
        MLVConverter("preprocessing")
        .set(engine="none")
        .set_handler(preprocess_callback)
    )

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Executes the ML model and attaches tensor outputs to each frame.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "gpu")
        .set("model", f"{model_base_path}/models/face_det_lite_w8a8.tflite")
    )

    # Queues tensor outputs before postprocessing.
    q3 = Element("queue", "q3")

    # Decodes model output tensors into face-detection metadata.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "qfd")
        .set("labels", f"{model_base_path}/labels/qfd-labels.json")
    )

    # Restricts the postprocessing output to a text metadata stream.
    mlf = TextFilter()

    # Merges metadata produced by the ML branch with original video frames.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Queues metadata into the muxer's ML sink pad.
    q4 = Element("queue", "q4")

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied to branch the tee into the display and
    # ML metadata branches, and to merge them back at the metamux.
    pipeline = (
        Pipeline("ml-external-preprocess-lightweight-face-detect")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(split)
        .add(q0)
        .add(q1)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(q3)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(q4)
        .add(mlmuxer)
        .add(overlay)
        .add(display)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "q0", "mlmuxer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing",
              "mlf", "q4", "mlmuxer")
        .link("mlmuxer", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    if "HOME" not in os.environ:
        raise EnvironmentError("Error: HOME environment variable is not set.")

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
