/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

std::string expand_home(const std::string& suffix) {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem path: " + suffix);
  }
  return std::string(home) + suffix;
}

}  // namespace

const std::string INPUT_FILE =
    expand_home("/Downloads/qimsdk_samples/media/H264_720p_30fps_FLAC.mp4");
const std::string MODEL_PATH =
    expand_home("/Downloads/qimsdk_samples/models/yamnet_float.tflite");
const std::string LABELS_PATH =
    expand_home("/Downloads/qimsdk_samples/labels/yamnet.json");

namespace {

//  Example pipeline:
//
//    src -> demux -> [video] parse -> decoder -> [vf:NV12] -> composer.sink_0
//                 -> [audio] flacparse -> flacdec -> audioconvert -> audioresample
//                    -> audiobuffersplit -> mlaconverter(lmfe) -> inferencing(cpu)
//                    -> postprocessing(yamnet) -> [render:BGRA] -> composer.sink_1
//    composer -> display
//
//  Reads an MP4 file containing a FLAC audio track, demuxes video and audio,
//  decodes the H.264 video for display, extracts audio features (LMFE) and
//  runs YAMNet audio classification on CPU, and composites the classification
//  label panel over the decoded video via qtivcomposer onto a Wayland display.

void create_and_execute_pipeline() {
  // Reads the input MP4 file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", INPUT_FILE);

  // Demultiplexes the MP4 container into video and audio elementary streams.
  // The SDK completes qtdemux's dynamic pad-added links internally for a
  // straight-line hop into the next added elements.
  Element demux("qtdemux", "demux");

  // --- Video branch: decode -> composer passthrough ---

  // Prepares the H.264 bitstream for the decoder.
  Element q_video("queue", "q_video");
  Element vparse("h264parse", "vparse");

  // Decodes the compressed H.264 stream into raw video frames.
  // I/O modes are set to dmabuf on both sides to avoid unnecessary buffer copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // --- Audio branch: FLAC decode -> feature extraction -> inference -> postprocess ---

  // Prepares the FLAC bitstream for the decoder.
  Element q_audio_source("queue", "q_audio_source");
  Element aparse("flacparse", "aparse");

  // Decodes the FLAC audio stream into raw PCM samples.
  Element adec("flacdec", "adec");
  Element q_pcm("queue", "q_pcm");

  // Normalizes decoded PCM to a format qtimlaconverter can consume.
  Element aconv("audioconvert", "aconv");

  // Resamples PCM to the 16kHz rate YAMNet expects.
  Element aresample("audioresample", "aresample");

  // Splits the continuous PCM stream into fixed-size chunks for feature
  // extraction (31200 samples ~= 1.95s at 16kHz, matching the corpus example).
  Element abuffersplit("audiobuffersplit", "abuffersplit");
  abuffersplit.set("output-buffer-size", 31200);

  // Extracts LMFE (log mel filterbank energy) features into model input tensors.
  Element mlaconverter("qtimlaconverter", "mlaconverter");
  mlaconverter.set("sample-rate", 16000);
  mlaconverter.set("feature", "lmfe");
  mlaconverter.set("params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;");

  // Executes the YAMNet TFLite model on CPU (delegate=none).
  //
  // runtime: cpu per request -> no external/gpu delegate is configured.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "none");
  inferencing.set("model", MODEL_PATH);

  // Postprocesses model outputs into audio classification metadata and
  // renders them as an RGBA label panel (audio-classification category
  // supports both text/x-raw and video/x-raw; RGBA image-mask output is
  // selected here via the downstream render VideoFilter caps).
  //
  // Confidence 51.0 is the user-provided postprocess confidence threshold,
  // encoded per the canonical settings JSON contract.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "yamnet");
  postprocessing.set("labels", LABELS_PATH);
  postprocessing.set("settings", "{\"confidence\": 51.0}");

  // --- Composite audio label panel over video and display ---

  // Composes the passthrough video and the audio-classification label panel
  // into a single output frame using the GPU compositor.
  Element composer("qtivcomposer", "composer");


  // Renders the composited frame on the Wayland display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  // Stream filters used in branch links.
  auto video_vf = VideoFilter().format("NV12");
  auto render_vf = VideoFilter().format("RGBA").resolution(368, 64);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // demux's video and audio dynamic pads each continue in insertion order
  // into their respective branch; both branches terminate at the composer's
  // two request sink pads.
  Pipeline pipeline("qimsdk-cpp-audio-classification-cpu");
  pipeline.add(src)
      .add(demux)
      .add(q_video)
      .add(vparse)
      .add(decoder)
      .add_stream_filter("video_vf", video_vf)
      .add(q_audio_source)
      .add(aparse)
      .add(adec)
      .add(q_pcm)
      .add(aconv)
      .add(aresample)
      .add(abuffersplit)
      .add(mlaconverter)
      .add(inferencing)
      .add(postprocessing)
      .add_stream_filter("render_vf", render_vf)
      .add(composer)
      .add(display)
      .link("src", "demux")
      .link("demux", "q_video", "vparse", "decoder", "video_vf", "composer")
      .link("demux", "q_audio_source", "aparse", "adec", "q_pcm", "aconv",
            "aresample", "abuffersplit", "mlaconverter", "inferencing",
            "postprocessing", "render_vf", "composer")
      .link("composer", "display");

  // Position the classification label panel in the corner of the video frame.
  // The panel size is fixed by render_vf; do not set composer pad geometry
  // here because this CPU audio path can deadlock during composer preroll.

  pipeline.execute();
}

}  // namespace

int main() {
  // Route GStreamer logs through the IMSDK logger and enable debug output.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  try {
    create_and_execute_pipeline();
  } catch (const std::exception& ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
