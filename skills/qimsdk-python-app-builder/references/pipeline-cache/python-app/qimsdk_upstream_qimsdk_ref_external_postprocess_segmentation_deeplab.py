#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom image-segmentation postprocess example."""

import argparse
import sys
import math
import json
import numpy as np
from typing import Dict, List
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
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML

from qimsdk import Element, Pipeline, VideoFilter, MLPostprocess, Segmentations


def load_labels(path: str) -> List[Dict[str, object]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    labels: List[Dict[str, object]] = []
    for entry in raw:
        idx = int(entry.get("id", -1))
        if idx < 0:
            continue

        while len(labels) <= idx:
            labels.append({"name": "", "color": 0x00000000})

        color_value = entry.get("color", "0x00000000")
        if isinstance(color_value, str):
            color = int(color_value, 16)
        else:
            color = int(color_value)

        labels[idx] = {
            "name": str(entry.get("label", "")),
            "color": color,
        }

    return labels


if "HOME" not in os.environ:
    raise EnvironmentError("Error: HOME environment variable is not set.")

LABELS = load_labels(f"{model_base_path}/labels/dv3-argmax-labels.json")

# Precompute lookup tables (fast, C-backed indexing)
label_array = np.array([l["name"] for l in LABELS], dtype=object)
color_array = np.array([l["color"] for l in LABELS], dtype=np.uint32)


def get_qscale_from_meta(mlframe, index=0) -> float:
    mlmeta = GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)

    if mlmeta is not None and hasattr(mlmeta, "qscale"):
        return mlmeta.qscale
    else:
        print ("WARNING: mlmeta has no attribute named qscale; returning 1.0")
        return 1.0


def get_qoffset_from_meta(mlframe, index=0) -> float:
    mlmeta = GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)

    if mlmeta is not None and hasattr(mlmeta, "qoffset"):
        return mlmeta.qoffset
    else:
        print ("WARNING: mlmeta has no attribute named qoffset; returning 0.0")
        return 0.0


def get_params(mlparams):
  # Get tensor dims from mlparams
  ok_w, tensor_w = mlparams.get_uint("input-tensor-width")
  ok_h, tensor_h = mlparams.get_uint("input-tensor-height")

  if not (ok_h and ok_w):
    raise KeyError("Missing input tensor width/height in mlparams")

  # Get region from mlparams
  ok_x, region_x = mlparams.get_int("input-region-x")
  ok_y, region_y = mlparams.get_int("input-region-y")
  ok_w, region_w = mlparams.get_int("input-region-width")
  ok_h, region_h = mlparams.get_int("input-region-height")

  if not (ok_x and ok_y and ok_h and ok_w):
    raise KeyError("Missing input region width/height in mlparams")

  return (region_x, region_y, region_w, region_h), (tensor_w, tensor_h)


def squeze_batch(dims: list, tensor:np.ndarray):
    if dims[0] != 1:
        raise ValueError(f"Batch>1 not supported in this callback (got {dims[0]})")

    # Squeze first dimension
    return dims[1:], tensor[0]


def lround(x: float) -> int:
    """Match std::lround behavior"""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


def segmentation_callback(mlframe, mlparams, segmentations: Segmentations):

    print(
        f"[external-postprocess][segmentation] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch

    # --- Tensor extraction ----------------------------------------------------
    tensor = mlframe.get_tensor(batch_idx)
    dims   = tensor.shape

    # If no scores dimension, add 1
    if len(dims) == 3:
        dims = [dims[0], dims[1], dims[2], 1]

    # [B][H][W] -> [B][H][W][C]
    tensor = tensor.reshape(dims)

    # Remove batch dimension -> [H][W][C]
    if tensor.ndim == 4 and tensor.shape[0] == 1:
        indata = tensor[0]
    else:
        raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

    H, W, C = indata.shape

    # --- Region mapping -------------------------------------------------------
    # 1) Read model input HW (used to scale to pixels)
    region, resolution = get_params(mlparams)
    (region_x, region_y, region_w, region_h) = region
    (resolution_w, resolution_h) = resolution

    wscale = W / resolution_w
    hscale = H / resolution_h

    left   = lround(region_x * wscale)
    top    = lround(region_y * hscale)
    right  = lround((region_x + region_w) * wscale)
    bottom = lround((region_y + region_h) * hscale)

    # Clamp just in case (defensive programming)
    left   = max(0, min(left, W))
    right  = max(0, min(right, W))
    top    = max(0, min(top, H))
    bottom = max(0, min(bottom, H))

    # --- ROI extraction -------------------------------------------------------
    roi = indata[top:bottom, left:right, :]

    # Argmax model ? IDs already in channel 0
    ids = roi[..., 0].astype(np.int64)   # (H, W)

    # --- Vectorized label/color mapping (C-backed) ----------------------------
    labels_roi = label_array[ids]
    colors_roi = color_array[ids]

    # --- Flatten for GObject --------------------------------------------------
    labels_flat = labels_roi.ravel().tolist()
    colors_flat = colors_roi.ravel().tolist()

    # --- Output object --------------------------------------------------------
    segmentation = GstQtiML.Segmentation()
    segmentation.n_rows = bottom - top
    segmentation.n_columns = right - left
    segmentation.labels = labels_flat
    segmentation.colors = colors_flat

    segmentations.append(segmentation)

    return True


#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> q8 -> composer -> display
#      split -> q4 -> preprocessing -> q5 -> inferencing -> q6 -> postprocessing -> [mlf] -> composer
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs external DeepLab v3 segmentation postprocessing callback logic, composites
#  the resulting segmentation mask over the original frame, and displays the
#  result through Wayland.


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

    # Composites multiple input streams into a single output frame.
    composer = Element("qtivcomposer", "composer")

    # Queues frames from tee into the composer branch.
    q8 = Element("queue", "q8")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Queues frames from tee into the ML branch.
    q4 = Element("queue", "q4")

    # Converts raw video frames into model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q5 = Element("queue", "q5")

    # Executes the ML model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{model_base_path}/models/dv3_argmax_int32.tflite")
    )

    # Queues inference output tensors before postprocessing.
    q6 = Element("queue", "q6")

    # Decodes model output tensors into segmentation masks via the external callback.
    postprocessing = (
        MLPostprocess("postprocessing")
        .set_handler(segmentation_callback)
    )

    # Restricts the postprocessing output to an RGBA metadata stream.
    mlf = VideoFilter().format("RGBA")

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied to branch the tee into the display and
    # ML segmentation branches, and to composite them back at the composer.
    pipeline = (
        Pipeline("ml-external-segmentation")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(split)

        .add(preprocessing)
        .add(q4)
        .add(inferencing)
        .add(q5)
        .add(postprocessing)
        .add(q6)
        .add_stream_filter("mlf", mlf)

        .add(composer)
        .add(q8)
        .add(display)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "q8", "composer", "display")
        .link("split", "q4", "preprocessing", "q5", "inferencing", "q6", "postprocessing", "mlf", "composer")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
