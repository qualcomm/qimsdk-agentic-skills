#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Single-stage image classification Python app, file-to-file.

Inception-v3 classification on ONNX Runtime (qtimlonnx) with the CPU
execution provider and optimization disabled, decoded from an MP4 file and
re-encoded to an output MP4 file.
"""

import os

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    TextFilter,
    VideoFilter,
)

#  Example pipeline:
#
#    source -> demux -> parser -> decoder -> q_dec -> [NV12 vf] -> tee name=split
#      split. -> metamux                                   (passthrough video)
#      split. -> q_cls -> pre -> infer -> post -> [text] -> metamux.
#      metamux -> overlay -> q_enc -> [NV12 vf] -> encoder -> parse_out
#              -> mux -> filesink
#
#  The pipeline decodes an MP4/H.264 file with the hardware decoder, runs
#  Inception-v3 classification on full frames via ONNX Runtime on the CPU
#  execution provider, overlays the top label with qtivoverlay, and
#  hardware-encodes the annotated stream to one output MP4 file.


def create_and_execute_pipeline() -> None:

    # -- source / decode ----------------------------------------------------

    # Reads the encoded MP4 file from disk.
    source = (
        Element("filesrc", "source")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/ai_demo_sample.mp4")
    )

    # Demuxes the MP4 container to extract the H.264 elementary stream.
    demux = Element("qtdemux", "demux")

    # Parses the H.264 stream for the hardware decoder.
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required to decouple the decoder.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before branching.
    vf = VideoFilter().format("NV12")

    # Single-input tee: one leg is the passthrough video, the other runs
    # classification; their metadata merges at qtimetamux.
    split = Element("tee", "split")

    # -- classification stage (Inception-v3, ONNX Runtime CPU) --------------

    # Queues frames from the tee into the classification branch.
    q_cls = Element("queue", "q_cls")

    # Converts raw video frames into the model input tensor format.
    cls_pre = (
        Element("qtimlvconverter", "cls_preproc")
        .set("mode", "image-batch-non-cumulative")
    )

    # Runs Inception-v3 classification on ONNX Runtime.
    #
    # Known SDK fact (execution provider): the CPU execution provider is
    # compiled into the ONNX Runtime core (libonnxruntime.so), so it runs on
    # any device with no extra install, unlike the QNN/HTP provider plugin.
    #
    # Known SDK fact (optimization level): optimization-level=disable-all keeps
    # the model graph intact. qtimlonnx inspects Transpose nodes to detect
    # NCHW/NHWC layout for its src caps; aggressive optimization can fold those
    # away and break layout/caps negotiation at preroll.
    #
    # Known SDK fact (no tensors= property): qtimlonnx has no tensors= property
    # to filter/reorder model outputs, so the model's native ONNX output order
    # must satisfy the module=mobilenet classification contract directly.
    cls_infer = (
        Element("qtimlonnx", "cls_inference")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/inception_v3.onnx")
        .set("execution-provider", "cpu")
        .set("optimization-level", "disable-all")
    )

    # Decodes classification tensor output into label metadata.
    cls_post = (
        Element("qtimlpostprocess", "cls_postproc")
        .set("module", "mobilenet")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/classification.txt")
    )

    # Stream filter marking the classification branch output as text metadata.
    cls_text = TextFilter()

    # Merges classification metadata with the passthrough video frames.
    metamux = Element("qtimetamux", "metamux")

    # -- overlay + encode to file -------------------------------------------

    # Renders the classification label overlay on the frame.
    overlay = Element("qtivoverlay", "overlay")

    # Queues frames before the hardware encoder.
    q_enc = Element("queue", "q_enc")

    # Normalizes to NV12 for the hardware encoder.
    enc_vf = VideoFilter().format("NV12")

    # Hardware-encodes the annotated stream to H.264 with DMA IO modes.
    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Parses the encoded H.264 stream for the muxer.
    enc_parser = Element("h264parse", "parse_out")

    # Muxes the encoded stream into an MP4 container.
    mux = Element("mp4mux", "mux")

    # Writes the output MP4 file to disk.
    sink = (
        Element("filesink", "sink")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/output/image_classification_onnx_cpu_out.mp4")
    )

    # -- assemble pipeline --------------------------------------------------
    pipeline = Pipeline("image-classification-onnx-cpu")

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(split)

    pipeline.add(q_cls)
    pipeline.add(cls_pre)
    pipeline.add(cls_infer)
    pipeline.add(cls_post)
    pipeline.add_stream_filter("cls_text", cls_text)
    pipeline.add(metamux)

    pipeline.add(overlay)
    pipeline.add(q_enc)
    pipeline.add_stream_filter("enc_vf", enc_vf)
    pipeline.add(encoder)
    pipeline.add(enc_parser)
    pipeline.add(mux)
    pipeline.add(sink)

    # source -> decode -> NV12 -> tee
    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split")

    # passthrough video leg + classification leg merge at metamux
    pipeline.link("split", "metamux")
    pipeline.link(
        "split", "q_cls", "cls_preproc", "cls_inference",
        "cls_postproc", "cls_text", "metamux",
    )

    # overlay the label, then encode to the output file
    pipeline.link(
        "metamux", "overlay", "q_enc", "enc_vf", "encoder",
        "parse_out", "mux", "sink",
    )

    # Finalize the MP4 container on EOS. Required: CPU ONNX inference runs well
    # below realtime, so the file-to-file run must drain to natural EOS for
    # mp4mux to write the moov atom and produce a playable output file.
    pipeline.eos(True)
    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
