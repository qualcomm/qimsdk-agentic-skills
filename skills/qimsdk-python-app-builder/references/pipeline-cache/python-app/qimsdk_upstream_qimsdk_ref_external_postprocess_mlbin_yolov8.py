#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom object-detection postprocess example."""

import argparse
import sys
import json

import numpy as np
from typing import Dict, List, Optional, Tuple
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

from qimsdk import Element, Pipeline, VideoFilter, ObjectDetections, MLVideoTFLiteBin

# QAI Hub app defaults for YOLO NMS  (score=0.45, IoU=0.70)
SCORE_THRESHOLD_DEFAULT = 0.70
IOU_THRESHOLD_DEFAULT = 0.50

DEFAULT_NAME = "unknown"
DEFAULT_COLOR = 0x00FF00FF # GREEN

EXPECTED_TENSORS = 3


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

LABELS = load_labels(f"{model_base_path}/labels/yolov8.json")

name_array = np.array([l["name"] for l in LABELS], dtype=object)
color_array = np.array([l["color"] for l in LABELS], dtype=np.uint32)

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> mlbin -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs YOLOv8 object detection with the preprocessing and inference handled
#  inside the ML bin and an external Python postprocessing callback, overlays
#  the detected objects, and displays the result through Wayland.


def nms_single_class_np(
        boxes_xyxy: np.ndarray,
        scores: np.ndarray,
        iou_thr: float) -> List[int]:
    # Greedy NMS for one class.

    # boxes_xyxy: [M, 4]; scores: [M]
    left   = boxes_xyxy[:, 0]
    top    = boxes_xyxy[:, 1]
    right  = boxes_xyxy[:, 2]
    bottom = boxes_xyxy[:, 3]

    if left.size == 0:
        return []

    w = np.maximum(0.0, right - left)
    h = np.maximum(0.0, bottom - top)

    areas = w * h
    order = scores.argsort()[::-1]

    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(left[i], left[rest])
        yy1 = np.maximum(top[i], top[rest])
        xx2 = np.minimum(right[i], right[rest])
        yy2 = np.minimum(bottom[i], bottom[rest])
        w_i = np.maximum(0.0, xx2 - xx1)
        h_i = np.maximum(0.0, yy2 - yy1)

        inter = w_i * h_i
        union = areas[i] + areas[rest] - inter

        iou = np.where(union > 0.0, inter / union, 0.0)
        remain = np.where(iou <= iou_thr)[0]

        order = rest[remain]
    return keep


def filter_entries(
    bboxes: np.ndarray,  # [N, 4]
    scores: np.ndarray,  # [N]
    classes: np.ndarray, # [N] uint8/int
    score_thr: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Filter by score & position

    left   = bboxes[:, 0]
    top    = bboxes[:, 1]
    right  = bboxes[:, 2]
    bottom = bboxes[:, 3]

    valid_coords = (
        (left >= 0.0) & (left <= 1.0) &
        (top >= 0.0) & (top <= 1.0) &
        (right >= 0.0) & (right <= 1.0) &
        (bottom >= 0.0) & (bottom <= 1.0)
    )

    mask = (scores >= score_thr) & valid_coords

    scores = scores[mask]
    classes = classes[mask].astype(np.int32)
    left = left[mask]
    top = top[mask]
    right = right[mask]
    bottom = bottom[mask]

    bboxes = np.stack([left, top, right, bottom], axis=1)

    return bboxes, scores, classes


def class_aware_nms_np(
    bboxes: np.ndarray,  # [N, 4]
    scores: np.ndarray,  # [N]
    classes: np.ndarray, # [N] uint8/int
    iou_thr: float,
    max_det: Optional[int] = None
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Filter by NMS per class, sort by score desc, trim to max_det.

    kept_idx: List[int] = []
    for c in np.unique(classes):
        cls_mask = (classes == c)

        inds = np.nonzero(cls_mask)[0]

        keep_local = nms_single_class_np(
            boxes_xyxy=bboxes[cls_mask],
            scores=scores[cls_mask],
            iou_thr=iou_thr
        )

        kept_idx.extend(inds[keep_local].tolist())

    kept_idx = np.array(kept_idx, dtype=int)

    # Sort by score
    kept_idx = kept_idx[np.argsort(scores[kept_idx])[::-1]]

    # Limit number of detections
    if max_det is not None:
        kept_idx = kept_idx[:max_det]

    return bboxes[kept_idx], scores[kept_idx], classes[kept_idx]


def map_boxes_to_region(
    bboxes: np.ndarray,
    in_size_hw: Tuple[int, int],
    region_xywh: Tuple[int, int, int, int]
) -> np.ndarray:
    # Convert model-grid pixel boxes to region-relative normalized coords (0..1).

    left   = bboxes[:, 0]
    top    = bboxes[:, 1]
    right  = bboxes[:, 2]
    bottom = bboxes[:, 3]

    H_in, W_in = in_size_hw
    x0, y0, w_region, h_region = region_xywh

    if H_in <=0 or W_in <=0 or w_region <=0 or h_region <=0:
        # Nothing to do
        return bboxes

    # Scale to region size
    left   = (left - x0) / w_region
    top    = (top - y0) / h_region
    right  = (right - x0) / w_region
    bottom = (bottom - y0) / h_region

    return np.stack([left, top, right, bottom], axis=1)


def get_params(mlparams) -> Tuple[Tuple[int, int, int, int], Tuple[int, int]]:
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


def detection_callback(mlframe, mlparams, detections: ObjectDetections):

    print(
        f"[external-postprocess][detection] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch
    n_tensors = mlframe.info.n_tensors

    if mlframe.info.n_tensors != EXPECTED_TENSORS:
        raise KeyError(
            f"[GR] Expected {EXPECTED_TENSORS} tensors, "
            f"got {mlframe.info.n_tensors}"
        )

    # 1) Read model input HW (used to scale to pixels)
    region, resolution = get_params(mlparams)

    tensors = list()

    # 2) Fetch tensor
    for tensor_idx in range(n_tensors):
        tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)

        # Squeze batch dimension if present
        if tensor.ndim == 3 or tensor.ndim == 2 and tensor.shape[0] == 1:
            indata = tensor[0]
        else:
            raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

        tensors.append(indata.astype(np.float32))

    # 3) Verify dimensions
    if not (tensors[0].shape[0] == tensors[1].shape[0] == tensors[2].shape[0]):
        raise KeyError(f"Size of all three tensors must be equal!")

    # 4) Map tensors to values
    bboxes = tensors[0]
    scores = tensors[1]
    classes = tensors[2]

    # 5) Map to region
    bboxes = map_boxes_to_region(bboxes, resolution, region)

    # 6) Filter entries by score and position
    bboxes, scores, classes = filter_entries(bboxes, scores, classes,
                                             score_thr=SCORE_THRESHOLD_DEFAULT)

    # 7) Class-aware NMS using QAI Hub defaults
    kept_bboxes, kept_scores, kept_classes = class_aware_nms_np(
            bboxes, scores, classes,
            iou_thr=IOU_THRESHOLD_DEFAULT, max_det=None
    )

    # 8) Vectorize names and colors
    cls_ids = kept_classes.astype(int)

    names  = np.full(cls_ids.shape, DEFAULT_NAME, dtype=object)
    colors = np.full(cls_ids.shape, DEFAULT_COLOR, dtype=np.uint32)

    valid = (cls_ids >= 0) & (cls_ids < len(name_array))

    names[valid] = name_array[cls_ids[valid]]
    colors[valid] = color_array[cls_ids[valid]]

    kept_confidences = kept_scores * 100.0

    # 9) Emit detections (batch=1).
    for (left, top, right, bottom), conf, name, color in \
            zip(kept_bboxes, kept_confidences, names, colors):
        detection = GstQtiML.Detection()

        detection.left = float(left)
        detection.top = float(top)
        detection.right = float(right)
        detection.bottom = float(bottom)
        detection.confidence = float(conf)
        detection.name = str(name)
        detection.color = np.uint32(color)

        # print(f"Appending detection: {detection}")
        detections.append(detection)

    return True


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

    # Restricts the decoded stream to NV12 before it reaches the ML bin.
    vf = VideoFilter().format("NV12")

    # Runs preprocessing, inference, and external postprocessing inside mlbin.
    #
    # Configures the model, the hardware that executes it (delegate),
    # and attaches the custom Python postprocessing callback.
    mlbin = (
        MLVideoTFLiteBin("mlbin")
        .set("inference-delegate", "external")
        .set("inference-external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("inference-model", f"{model_base_path}/models/yolov8_det_quantized.tflite")
        .set_postprocess_handler(detection_callback)
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
        Pipeline("ml-external-detection")
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
