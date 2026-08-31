// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <iostream>
#include <cstdlib>
#include <stdexcept>

#include <qti/qimsdk.h>

using namespace qti;

const std::string HOME_PATH = [] {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return std::string(home);
}();

//  Example pipeline:
//
//    source → demux → parse → decoder → [NV12] → tee name=split
//      split. → qtimetamux
//      split. → q_ai → qtimlvconverter → q_infer → qtimltflite → q_post
//             → qtimlpostprocess(module=qfd) → [mlf:text] → qtimetamux → qtivoverlay → waylandsink
//
//  Decodes an MP4 file with the hardware H.264 decoder, runs Face Detection
//  Lite (qfd) inference on full frames via the TFLite external delegate on
//  HTP/NPU, overlays detected face bounding boxes, and displays on Wayland.

void create_and_execute_pipeline() {

  // Reads the input MP4 file from disk.
  Element source("filesrc", "src");
  source.set("location", HOME_PATH + "/Downloads/qimsdk_samples/media/15s.mp4");

  // Demultiplexes the MP4 container; SDK handles the dynamic pad-added link internally.
  Element demux("qtdemux", "demux");

  // Parses the H.264 elementary stream before hardware decode.
  Element parser("h264parse", "parse");

  // Hardware-accelerated H.264 decode with zero-copy DMA buffers on both sides.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue immediately after hardware decode.
  Element q_dec("queue", "q_dec");

  // Splits the decoded video into a passthrough branch and an AI branch.
  Element split("tee", "split");

  // Isolates the AI branch from the tee.
  Element q_ai("queue", "q_ai");

  // Converts raw video frames into the model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q_infer("queue", "q_infer");

  // Runs Face Detection Lite inference on the NPU via the TFLite external
  // delegate (QNN HTP backend).
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  inferencing.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/face_det_lite.tflite");

  // Queues raw output tensors before postprocess.
  Element q_post("queue", "q_post");

  // Decodes model output tensors into face detection metadata (module=qfd).
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "qfd");
  postprocessing.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/face_det_lite.json");

  // Merges detection metadata produced by the AI branch with the original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Renders the detected face bounding boxes over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Renders the final annotated video fullscreen on the Wayland display.
  Element display("waylandsink", "display");
  display.set("sync", true);
  display.set("fullscreen", true);

  // Stream filters used to constrain caps on branch links.
  auto videofilter = qti::VideoFilter().format("NV12");
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied.
  Pipeline pipeline("face-detection-pipeline");
  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("videofilter", videofilter)
          .add(split)
          .add(q_ai)
          .add(preprocessing)
          .add(q_infer)
          .add(inferencing)
          .add(q_post)
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "videofilter", "split")
          .link("split", "mlmuxer")
          .link("split", "q_ai", "preprocessing", "q_infer", "inferencing", "q_post", "postprocessing", "mlf", "mlmuxer")
          .link("mlmuxer", "overlay", "display")
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
