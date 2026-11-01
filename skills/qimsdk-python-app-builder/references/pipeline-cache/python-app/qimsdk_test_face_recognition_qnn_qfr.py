#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Three-stage face recognition daisy-chain from an MP4 file, written to an
output MP4 file. All three stages run on the native Qualcomm AI Engine Direct
(QNN) runtime (`qtimlqnn`), loading cached-context `.bin` models on the
HTP/NPU backend.

Stage 1 (qfd):       face detection on the full frame.
Stage 2 (lite-3dmm): facial landmark / 3DMM alignment on the Stage 1 ROI.
Stage 3 (qfr):       face recognition / embedding match on the Stage 2 ROI.
"""

import os

from qimsdk import Element, Pipeline, TextFilter, VideoFilter

#  Example pipeline (three tee/qtimetamux stages, file-to-file):
#
#    src -> demux -> parse -> decoder -> q_dec -> [vf:NV12] -> split1(tee)
#      split1. -> metamux_1                                   (passthrough)
#      split1. -> q_stage1 -> pre1 -> infer1 -> post1 -> [text1] -> metamux_1.
#    metamux_1 -> split2(tee)
#      split2. -> metamux_2                                   (passthrough)
#      split2. -> q_stage2 -> pre2 -> infer2 -> post2 -> [text2] -> metamux_2.
#    metamux_2 -> split3(tee)
#      split3. -> metamux_3                                   (passthrough)
#      split3. -> q_stage3 -> pre3 -> infer3 -> post3 -> [text3] -> metamux_3.
#    metamux_3 -> overlay -> q_enc -> [enc_vf:NV12] -> encoder
#              -> parse_out -> mux -> filesink
#
#  Reads an MP4/H.264 file, decodes it through the hardware decoder, and runs
#  the face recognition chain on the native QNN runtime (qtimlqnn) with the
#  HTP/NPU backend: Stage 1 Face Detection Lite (module=qfd) locates the face,
#  Stage 2 facemap-3dmm (module=lite-3dmm, roi-batch-cumulative) aligns it,
#  Stage 3 face recognition (module=qfr, roi-batch-cumulative) emits the
#  identity match. Each stage's metadata is merged back with its own
#  qtimetamux; qtivoverlay draws the box/landmarks/label, and the annotated
#  stream is hardware-encoded to an output MP4 file.
#
#  This is the qtimlqnn/.bin sibling of qimsdk_test_face_recognition_snpe_qfr.py
#  (qtimlsnpe/.dlc/delegate=dsp): identical topology and the same built-in qfr
#  Stage-3 postprocess. qtimlqnn loads a cached-context .bin model and selects
#  the HTP/NPU backend via backend=/usr/lib/libQnnHtp.so plus the QNN system
#  library system=/usr/lib/libQnnSystem.so.
#
#  Known SDK fact (Stage 1): the face_det_lite model exports 3 separate named
#  output tensors (heatmap/1ch, bbox/4ch, landmark/10ch). Without an explicit
#  tensors= list, qtimlqnn's default output set does not present all three and
#  module=qfd fails caps negotiation. See model-catalog.md
#  "Lightweight-Face-Detection".
#
#  Known SDK fact (Stage 3): the recognition .bin is a MULTI-HEAD model with
#  six output heads (id_feature[1,512], liveness_feature[1,32],
#  eye_closeness[1,2], glasses[1,2], mask[1,2], sunglasses[1,2]). module=qfr
#  requires all six, in order, so tensors= must name every head; without it the
#  default output set is offered against qfr's six-tensor contract and caps
#  negotiation fails. See model-catalog.md "Facial-Attribute-Detection" and
#  plugin-catalog.md "Tensor Filter - Decision Rule" (multi-head case).
#
#  Stage 2 (lite-3dmm) needs no tensors= (single native output tensor) but
#  DOES require /etc/data/{blendShape,meanFace,shapeBasis}.bin on the device
#  (paths hard-coded inside facemap_3dmm_settings.json); without them Stage 2
#  fails "Failed to open /etc/data/meanFace.bin".

INPUT_FILE = f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4"

# QNN HTP/NPU backend + system shared libraries (native QNN runtime).
QNN_BACKEND = "/usr/lib/libQnnHtp.so"
QNN_SYSTEM = "/usr/lib/libQnnSystem.so"

# Stage 1 - face detection (face_det_lite, native QNN cached .bin on HTP).
STAGE1_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/face_det_lite_w8a8.bin"
STAGE1_LABELS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/face_detection.json"

# Stage 2 - face landmark / 3DMM alignment (facemap_3dmm, native QNN .bin).
STAGE2_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/facemap_3dmm_w8a8.bin"
STAGE2_SETTINGS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/config/facemap_3dmm_settings.json"

# Stage 3 - face recognition / embedding match (multi-head .bin, native QNN).
STAGE3_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/Facial-Attribute-Detection_w8a8.bin"
STAGE3_LABELS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/face_recognition.json"
STAGE3_SETTINGS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/config/face_recognition_settings.json"

# Confidence threshold (percent) applied to Stage 1 detection.
STAGE1_CONFIDENCE = 51.0

OUTPUT_FILE = "/tmp/face_recognition_qnn_out.mp4"


def create_and_execute_pipeline() -> None:
    # -- source / decode ---------------------------------------------------
    source = Element("filesrc", "source")
    source.set("location", INPUT_FILE)

    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")

    decoder = Element("v4l2h264dec", "decoder")
    decoder.set("capture-io-mode", 4)
    decoder.set("output-io-mode", 4)

    q_dec = Element("queue", "q_dec")
    vf = VideoFilter().format("NV12")

    # -- stage 1: face detection (qfd) --------------------------------------
    split1 = Element("tee", "split1")
    q_stage1 = Element("queue", "q_stage1")

    stage1_pre = Element("qtimlvconverter", "stage_01_preproc")
    stage1_pre.set("mode", "image-batch-non-cumulative")

    stage1_infer = Element("qtimlqnn", "stage_01_inference")
    stage1_infer.set("model", STAGE1_MODEL)
    stage1_infer.set("backend", QNN_BACKEND)
    stage1_infer.set("system", QNN_SYSTEM)
    # 3 separate named output tensors; see "Known SDK fact (Stage 1)" above.
    stage1_infer.set("tensors", ["heatmap", "bbox", "landmark"])

    stage1_post = Element("qtimlpostprocess", "stage_01_postproc")
    stage1_post.set("module", "qfd")
    stage1_post.set("labels", STAGE1_LABELS)
    stage1_post.set("settings", f'{{"confidence": {STAGE1_CONFIDENCE}}}')
    stage1_post.set("results", 6)

    stage1_text = TextFilter()
    metamux1 = Element("qtimetamux", "metamux_1")

    # -- stage 2: face landmark / 3DMM alignment (lite-3dmm) ----------------
    split2 = Element("tee", "split2")
    q_stage2 = Element("queue", "q_stage2")

    stage2_pre = Element("qtimlvconverter", "stage_02_preproc")
    stage2_pre.set("mode", "roi-batch-cumulative")
    stage2_pre.set("image-disposition", "centre")

    stage2_infer = Element("qtimlqnn", "stage_02_inference")
    stage2_infer.set("model", STAGE2_MODEL)
    stage2_infer.set("backend", QNN_BACKEND)
    stage2_infer.set("system", QNN_SYSTEM)
    # Single native output tensor -> no tensors= needed (see header note).

    stage2_post = Element("qtimlpostprocess", "stage_02_postproc")
    stage2_post.set("module", "lite-3dmm")
    stage2_post.set("settings", STAGE2_SETTINGS)
    stage2_post.set("results", 6)

    stage2_text = TextFilter()
    metamux2 = Element("qtimetamux", "metamux_2")

    # -- stage 3: face recognition (qfr) ------------------------------------
    split3 = Element("tee", "split3")
    q_stage3 = Element("queue", "q_stage3")

    stage3_pre = Element("qtimlvconverter", "stage_03_preproc")
    stage3_pre.set("mode", "roi-batch-cumulative")
    stage3_pre.set("image-disposition", "centre")

    stage3_infer = Element("qtimlqnn", "stage_03_inference")
    stage3_infer.set("model", STAGE3_MODEL)
    stage3_infer.set("backend", QNN_BACKEND)
    stage3_infer.set("system", QNN_SYSTEM)
    # Multi-head model -> name all six outputs in qfr's expected order.
    # See "Known SDK fact (Stage 3)" above.
    stage3_infer.set("tensors", [
        "id_feature", "liveness_feature", "eye_closeness",
        "glasses", "mask", "sunglasses",
    ])

    stage3_post = Element("qtimlpostprocess", "stage_03_postproc")
    stage3_post.set("module", "qfr")
    stage3_post.set("labels", STAGE3_LABELS)
    stage3_post.set("settings", STAGE3_SETTINGS)
    stage3_post.set("results", 6)

    stage3_text = TextFilter()
    metamux3 = Element("qtimetamux", "metamux_3")

    # -- overlay + encode to file -------------------------------------------
    overlay = Element("qtivoverlay", "overlay")

    q_enc = Element("queue", "q_enc")
    enc_vf = VideoFilter().format("NV12")

    encoder = Element("v4l2h264enc", "encoder")
    encoder.set("capture-io-mode", 4)
    encoder.set("output-io-mode", 4)

    enc_parser = Element("h264parse", "parse_out")
    mux = Element("mp4mux", "mux")
    sink = Element("filesink", "sink")
    sink.set("location", OUTPUT_FILE)

    # -- assemble pipeline --------------------------------------------------
    pipeline = Pipeline("face-recognition-qnn-mp4-to-file")

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)

    pipeline.add(split1)
    pipeline.add(q_stage1)
    pipeline.add(stage1_pre)
    pipeline.add(stage1_infer)
    pipeline.add(stage1_post)
    pipeline.add_stream_filter("stage1_text", stage1_text)
    pipeline.add(metamux1)

    pipeline.add(split2)
    pipeline.add(q_stage2)
    pipeline.add(stage2_pre)
    pipeline.add(stage2_infer)
    pipeline.add(stage2_post)
    pipeline.add_stream_filter("stage2_text", stage2_text)
    pipeline.add(metamux2)

    pipeline.add(split3)
    pipeline.add(q_stage3)
    pipeline.add(stage3_pre)
    pipeline.add(stage3_infer)
    pipeline.add(stage3_post)
    pipeline.add_stream_filter("stage3_text", stage3_text)
    pipeline.add(metamux3)

    pipeline.add(overlay)
    pipeline.add(q_enc)
    pipeline.add_stream_filter("enc_vf", enc_vf)
    pipeline.add(encoder)
    pipeline.add(enc_parser)
    pipeline.add(mux)
    pipeline.add(sink)

    # source -> decode -> NV12 -> stage 1 split
    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split1")

    # stage 1: passthrough + detection branches merge at metamux_1
    pipeline.link("split1", "metamux_1")
    pipeline.link(
        "split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
        "stage_01_postproc", "stage1_text", "metamux_1",
    )

    # stage 2: metamux_1 output feeds stage 2 split
    pipeline.link("metamux_1", "split2")
    pipeline.link("split2", "metamux_2")
    pipeline.link(
        "split2", "q_stage2", "stage_02_preproc", "stage_02_inference",
        "stage_02_postproc", "stage2_text", "metamux_2",
    )

    # stage 3: metamux_2 output feeds stage 3 split
    pipeline.link("metamux_2", "split3")
    pipeline.link("split3", "metamux_3")
    pipeline.link(
        "split3", "q_stage3", "stage_03_preproc", "stage_03_inference",
        "stage_03_postproc", "stage3_text", "metamux_3",
    )

    # overlay + encode to file
    pipeline.link(
        "metamux_3", "overlay", "q_enc", "enc_vf", "encoder",
        "parse_out", "mux", "sink",
    )

    # Finalize the MP4 container on EOS.
    pipeline.eos(True)
    pipeline.execute()


def main() -> None:
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
