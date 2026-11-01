#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom image-classification postprocess example."""

import argparse
import sys
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
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/classification_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML

from qimsdk import Pipeline, VideoFilter, TextFilter, MLPostprocess, ImageClassifications

K_DEFAULT_THRESHOLD = 0.70

DEFAULT_NAME = "unknown"
DEFAULT_COLOR = 0x00FF00FF # GREEN


def load_labels(path: str) -> List[Dict[str, object]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    labels: List[Dict[str, object]] = []
    for entry in raw:
        idx = int(entry.get("id", -1))
        if idx < 0:
            continue

        while len(labels) <= idx:
            labels.append({"name": "", "color": 0x00FF00FF})

        color_value = entry.get("color", "0x00FF00FF")
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

LABELS = load_labels(f"{model_base_path}/labels/resnet101.json")

label_array = np.array([l["name"] for l in LABELS], dtype=str)
color_array = np.array([l["color"] for l in LABELS], dtype=np.uint32)


def _softmax_stable(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last dimension."""
    m = np.max(logits, axis=-1, keepdims=True)
    ex = np.exp(logits - m)
    return ex / np.sum(ex, axis=-1, keepdims=True)


def classification_callback(mlframe, mlparams, classifications: ImageClassifications):

    print(
        f"[external-postprocess][classification] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch

    # 1) Get reshaped tensor
    tensor = mlframe.get_tensor(batch_idx)

    # 2) Shape handling [1][N] -> [N]
    if tensor.ndim == 2 and tensor.shape[0] == 1:
        logits = tensor[0]
    else:
        raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

    # 3) Softmax
    probs = _softmax_stable(logits.astype(np.float32))  # vector of length N

    # 4) Top-k, threshold (percent)
    #    For single best class only, set k=1.
    k = min(5, probs.shape[0])  # top-5
    top_idx = np.argsort(probs)[::-1][:k]

    confidence_pcts = probs[top_idx] * 100.0
    valid_mask = confidence_pcts >= (K_DEFAULT_THRESHOLD * 100)

    valid_idxs = top_idx[valid_mask]
    confidence_pcts = confidence_pcts[valid_mask]

    names  = np.full(valid_idxs.shape, DEFAULT_NAME, dtype=object)
    colors = np.full(valid_idxs.shape, DEFAULT_COLOR, dtype=np.uint32)

    mask = valid_idxs < len(label_array)
    names[mask]  = label_array[valid_idxs[mask]]
    colors[mask] = color_array[valid_idxs[mask]]

    # 5) Emit Classifications
    for conf, name, color, cls_idx in \
            zip(confidence_pcts, names, colors, valid_idxs):
        classification = GstQtiML.Classification()

        classification.confidence = float(conf)
        classification.name  = str(name)
        classification.color = np.uint32(color)

        # Debug print for parity checks
        print(f"[IC] cls={cls_idx} prob={conf:.2f}%")

        # print(f"Appending classification: {classification}")
        classifications.append(classification)

    return True

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> mlmuxer
#      split -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> postprocessing(custom callback) -> [mlf] -> mlmuxer -> q4 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs image classification with an external Python postprocessing callback,
#  overlays the classification result, and displays it through Wayland.

def create_and_execute_pipeline() -> None:

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Elements are created on the fly as they are added, and linking is
    # implicit, following the order in which elements are added.
    #
    # async=false enforce state transition to ensure the buffers are returned on time.
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    postprocessing = (
        MLPostprocess("postprocessing")
        .set(results=1)
        .set_handler(classification_callback)
    )

    pipeline = (
        Pipeline("ml-external-classification")
        .add("filesrc", "src", "location", input_config)
        .add("qtdemux", "demux")
        .add("h264parse", "parse")
        .add("v4l2h264dec", "decoder", "capture-io-mode", 4, "output-io-mode", 4)
        .add_stream_filter("vf", VideoFilter().format("NV12"))
        .add("tee", "split")
        .add("queue", "q1")
        .add("qtimlvconverter", "preprocessing")
        .add("queue", "q2")
        .add("qtimltflite", "inferencing",
            "delegate", "external",
            "external-delegate-path", "libQnnTFLiteDelegate.so",
            "external-delegate-options", "QNNExternalDelegate,backend_type=htp;",
            "model", f"{model_base_path}/models/Resnet101_Quantized.tflite")
        .add("queue", "q3")
        .add(postprocessing)
        .add_stream_filter("mlf", TextFilter())
        .add("qtimetamux", "mlmuxer")
        .add("queue", "q4")
        .add("qtivoverlay", "overlay")
        .add("waylandsink", "display", "fullscreen", True)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "mlmuxer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf", "mlmuxer", "q4", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
