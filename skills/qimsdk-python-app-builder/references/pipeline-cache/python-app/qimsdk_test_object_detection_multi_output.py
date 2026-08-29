#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""ISP camera YOLOv5 object detection with simultaneous multi-output.

Runs YOLOv5 object detection on a single ISP camera stream, overlays
bounding boxes, and tees the overlaid stream to three simultaneous outputs:
Wayland display, an MP4 file, and an RTSP re-stream.
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

#  Example pipeline (see README Pipeline Flow for the full element graph):
#
#    camera source -> NV12 filter -> tee name=split
#      split. -> q_meta -> mlmuxer
#      split. -> q_pre -> preprocess -> q_infer -> inference (HTP/NPU)
#             -> q_post -> postprocess -> [mlf:text] -> mlmuxer
#    mlmuxer -> overlay -> tee name=out_split
#      out_split. -> q_disp -> waylandsink
#      out_split. -> q_file -> encoder -> parser -> mp4mux -> filesink
#      out_split. -> q_rtsp -> encoder -> parser -> rtsp server
#
#  The overlaid detection stream is fanned out to all three sinks at once;
#  none of the outputs disable the others.


def create_and_execute_pipeline() -> None:

    # Captures frames from the ISP camera (camera index 0).
    source = Element("qticamsrc", "source").set("camera", 0)

    # Camera resolution/framerate not specified by the request; defaulting
    # to 1920x1080 @ 30fps per skill convention (documented in README).
    videostream = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Splits the camera stream into the video-passthrough branch and the
    # AI branch feeding the detection metadata mux.
    split = Element("tee", "split")

    # Queue decoupling the metadata (video-passthrough) branch off the tee.
    q_meta = Element("queue", "q_meta")

    # Queue decoupling the AI branch off the tee.
    q_pre = Element("queue", "q_pre")

    # Converts raw NV12 frames into the model's input tensor format.
    preprocessing = Element("qtimlvconverter", "preprocessing")

    # Queue between preprocessing and inference.
    q_infer = Element("queue", "q_infer")

    # Runs YOLOv5 (yolov5_float.tflite) inference via the TFLite external
    # delegate targeting the HTP/NPU, per the requested backend.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/yolov5_float.tflite")
        .set("delegate", "external")
        .set("external-delegate-path", "libQnnTFLiteDelegate.so")
        .set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;")
    )

    # Queue between inference and postprocess.
    q_post = Element("queue", "q_post")

    # Decodes inference output tensors into detection metadata.
    # Yolo-V5 uses the yolov8-family postprocess module per the model
    # catalog; confidence threshold set to the requested 51.0.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "yolov8")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/yolov8.json")
        .set("settings", '{"confidence": 51.0}')
        .set("bbox-stabilization", True)
    )

    # Marks the ML branch output as text metadata before muxing.
    mlf = TextFilter()

    # Merges detection metadata back onto the original video buffer.
    mlmuxer = Element("qtimetamux", "mlmuxer")

    # Renders bounding boxes/labels onto the video frame.
    overlay = Element("qtivoverlay", "overlay")

    # Splits the overlaid stream into display, file, and RTSP branches —
    # all three run simultaneously.
    out_split = Element("tee", "out_split")

    # --- Display branch ---
    q_disp = Element("queue", "q_disp")
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", False)
    )

    # --- File (MP4) branch ---
    q_file = Element("queue", "q_file")
    # Camera-fed encode branch: driver manages capture side, encoder
    # imports the (overlay-rendered, camera-originated) DMA buffers.
    file_encoder = (
        Element("v4l2h264enc", "file_encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 5)
    )
    file_parser = Element("h264parse", "file_parser")
    file_mux = Element("mp4mux", "file_mux")
    file_sink = Element("filesink", "file_sink").set(
        "location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/output/detection_output.mp4"
    )

    # --- RTSP branch ---
    q_rtsp = Element("queue", "q_rtsp")
    rtsp_encoder = (
        Element("v4l2h264enc", "rtsp_encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 5)
    )
    # config-interval=-1 inserts SPS/PPS before every IDR frame, required
    # for RTSP clients that can join mid-stream.
    rtsp_parser = Element("h264parse", "rtsp_parser").set("config-interval", -1)
    rtsp_server = (
        Element("qtirtspbin", "rtsp_server")
        .set("address", "0.0.0.0")
        .set("port", "8900")
        .set("mpoint", "/live")
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("yolov5-multi-output-pipeline")
        .add(source)
        .add_stream_filter("videostream", videostream)
        .add(split)
        .add(q_meta)
        .add(q_pre)
        .add(preprocessing)
        .add(q_infer)
        .add(inferencing)
        .add(q_post)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(mlmuxer)
        .add(overlay)
        .add(out_split)
        .add(q_disp)
        .add(display)
        .add(q_file)
        .add(file_encoder)
        .add(file_parser)
        .add(file_mux)
        .add(file_sink)
        .add(q_rtsp)
        .add(rtsp_encoder)
        .add(rtsp_parser)
        .add(rtsp_server)
        .link("source", "videostream", "split")
        .link("split", "q_meta", "mlmuxer")
        .link(
            "split", "q_pre", "preprocessing", "q_infer", "inferencing",
            "q_post", "postprocessing", "mlf", "mlmuxer",
        )
        .link("mlmuxer", "overlay", "out_split")
        .link("out_split", "q_disp", "display")
        .link("out_split", "q_file", "file_encoder", "file_parser", "file_mux", "file_sink")
        .link("out_split", "q_rtsp", "rtsp_encoder", "rtsp_parser", "rtsp_server")
    )

    # Required so mp4mux finalizes the MP4 container on shutdown.
    pipeline.eos(True)

    pipeline.execute()


def main() -> None:

    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
