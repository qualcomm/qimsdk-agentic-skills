/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

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
//    src -> demux -> parse -> decoder -> [videofilter NV12] -> tee
//      tee passthrough -> qtimetamux -> qtivoverlay -> display
//      tee AI branch    -> qtimlvconverter -> qtimltflite (HTP/NPU) ->
//                          qtimlpostprocess (module=mobilenet) -> [TextFilter] -> qtimetamux
//
//  Reads an MP4/H.264 file, decodes it via the hardware decoder, runs
//  MobileNet-v2 (w8a8) image classification on full frames via TFLite
//  external delegate on HTP/NPU, and overlays the top classification
//  label/confidence on the video before rendering to display.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/Downloads/qimsdk_samples/media/15s.mp4");

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

  // Prepares the H.264 bitstream for the decoder.
  Element parse("h264parse", "parse");

  // Decodes the compressed H.264 stream into raw video frames.
  // I/O modes are set for DMA buffer usage to avoid unnecessary copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue immediately after the hardware decoder before any branching.
  Element q_dec("queue", "q_dec");

  // Normalizes decoded output to NV12 before branching/AI preprocessing.
  auto videofilter = VideoFilter().format("NV12");

  // AI branch: isolates the inference path from the passthrough branch.
  Element q_ai("queue", "q_ai");

  // Converts NV12 video frames into normalized input tensors for the model.
  Element preprocess("qtimlvconverter", "preprocess");

  // Runs MobileNet-v2 (w8a8) classification via TFLite external delegate on HTP/NPU.
  Element inference("qtimltflite", "inference");
  inference.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite");
  inference.set("delegate", "external");
  inference.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inference.set("external-delegate-options",
                "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  // Parses classification tensors into top-N labels above the confidence threshold.
  Element postprocess("qtimlpostprocess", "postprocess");
  postprocess.set("module", "mobilenet");
  postprocess.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/mobilenet.json");
  postprocess.set("settings", "{\"confidence\": 51.0}");
  postprocess.set("results", 5);

  // Carries serialized classification metadata into the metadata muxer.
  auto mlfilter = TextFilter();

  // Synchronizes classification metadata with the original video buffer.
  Element metamux("qtimetamux", "metamux");

  // Draws the top label and confidence score onto the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Renders the annotated video stream on display.
  Element display("waylandsink", "display");
  display.set("sync", true);
  display.set("fullscreen", true);

  Pipeline pipeline("image-classification-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("videofilter", videofilter)
          .add("tee", "split")
          .add(q_ai)
          .add(preprocess)
          .add(inference)
          .add(postprocess)
          .add_stream_filter("mlfilter", mlfilter)
          .add(metamux)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "videofilter", "split")
          .link("split", "metamux")
          .link("split", "q_ai", "preprocess", "inference", "postprocess", "mlfilter", "metamux")
          .link("metamux", "overlay", "display");

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
