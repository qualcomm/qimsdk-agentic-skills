#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom tensor postprocess example."""

import argparse
import sys
import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML

import json

import numpy as np
from typing import Dict, List, Tuple
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
                    default="0",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

from qimsdk import (Element, Pipeline, VideoFilter, TextFilter, MLPostprocess,
    ObjectDetections, Poses, Tensors, ImageClassifications)

#  Example pipeline:
#
#    src -> [vf] -> t_split_1
#      t_split_1 -> metamux_1
#      t_split_1 -> q_s1_pre -> stage_01_preproc -> q_s1_inf -> stage_01_inference
#                -> q_s1_post -> stage_01_postproc -> [mlf_s1] -> metamux_1
#
#    metamux_1 -> q_roi -> roi_transform -> t_split_2
#      t_split_2 -> metamux_2
#      t_split_2 -> q_s2_pre -> stage_02_preproc -> q_s2_inf -> stage_02_inference -> t_split_3
#
#    t_split_3 -> q_s2_post1 -> stage_02_1_postproc -> [mlf_s2_1] -> metamux_2
#    t_split_3 -> q_s2_post2 -> stage_02_2_postproc -> q_s3_1 -> stage_03_1_inference
#              -> q5 -> stage_03_2_inference -> q6 -> stage_03_postproc -> [mlf_s3] -> metamux_2
#
#    metamux_2 -> q7 -> overlay -> sink
#
#  The pipeline reads camera frames and runs a 3-stage cascade: stage 01 detects
#  palms (qtimlvconverter/qtimltflite/palmd_callback), whose detections are muxed
#  back onto the raw frame at metamux_1 and cropped to a hand ROI by roi_transform.
#  A second tee then fans that ROI-cropped stream into stage 02 (hand landmarks,
#  hlandmark_callback) and, from a third tee off the same inference, into stage 03
#  (tensor_callback reshapes stage 02's raw tensors for two more qtimltflite
#  inferences, then gesture_callback classifies the gesture). Both branch results
#  are muxed back at metamux_2 before overlay and display.


EXPECTED_DET_TENSORS = 2
EXPECTED_POSE_TENSORS = 4

DEFAULT_NAME   = "unknown"
DEFAULT_COLOR  = 0x00FF00FF # GREEN
LANDMARK_COLOR = 0xFF0000FF # RED

K_ANCHOR_SIZES = [8, 16, 16, 16]

SCORE_THRESHOLD_DEFAULT = 0.70
IOU_THRESHOLD_DEFAULT = 0.5
K_DEFAULT_THRESHOLD = 0.70

############################### LABELS ###############################

LANDMARK_NAMES = [
    {"id": 0, "name": "wrist_center"},
    {"id": 1, "name": "index_base"},
    {"id": 2, "name": "middle_base"},
    {"id": 3, "name": "ring_base"},
    {"id": 4, "name": "pinky_base"},
    {"id": 5, "name": "palm_center"},
    {"id": 6, "name": "thumb_base"},
]


CONNECTIONS = [
    {"id": 0, "connection": 17},
    {"id": 1, "connection": 0},
    {"id": 2, "connection": 1},
    {"id": 3, "connection": 2},
    {"id": 4, "connection": 3},
    {"id": 5, "connection": 0},
    {"id": 6, "connection": 5},
    {"id": 7, "connection": 6},
    {"id": 8, "connection": 7},
    {"id": 9, "connection": 5},
    {"id": 10, "connection": 9},
    {"id": 11, "connection": 10},
    {"id": 12, "connection": 11},
    {"id": 13, "connection": 9},
    {"id": 14, "connection": 13},
    {"id": 15, "connection": 14},
    {"id": 16, "connection": 15},
    {"id": 17, "connection": 13},
    {"id": 18, "connection": 17},
    {"id": 19, "connection": 18},
    {"id": 20, "connection": 19}
]


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

DET_LABELS = load_labels(f"{model_base_path}/labels/palmd_labels.json")

POSE_LABELS = load_labels(f"{model_base_path}/labels/hlandmarks.json")

CLS_LABELS = load_labels(f"{model_base_path}/labels/gesture_rec.json")

det_lmk_label_array = np.array([l["name"] for l in LANDMARK_NAMES], dtype=object)

pose_label_array = np.array([l["name"] for l in POSE_LABELS], dtype=str)
pose_color_array = np.array([l["color"] for l in POSE_LABELS], dtype=np.uint32)

cls_label_array = np.array([l["name"] for l in CLS_LABELS], dtype=str)
cls_color_array = np.array([l["color"] for l in CLS_LABELS], dtype=np.uint32)

############################### UTILS ###############################


def nms_single_class(
        boxes_xyxy: np.ndarray,
        landmarks: np.ndarray,
        confidences: np.ndarray,
        iou_thr: float) -> Tuple[np.ndarray, np.ndarray]:
    # Greedy NMS for one class.

    x1 = boxes_xyxy[:, 0]
    y1 = boxes_xyxy[:, 1]
    x2 = boxes_xyxy[:, 2]
    y2 = boxes_xyxy[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = confidences.argsort()[::-1]

    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)

        if order.size == 1:
            break

        rest = order[1:]

        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)

        inter = w * h
        iou = inter / (areas[i] +  areas[rest] - inter)

        order = rest[iou <= iou_thr]

    return boxes_xyxy[keep], landmarks[keep], confidences[keep]


def filter_entries(
        bboxes: np.ndarray,     # [N][4]
        landmarks: np.ndarray,  # [N][K][2]
        logits: np.ndarray,     # [N]
        score_thr: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
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

    confidences = 1 / (1 + np.exp(-logits))

    mask = (confidences >= score_thr) & valid_coords

    left = left[mask]
    top = top[mask]
    right = right[mask]
    bottom = bottom[mask]
    landmarks = landmarks[mask]
    confidences = confidences[mask] * 100

    bboxes = np.stack([left, top, right, bottom], axis=1)

    return bboxes, landmarks, confidences


def decode_anchors_to_normalized_xyxy(
        bboxes: np.ndarray,
        region: Tuple[int, int, int, int],
        anchors: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    P, L = bboxes.shape
    region_x, region_y, region_w, region_h = region

    anchor_idx = np.arange(P) // 2
    anchors_xy = anchors[anchor_idx] # [N, 2]

    cx = bboxes[:, 0] + anchors_xy[:, 0]
    cy = bboxes[:, 1] + anchors_xy[:, 1]
    w  = bboxes[:, 2]
    h  = bboxes[:, 3]

    left   = (cx - w / 2.0 - region_x) / region_w
    top    = (cy - h / 2.0 - region_y) / region_h
    right  = (cx + w / 2.0 - region_x) / region_w
    bottom = (cy + h / 2.0 - region_y) / region_h

    left   = np.maximum(left,   0.0)
    top    = np.maximum(top,    0.0)
    right  = np.minimum(right,  1.0)
    bottom = np.minimum(bottom, 1.0)

    boxes = np.stack([left, top, right, bottom], axis=1)

    K = (L - 4) // 2

    lmks = bboxes[:, 4:].reshape(P, K, 2)

    # Replace 0 with 1 for division
    lmks[:, :, 0] += anchors_xy[:, None, 0]
    lmks[:, :, 1] += anchors_xy[:, None, 1]

    x1 = cx - w / 2.0
    y1 = cy - h / 2.0

    safe_w = np.where(w == 0, 1, w)
    safe_h = np.where(h == 0, 1, h)

    lmks[:, :, 0] = (lmks[:, :, 0] - x1[:, None]) / safe_w[:, None]
    lmks[:, :, 1] = (lmks[:, :, 1] - y1[:, None]) / safe_h[:, None]

    lmks = np.clip(lmks, 0.0, 1.0)

    return boxes, lmks


def map_boxes_to_region(
        bboxes: np.ndarray,
        in_size_hw: Tuple[int, int],
        region_xywh: Tuple[int, int, int, int]) -> np.ndarray:
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


def generate_anchors(resolution):
    resolution_w, resolution_h = resolution

    anchors = list()
    for i in range(len(K_ANCHOR_SIZES)):
      for y in range(resolution_h // K_ANCHOR_SIZES[i]):
        for x in range(resolution_w // K_ANCHOR_SIZES[i]):
          cx = (x + 0.5) * K_ANCHOR_SIZES[i]
          cy = (y + 0.5) * K_ANCHOR_SIZES[i]
          anchors.append([cx, cy])

    return anchors


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


def map_coords_to_region(coords: np.ndarray,
        region: Tuple[int, int, int, int]) -> Tuple[np.ndarray, np.ndarray]:
    region_x, region_y, region_w, region_h = region

    if region_w == 0 or region_h == 0:
        raise KeyError("region width and/or height are 0!")

    x = coords[:, 0]
    y = coords[:, 1]

    kp_x = (x - region_x) / region_w
    kp_y = (y - region_y) / region_h

    kp_x = np.clip(kp_x, 0.0, 1.0)
    kp_y = np.clip(kp_y, 0.0, 1.0)

    return (kp_x, kp_y)


############################### CALLBACKS ###############################


def palmd_callback(mlframe, mlparams, detections: ObjectDetections):

    print(
        f"[external-postprocess][detection] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch
    n_tensors = mlframe.info.n_tensors

    if mlframe.info.n_tensors != EXPECTED_DET_TENSORS:
        raise KeyError(
            f"[GR] Expected {EXPECTED_DET_TENSORS} tensors, "
            f"got {mlframe.info.n_tensors}"
        )

    # 1) Read model input HW (used to scale to pixels)
    region, resolution = get_params(mlparams)

    tensors = list()

    # 2) Fetch tensor (and meta with qscale/qoffset)
    for tensor_idx in range(mlframe.info.n_tensors):
        tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)

        # Squeze batch dimension if present
        if tensor.ndim == 3 and tensor.shape[0]:
            indata = tensor[0]
        else:
            raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

        # Dequantize if needed
        indata = dequantize_if_int(indata, mlframe, tensor_idx)

        tensors.append(indata)

    if not hasattr(palmd_callback, "anchors"):
        palmd_callback.anchors = generate_anchors(resolution)

    anchors = palmd_callback.anchors

    # [N][K]
    bboxes = tensors[0]
    # [N][1] -> [N]
    logits = tensors[1].reshape(-1)

    # 5) Map to region
    # bboxes: [N][4]; landmarks: [N][K][2]
    bboxes_xyxy, landmarks = decode_anchors_to_normalized_xyxy(bboxes, region, anchors=np.asarray(anchors))

    # 6) Filter entries by score and position
    bboxes_xyxy, landmarks, confidences = filter_entries(bboxes_xyxy, landmarks, logits,
                                             score_thr=SCORE_THRESHOLD_DEFAULT)

    # 7) Class-aware NMS
    kept_bboxes, kept_landmarks, kept_confidences = nms_single_class(
            bboxes_xyxy, landmarks, confidences, iou_thr=IOU_THRESHOLD_DEFAULT
    )

    entry_name  = DET_LABELS[0]["name"]
    entry_color = DET_LABELS[0]["color"]

    for (left, top, right, bottom), conf, landmark in \
            zip(kept_bboxes, kept_confidences, kept_landmarks):
        detection = GstQtiML.Detection()

        detection.left = float(left)
        detection.top = float(top)
        detection.right = float(right)
        detection.bottom = float(bottom)

        detection.confidence = float(conf)
        detection.name  = str(entry_name)
        detection.color = np.uint32(entry_color)

        lmk_list = []

        for lmk_name, (lmk_x, lmk_y) in zip(det_lmk_label_array, landmark):
            lmk = GstQtiML.Keypoint()

            lmk.name = str(lmk_name)
            lmk.color = LANDMARK_COLOR
            lmk.x = float(lmk_x)
            lmk.y = float(lmk_y)

            lmk_list.append(lmk)

        detection.landmarks = lmk_list

        detections.append(detection)

    return True


def hlandmark_callback (mlframe, mlparams, poses: Poses):

    print(
        f"[external-postprocess][pose] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch
    n_tensors = mlframe.info.n_tensors

    if mlframe.info.n_tensors != EXPECTED_POSE_TENSORS:
        raise KeyError(
            f"[GR] Expected {EXPECTED_POSE_TENSORS} tensors, "
            f"got {mlframe.info.n_tensors}"
        )

    # 1) Read model input W/H and the region
    region, resolution = get_params(mlparams)

    tensors = list()

    # 2) Fetch tensor (and meta with qscale/qoffset)
    for tensor_idx in range(mlframe.info.n_tensors):
        tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)

        # Squeze batch dimension if present
        if tensor.ndim == 2 and tensor.shape[0] == 1:
            indata = tensor[0]
        else:
            raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

        # Dequantize if needed
        indata = dequantize_if_int(indata, mlframe, tensor_idx)

        tensors.append(indata)

    if tensors[0].shape[0] != tensors[3].shape[0]:
        raise KeyError ("Keypoint count of first and third tensor must be "
                        f"equal: {tensors[0].shape[0]} != {tensors[3].shape[0]}")

    # There are 3 coordinates per point - x, y, z -> Reshape to [N/3, 3 <x, y, z>]
    coordinates = tensors[0].reshape(-1, 3) # [N / 3][3]
    scores = tensors[1]
    confidence = scores[0]

    n_keypoints = len(coordinates)

    if (confidence < K_DEFAULT_THRESHOLD):
        return True

    x_coords, y_coords = map_coords_to_region(coordinates, region)

    pose = GstQtiML.Pose()

    pose.confidence = confidence

    confidence = confidence * 100

    idxs = np.arange(n_keypoints)
    valid = idxs < len(pose_label_array)

    names  = np.where(valid, pose_label_array.take(idxs, mode='clip'), DEFAULT_NAME)
    colors = np.where(valid, pose_color_array.take(idxs, mode='clip'), DEFAULT_COLOR)

    keypoints = []
    for x, y, name, color in zip(x_coords, y_coords, names, colors):
        kp = GstQtiML.Keypoint()

        kp.x = float(x)
        kp.y = float(y)
        kp.name  = str(name)
        kp.color = np.uint32(color)
        kp.confidence = confidence

        keypoints.append(kp)

    valid_connections = [
            (lk["id"], lk["connection"])
            for lk in CONNECTIONS
            if lk["id"] < len(keypoints) and lk["connection"] < len(keypoints)
    ]

    links = []

    # 8) Build edges where both endpoints pass threshold
    for lkp_idx, rkp_idx in valid_connections:
        link = GstQtiML.KeypointLink()
        link.l_kp = keypoints[lkp_idx]
        link.r_kp = keypoints[rkp_idx]
        link.color = DEFAULT_COLOR
        links.append(link)

    pose.name = "Pose"
    pose.color = DEFAULT_COLOR
    pose.keypoints = keypoints
    pose.links = links

    poses.append(pose)

    return True


def tensor_callback(mlframe, mlparams, tensors: Tensors):

    print(
        f"[external-postprocess][tensor] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch
    n_tensors = mlframe.info.n_tensors

    if mlframe.info.n_tensors != EXPECTED_POSE_TENSORS:
        raise KeyError(
            f"[GR] Expected {EXPECTED_POSE_TENSORS} tensors, "
            f"got {mlframe.info.n_tensors}"
        )

    # ---- Extract tensors directly ----

    landmarks_2d = mlframe.get_tensor(n_tensors * batch_idx + 0)   # [B][63]
    lm_conf      = mlframe.get_tensor(n_tensors * batch_idx + 1)   # [B][1]
    handedness   = mlframe.get_tensor(n_tensors * batch_idx + 2)   # [B][1]
    landmarks_3d = mlframe.get_tensor(n_tensors * batch_idx + 3)   # [B][63]

    batch_2d = landmarks_2d.shape[0]
    batch_3d = landmarks_3d.shape[0]

    landmarks_2d = landmarks_2d.reshape(batch_2d, -1, 3) # [B][21][3]
    landmarks_3d = landmarks_3d.reshape(batch_3d, -1, 3) # [B][21][3]

    # ---- Sanity checks ----
    if landmarks_2d.ndim != 3 or landmarks_2d.shape[-2:] != (21, 3):
        raise KeyError(f"[GR] Invalid 2D landmarks shape: {landmarks_2d.shape}")

    if landmarks_3d.ndim != 3 or landmarks_3d.shape[-2:] != (21, 3):
        raise KeyError(f"[GR] Invalid 3D landmarks shape: {landmarks_3d.shape}")

    if landmarks_2d.shape[0] != landmarks_3d.shape[0]:
        raise KeyError("[GR] Batch mismatch between 2D and 3D landmarks")

    # ---- Optional: normalize handedness shape ----
    # Some models output [B,1], others [B]
    if handedness.ndim == 2 and handedness.shape[1] == 1:
        handedness = handedness[:, 0]

    np.copyto(tensors.get_tensor(0), landmarks_2d)
    np.copyto(tensors.get_tensor(1), handedness)
    np.copyto(tensors.get_tensor(2), landmarks_3d)

    return True


def gesture_callback(mlframe, mlparams, classifications: ImageClassifications):

    print(
        f"[external-postprocess][classification] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch

    # 1) Get tensor
    tensor = mlframe.get_tensor(batch_idx)

    # Squeze batch dimension
    if tensor.ndim == 2 and tensor.shape[0] == 1:
        scores = tensor[0]
    else:
        raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

    n_inferences = scores.shape[0]

    valid_mask = scores >= K_DEFAULT_THRESHOLD

    valid_idxs = np.arange(n_inferences)[valid_mask]
    confidence_pcts = scores[valid_mask]

    names  = np.full(valid_idxs.shape, DEFAULT_NAME, dtype=object)
    colors = np.full(valid_idxs.shape, DEFAULT_COLOR, dtype=np.uint32)

    mask = valid_idxs < len(cls_label_array)
    names[mask]  = cls_label_array[valid_idxs[mask]]
    colors[mask] = cls_color_array[valid_idxs[mask]]

    # Fill the prediction table.
    for conf, name, color, cls_idx in \
            zip(confidence_pcts, names, colors, valid_idxs):
        classification = GstQtiML.Classification()

        classification.confidence = float(conf)
        classification.name  = str(name)
        classification.color = np.uint32(color)

        # Debug print for parity checks
        print(f"[IC] cls={cls_idx} prob={conf:.2f}%")

        classifications.append(classification)

    return True


############################### GSTREAMER ###############################


def create_and_execute_pipeline() -> None:

    # Captures frames from the camera source.
    src = Element("qtiqmmfsrc", "src")
    src.set("camera", int(input_config))

    # Restricts the camera stream to NV12/1080p/30fps.
    vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Splits decoded frames into the MetaMux1-passthrough and stage 01 branches.
    t_split_1 = Element("tee", "t_split_1")

    # Queues frames from the first tee into the palm-detection preprocess stage.
    q_s1_pre = Element("queue", "q_s1_pre")

    # Converts raw video frames into the palm-detection model input tensor format.
    stage_01_preproc = Element("qtimlvconverter", "stage_01_preproc")

    # Queues converted tensors before palm-detection inference.
    q_s1_inf = Element("queue", "q_s1_inf")

    # Executes the palm-detection model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    stage_01_inference = (
        Element("qtimltflite", "stage_01_inference")
        .set("delegate", "gpu")
        .set("model", f"{model_base_path}/models/palm_detection_full.tflite")
    )

    # Queues inference output tensors before palm-detection postprocessing.
    q_s1_post = Element("queue", "q_s1_post")

    # Decodes palm-detection model output tensors via the external callback.
    palmd_postprocessing = (
        MLPostprocess("stage_01_postproc")
        .set(results=2)
        .set_handler(palmd_callback)
    )

    # Restricts the palm-detection postprocessing output to a text metadata stream.
    mlf_s1 = TextFilter()

    # Merges palm-detection metadata produced by stage 01 with the original video frames.
    metamux_1 = Element("qtimetamux", "metamux_1")

    # Queues the merged stream before the ROI transform.
    q_roi = Element("queue", "q_roi")

    # Crops the merged stream down to the detected hand ROI for stage 02.
    roi_transform = (
        Element("qtimetatransform", "roi_transform")
        .set("module", "roi-palmd")
    )

    # Splits the ROI-cropped stream into the MetaMux2-passthrough and stage 02 branches.
    t_split_2 = Element("tee", "t_split_2")

    # Queues frames from the second tee into the hand-landmark preprocess stage.
    q_s2_pre = Element("queue", "q_s2_pre")

    # Converts ROI-cropped frames into the hand-landmark model input tensor format.
    stage_02_preproc = (
        Element("qtimlvconverter", "stage_02_preproc")
        .set("mode", "roi-batch-cumulative")
    )

    # Queues converted tensors before hand-landmark inference.
    q_s2_inf = Element("queue", "q_s2_inf")

    # Executes the hand-landmark model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    stage_02_inference = (
        Element("qtimltflite", "stage_02_inference")
        .set("delegate", "xnnpack")
        .set("model", f"{model_base_path}/models/hand_landmark_full.tflite")
    )

    # Splits stage 02's inference output into the landmarks and gesture branches.
    t_split_3 = Element("tee", "t_split_3")

    # Queues frames from the third tee into the landmarks postprocessing branch.
    q_s2_post1 = Element("queue", "q_s2_post1")

    # Decodes hand-landmark model output tensors via the external callback.
    hlandmark_postprocessing = (
        MLPostprocess("stage_02_1_postproc")
        .set(results=6)
        .set_handler(hlandmark_callback)
    )

    # Restricts the landmarks postprocessing output to a text metadata stream.
    mlf_s2_1 = TextFilter()

    # Queues frames from the third tee into the gesture-classification branch.
    q_s2_post2 = Element("queue", "q_s2_post2")

    # Reshapes stage 02's raw landmark tensors into the gesture-embedder input format.
    tensor_postprocessing = (
        MLPostprocess("stage_02_2_postproc")
        .set(results=6)
        .set_handler(tensor_callback)
    )

    # Queues reshaped tensors before the gesture-embedder inference.
    q_s3_1 = Element("queue", "q_s3_1")

    # Executes the gesture-embedder model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    stage_03_1_inference = (
        Element("qtimltflite", "stage_03_1_inference")
        .set("delegate", "gpu")
        .set("model", f"{model_base_path}/models/gesture_embedder.tflite")
    )

    # Queues gesture-embedder output tensors before the classifier inference.
    q5 = Element("queue", "q5")

    # Executes the canned-gesture classifier model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    stage_03_2_inference = (
        Element("qtimltflite", "stage_03_2_inference")
        .set("delegate", "gpu")
        .set("model", f"{model_base_path}/models/canned_gesture_classifier.tflite")
    )

    # Queues classifier output tensors before gesture postprocessing.
    q6 = Element("queue", "q6")

    # Decodes the canned-gesture classifier output tensors via the external callback.
    gesture_postprocessing = (
        MLPostprocess("stage_03_postproc")
        .set(results=8)
        .set_handler(gesture_callback)
    )

    # Restricts the gesture postprocessing output to a text metadata stream.
    mlf_s3 = TextFilter()

    # Merges the landmarks and gesture metadata back onto the ROI-cropped frames.
    metamux_2 = Element("qtimetamux", "metamux_2")

    # Queues data between MetaMux2 and the overlay.
    q7 = Element("queue", "q7")

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    #
    # sync=false disables strict rendering synchronization to the pipeline clock.
    # fullscreen=true renders the output fullscreen on the target display.
    sink = (
        Element("waylandsink", "sink")
        .set("sync", False)
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied to branch each tee into a metadata-passthrough
    # path and an ML path, and to merge them back at each metamux in turn.
    pipeline = (
        Pipeline("hand-gesture-pipeline")
        .add(src)
        .add_stream_filter("vf", vf)
        .add(t_split_1)
        .add(q_s1_pre)
        .add(stage_01_preproc)
        .add(q_s1_inf)
        .add(stage_01_inference)
        .add(q_s1_post)
        .add(palmd_postprocessing)
        .add_stream_filter("mlf_s1", mlf_s1)
        .add(metamux_1)
        .add(q_roi)
        .add(roi_transform)
        .add(t_split_2)
        .add(q_s2_pre)
        .add(stage_02_preproc)
        .add(q_s2_inf)
        .add(stage_02_inference)
        .add(t_split_3)
        .add(q_s2_post1)
        .add(hlandmark_postprocessing)
        .add_stream_filter("mlf_s2_1", mlf_s2_1)
        .add(q_s2_post2)
        .add(tensor_postprocessing)
        .add(q_s3_1)
        .add(stage_03_1_inference)
        .add(q5)
        .add(stage_03_2_inference)
        .add(q6)
        .add(gesture_postprocessing)
        .add_stream_filter("mlf_s3", mlf_s3)
        .add(metamux_2)
        .add(q7)
        .add(overlay)
        .add(sink)

        # Base video path
        .link("src", "vf", "t_split_1")

        # Raw video -> MetaMux1
        .link("t_split_1", "metamux_1")

        # Stage 01 ML
        .link("t_split_1", "q_s1_pre", "stage_01_preproc",
            "q_s1_inf", "stage_01_inference",
            "q_s1_post", "stage_01_postproc",
            "mlf_s1", "metamux_1")

        # ROI transform
        .link("metamux_1", "q_roi", "roi_transform", "t_split_2")

        # Stage 02
        .link("t_split_2", "q_s2_pre", "stage_02_preproc",
            "q_s2_inf", "stage_02_inference", "t_split_3")

        # Landmarks branch
        .link("t_split_3", "q_s2_post1",
            "stage_02_1_postproc", "mlf_s2_1", "metamux_2")

        # Gesture branch
        .link("t_split_3", "q_s2_post2",
            "stage_02_2_postproc",
            "q_s3_1",
            "stage_03_1_inference",
            "q5",
            "stage_03_2_inference",
            "q6",
            "stage_03_postproc",
            "mlf_s3",
            "metamux_2")

        # Passthrough meta
        .link("t_split_2", "metamux_2")

        # Display
        .link("metamux_2", "q7", "overlay", "sink")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
