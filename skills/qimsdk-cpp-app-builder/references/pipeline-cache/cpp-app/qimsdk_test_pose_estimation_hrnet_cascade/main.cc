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

// Stage paths are resolved at runtime; C++ strings do not expand `$HOME`.
const std::string INPUT_FILE =
    expand_home("/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4");
const std::string STAGE1_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/person_foot_detection_w8a8.tflite");
const std::string STAGE1_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/foot_track_net.json");
const std::string STAGE1_SETTINGS =
    expand_home("/Downloads/qimsdk_samples/labels/foot_track_net_settings.json");
const std::string STAGE2_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/hrnetpose_w8a8.tflite");
const std::string STAGE2_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/hrnet.json");
const std::string STAGE2_SETTINGS =
    expand_home("/Downloads/qimsdk_samples/labels/hrnet_settings.json");

//  Example pipeline:

//  Example pipeline:
//
//    src → demux → parse → decoder → q_dec → [videofilter] → split1
//      split1. → metamux1
//      split1. → q_stage1 → qpd detection → mlf1 → metamux1
//      metamux1 → split2
//      split2. → metamux2
//      split2. → q_stage2 → HRNet ROI pose → mlf2 → metamux2
//      metamux2 → qtivoverlay → waylandsink
//
//  HRNet is a top-down model: person-foot detection supplies the ROIs needed
//  by the second-stage HRNet pose inference.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", INPUT_FILE);

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

  // Prepares the H.264 bitstream for the decoder.
  Element parse("h264parse", "parse");

  // Decodes the compressed H.264 stream into raw video frames.
  //
  // The I/O mode is configured to enforce DMA buffer usage,
  // avoiding unnecessary buffer copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue placed immediately after the hardware decoder.
  Element q_dec("queue", "q_dec");

  // Stage 1 splits full frames into passthrough and person-foot detection.
  Element split1("tee", "split1");
  Element q_stage1("queue", "q_stage1");

  Element stage1_preproc("qtimlvconverter", "stage1_preproc");
  stage1_preproc.set("mode", "image-batch-non-cumulative");
  Element stage1_infer("qtimltflite", "stage1_infer");
  stage1_infer.set("delegate", "external");
  stage1_infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  stage1_infer.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  stage1_infer.set("model", STAGE1_MODEL);
  Element stage1_post("qtimlpostprocess", "stage1_post");
  stage1_post.set("module", "qpd");
  stage1_post.set("labels", STAGE1_LABELS);
  stage1_post.set("settings", STAGE1_SETTINGS);
  stage1_post.set("results", 10);
  auto mlf1 = TextFilter();
  Element metamux1("qtimetamux", "metamux1");

  // Stage 2 consumes the stage-1 person ROIs for top-down HRNet.
  Element split2("tee", "split2");
  Element q_stage2("queue", "q_stage2");
  Element stage2_preproc("qtimlvconverter", "stage2_preproc");
  stage2_preproc.set("mode", "roi-batch-cumulative");
  Element stage2_infer("qtimltflite", "stage2_infer");
  stage2_infer.set("delegate", "external");
  stage2_infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  stage2_infer.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  stage2_infer.set("model", STAGE2_MODEL);
  Element stage2_post("qtimlpostprocess", "stage2_post");
  stage2_post.set("module", "hrnet");
  stage2_post.set("labels", STAGE2_LABELS);
  stage2_post.set("settings", STAGE2_SETTINGS);
  stage2_post.set("results", 2);
  auto mlf2 = TextFilter();
  Element metamux2("qtimetamux", "metamux2");

  // Stage-1 and stage-2 inference elements are configured above.
  // Each stage merges its own metadata with the corresponding full-frame/ROI video.

  // Renders the skeleton keypoint metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // sync=true keeps rendering synchronized to the pipeline clock, which is
  // the default for ordinary single-file playback.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("sync", true);
  display.set("fullscreen", true);

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto videofilter = qti::VideoFilter().format("NV12");
  auto mlf1_unused = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied for the tee/qtimetamux fan-in branches.
  Pipeline pipeline("hrnet-pose-cascade-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("videofilter", videofilter)
          .add(split1)
          .add(q_stage1)
          .add(stage1_preproc)
          .add(stage1_infer)
          .add(stage1_post)
          .add_stream_filter("mlf1", mlf1)
          .add(metamux1)
          .add(split2)
          .add(q_stage2)
          .add(stage2_preproc)
          .add(stage2_infer)
          .add(stage2_post)
          .add_stream_filter("mlf2", mlf2)
          .add(metamux2)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "videofilter", "split1")
          .link("split1", "metamux1")
          .link("split1", "q_stage1", "stage1_preproc", "stage1_infer", "stage1_post", "mlf1", "metamux1")
          .link("metamux1", "split2")
          .link("split2", "metamux2")
          .link("split2", "q_stage2", "stage2_preproc", "stage2_infer", "stage2_post", "mlf2", "metamux2")
          .link("metamux2", "overlay", "display")
          .execute();
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
