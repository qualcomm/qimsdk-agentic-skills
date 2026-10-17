#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Three-stage face recognition daisy-chain from an MP4 file, written to an
output MP4 file. All three stages run on ONNX Runtime (`qtimlonnx`), loading
`.onnx` models on the CPU execution provider.

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
#  the face recognition chain on ONNX Runtime (qtimlonnx) with the CPU
#  execution provider: Stage 1 Face Detection Lite (module=qfd) locates the
#  face, Stage 2 facemap-3dmm (module=lite-3dmm, roi-batch-cumulative) aligns
#  it, Stage 3 face recognition (module=qfr, roi-batch-cumulative) emits the
#  identity match. Each stage's metadata is merged back with its own
#  qtimetamux; qtivoverlay draws the box/landmarks/label, and the annotated
#  stream is hardware-encoded to an output MP4 file.
#
#  This is the qtimlonnx/.onnx sibling of qimsdk_test_face_recognition_qnn_qfr.py
#  (qtimlqnn/.bin) and qimsdk_test_face_recognition_snpe_qfr.py (qtimlsnpe/.dlc):
#  identical topology and the same built-in qfr Stage-3 postprocess, with
#  qtimlqnn/qtimlsnpe swapped for qtimlonnx.
#
#  Known SDK fact (execution provider): the CPU execution provider is compiled
#  into the ONNX Runtime core (libonnxruntime.so), so it runs on any device
#  with no extra install. The QNN/HTP execution provider instead needs ONNX
#  Runtime's own QNN provider plugin on-device (libonnxruntime_providers_qnn.so
#  + libonnxruntime_providers_shared.so), a separate install from the QNN
#  backend libs and not expressible in the pipeline. CPU is therefore the
#  portable, device-independent path. To move onto HTP, install the ORT QNN
#  provider plugin on the device, then set execution-provider=qnn +
#  backend-path=/usr/lib/libQnnHtp.so; the topology is otherwise unchanged.
#
#  Known SDK fact (optimization level): optimization-level=disable-all keeps the
#  model graph intact. qtimlonnx detects NCHW/NHWC layout by inspecting
#  Transpose nodes to set its src caps; aggressive optimization can fold those
#  nodes away, which breaks layout detection and surfaces as empty-caps
#  gst_caps_get_structure assertions at preroll. Disabling optimization keeps
#  the Transpose nodes present so layout/caps negotiate.
#
#  Known SDK fact (no tensors= property): unlike qtimlsnpe/qtimlqnn, qtimlonnx
#  has NO tensors= property to filter or reorder model outputs. ONNX Runtime
#  emits all native model outputs in order, so Stage 1's qfd 3-tensor contract
#  and Stage 3's qfr 6-tensor contract must be satisfied directly by each
#  model's native ONNX output order. See plugin-catalog.md "Tensor Filter -
#  Decision Rule".
#
#  Known SDK fact (Stage 3 model identity): the recognition model is the
#  genuine multi-head model.onnx, whose graph carries all six recognition head
#  names (id_feature[1,512], liveness_feature[1,32], eye_closeness[1,2],
#  glasses[1,2], mask[1,2], sunglasses[1,2]) that module=qfr requires. It stores
#  its weights externally in model.data beside the .onnx, so BOTH files must
#  live in the same directory for qtimlonnx/ORT to load it. The similarly named
#  face-attrib-net-w8a8-w8a8.onnx is a DIFFERENT, attribute-only model
#  (glasses/mask/sunglasses + a probability head; NO id_feature/liveness/
#  eye_closeness) and must NOT be used for qfr. See model-catalog.md
#  "Facial-Attribute-Detection".
#
#  Stage 2 (lite-3dmm) requires /etc/data/{blendShape,meanFace,shapeBasis}.bin
#  on the device (paths hard-coded inside facemap_3dmm_settings.json); without
#  them Stage 2 fails "Failed to open /etc/data/meanFace.bin".

INPUT_FILE = f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4"

# ONNX Runtime execution provider. CPU is built into the ORT core and runs on
# any device; the QNN/HTP provider plugin is a separate device install, so CPU
# is the portable choice. See header note.
ONNX_EP = "cpu"

# ONNX Runtime graph optimization level. disable-all keeps Transpose nodes
# intact so qtimlonnx's layout/caps detection works. See header note.
ONNX_OPT_LEVEL = "disable-all"

# Stage 1 - face detection (face_det_lite, ONNX Runtime on CPU EP).
STAGE1_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/face-det-lite-w8a8-w8a8.onnx"
STAGE1_LABELS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/face_detection.json"

# Stage 2 - face landmark / 3DMM alignment (facemap_3dmm, ONNX Runtime on CPU EP).
STAGE2_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/facemap-3dmm-w8a8-w8a8.onnx"
STAGE2_SETTINGS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/config/facemap_3dmm_settings.json"

# Stage 3 - face recognition / embedding match (multi-head model.onnx, CPU EP).
# model.onnx + its external-weights sidecar model.data must live together.
STAGE3_MODEL = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/model.onnx"
STAGE3_LABELS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/face_recognition.json"
STAGE3_SETTINGS = f"{os.environ['HOME']}/Downloads/qimsdk_samples/config/face_recognition_settings.json"

# Confidence threshold (percent) applied to Stage 1 detection.
STAGE1_CONFIDENCE = 51.0

OUTPUT_FILE = "/tmp/face_recognition_onnx_out.mp4"


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

    stage1_infer = Element("qtimlonnx", "stage_01_inference")
    stage1_infer.set("model", STAGE1_MODEL)
    stage1_infer.set("execution-provider", ONNX_EP)
    stage1_infer.set("optimization-level", ONNX_OPT_LEVEL)
    # CPU EP: no backend-path (meaningful only for the QNN EP). qtimlonnx has no
    # tensors= property, so the model's 3 native outputs must already match
    # qfd's 3-tensor contract in order. See header note.

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

    stage2_infer = Element("qtimlonnx", "stage_02_inference")
    stage2_infer.set("model", STAGE2_MODEL)
    stage2_infer.set("execution-provider", ONNX_EP)
    stage2_infer.set("optimization-level", ONNX_OPT_LEVEL)

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

    stage3_infer = Element("qtimlonnx", "stage_03_inference")
    stage3_infer.set("model", STAGE3_MODEL)
    stage3_infer.set("execution-provider", ONNX_EP)
    stage3_infer.set("optimization-level", ONNX_OPT_LEVEL)
    # CPU EP: no backend-path. This .onnx is the multi-head recognition model
    # exporting all six outputs [1,512][1,32][1,2][1,2][1,2][1,2] in native
    # order; qtimlonnx has no tensors= to filter/reorder, so qfr's 6-tensor
    # caps negotiation relies on the model's native output order. External
    # weights live in model.data beside the .onnx. See header note.

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
    pipeline = Pipeline("face-recognition-onnx-mp4-to-file")

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
