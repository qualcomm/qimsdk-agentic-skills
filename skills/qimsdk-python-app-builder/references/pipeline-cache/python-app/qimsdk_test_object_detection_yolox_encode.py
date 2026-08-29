#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Single-stream YOLOX object detection from an MP4 file, encoded to an output MP4 file."""

import os

from qimsdk import Element, Pipeline, TextFilter, VideoFilter

#  Example pipeline:
#
#    src -> demux -> parse -> decoder -> q_dec -> [vf:NV12] -> tee name=split
#      split. -> obj_mux -> qtivoverlay -> encoder -> parse_out -> mux -> filesink
#      split. -> q1 -> preprocessing -> q2 -> inferencing -> q4
#             -> postprocessing -> [mlf:text] -> q5 -> obj_mux.
#
#  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
#  runs YOLOX (yolov8 postprocess module) object detection on full frames using
#  the HTP/NPU external delegate, overlays the detected bounding boxes and class
#  labels on each frame, and hardware-encodes the annotated stream to an output
#  MP4 file.

INPUT_FILE = f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/15s.mp4"
MODEL_PATH = f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/yolox_w8a8.tflite"
LABELS_PATH = f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/yolov8.json"
OUTPUT_FILE = "/tmp/obj_detect_out.mp4"


def create_and_execute_pipeline() -> None:

    # Reads the input MP4 file as raw bytes.
    source = (
        Element("filesrc", "source")
        .set("location", INPUT_FILE)
    )

    # Extracts the elementary H.264 stream from the MP4 container.
    demux = Element("qtdemux", "demux")

    # Prepares the H.264 bitstream for the decoder.
    parser = Element("h264parse", "parser")

    # Decodes the compressed H.264 stream into raw video frames.
    #
    # DMA I/O modes are used to avoid unnecessary buffer copies. This is a
    # file source decoded through the hardware decoder, so both io-modes are 4.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode to decouple the decoder thread.
    q_dec = Element("queue", "q_dec")

    # Restricts the decoded stream to NV12 before branching for AI/overlay.
    videofilter = VideoFilter().format("NV12")

    # Splits decoded frames into a video/overlay branch and an AI branch.
    split = Element("tee", "split")

    # Queues frames from the tee into the AI branch.
    q1 = Element("queue", "q1")

    # Converts raw NV12 video frames into the model input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queues converted tensors before inference.
    q2 = Element("queue", "q2")

    # Runs YOLOX object detection on full frames using the TFLite external
    # delegate targeting the HTP/NPU.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", MODEL_PATH)
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    )

    # Queues tensor outputs before postprocessing.
    q4 = Element("queue", "q4")

    # Decodes YOLOX model output tensors into detection metadata.
    # YOLOX detection uses the yolov8 postprocess module per the model catalog.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "yolov8")
        .set("labels", LABELS_PATH)
        .set("settings", '{"confidence": 51.0}')
    )

    # Stream filter marking the AI branch output as text metadata.
    mlf = TextFilter()

    # Queues metadata before it is merged back with the video branch.
    q5 = Element("queue", "q5")

    # Merges detection metadata produced by the AI branch with the original
    # video frames from the passthrough branch.
    obj_mux = Element("qtimetamux", "obj_mux")

    # Renders detected bounding boxes and class labels on top of the frame.
    overlay = Element("qtivoverlay", "overlay")

    # Hardware-encodes the annotated NV12 stream back to H.264.
    #
    # File source path decoded via hardware decoder: capture/output io-mode 4.
    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Prepares the encoded H.264 stream for muxing.
    parse_out = Element("h264parse", "parse_out")

    # Multiplexes the H.264 stream into an MP4 container.
    mux = Element("mp4mux", "mux")

    # Writes the final annotated video to the output MP4 file.
    sink = (
        Element("filesink", "sink")
        .set("location", OUTPUT_FILE)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    pipeline = (
        Pipeline("obj-detect-file-pipeline")
        .add(source)
        .add(demux)
        .add(parser)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("videofilter", videofilter)
        .add(split)
        .add(q1)
        .add(preprocessing)
        .add(q2)
        .add(inferencing)
        .add(q4)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(q5)
        .add(obj_mux)
        .add(overlay)
        .add(encoder)
        .add(parse_out)
        .add(mux)
        .add(sink)
        .link("source", "demux", "parser", "decoder", "q_dec", "videofilter", "split")
        .link("split", "obj_mux")
        .link("split", "q1", "preprocessing", "q2", "inferencing", "q4",
              "postprocessing", "mlf", "q5", "obj_mux")
        .link("obj_mux", "overlay", "encoder", "parse_out", "mux", "sink")
    )

    # Ensures the MP4 muxer finalizes the container correctly on EOS.
    pipeline.eos(True)

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
