#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Custom PoseNet pose-estimation postprocess example.

Reworked to follow the C++ PoseNet postprocess module closely while keeping the
uploaded Python pipeline unchanged.

C++ flow mirrored here:
    Configure/load labels + optional settings
    ExtractRootpoints
    TraverseSkeletonLinks(backwards=True)
    TraverseSkeletonLinks(backwards=False)
    NonMaxSuppression
    Build display links from configured connections
    Transform keypoints to region-normalized coordinates
"""

import argparse
import sys
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
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
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/pose_sample.mp4",
                    help="Input source configuration (camera number, device, or file path)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

import numpy as np

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstQtiML", "1.0")

from gi.repository import GstQtiML

from qimsdk import Element, Pipeline, VideoFilter, TextFilter, MLPostprocess, Poses


K_DEFAULT_THRESHOLD = 0.70
K_NMS_THRESHOLD_RADIUS = 20.0
K_LOCAL_MAXIMUM_RADIUS = 1
K_NUM_REFINEMENT_STEPS = 2

EXPECTED_TENSORS_3 = 3
EXPECTED_TENSORS_5 = 5

DEFAULT_NAME = "unknown"
DEFAULT_COLOR = 0x00FF00FF
KPS_COUNT = 17
ROOT_IDS = {0, 5, 6}  # nose + shoulders

TRAVERSAL_LINKS: Sequence[Tuple[int, int]] = [
    (0, 1),
    (1, 3),
    (0, 2),
    (2, 4),
    (0, 5),
    (5, 7),
    (7, 9),
    (5, 11),
    (11, 13),
    (13, 15),
    (0, 6),
    (6, 8),
    (8, 10),
    (6, 12),
    (12, 14),
    (14, 16),
]

CONNECTIONS: Sequence[Tuple[int, int]] = [
    (0, 1), (0, 2), (0, 5), (0, 6),
    (1, 3),
    (2, 4),
    (5, 6), (5, 7), (5, 11),
    (6, 8), (6, 12),
    (7, 9),
    (8, 10),
    (11, 13),
    (12, 14),
    (13, 15),
    (14, 16),
]


@dataclass
class LabelEntry:
    name: str = DEFAULT_NAME
    color: np.uint32 = DEFAULT_COLOR


@dataclass
class RootPoint:
    id: int = 0
    x: float = 0.0
    y: float = 0.0
    confidence: float = 0.0


@dataclass
class KeypointLinkIds:
    s_kp_id: int
    d_kp_id: int


def load_labels(path: str) -> List[Dict[str, object]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    labels: List[Dict[str, object]] = []
    for entry in raw:
        idx = int(entry.get("id", -1))
        if idx < 0:
            continue

        while len(labels) <= idx:
            labels.append({"name": DEFAULT_NAME, "color": DEFAULT_COLOR})

        color_value = entry.get("color", DEFAULT_COLOR)
        color = int(color_value, 16) if isinstance(color_value, str) else int(color_value)
        labels[idx] = {
            "name": str(entry.get("label", DEFAULT_NAME)),
            "color": color,
        }

    return labels


def load_pose_labels() -> List[LabelEntry]:
    if Path(f"{model_base_path}/labels/posenet.json").exists():
        return [LabelEntry(str(x["name"]), np.uint32(x["color"])) for x in load_labels(f"{model_base_path}/labels/posenet.json")]

    print("[POSE] WARNING: posenet labels not found; using fallback labels")
    return [LabelEntry(f"kp_{i}", DEFAULT_COLOR) for i in range(KPS_COUNT)]


def load_settings() -> Tuple[float, List[KeypointLinkIds], List[KeypointLinkIds]]:
    threshold = K_DEFAULT_THRESHOLD
    links = [KeypointLinkIds(a, b) for a, b in TRAVERSAL_LINKS]
    connections = [KeypointLinkIds(a, b) for a, b in CONNECTIONS]

    path = Path(f"{model_base_path}/labels/posenet_settings.json")
    if path.exists():
        if not path.exists():
            return

        try:
            with path.open("r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception as exc:
            print(f"[POSE] WARNING: failed to parse posenet settings file: {exc}")
            return

        if "confidence" in settings:
            threshold = float(settings["confidence"]) / 100.0

        nodes = settings.get("posenet", [])
        if isinstance(nodes, list):
            loaded_links: List[KeypointLinkIds] = []
            if _load_links_recursive(nodes, 0, loaded_links):
                links = loaded_links

            loaded_connections = _load_connections(nodes)
            if loaded_connections:
                connections = loaded_connections

        print(
            f"[POSE] settings loaded "
            f"threshold={threshold}, traversal_links={len(links)}, connections={len(connections)}"
        )
        return threshold, links, connections

    print(
        f"[POSE] WARNING: posenet settings not found; using fallback graph: "
        f"traversal_links={len(links)}, connections={len(connections)}"
    )
    return threshold, links, connections


def _load_links_recursive(nodes: Sequence[object], idx: int, links: List[KeypointLinkIds]) -> bool:
    if idx >= len(nodes):
        return False

    node = nodes[idx]
    if not isinstance(node, dict):
        return True

    if "id" not in node:
        return False

    s_kp_id = int(node["id"])
    link_arr = node.get("links")
    if not isinstance(link_arr, list):
        return True

    for val in link_arr:
        if not isinstance(val, (int, float)):
            continue
        d_kp_id = int(val)
        links.append(KeypointLinkIds(s_kp_id, d_kp_id))
        if not _load_links_recursive(nodes, d_kp_id, links):
            return False

    return True


def _load_connections(nodes: Sequence[object]) -> List[KeypointLinkIds]:
    connections: List[KeypointLinkIds] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "id" not in node or "connection" not in node:
            continue
        connections.append(KeypointLinkIds(int(node["id"]), int(node["connection"])))
    return connections


POSE_LABELS = load_pose_labels()
POSE_THRESHOLD, TRAVERSAL_LINKS, CONNECTIONS = load_settings()

pose_label_array = np.array([l.name for l in POSE_LABELS], dtype=object)
pose_color_array = np.array([l.color for l in POSE_LABELS], dtype=np.uint32)


def label_name(kp_id: int) -> str:
    return str(pose_label_array[kp_id]) if 0 <= kp_id < len(pose_label_array) else DEFAULT_NAME


def label_color(kp_id: int) -> int:
    return int(pose_color_array[kp_id]) if 0 <= kp_id < len(pose_color_array) else DEFAULT_COLOR


def get_qscale_from_meta(mlframe, index=0) -> float:
    mlmeta = GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)
    if mlmeta is not None and hasattr(mlmeta, "qscale"):
        return float(mlmeta.qscale)
    print("WARNING: mlmeta has no attribute named qscale; returning 1.0")
    return 1.0


def get_qoffset_from_meta(mlframe, index=0) -> float:
    mlmeta = GstQtiML.buffer_get_ml_tensor_meta_id(mlframe.buffer, index)
    if mlmeta is not None and hasattr(mlmeta, "qoffset"):
        return float(mlmeta.qoffset)
    print("WARNING: mlmeta has no attribute named qoffset; returning 0.0")
    return 0.0


def get_params(mlparams) -> Tuple[Tuple[int, int, int, int], Tuple[int, int]]:
    ok_w, tensor_w = mlparams.get_uint("input-tensor-width")
    ok_h, tensor_h = mlparams.get_uint("input-tensor-height")
    if not (ok_h and ok_w):
        raise KeyError("Missing input tensor width/height in mlparams")

    ok_x, region_x = mlparams.get_int("input-region-x")
    ok_y, region_y = mlparams.get_int("input-region-y")
    ok_rw, region_w = mlparams.get_int("input-region-width")
    ok_rh, region_h = mlparams.get_int("input-region-height")
    if not (ok_x and ok_y and ok_rw and ok_rh):
        raise KeyError("Missing input region x/y/width/height in mlparams")

    return (int(region_x), int(region_y), int(region_w), int(region_h)), (int(tensor_w), int(tensor_h))


def dequantize_if_int(arr: np.ndarray, mlframe, index: int) -> np.ndarray:
    if arr.dtype in (np.int8, np.uint8, np.int16, np.uint16, np.int32, np.uint32):
        qscale = get_qscale_from_meta(mlframe, index)
        qoffset = get_qoffset_from_meta(mlframe, index)
        return (arr.astype(np.float32) - float(qoffset)) * float(qscale)
    return arr.astype(np.float32, copy=False)


def layout_info(tensors: Sequence[np.ndarray]) -> Tuple[bool, int, int, int, int]:
    is_five_tensor = (len(tensors) == EXPECTED_TENSORS_5)

    if is_five_tensor:
        n_keypoints = int(tensors[0].shape[0])
        n_rows = int(tensors[0].shape[1])
        n_columns = int(tensors[0].shape[2])
        n_edges = int(tensors[2].shape[0] // 2)
    else:
        n_rows = int(tensors[0].shape[0])
        n_columns = int(tensors[0].shape[1])
        n_keypoints = int(tensors[0].shape[2])
        n_edges = int(tensors[2].shape[2] // 4)

    return is_five_tensor, n_keypoints, n_rows, n_columns, n_edges


def heatmap_at(heatmap: np.ndarray, is_five_tensor: bool, kp_idx: int, row: int, column: int) -> float:
    if is_five_tensor:
        return float(heatmap[kp_idx, row, column])
    return float(heatmap[row, column, kp_idx])


def offset_y_at(offsets: np.ndarray, is_five_tensor: bool, kp_idx: int, row: int, column: int, n_keypoints: int) -> float:
    if is_five_tensor:
        return float(offsets[kp_idx, row, column])
    return float(offsets[row, column, kp_idx])


def offset_x_at(offsets: np.ndarray, is_five_tensor: bool, kp_idx: int, row: int, column: int, n_keypoints: int) -> float:
    if is_five_tensor:
        return float(offsets[kp_idx + n_keypoints, row, column])
    return float(offsets[row, column, kp_idx + n_keypoints])


def displacement_y_at(displacements: np.ndarray, is_five_tensor: bool, edge_id: int,
                      row: int, column: int, n_edges: int, backwards: bool) -> float:
    if is_five_tensor:
        return float(displacements[edge_id, row, column])
    channel = edge_id + (n_edges * 2 if backwards else 0)
    return float(displacements[row, column, channel])


def displacement_x_at(displacements: np.ndarray, is_five_tensor: bool, edge_id: int,
                      row: int, column: int, n_edges: int, backwards: bool) -> float:
    if is_five_tensor:
        return float(displacements[edge_id + n_edges, row, column])
    channel = edge_id + n_edges + (n_edges * 2 if backwards else 0)
    return float(displacements[row, column, channel])


def sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + math.exp(-float(x))))


def grid_from_xy(x: float, y: float, paxel_x: float, paxel_y: float,
                 n_rows: int, n_columns: int) -> Tuple[int, int]:
    row = int(np.clip(round(y / paxel_y), 0, n_rows - 1))
    column = int(np.clip(round(x / paxel_x), 0, n_columns - 1))
    return row, column


def extract_rootpoints(tensors: Sequence[np.ndarray], resolution: Tuple[int, int]) -> List[RootPoint]:
    source_width, source_height = resolution
    rootpoints: List[RootPoint] = []

    is_five_tensor, n_keypoints, n_rows, n_columns, _ = layout_info(tensors)
    heatmap = tensors[0]
    offsets = tensors[1]

    paxelsize_x = (source_width - 1) / (n_columns - 1)
    paxelsize_y = (source_height - 1) / (n_rows - 1)

    threshold_logit = math.log(POSE_THRESHOLD / (1.0 - POSE_THRESHOLD))

    for kp_idx in range(n_keypoints):

        if kp_idx not in ROOT_IDS:
            continue

        for row in range(n_rows):
            for column in range(n_columns):
                confidence_logit = heatmap_at(heatmap, is_five_tensor, kp_idx, row, column)
                if confidence_logit < threshold_logit:
                    continue

                ymin = max(row - K_LOCAL_MAXIMUM_RADIUS, 0)
                ymax = min(row + K_LOCAL_MAXIMUM_RADIUS + 1, n_rows)
                xmin = max(column - K_LOCAL_MAXIMUM_RADIUS, 0)
                xmax = min(column + K_LOCAL_MAXIMUM_RADIUS + 1, n_columns)

                score = np.finfo(np.float32).tiny
                for y in range(ymin, ymax):
                    for x in range(xmin, xmax):
                        current_score = heatmap_at(heatmap, is_five_tensor, kp_idx, y, x)
                        if current_score > score:
                            score = current_score

                if confidence_logit < score:
                    continue

                confidence = sigmoid(confidence_logit) * 100.0

                rootpoint = RootPoint()
                rootpoint.id = kp_idx
                rootpoint.confidence = confidence
                rootpoint.x = column * paxelsize_x
                rootpoint.y = row * paxelsize_y

                # C++ intent: apply y/x offsets for this keypoint at the root grid cell.
                rootpoint.y += offset_y_at(offsets, is_five_tensor, kp_idx, row, column, n_keypoints)
                rootpoint.x += offset_x_at(offsets, is_five_tensor, kp_idx, row, column, n_keypoints)

                rootpoints.append(rootpoint)

    rootpoints.sort(key=lambda x: x.confidence, reverse=True)
    return rootpoints


def make_empty_keypoint(kp_id: int) -> GstQtiML.Keypoint:
    keypoint = GstQtiML.Keypoint()
    keypoint.x = 0.0
    keypoint.y = 0.0
    keypoint.confidence = 0.0
    keypoint.name = label_name(kp_id)
    keypoint.color = label_color(kp_id)
    return keypoint


def traverse_skeleton_links(tensors: Sequence[np.ndarray], keypoints: List[GstQtiML.Keypoint], conf: float,
                            resolution: Tuple[int, int], backwards: bool) -> Tuple[float, List[GstQtiML.Keypoint]]:
    source_width, source_height = resolution

    is_five_tensor, n_keypoints, n_rows, n_columns, n_edges = layout_info(tensors)
    heatmap = tensors[0]
    offsets = tensors[1]
    displacements = tensors[3] if (is_five_tensor and backwards) else tensors[2]

    paxelsize_x = (source_width - 1) / (n_columns - 1)
    paxelsize_y = (source_height - 1) / (n_rows - 1)

    base = (n_edges - 1) if backwards else 0

    for edge in range(n_edges):
        edge_id = abs(base - edge)
        if edge_id >= len(TRAVERSAL_LINKS):
            continue

        link = TRAVERSAL_LINKS[edge_id]
        s_kp_id = link.d_kp_id if backwards else link.s_kp_id
        d_kp_id = link.s_kp_id if backwards else link.d_kp_id

        s_kp = keypoints[s_kp_id]
        d_kp = keypoints[d_kp_id]

        # Skip if source is not present or destination is already populated.
        if (s_kp.confidence == 0.0) or (d_kp.confidence != 0.0):
            continue

        row, column = grid_from_xy(s_kp.x, s_kp.y, paxelsize_x, paxelsize_y, n_rows, n_columns)

        d_kp.y = s_kp.y + displacement_y_at(displacements, is_five_tensor, edge_id, row, column, n_edges, backwards)
        d_kp.x = s_kp.x + displacement_x_at(displacements, is_five_tensor, edge_id, row, column, n_edges, backwards)

        for _ in range(K_NUM_REFINEMENT_STEPS):
            row, column = grid_from_xy(d_kp.x, d_kp.y, paxelsize_x, paxelsize_y, n_rows, n_columns)
            d_kp.y = row * paxelsize_y + offset_y_at(offsets, is_five_tensor, d_kp_id, row, column, n_keypoints)
            d_kp.x = column * paxelsize_x + offset_x_at(offsets, is_five_tensor, d_kp_id, row, column, n_keypoints)

        d_kp.y = float(np.clip(d_kp.y, 0.0, source_height - 1))
        d_kp.x = float(np.clip(d_kp.x, 0.0, source_width - 1))

        row, column = grid_from_xy(d_kp.x, d_kp.y, paxelsize_x, paxelsize_y, n_rows, n_columns)
        confidence_logit = heatmap_at(heatmap, is_five_tensor, d_kp_id, row, column)

        d_kp.confidence = sigmoid(confidence_logit) * 100.0
        d_kp.name = label_name(d_kp_id)
        d_kp.color = label_color(d_kp_id)

        keypoints[d_kp_id] = d_kp

        conf += d_kp.confidence / n_keypoints

    return (conf, keypoints)


def non_max_suppression(l_entry: GstQtiML.Pose, entries: List[GstQtiML.Pose]) -> int:
    n_keypoints = len(l_entry.keypoints)
    threshold = K_NMS_THRESHOLD_RADIUS * K_NMS_THRESHOLD_RADIUS

    for idx, r_entry in enumerate(entries):
        n_overlaps = 0
        for num in range(n_keypoints):
            l_kp = l_entry.keypoints[num]
            r_kp = r_entry.keypoints[num]
            distance = ((l_kp.x - r_kp.x) ** 2) + ((l_kp.y - r_kp.y) ** 2)
            if distance <= threshold:
                n_overlaps += 1

        if n_overlaps < (n_keypoints // 2):
            continue

        if l_entry.confidence > r_entry.confidence:
            return idx

        if l_entry.confidence <= r_entry.confidence:
            return -2

    return -1


def build_links_from_connections(keypoints: List[GstQtiML.Keypoint]) -> List[GstQtiML.KeypointLink]:
    links: List[GstQtiML.KeypointLink] = []

    for lk in CONNECTIONS:
        if lk.s_kp_id >= len(keypoints) or lk.d_kp_id >= len(keypoints):
            continue

        link = GstQtiML.KeypointLink()
        link.l_kp = keypoints[lk.s_kp_id]
        link.r_kp = keypoints[lk.d_kp_id]
        link.color = DEFAULT_COLOR
        links.append(link)

    return links


def keypoint_transform_coordinates(keypoint: GstQtiML.Keypoint, region: Tuple[int, int, int, int]) -> None:
    x, y, width, height = region
    if width <= 0 or height <= 0:
        keypoint.x = float(np.clip(keypoint.x, 0.0, 1.0))
        keypoint.y = float(np.clip(keypoint.y, 0.0, 1.0))
        return

    keypoint.x = float((keypoint.x - x) / width)
    keypoint.y = float((keypoint.y - y) / height)

    return keypoint


def parse_tensor_frame(tensors: Sequence[np.ndarray], region: Tuple[int, int, int, int],
                       resolution: Tuple[int, int]) -> List[GstQtiML.Pose]:
    estimations: List[GstQtiML.Pose] = []

    is_five_tensor, n_keypoints, _, _, _ = layout_info(tensors)
    rootpoints = extract_rootpoints(tensors, resolution)

    for rootpoint in rootpoints:
        entry = GstQtiML.Pose()
        entry.name = "Pose"
        entry.color = DEFAULT_COLOR

        keypoints:List[GstQtiML.KeyPoint] = []
        for kp_id in range(n_keypoints):
            keypoints.append(make_empty_keypoint(kp_id))

        kp = GstQtiML.Keypoint()
        kp.x = float(rootpoint.x)
        kp.y = float(rootpoint.y)
        kp.confidence = float(rootpoint.confidence)
        kp.name = label_name(rootpoint.id)
        kp.color = label_color(rootpoint.id)

        keypoints[rootpoint.id] = kp

        confidence = kp.confidence / n_keypoints

        (confidence, keypoints) = traverse_skeleton_links(tensors, keypoints, confidence, resolution, backwards=True)
        (confidence, keypoints) = traverse_skeleton_links(tensors, keypoints, confidence, resolution, backwards=False)

        entry.confidence = confidence
        entry.keypoints = keypoints

        nms = non_max_suppression(entry, estimations)
        if nms == -2:
            continue

        if nms >= 0:
            del estimations[nms]

        estimations.append(entry)

    # Transform coordinates
    for en_id in range(len(estimations)):
        keypoints = estimations[en_id].keypoints
        for kp_id in range(len(keypoints)):
            keypoints[kp_id] = keypoint_transform_coordinates(keypoints[kp_id], region)

        estimations[en_id].keypoints = keypoints
        estimations[en_id].links = build_links_from_connections(keypoints)

    return estimations


def pose_callback(mlframe, mlparams, poses: Poses):
    n_tensors = int(getattr(mlframe.info, "n_tensors", EXPECTED_TENSORS_3))
    print(f"[external-postprocess][pose_estimation] called: tensors={n_tensors}")

    if n_tensors not in (EXPECTED_TENSORS_3, EXPECTED_TENSORS_5):
        raise KeyError(f"Expected 3 or 5 PoseNet tensors, got {n_tensors}")

    batch_idx = 0
    region, resolution = get_params(mlparams)

    tensors: List[np.ndarray] = []
    for tensor_idx in range(n_tensors):
        tensor = mlframe.get_tensor(n_tensors * batch_idx + tensor_idx)

        if tensor.ndim == 4 and tensor.shape[0] == 1:
            tensor = tensor[0]
        else:
            raise KeyError(f"Unexpected tensor shape: {tensor.shape}")

        tensor = dequantize_if_int(tensor, mlframe, tensor_idx)
        tensors.append(tensor)

    decoded = parse_tensor_frame(tensors, region, resolution)

    for pose in decoded:
        poses.append(pose)

    print(
        f"[external-postprocess][pose_estimation] decoded: "
        f"poses(out)={len(decoded)}, decode_ok=true"
    )
    return True


#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> [vf] -> split (tee)
#      split -> mlmuxer -> q4 -> overlay -> display
#      split -> q1 -> preprocessing -> q2 -> inferencing -> q3
#             -> pose_postprocessing -> [mlf] -> mlmuxer
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs external PoseNet postprocessing callback logic, merges the resulting
#  pose metadata with the original video frames, overlays it, and displays the
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

    # Splits decoded frames into the metadata-passthrough and ML branches.
    split = Element("tee", "split")

    # Queues frames from tee into the ML branch.
    q1 = Element("queue", "q1")

    # Queues inference output tensors before postprocessing.
    q3 = Element("queue", "q3")

    # Converts raw video frames into model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Executes the ML model and attaches tensor outputs to each frame.
    #
    # Configures the model and the hardware delegate used for execution.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;")
        .set("model", f"{model_base_path}/models/posenet_mobilenet_v1_075_481_641_quant.tflite")
    )

    # Decodes model output tensors into pose metadata via the external callback.
    pose_postprocessing = (
        MLPostprocess("pose_postprocessing")
        .set("results", 2)
        .set_handler(pose_callback)
    )

    # Restricts the postprocessing output to a text metadata stream.
    mlf = TextFilter()

    # Merges metadata produced by the ML branch with the original video frames.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Queues data between pipeline stages.
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
    # ML pose branches, and to merge them back at the metamux.
    pipeline = (
        Pipeline("ml-external-pose")
        .add(src)
        .add(demux)
        .add(parse)
        .add(decoder)
        .add_stream_filter("vf", vf)
        .add(split)
        .add(q1)
        .add(q3)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(pose_postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(mlmuxer)
        .add(q4)
        .add(overlay)
        .add(display)
        .link("src", "demux", "parse", "decoder", "vf", "split")
        .link("split", "mlmuxer")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "pose_postprocessing", "mlf", "mlmuxer")
        .link("mlmuxer", "q4", "overlay", "display")
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
