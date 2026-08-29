#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom object-detection postprocess example."""

import json

import numpy as np
from typing import Dict, List, Optional, Tuple
import os

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML

from qimsdk import Pipeline, VideoFilter, TextFilter, MLPostprocess, ObjectDetections

# QAI Hub app defaults for YOLO NMS  (score=0.45, IoU=0.70)
SCORE_THRESHOLD_DEFAULT = 0.45
IOU_THRESHOLD_DEFAULT = 0.70

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


LABELS = load_labels(f"{os.environ['HOME']}/labels/yolov8.json")

name_array = np.array([l["name"] for l in LABELS], dtype=object)
color_array = np.array([l["color"] for l in LABELS], dtype=np.uint32)


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


def dequantize_if_int(arr: np.ndarray, mlframe: float, index: float) -> np.ndarray:
    # If delegate produced integer logits, convert to float using
    # float = (arr - zero_point) * scale.

    if arr.dtype in (np.int8, np.uint8, np.int16, np.uint16, np.int32):
        qscale  = get_qscale_from_meta(mlframe, index)
        qoffset = get_qoffset_from_meta(mlframe, index)
        return (arr.astype(np.float32) - float(qoffset)) * float(qscale)

    return arr.astype(np.float32)


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

    # 2) Fetch tensor (and meta with qscale/qoffset)
    for tensor_idx in range(n_tensors):
        tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)

        # Squeze batch dimension if present
        if tensor.ndim == 3 or tensor.ndim == 2 and tensor.shape[0] == 1:
            indata = tensor[0]
        else:
            raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

        # Dequantize if needed
        indata = dequantize_if_int(indata, mlframe, tensor_idx)

        tensors.append(indata)

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

        detections.append(detection)

    return True

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> mlmuxer
#      split -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> postprocessing(custom callback) -> [mlf] -> mlmuxer -> q4 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs YOLOv8 object detection with an external Python postprocessing callback,
#  overlays the detected objects, and displays the result through Wayland.

def create_and_execute_pipeline() -> None:

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Elements are created on the fly as they are added, and linking is
    # implicit, following the order in which elements are added.
    #
    # async=false enforce state transition to ensure the buffers are returned on time.
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    postprocessing = MLPostprocess("postprocessing").set_handler(
        detection_callback,
    )

    pipeline = (
        Pipeline("ml-external-detection")
        .add("filesrc", "src", "location", f"{os.environ['HOME']}/media/video.mp4")
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
            "model", f"{os.environ['HOME']}/models/yolov8_det_quantized.tflite")
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
