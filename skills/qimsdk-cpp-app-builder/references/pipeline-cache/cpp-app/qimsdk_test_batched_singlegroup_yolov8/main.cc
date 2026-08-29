/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

// qimsdk-cpp-batched-singlegroup-yolov8
//
// Single-batch-group batched YOLOv8 object detection C++ app: 4 MP4 decode
// streams share ONE qtimltflite HTP/NPU inference instance via qtibatch (mux
// 4 streams in) / qtimldemux (split back to 4 per-stream results). Each
// stream keeps its own qtimlpostprocess and its own pair of qtivcomposer
// sink pads (raw passthrough + RGBA detection mask), composited into a 4x1
// row on a single Wayland display. This is the minimal shape of the batched
// multi-stream pattern (one group); see qimsdk_test_batched_multistream_-
// yolov8_grid for the 12-stream / 3-group variant.
//
// Two device-verified construction requirements this file encodes:
//   1. The batch-group qtimldemux MUST NOT reuse the "demux_" name prefix
//      that the per-stream qtdemux elements use -- "demux_<stream>" and a
//      "demux_<group>" collide, and gst_bin_add() aborts on the duplicate
//      name ("Failed to add external element"). This file names it
//      "mldemux_0".
//   2. A tee (split_<i>) has multiple request src pads; a single chained
//      pipeline.link() call only takes ONE leg off it. BOTH legs
//      (passthrough -> qpass, AI -> qai) are linked with their own explicit
//      pipeline.link() calls, or the passthrough leg is left dangling
//      (NOT_LINKED: composer gets no video, preroll never completes).
//
// Stack-local qti::Element wrappers inside helper functions with
// pipeline.execute() -- the wrappers' GstElements are owned by the pipeline
// bin after pipeline.add(), so the local wrappers going out of scope is fine.

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <sys/resource.h>
#include <unistd.h>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

constexpr int kNumStreams = 4;
constexpr int kBatchGroupSize = 4;

// Media/model/label paths are resolved under $HOME at startup. C++ string
// literals do NOT expand shell variables, so a literal "$HOME/..." would be
// passed byte-for-byte to open() and fail; resolve HOME in code with an
// explicit unset check. Replace these relative paths with the actual
// on-device locations if they differ.
std::string home_path() {
  const char *home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return home;
}

std::string input_file()  { return home_path() + "/Downloads/qimsdk_samples/media/office.mp4"; }
std::string model_path()  { return home_path() + "/Downloads/qimsdk_samples/models/yolov8_det_quantized_batch_4.tflite"; }
std::string labels_path() { return home_path() + "/Downloads/qimsdk_samples/labels/yolov8.json"; }

constexpr int kGridCols = 4;
constexpr int kTileWidth = 480;
constexpr int kTileHeight = 270;

void raise_fd_limit() {
  struct rlimit rl{10000, 10000};
  if (setrlimit(RLIMIT_NOFILE, &rl) != 0) {
    std::cerr << "Warning: failed to raise fd limit programmatically; "
                 "run 'ulimit -n 10000' before launching this app.\n";
  }
}

// Agent's original style: Element objects are LOCAL to this function.
void build_stream_source(Pipeline &pipeline, int idx) {
  const std::string sfx = std::to_string(idx);

  Element source("filesrc", "src_" + sfx);
  source.set("location", input_file());
  Element demux("qtdemux", "demux_" + sfx);
  Element parse("h264parse", "parse_" + sfx);
  Element decoder("v4l2h264dec", "dec_" + sfx);
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);
  Element q_dec("queue", "qdec_" + sfx);

  pipeline.add(source).add(demux).add(parse).add(decoder).add(q_dec);
  pipeline.add_stream_filter("vf_" + sfx, VideoFilter().format("NV12"));

  Element split("tee", "split_" + sfx);
  Element q_pass("queue", "qpass_" + sfx);
  Element q_ai("queue", "qai_" + sfx);
  pipeline.add(split).add(q_pass).add(q_ai);

  pipeline.link("src_" + sfx, "demux_" + sfx, "parse_" + sfx, "dec_" + sfx,
                "qdec_" + sfx, "vf_" + sfx, "split_" + sfx);

  // FIX #3: link BOTH tee legs explicitly (the passthrough leg was missing).
  pipeline.link("split_" + sfx, "qpass_" + sfx);
  pipeline.link("split_" + sfx, "qai_" + sfx);
}

void build_batch_group(Pipeline &pipeline) {
  Element batch("qtibatch", "batch_0");
  Element q_batch("queue", "qbatch_0");
  Element converter("qtimlvconverter", "mlv_0");
  Element q_mlv("queue", "qmlv_0");
  Element infer("qtimltflite", "infer_0");
  infer.set("model", model_path());
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,htp_device_id=(string)0,"
            "htp_performance_mode=(string)2,log_level=(string)1;");
  Element q_infer("queue", "qinfer_0");

  // FIX #1: mldemux_ (not demux_) to avoid collision with per-stream qtdemux.
  Element demux("qtimldemux", "mldemux_0");

  pipeline.add(batch).add(q_batch).add(converter).add(q_mlv)
      .add(infer).add(q_infer).add(demux);

  pipeline.link("batch_0", "qbatch_0", "mlv_0", "qmlv_0", "infer_0");
  pipeline.link("infer_0", "qinfer_0", "mldemux_0");

  for (int k = 0; k < kBatchGroupSize; ++k) {
    const std::string sfx = std::to_string(k);

    pipeline.link("qai_" + sfx, "batch_0");

    Element q_post("queue", "qpost_" + sfx);
    Element post("qtimlpostprocess", "post_" + sfx);
    post.set("module", "yolov8");
    post.set("labels", labels_path());
    post.set("settings", "{\"confidence\": 51.0}");
    Element q_mask("queue", "qmask_" + sfx);

    pipeline.add(q_post).add(post).add(q_mask);
    pipeline.add_stream_filter("render_vf_" + sfx, VideoFilter().format("RGBA"));

    pipeline.link("mldemux_0", "qpost_" + sfx, "post_" + sfx,
                  "render_vf_" + sfx, "qmask_" + sfx);
  }
}

void link_composer_pads(Pipeline &pipeline) {
  for (int i = 0; i < kNumStreams; ++i) {
    const std::string sfx = std::to_string(i);
    const int col = i % kGridCols;
    const std::vector<int> position{col * kTileWidth, 0};
    const std::vector<int> dimensions{kTileWidth, kTileHeight};

    pipeline.link("qpass_" + sfx, "composer");
    pipeline.get("composer").input(2 * i)
        .set("position", position).set("dimensions", dimensions);

    pipeline.link("qmask_" + sfx, "composer");
    pipeline.get("composer").input(2 * i + 1)
        .set("position", position).set("dimensions", dimensions);
  }
}

void create_and_execute_pipeline() {
  Pipeline pipeline("qimsdk-cpp-batched-singlegroup-yolov8");

  Element composer("qtivcomposer", "composer");
  pipeline.add(composer);

  for (int i = 0; i < kNumStreams; ++i) {
    build_stream_source(pipeline, i);
  }

  build_batch_group(pipeline);
  link_composer_pads(pipeline);

  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", false);
  pipeline.add(display);
  pipeline.link("composer", "display");

  // Agent's original: execute() (NOT prepare/start/wait/stop).
  pipeline.execute();
}

}  // namespace

int main() {
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  raise_fd_limit();

  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
