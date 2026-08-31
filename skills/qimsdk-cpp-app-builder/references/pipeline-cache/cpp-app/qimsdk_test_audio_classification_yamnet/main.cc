// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

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

//  Example pipeline:
//
//    src -> demux --(video)--> parse -> decoder -> [NV12] -> composer(sink_0) -> display
//                 --(audio)--> flacparse -> flacdec -> audioconvert -> audioresample
//                              -> audiobuffersplit -> qtimlaconverter(lmfe)
//                              -> qtimltflite(yamnet) -> qtimlpostprocess(module=yamnet)
//                              -> [RGBA label panel] -> composer(sink_1) -> display
//
//  qtdemux exposes video and audio as separate dynamic pads; both branches are
//  linked explicitly from "demux" and the SDK resolves each link by caps.
//  Audio classification results are rendered as an RGBA label panel and
//  composited over the video via qtivcomposer (Pattern A1g) rather than routed
//  through TextFilter/qtimetamux.

void create_and_execute_pipeline() {

  // Reads the input MP4 file (H.264 video + FLAC audio) as raw bytes.
  Element src("filesrc", "src");
  src.set("location", INPUT_FILE);

  // Demultiplexes the MP4 container into elementary video and audio streams.
  Element demux("qtdemux", "demux");

  // --- Video branch: decode H.264 and normalize to NV12 ---

  Element q_video("queue", "q_video");
  Element parse("h264parse", "parse");

  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  Element q_dec("queue", "q_dec");

  // --- Audio branch: decode FLAC, extract LMFE features, run YAMNet ---

  Element q_audio_source("queue", "q_audio_source");
  Element flac_parse("flacparse", "flac_parse");
  Element flac_dec("flacdec", "flac_dec");
  Element q_audio_convert("queue", "q_audio_convert");
  Element audio_convert("audioconvert", "audio_convert");
  Element audio_resample("audioresample", "audio_resample");

  Element q_audio("queue", "q_audio");

  // Splits the audio stream into fixed-size chunks matching the classifier's
  // expected feature window.
  Element audio_split("audiobuffersplit", "audio_split");
  audio_split.set("output-buffer-size", 31200);

  Element q_feat("queue", "q_feat");

  // Converts raw audio samples into LMFE (log mel filterbank energy) tensors.
  Element audio_pre("qtimlaconverter", "audio_pre");
  audio_pre.set("sample-rate", 16000);
  audio_pre.set("feature", "lmfe");
  audio_pre.set("params", "params,nfft=96,nhop=160,nmels=64,chunklen=0.96;");

  Element q_infer("queue", "q_infer");

  // Runs YAMNet audio classification.
  Element infer("qtimltflite", "infer");
  infer.set("model", MODEL_PATH);

  Element q_post("queue", "q_post");

  // Decodes classifier output tensors into a rendered label panel.
  Element post("qtimlpostprocess", "post");
  post.set("module", "yamnet");
  post.set("labels", LABELS_PATH);
  post.set("settings", "{\"confidence\": 10.0}");
  post.set("results", 3);

  Element q_comp_audio("queue", "q_comp_audio");

  // Composites the video frame and the audio-classification label panel.
  //
  // qtimlpostprocess src caps for rendered output are {RGBA, RGBx} only.
  auto video_filter = VideoFilter().format("NV12");
  auto render_filter = VideoFilter().format("RGBA");

  Element composer("qtivcomposer", "composer");

  // Render on display. Audio classification pipelines omit sync per the
  // sample-app convention.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  Pipeline pipeline("audio-classification-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(q_video)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("video_filter", video_filter)
          .add(q_audio_source)
          .add(flac_parse)
          .add(flac_dec)
          .add(q_audio_convert)
          .add(q_audio)
          .add(audio_convert)
          .add(audio_resample)
          .add(audio_split)
          .add(q_feat)
          .add(audio_pre)
          .add(q_infer)
          .add(infer)
          .add(q_post)
          .add(post)
          .add_stream_filter("render_filter", render_filter)
          .add(q_comp_audio)
          .add(composer)
          .add(display)
          .link("src", "demux")
          .link("demux", "q_video", "parse", "decoder", "q_dec", "video_filter", "composer")
          .link("demux", "q_audio_source", "flac_parse", "flac_dec", "q_audio_convert",
                "q_audio", "audio_convert", "audio_resample", "audio_split", "q_feat",
                "audio_pre", "q_infer", "infer", "q_post", "post", "render_filter",
                "q_comp_audio", "composer")
          .link("composer", "display");

  // Size the audio-classification label panel over the composited video; the
  // panel occupies a fixed-size overlay region rather than the full frame.
  pipeline.get("composer").input(1).set("position", std::vector<int>{50, 50});
  pipeline.get("composer").input(1).set("dimensions", std::vector<int>{368, 64});

  pipeline.execute();
}

int main() {
  // Route GStreamer logs through the IMSDK logger and enable debug output.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
