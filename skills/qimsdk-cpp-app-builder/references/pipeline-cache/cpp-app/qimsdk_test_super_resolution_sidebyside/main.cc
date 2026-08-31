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

void create_and_execute_pipeline() {
  Pipeline pipeline("qimsdk-cpp-super-resolution-sidebyside");

  // Source / decode: hardware H.264 decode of the input MP4
  Element source("filesrc", "src");
  source.set("location", expand_home("/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4"));

  Element demux("qtdemux", "demux");
  Element parser("h264parse", "parse");

  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  Element q_dec("queue", "q_dec");

  // Split into passthrough branch and AI super-resolution branch
  Element split("tee", "split");
  Element q_video("queue", "q_video");
  Element q_ai("queue", "q_ai");

  // AI branch: preprocess -> QuickSRNet inference (HTP/NPU) -> postprocess
  Element preproc("qtimlvconverter", "pre");

  Element infer("qtimltflite", "infer");
  infer.set("model", expand_home("/Downloads/qimsdk_samples/models/quicksrnetlarge_w8a8.tflite"));
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
             "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  Element postproc("qtimlpostprocess", "post");
  postproc.set("module", "srnet");

  // srnet emits rendered video; use RGBA, which is supported by the
  // qtimlpostprocess src caps on the target device.
  Element q_post("queue", "q_post");
  auto render_filter = qti::VideoFilter().format("RGBA");

  // Compose original (left) and upscaled (right) side-by-side.
  // Source is 1080p, so each pane gets half the width at full height.
  Element composer("qtivcomposer", "mixer");

  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add(split)
          .add(q_video)
          .add(q_ai)
          .add(preproc)
          .add(infer)
          .add(postproc)
          .add(q_post)
          .add_stream_filter("render_filter", render_filter)
          .add(composer)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "split")
          .link("split", "q_video", "mixer")
          .link("split", "q_ai", "pre", "infer", "post", "q_post", "render_filter", "mixer")
          .link("mixer", "display");

  // Side-by-side layout: passthrough (sink_0) left, upscaled SR output (sink_1) right.
  pipeline.get("mixer").input(0).set("position", std::vector<int>{0, 0});
  pipeline.get("mixer").input(0).set("dimensions", std::vector<int>{960, 1080});
  pipeline.get("mixer").input(1).set("position", std::vector<int>{960, 0});
  pipeline.get("mixer").input(1).set("dimensions", std::vector<int>{960, 1080});

  pipeline.execute();
}

int main() {
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
