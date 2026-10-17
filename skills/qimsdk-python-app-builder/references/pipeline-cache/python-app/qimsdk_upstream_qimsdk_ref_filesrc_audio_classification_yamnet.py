#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""MP4 file audio classification using YamNet."""

import argparse
import os
import sys


class HelpOnErrorArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        sys.stderr.write(f"\n{self.prog}: error: {message}\n")
        sys.exit(2)


# Base path for sample assets (models/, labels/ live under it).
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
                    default=f"{os.environ['HOME']}/Downloads/qimsdk_samples/media/audio_video_sample.mp4",
                    help="Input MP4 file path (H.264 video + FLAC audio)")
args = parser.parse_args()

model_base_path = args.model_base_path
input_config = args.input_config

from qimsdk import AudioFilter, AudioFormat, Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    src -> demux -+-> vqueue -> vparse -> vdecoder -> [vf] -> composer(sink_0)
#                  \-> aq1 -> aparse -> adecoder -> aq2 -> convert -> resample
#                     -> [af] -> splitter -> aq3 -> converter -> q1
#                     -> inferencing -> postprocessing -> [mlf] -> aq4
#                     -> composer(sink_1)
#    composer -> oq -> display
#
#  The pipeline reads an MP4 file with H.264 video and FLAC audio tracks. The
#  video stream is decoded and passed straight to the composer. The audio
#  stream is decoded, normalized to signed 16-bit PCM, split into fixed-size
#  buffers, converted into YamNet's model input tensor format, and
#  classified. The composer overlays the classification result as a label
#  card on top of the video before displaying the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Reads the input MP4 file as raw bytes.
    src = (
        Element("filesrc", "src")
        .set("location", input_config)
    )

    # Extracts elementary streams from the MP4 container.
    demux = Element("qtdemux", "demux")

    # Buffers compressed video access units while the audio-classification
    # branch accumulates its first inference window (~2s at 16kHz for
    # YamNet's 31200-sample buffer). qtivcomposer withholds output until
    # both sink pads have a buffer, so the default queue size is not enough
    # to absorb that startup latency without stalling the demuxer.
    vqueue = (
        Element("queue", "vqueue")
        .set("max-size-buffers", 0)
        .set("max-size-bytes", 0)
        .set("max-size-time", 5000000000)
    )

    # Prepares the H.264 bitstream for the decoder.
    vparse = Element("h264parse", "vparse")

    # Decodes the compressed H.264 stream into raw video frames.
    #
    # The I/O mode is configured to enforce DMA buffer usage,
    # avoiding unnecessary buffer copies.
    vdecoder = (
        Element("v4l2h264dec", "vdecoder")
        .set("output-io-mode", 4)
        .set("capture-io-mode", 4)
    )

    # Restricts the video stream to NV12 before it reaches the composer.
    vf = VideoFilter().format("NV12")

    # Queues data between pipeline stages.
    aq1 = Element("queue", "aq1")

    # Prepares the FLAC bitstream for the decoder.
    aparse = Element("flacparse", "aparse")

    # Decodes the compressed FLAC stream into raw PCM audio.
    adecoder = Element("flacdec", "adecoder")

    # Queues data between pipeline stages.
    aq2 = Element("queue", "aq2")

    # Converts the decoded audio to a format audioresample can operate on.
    convert = Element("audioconvert", "convert")

    # Resamples the audio stream to the rate expected by the ML converter.
    resample = Element("audioresample", "resample")

    # Restricts the audio stream to signed 16-bit PCM.
    af = AudioFilter().format(AudioFormat.S16LE)

    # Splits the raw audio stream into fixed-size buffers sized for YamNet's
    # expected input window.
    splitter = (
        Element("audiobuffersplit", "splitter")
        .set("output-buffer-size", 31200)
    )

    # Queues data between pipeline stages.
    aq3 = Element("queue", "aq3")

    # Converts raw audio buffers into model input tensor format.
    converter = (
        Element("qtimlaconverter", "converter")
        .set("sample-rate", 16000)
    )

    # Queues converted tensors before inference.
    q1 = Element("queue", "q1")

    # Executes the ML model and attaches classification tensor outputs to
    # each buffer.
    inferencing = (
        Element("qtimltflite", "inferencing")
        .set("model", f"{model_base_path}/models/yamnet.tflite")
    )

    # Decodes model output tensors into an audio classification label card.
    #
    # results caps the number of classifications kept per inference window;
    # settings sets the minimum confidence (percent) a label must reach to
    # be reported.
    postprocessing = (
        Element("qtimlpostprocess", "postprocessing")
        .set("module", "yamnet")
        .set("labels", f"{model_base_path}/labels/yamnet.json")
        .set("settings", '{"confidence": 10.0}')
        .set("results", 3)
    )

    # Restricts the classification label card to a fixed size before it
    # reaches the composer.
    #
    # qtimlpostprocess only emits RGBA or RGBx frames; BGRA is not supported.
    mlf = VideoFilter().format("RGBA").resolution(368, 64)

    # Queues data between pipeline stages.
    aq4 = Element("queue", "aq4")

    # Composites the video stream and the classification label card into a
    # single output frame.
    composer = Element("qtivcomposer", "composer")

    # Queues data between pipeline stages.
    oq = Element("queue", "oq")

    # Render the composited stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the pipeline, adds and links elements, and executes it.
    #
    # Explicit linking is applied to branch the demuxed streams into the
    # video and audio-classification paths, and to merge them back at the
    # composer.
    pipeline = (
        Pipeline("audio-classification-yamnet")
        .add(src)
        .add(demux)
        .add(vqueue)
        .add(vparse)
        .add(vdecoder)
        .add_stream_filter("vf", vf)
        .add(aq1)
        .add(aparse)
        .add(adecoder)
        .add(aq2)
        .add(convert)
        .add(resample)
        .add_stream_filter("af", af)
        .add(splitter)
        .add(aq3)
        .add(converter)
        .add(q1)
        .add(inferencing)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(aq4)
        .add(composer)
        .add(oq)
        .add(display)

        .link("src", "demux")
        .link("demux", "vqueue", "vparse", "vdecoder", "vf", "composer")
        .link("demux", "aq1", "aparse", "adecoder", "aq2", "convert",
              "resample", "af", "splitter", "aq3", "converter", "q1",
              "inferencing", "postprocessing", "mlf", "aq4", "composer")
        .link("composer", "oq", "display")
    )

    pipeline.get("composer").input(1).set("position", "<50, 50>")
    pipeline.get("composer").input(1).set("dimensions", "<368, 64>")

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
