#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom pose-estimation postprocess example."""

import json

import numpy as np
from typing import Dict, List, Optional, Sequence, Tuple
import os

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GLib, Gst, GstQtiML

from qimsdk import Element, Pipeline, VideoFilter, TextFilter, MLPostprocess, ObjectDetections, Poses

# QAI Hub app defaults for YOLO NMS  (score=0.45, IoU=0.70)
SCORE_THRESHOLD_DEFAULT = 0.45
IOU_THRESHOLD_DEFAULT = 0.70
K_DEFAULT_THRESHOLD = 0.70

DEFAULT_NAME = "unknown"
DEFAULT_COLOR = 0x00FF00FF # GREEN

KPS_COUNT = 17

SKELETON_EDGES = [
  (0,1), (0,2), (0,5), (0,6),
  (1,3),
  (2,4),
  (5,6), (5,7), (5,11),
  (6,8), (6,12),
  (7,9),
  (8,10),
  (11,13),
  (12,14),
  (13,15),
  (14,16),
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


DET_LABELS = load_labels(f"{os.environ['HOME']}/labels/yolov5m.json")

POSE_LABELS = load_labels(f"{os.environ['HOME']}/labels/hrnet.json")

det_label_array = np.array([l["name"] for l in DET_LABELS], dtype=str)
det_color_array = np.array([l["color"] for l in DET_LABELS], dtype=np.uint32)

pose_label_array = np.array([l["name"] for l in POSE_LABELS], dtype=str)
pose_color_array = np.array([l["color"] for l in POSE_LABELS], dtype=np.uint32)


def cxcywh_to_xyxy_np(boxes: np.ndarray) -> np.ndarray:
    """
    (cx, cy, w, h) -> (x1, y1, x2, y2)
    boxes: [N,4] float32/float64
    """
    xy = boxes[:, 0:2]
    wh = boxes[:, 2:4]
    half = wh * 0.5
    x1y1 = xy - half
    x2y2 = xy + half
    return np.concatenate([x1y1, x2y2], axis=1)


def nms_single_class_np(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thr: float) -> List[int]:
    """Greedy NMS for one class. boxes_xyxy: [M,4], scores: [M]"""
    if boxes_xyxy.size == 0:
        return []
    x1, y1, x2, y2 = boxes_xyxy.T
    w = np.maximum(0.0, x2 - x1)
    h = np.maximum(0.0, y2 - y1)
    areas = w * h
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        w_i = np.maximum(0.0, xx2 - xx1)
        h_i = np.maximum(0.0, yy2 - yy1)
        inter = w_i * h_i
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0.0, inter / union, 0.0)
        remain = np.where(iou <= iou_thr)[0]
        order = rest[remain]
    return keep


def class_aware_nms_np(
    boxes_xyxy: np.ndarray,  # [N,4]
    scores: np.ndarray,      # [N]
    classes: np.ndarray,     # [N] uint8/int
    score_thr: float,
    iou_thr: float,
    max_det: Optional[int] = None
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Filter by score, NMS per class, sort by score desc, trim to max_det."""
    mask = scores >= score_thr
    boxes_xyxy = boxes_xyxy[mask]
    scores = scores[mask]
    classes = classes[mask]
    if boxes_xyxy.size == 0:
        return boxes_xyxy, scores, classes

    kept_idx: List[int] = []
    for c in np.unique(classes):
        cls_mask = (classes == c)
        inds = np.nonzero(cls_mask)[0]
        keep_local = nms_single_class_np(boxes_xyxy[cls_mask], scores[cls_mask], iou_thr)
        kept_idx.extend(inds[keep_local].tolist())

    kept_idx = np.array(kept_idx, dtype=int)
    kept_idx = kept_idx[np.argsort(scores[kept_idx])[::-1]]  # sort by score desc
    if max_det is not None:
        kept_idx = kept_idx[:max_det]
    return boxes_xyxy[kept_idx], scores[kept_idx], classes[kept_idx]


def decode_yolov5_np(det: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    QAI Hub v5/v6/v7 decode (detect_postprocess):
      det: [N, 4+1+C] = [cx,cy,w,h, obj, class_scores...]
      returns: boxes_xyxy [N,4], scores [N], class_idx [N]
    """

    N, K = det.shape
    boxes_xywh = det[:, :4].astype(np.float32)
    obj = det[:, 4].astype(np.float32)
    cls_scores = det[:, 5:].astype(np.float32)
    if cls_scores.size == 0:
        # Single-class edge case
        cls_idx = np.zeros((N,), dtype=np.uint8)
        cls_max = np.ones((N,), dtype=np.float32)
    else:
        cls_idx = np.argmax(cls_scores, axis=1).astype(np.uint8)
        cls_max = cls_scores[np.arange(N), cls_idx]
    boxes_xyxy = cxcywh_to_xyxy_np(boxes_xywh)
    scores = np.clip(obj * cls_max, 0.0, 1.0).astype(np.float32)
    return boxes_xyxy, scores, cls_idx


def map_boxes_to_region_xyxy_px(
    boxes_xyxy_in: np.ndarray,
    in_size_hw: Tuple[int, int],
    region_xywh: Tuple[int, int, int, int]
) -> np.ndarray:
    """
    Convert model-grid pixel boxes to region-relative normalized coords (0..1).
    in_size_hw=(H_in, W_in), region=(x0,y0,w_region,h_region)
    """
    H_in, W_in = in_size_hw
    x0, y0, w_region, h_region = region_xywh
    if H_in <=0 or W_in <=0 or w_region <=0 or h_region <=0:
        # Nothing to do
        return boxes_xyxy_in.copy()

    # Scale from model input grid to region pixels
    b = boxes_xyxy_in.copy()

    # Normalize to region [0..1]
    b[:, 0] = (b[:, 0] - x0) / w_region
    b[:, 2] = (b[:, 2] - x0) / w_region
    b[:, 1] = (b[:, 1] - y0) / h_region
    b[:, 3] = (b[:, 3] - y0) / h_region

    # Clamp to [0,1]
    b = np.clip(b, 0.0, 1.0)

    return b


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

    # 1) Read model input HW (used to scale to pixels)
    region, resolution = get_params(mlparams)
    tensor_w, tensor_h = resolution

    # 2) Fetch tensor
    tensor = mlframe.get_tensor(batch_idx)

    # Squeze batch dimension [B][N][K] -> [N][K]
    if tensor.ndim == 3 and tensor.shape[0] == 1:
        indata = tensor[0]
    else:
        raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

    # 3) Dequantize if needed
    indata = dequantize_if_int(indata, mlframe, batch_idx)

    # 4) Decode YOLOv5 like QAI Hub (xywh->xyxy, score=obj*best_class, class_idx=argmax)
    boxes_xyxy, scores, class_idx = decode_yolov5_np(indata)

    # 5) Convert to pixels (model grid is tensor_w x tensor_h)
    # NOTE: decode produced xyxy in *model units* (if the head is normalized 0..1).
    # If your model outputs are already 0..1, multiply by tensor size to get pixels:
    boxes_px = boxes_xyxy.copy()
    boxes_px[:, [0, 2]] *= float(tensor_w)
    boxes_px[:, [1, 3]] *= float(tensor_h)

    # 6) Class-aware NMS using QAI Hub defaults
    kept_boxes, kept_scores, kept_classes = class_aware_nms_np(
        boxes_px, scores, class_idx,
        score_thr=SCORE_THRESHOLD_DEFAULT,
        iou_thr=IOU_THRESHOLD_DEFAULT,
        max_det=None
    )

    # 7) Map to region, normalize to [0,1] within that region
    kept_norm = map_boxes_to_region_xyxy_px(kept_boxes, resolution, region)

    cls_ids = kept_classes.astype(int)

    names  = np.full(cls_ids.shape, DEFAULT_NAME, dtype=object)
    colors = np.full(cls_ids.shape, DEFAULT_COLOR, dtype=np.uint32)

    valid = (cls_ids >= 0) & (cls_ids < len(det_label_array))

    names[valid]  = det_label_array[cls_ids[valid]]
    colors[valid] = det_color_array[cls_ids[valid]]

    confidences = kept_scores * 100.0

    # 8) Emit detections (batch=1).
    for (x1, y1, x2, y2), conf, name, color in \
            zip(kept_norm, confidences, names, colors):
        entry = GstQtiML.Detection()

        entry.left = float(x1)
        entry.top = float(y1)
        entry.right = float(x2)
        entry.bottom = float(y2)
        entry.confidence = float(conf)
        entry.name  = str(name)
        entry.color = np.uint32(color)

        detections.append(entry)

    return True


def decode_hrnet_heatmaps_np(
    heatmaps: np.ndarray,
    *,
    refine_quarter: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Decode keypoints from heatmaps.

    Args
    ----
    heatmaps : np.ndarray
        Shape can be [H, W, K], [1, H, W, K], or [K, H, W].
    refine_quarter : bool
        If True, apply a simple quarter-pixel refinement by checking E/W and N/S
        neighbors around the argmax (matching your current code style).

    Returns
    -------
    kps_xy : np.ndarray (K, 2) in heatmap pixel coords (x, y)
    kps_score : np.ndarray (K,) confidence per keypoint
    """
    H, W, K = heatmaps.shape
    hm_flat = heatmaps.reshape(H * W, K)              # [H*W, K]
    argmax_idx = np.argmax(hm_flat, axis=0)     # [K]
    kps_score = hm_flat[argmax_idx, np.arange(K)].astype(np.float32)

    # Compute (x, y) from flat argmax
    y = (argmax_idx // W).astype(np.float32)    # [K]
    x = (argmax_idx %  W).astype(np.float32)    # [K]

    # Optional quarter-pixel refinement (E/W, N/S sign checks)
    if refine_quarter:
        # indices for neighbors (clamp to edges)
        x_m1 = np.clip(x - 1, 0, W - 1).astype(np.int32)
        x_p1 = np.clip(x + 1, 0, W - 1).astype(np.int32)
        y_m1 = np.clip(y - 1, 0, H - 1).astype(np.int32)
        y_p1 = np.clip(y + 1, 0, H - 1).astype(np.int32)
        x_i = x.astype(np.int32)
        y_i = y.astype(np.int32)

        # compare left/right, up/down values
        # hm[y, x +/- 1, k], hm[y +/- 1, x, k]
        lr = np.sign(heatmaps[y_i, x_p1, np.arange(K)] - heatmaps[y_i, x_m1, np.arange(K)])
        ud = np.sign(heatmaps[y_p1, x_i, np.arange(K)] - heatmaps[y_m1, x_i, np.arange(K)])

        x = x + 0.25 * lr
        y = y + 0.25 * ud

    kps_xy = np.stack([x, y], axis=1)  # [K,2]
    return kps_xy, kps_score


def map_keypoints_to_region_norm(
    kps_xy_in: np.ndarray,             # [K,2] in heatmap pixel coords (x, y)
    H_hm: int, W_hm: int,              # heatmap spatial size
    resolution: Tuple[int, int],       # model input spatial size (preprocessor grid)
    region: Tuple[int, int, int, int], # {x,y,width,height}
) -> np.ndarray:
    """
    1) Scale from heatmap pixels -> model input pixels (assumes heatmap aligns to model grid).
    2) Map to region pixels and normalize to [0..1] within region.
    """
    x0, y0, w_reg, h_reg = region
    tensor_w, tensor_h = resolution

    if w_reg <= 0 or h_reg <= 0:
        return np.clip(kps_xy_in.astype(np.float32), 0.0, 1.0)

    # heatmap->model-grid scaling
    sx = float(tensor_w) / float(W_hm)
    sy = float(tensor_h) / float(H_hm)
    px = kps_xy_in[:, 0] * sx
    py = kps_xy_in[:, 1] * sy

    # model-grid pixels -> region normalized
    nx = (px - x0) / float(w_reg)
    ny = (py - y0) / float(h_reg)

    kps_norm = np.stack([nx, ny], axis=1).astype(np.float32)
    return np.clip(kps_norm, 0.0, 1.0)


def build_links_from_keypoints(
    keypoints: List[GstQtiML.Keypoint],
    edges: Sequence[Tuple[int,int]],
    score_threshold: float,
) -> List[Tuple[int, int]]:
    """
    Build edges only if both endpoints' confidence >= threshold.
    Returns list of (i,j) index pairs; your overlay can fetch actual points by index.
    """
    links: List[GstQtiML.KeypointLink] = []

    for a, b in edges:
        if a >= len(keypoints) or b >= len(keypoints):
            continue
        if (keypoints[a].confidence >= score_threshold) and (keypoints[b].confidence >= score_threshold):
            link = GstQtiML.KeypointLink()
            link.l_kp = keypoints[a]
            link.r_kp = keypoints[b]
            link.color = 0x00FF00FF
            links.append(link)

    return links


def pose_callback(mlframe, mlparams, poses: Poses):

    print(
        f"[external-postprocess][pose] called: "
        f"tensors={mlframe.info.type}"
    )

    batch_idx = 0  # single batch

    # 1) Read model input W/H and the region
    region, resolution = get_params(mlparams)

    # 2) Fetch tensor
    tensor = mlframe.get_tensor(batch_idx)

    # Squeze batch dimension [B][H][W][K] -> [H][W][K]
    if tensor.ndim == 4 and tensor.shape[0] == 1:
        heatmaps = tensor[0]
    else:
        raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

    H, W, K = heatmaps.shape

    # 4) Dequantize if needed
    heatmaps = dequantize_if_int(heatmaps, mlframe, batch_idx)

    # 5) Decode keypoints (xy in heatmap pixels), get confidence per kp
    kps_xy_hm, kps_score = decode_hrnet_heatmaps_np(heatmaps, refine_quarter=True)

    # 6) Map kp coords into REGION-normalized [0..1] for overlay
    kps_norm = map_keypoints_to_region_norm(
        kps_xy_hm, H, W, resolution, region
    )

    # 7) Emit a single pose (batch is 1)
    pose = GstQtiML.Pose()

    # Average confidence
    pose.confidence = float(np.mean(kps_score)) if kps_score.size else 0.0

    n = kps_norm.shape[0]
    idxs = np.arange(kps_norm.shape[0])

    names  = np.full(kps_norm.shape[0], DEFAULT_NAME, dtype=object)
    colors = np.full(kps_norm.shape[0], DEFAULT_COLOR, dtype=np.uint32)

    valid = idxs < len(pose_label_array)

    names[valid]  = pose_label_array[idxs[valid]]
    colors[valid] = pose_color_array[idxs[valid]]

    # Fill keypoints:
    keypoints = []
    for (x, y), conf, name, color in zip(kps_norm, kps_score, names, colors):
        kp = GstQtiML.Keypoint()

        kp.x = float(x)
        kp.y = float(y)
        kp.confidence = float(conf)
        kp.name  = str(name)
        kp.color = np.uint32(color)

        keypoints.append(kp)

    # 8) Build edges where both endpoints pass threshold
    try:
        links = build_links_from_keypoints(
            keypoints, SKELETON_EDGES, score_threshold=K_DEFAULT_THRESHOLD
        )
    except Exception as e:
        print("[POSE] linking failed:", e)

    pose.name = "Pose"
    pose.color = DEFAULT_COLOR
    pose.keypoints = keypoints
    pose.links = links

    # Threshold the whole pose
    if pose.confidence < K_DEFAULT_THRESHOLD:
        return True

    #print(f"Appending entry: {entry}")
    poses.append(pose)

    return True


#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> mlmuxer
#      split -> q1 -> preprocessing -> q2 -> inferencing -> q5
#             -> det_postprocessing -> [mlf] -> mlmuxer -> q7 -> split2 (tee)
#        split2 -> mlmuxer2
#        split2 -> q3 -> preprocessing2 -> q4 -> inferencing2 -> q6
#               -> pose_postprocessing -> [mlf2] -> mlmuxer2
#      mlmuxer2 -> q8 -> overlay -> display
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs a first-stage YOLOv5-style detector whose detections are merged back
#  into the frame metadata, then re-tees the merged stream into a second-stage
#  HRNet pose estimator whose keypoints are merged in turn, before overlaying
#  and displaying the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads the input media file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", f"{os.environ['HOME']}/media/Draw_1080p_180s_30FPS.mp4")
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

    # Splits decoded frames into the metadata-passthrough and detection ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the detection ML branch.
    q1 = Element("queue", "q1")

    # Queues detection inference output tensors before postprocessing.
    q5 = Element("queue", "q5")

    # Converts raw video frames into detector model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before detection inference.
    q2 = Element("queue", "q2")

    # Executes the YOLOv5-style detection model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{os.environ['HOME']}/models/yolov5m-320x320-int8.tflite")
    )

    # Decodes detection model output tensors via the external callback.
    det_postprocessing = (
        MLPostprocess("det_postprocessing")
        .set_handler(detection_callback)
    )

    # Restricts the detection postprocessing output to a text metadata stream.
    mlf = TextFilter()

    # Merges detection metadata produced by the ML branch with the original video frames.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Queues data between pipeline stages.
    q7 = Element("queue", "q7")

    # Re-splits the merged stream into the metadata-passthrough and pose ML branches.
    split2 = Element("tee", "split2")

    # Queues frames from the second tee into the pose ML branch.
    q3 = Element("queue", "q3")

    # Queues pose inference output tensors before postprocessing.
    q6 = Element("queue", "q6")

    # Converts detection ROIs into the HRNet pose model input tensor format.
    preprocessing2 = (
        Element("qtimlvconverter", "preprocessing2")
        .set("mode", "roi-batch-cumulative")
        .set("image_disposition", "centre")
    )

    # Queues converted tensors before pose inference.
    q4 = Element("queue", "q4")

    # Executes the HRNet pose model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    inferencing2 = (
        Element("qtimltflite", "inferencing2")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{os.environ['HOME']}/models/hrnet_pose_w8a8.tflite")
    )

    # Decodes pose model output tensors via the external callback.
    pose_postprocessing = (
        MLPostprocess("pose_postprocessing")
        .set_handler(pose_callback)
    )

    # Restricts the pose postprocessing output to a text metadata stream.
    mlf2 = TextFilter()

    # Merges pose metadata produced by the ML branch with the frames coming from the first stage.
    mlmuxer2 = Element("qtimetamux", "mlmuxer2")

    # Queues data between pipeline stages.
    q8 = Element("queue", "q8")

    # Renders ML metadata over the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied to branch each tee into a metadata-passthrough
    # path and an ML path, and to merge them back at each metamux in turn.
    pipeline = (
        Pipeline("ml-external-pose")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(split)
        .add(q1)
        .add(q5)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(det_postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(mlmuxer)
        .add(q7)
        .add(split2)
        .add(q3)
        .add(q6)
        .add(preprocessing2)
        .add(q4)
        .add(inferencing2)
        .add(pose_postprocessing)
        .add_stream_filter("mlf2", mlf2)
        .add(mlmuxer2)
        .add(q8)
        .add(overlay)
        .add(display)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "mlmuxer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q5", "det_postprocessing", "mlf", "mlmuxer", "q7", "split2")
        .link("split2", "mlmuxer2")
        .link("split2", "q3", "preprocessing2", "q4", "inferencing2", "q6", "pose_postprocessing", "mlf2", "mlmuxer2")
        .link("mlmuxer2", "q8", "overlay", "display")
    )

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
