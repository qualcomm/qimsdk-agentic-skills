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

//  Example pipeline:
//
//    source -> demux -> parse -> decoder -> queue -> VideoFilter(NV12) -> tee name=split
//      split. -> queue -> qtivcomposer sink_0 (left, original video)
//      split. -> queue -> qtimlvconverter -> queue -> qtimltflite -> queue
//             -> qtimlpostprocess(midas-v2) -> VideoFilter(RGBA) -> queue -> qtivcomposer sink_1 (right, depth)
//    qtivcomposer -> waylandsink
//
//  Decodes an MP4 file, runs MiDaS-v2 monocular depth estimation on full
//  frames via the TFLite external (HTP/NPU) delegate, and composes the
//  original video (left) alongside the depth-rendered output (right) into
//  one side-by-side frame for display.

void create_and_execute_pipeline() {

  // Reads the input MP4 file from disk. Element properties do not expand
  // shell variables, so resolve HOME in the application before setting them.
  Element source("filesrc", "source");
  source.set("location", expand_home("/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4"));

  // Demuxes the MP4 container into elementary streams.
  Element demux("qtdemux", "demux");

  // Parses the H.264 bitstream ahead of hardware decode.
  Element parse("h264parse", "parse");

  // Hardware-decodes H.264 video via the Qualcomm decoder.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue immediately after hardware decode (required for DMA writability).
  Element q_dec("queue", "q_dec");

  // Splits the decoded frame into a passthrough branch and a depth-inference branch.
  Element split("tee", "split");

  // Decouples the passthrough branch feeding the composer's left pane.
  Element q_video("queue", "q_video");

  // Decouples the depth-inference branch off the tee.
  Element q_ai("queue", "q_ai");

  // Converts raw video frames into the model's input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues the tensor before inference.
  Element q_infer("queue", "q_infer");

  // Runs MiDaS-v2 monocular depth estimation via the TFLite HTP/NPU external delegate.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("model", expand_home("/Downloads/qimsdk_samples/models/midas_v2_w8a8.tflite"));
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  // Queues output tensors ahead of postprocess.
  Element q_post("queue", "q_post");

  // Decodes the depth-map tensor into a rendered RGBA depth-overlay frame.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "midas-v2");
  postprocessing.set("labels", expand_home("/Downloads/qimsdk_samples/labels/monodepth.json"));

  // Queues the rendered depth frame ahead of the composer.
  Element q_render("queue", "q_render");

  // Composes the passthrough (left) and depth-rendered (right) panes side by side.
  Element composer("qtivcomposer", "composer");

  // Renders the composed side-by-side frame on the Wayland display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  // Stream filters used in branch links.
  // NV12 normalizes decoder output before branching/AI preprocessing.
  auto videostream = qti::VideoFilter().format("NV12");
  // qtimlpostprocess src caps are {RGBA, RGBx} only (BGRA fails to link);
  // no pinned resolution — the composer sink-pad "dimensions" scales this pane.
  auto render_filter = qti::VideoFilter().format("RGBA");

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied.
  Pipeline pipeline("qimsdk-cpp-monodepth-sidebyside");
  pipeline.add(source)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("videostream", videostream)
          .add(split)
          .add(q_video)
          .add(q_ai)
          .add(preprocessing)
          .add(q_infer)
          .add(inferencing)
          .add(q_post)
          .add(postprocessing)
          .add_stream_filter("render_filter", render_filter)
          .add(q_render)
          .add(composer)
          .add(display)
          .link("source", "demux", "parse", "decoder", "q_dec", "videostream", "split")
          .link("split", "q_video", "composer")
          .link("split", "q_ai", "preprocessing", "q_infer", "inferencing", "q_post", "postprocessing", "render_filter", "q_render", "composer")
          .link("composer", "display");

  // Left pane: original decoded video, 960x1080 at (0, 0).
  pipeline.get("composer").input(0).set("position", std::vector<int>{0, 0});
  pipeline.get("composer").input(0).set("dimensions", std::vector<int>{960, 1080});

  // Right pane: depth-overlayed output, 960x1080 at (960, 0).
  pipeline.get("composer").input(1).set("position", std::vector<int>{960, 0});
  pipeline.get("composer").input(1).set("dimensions", std::vector<int>{960, 1080});

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
