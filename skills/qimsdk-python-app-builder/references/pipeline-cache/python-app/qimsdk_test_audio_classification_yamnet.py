#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""YAMNet audio classification Python app with video overlay via composer."""

import os

from qimsdk import Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    source -> demux
#      demux video pad -> q_vid -> parser -> decoder -> [NV12 vf] -> q_comp -> composer.sink_0
#      demux audio pad -> q_aud -> flacparse -> flacdec -> q_conv -> audioconvert -> audioresample
#          -> audiobuffersplit -> q_feat -> qtimlaconverter -> q_infer -> qtimltflite
#          -> qtimlpostprocess(module=yamnet) -> [RGBA render vf] -> q_render -> composer.sink_1
#      composer -> display
#
#  The pipeline demuxes an MP4 file containing H.264 video and FLAC audio.
#  The video path decodes and feeds the composer directly. The audio path
#  decodes FLAC, converts audio into LMFE features, runs YAMNet classification
#  on the HTP/NPU-free CPU path, and renders the classification labels as a
#  RGBA overlay panel composited onto the video via qtivcomposer.


def create_and_execute_pipeline() -> None:

    # Reads the encoded MP4 file (H.264 video + FLAC audio) from disk.
    source = (
        Element("filesrc", "source")
        .set("location", f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/H264_720p_30fps_FLAC.mp4")
    )

    # Demuxes the MP4 container; exposes separate video and audio pads.
    demux = Element("qtdemux", "demux")

    # --- Video branch ---

    # Queue to decouple the demux video pad.
    q_vid = Element("queue", "q_vid")

    # Parses the H.264 elementary stream for the hardware decoder.
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4, "output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required to decouple the decoder.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before compositing.
    videostream = VideoFilter().format("NV12")

    # Queue before the composer video input.
    q_comp = Element("queue", "q_comp")

    # --- Audio branch ---

    # Queue to decouple the demux audio pad.
    q_aud = Element("queue", "q_aud")

    # Parses the FLAC elementary stream.
    flac_parser = Element("flacparse", "flac_parser")

    # Decodes FLAC audio to raw PCM.
    flac_decoder = Element("flacdec", "flac_decoder")

    # Queue between audio decode and audio conversion.
    q_conv = Element("queue", "q_conv")

    # Converts raw PCM into a canonical audio format for feature extraction.
    audioconvert = Element("audioconvert", "audioconvert")

    # Resamples audio to the rate expected by the feature converter.
    audioresample = Element("audioresample", "audioresample")

    # Splits the audio stream into fixed-size buffers for the feature converter.
    bufsplit = (
        Element("audiobuffersplit", "bufsplit")
        .set("output-buffer-size", 31200)
    )

    # Queue before feature extraction.
    q_feat = Element("queue", "q_feat")

    # Converts audio buffers into LMFE (log-mel filterbank energy) feature tensors.
    featconv = (
        Element("qtimlaconverter", "featconv")
        .set("sample-rate", 16000)
        .set("feature", "lmfe")
        .set("params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;")
    )

    # Queue before inference.
    q_infer = Element("queue", "q_infer")

    # Runs YAMNet audio event classification. YAMNet float uses no delegate
    # (CPU path) per the model catalog.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", f"{os.environ['HOME']}/Downloads/qimsdk_samples/models/yamnet_float.tflite")
    )

    # Decodes the classification tensor output into labeled results.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "yamnet")
        .set("labels", f"{os.environ['HOME']}/Downloads/qimsdk_samples/labels/yamnet.json")
        .set("settings", '{"confidence": 10.0}')
        .set("results", 3)
    )

    # Renders the classification output as an RGBA panel for compositing.
    # qtimlpostprocess only emits {RGBA, RGBx} (BGRA fails to link); leave the size
    # unpinned (pinning it makes postproc caps fixation fail) — the composer sink-pad
    # dimensions below scale the panel to 368x64.
    render_filter = VideoFilter().format("RGBA")

    # Queue before the composer audio-overlay input.
    q_render = Element("queue", "q_render")

    # --- Composer + display ---

    # Composites the video branch (sink_0) and the audio classification
    # overlay panel (sink_1) into a single frame.
    composer = Element("qtivcomposer", "composer")

    # Renders the composited stream fullscreen on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied.
    pipeline = (
        Pipeline("audio-classification-pipeline")
        .add(source)
        .add(demux)
        .add(q_vid)
        .add(parser)
        .add(decoder)
        .add(q_dec)
        .add_stream_filter("videostream", videostream)
        .add(q_comp)
        .add(q_aud)
        .add(flac_parser)
        .add(flac_decoder)
        .add(q_conv)
        .add(audioconvert)
        .add(audioresample)
        .add(bufsplit)
        .add(q_feat)
        .add(featconv)
        .add(q_infer)
        .add(inferencing)
        .add(postprocessing)
        .add_stream_filter("render_filter", render_filter)
        .add(q_render)
        .add(composer)
        .add(display)
        .link("source", "demux")
        .link("demux", "q_vid", "parser", "decoder", "q_dec", "videostream", "q_comp", "composer")
        .link("demux", "q_aud", "flac_parser", "flac_decoder", "q_conv", "audioconvert",
              "audioresample", "bufsplit", "q_feat", "featconv", "q_infer", "inferencing",
              "postprocessing", "render_filter", "q_render", "composer")
        .link("composer", "display")
    )

    # Position the audio classification overlay panel in the composited frame.
    pipeline.get("composer").input(1).set("position", [50, 50])
    pipeline.get("composer").input(1).set("dimensions", [368, 64])

    pipeline.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
