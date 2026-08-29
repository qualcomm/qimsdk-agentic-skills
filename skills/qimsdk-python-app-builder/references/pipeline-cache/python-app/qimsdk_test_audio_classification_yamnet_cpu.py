#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Audio classification (YAMNet) over an MP4-muxed FLAC audio track, with the
video track and the classification label composited side-by-side on a
Wayland display."""

from qimsdk import (
    Element,
    Pipeline,
    VideoFilter,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
)

import os

INPUT_FILE = os.path.expandvars("$HOME/Downloads/qimsdk_samples/media/H264_720p_30fps_FLAC.mp4")
MODEL_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/models/yamnet_float.tflite")
LABELS_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/labels/yamnet.json")

#  Example pipeline:
#
#    src -> demux -+-> [video] parser -> decoder -> [NV12 videofilter] -> qtivcomposer -> display
#                  \-> [audio] flacparse -> flacdec -> audioconvert -> audioresample
#                       -> audiobuffersplit -> qtimlaconverter -> qtimltflite -> qtimlpostprocess
#                       -> [RGBA videofilter] -> qtivcomposer
#
#  qtdemux exposes the video and audio elementary streams from the MP4
#  container on two dynamic pads; qtivcomposer merges the decoded video
#  and the rendered classification-label overlay into a single display.


def create_and_execute_pipeline() -> None:

    # --- source/demux ---
    src = Element("filesrc", "src").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")

    # --- video branch: decode ---
    parser = Element("h264parse", "parser")
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    q_dec = Element("queue", "q_dec")
    vf_video = VideoFilter().format("NV12")
    q_video = Element("queue", "q_video")

    # --- audio branch: decode + feature extraction ---
    q_audio = Element("queue", "q_audio")
    flacparse = Element("flacparse", "flacparse")
    flacdec = Element("flacdec", "flacdec")
    q_pcm = Element("queue", "q_pcm")
    audioconvert = Element("audioconvert", "audioconvert")
    audioresample = Element("audioresample", "audioresample")
    bufsplit = Element("audiobuffersplit", "bufsplit").set("output-buffer-size", 31200)
    q_feat = Element("queue", "q_feat")

    # Extracts LMFE audio features required by the YAMNet model input.
    aconverter = Element("qtimlaconverter", "aconverter").set(
        "sample-rate", 16000,
        "feature", "lmfe",
        "params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;",
    )
    q_infer = Element("queue", "q_infer")

    # Runs YAMNet inference on the extracted audio features (CPU runtime, no delegate).
    infeng = Element("qtimltflite", "infeng").set("model", MODEL_PATH)

    # Decodes the YAMNet output into audio-classification labels.
    postprocess = Element("qtimlpostprocess", "postprocess").set(
        "module", "yamnet",
        "labels", LABELS_PATH,
        "settings", '{"confidence": 10.0}',
        "results", 3,
    )

    # Renders the classification label as a small RGBA overlay frame.
    # qtimlpostprocess emits video/x-raw {RGBA, RGBx} (BGRA fails to link). This
    # audio-overlay topology has no composer pad geometry, so the panel size is
    # pinned here via .resolution(); that is intentional for this app and was
    # device-verified working. (Video-postprocess apps that DO set composer
    # dimensions must NOT pin resolution here — see generation-rules.md.)
    vf_label = VideoFilter().format("RGBA").resolution(368, 64)
    q_label = Element("queue", "q_label")

    # Composites the decoded video and the label overlay into one frame.
    mixer = Element("qtivcomposer", "mixer")

    q_out = Element("queue", "q_out")
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
        .set("sync", True)
    )

    pipeline = Pipeline("audio-classification-pipeline")
    pipeline.add(src)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf_video", vf_video)
    pipeline.add(q_video)
    pipeline.add(q_audio)
    pipeline.add(flacparse)
    pipeline.add(flacdec)
    pipeline.add(q_pcm)
    pipeline.add(audioconvert)
    pipeline.add(audioresample)
    pipeline.add(bufsplit)
    pipeline.add(q_feat)
    pipeline.add(aconverter)
    pipeline.add(q_infer)
    pipeline.add(infeng)
    pipeline.add(postprocess)
    pipeline.add_stream_filter("vf_label", vf_label)
    pipeline.add(q_label)
    pipeline.add(mixer)
    pipeline.add(q_out)
    pipeline.add(display)

    pipeline.link("src", "demux")

    # Video branch: demux's video pad into the decoder, then into the composer.
    pipeline.link(
        "demux", "parser", "decoder", "q_dec", "vf_video", "q_video", "mixer",
    )

    # Audio branch: demux's audio pad into feature extraction, inference, and
    # postprocess, rendered as a label overlay into the composer.
    pipeline.link(
        "demux", "q_audio", "flacparse", "flacdec", "q_pcm", "audioconvert",
        "audioresample", "bufsplit", "q_feat", "aconverter", "q_infer",
        "infeng", "postprocess", "vf_label", "q_label", "mixer",
    )

    pipeline.link("mixer", "q_out", "display")

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
