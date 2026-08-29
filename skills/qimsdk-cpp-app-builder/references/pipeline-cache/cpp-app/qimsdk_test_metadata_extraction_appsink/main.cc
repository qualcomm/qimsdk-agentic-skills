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
//    filesrc -> qtdemux -> h264parse -> v4l2h264dec -> [NV12] -> tee name=split
//      split. -> queue -> qtimetamux(obj_mux) -> qtivoverlay -> waylandsink
//      split. -> queue -> qtimlvconverter -> queue -> qtimltflite -> queue
//              -> qtimlpostprocess -> tee name=meta_split
//                   meta_split. -> queue -> [TextFilter] -> obj_mux.
//                   meta_split. -> queue -> AppSink (app-side metadata parsing)
//
//  The pipeline reads an MP4/H.264 file, decodes it, runs YOLOv8 object
//  detection on the Qualcomm HTP/NPU, overlays bounding boxes on the display
//  branch, and also delivers the raw serialized detection metadata to an
//  AppSink for application-side parsing.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/Downloads/qimsdk_samples/media/15s.mp4");

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

  // Queue immediately after the hardware decoder.
  Element decq("queue", "decq");

  // Splits decoded frames into the display branch and the AI branch.
  Element split("tee", "split");

  // Queues the passthrough branch feeding the metadata mux.
  Element q_video("queue", "q_video");

  // Queues frames before tensor conversion.
  Element q_pre("queue", "q_pre");

  // Converts raw video frames into model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q_infer("queue", "q_infer");

  // Executes the ML model on the Qualcomm HTP/NPU via the TFLite external delegate.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  inferencing.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/yolov8_det_w8a8.tflite");

  // Queues tensor output before postprocessing.
  Element q_post("queue", "q_post");

  // Decodes model output tensors into serialized detection metadata (text/x-raw).
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "yolov8");
  postprocessing.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/yolov8.json");
  postprocessing.set("settings", "{\"confidence\": 51.0}");
  postprocessing.set("bbox-stabilization", true);

  // Splits detection metadata into a display branch and an app-side parsing branch.
  Element meta_split("tee", "meta_split");

  // Queues metadata bound for the overlay mux.
  Element q_meta_mux("queue", "q_meta_mux");

  // Queues metadata bound for application-side consumption.
  Element q_meta_app("queue", "q_meta_app");

  // Merges detection metadata with the original video frames.
  Element mlmuxer("qtimetamux", "obj_mux");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // sync=true keeps rendering synchronized to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  // Stream filters used in branch links.
  auto vf = VideoFilter().format("NV12");
  auto textf = TextFilter();

  // Application-side metadata parser: consumes the raw serialized detection
  // text/x-raw buffers emitted by qtimlpostprocess.
  //
  // TODO: parse the incoming buffer's serialized GstStructure text
  // (ObjectDetection / bounding-boxes) into application-specific data as
  // needed; this placeholder only logs the raw buffer size.
  AppSink meta_sink("meta_sink");
  meta_sink.set("sync", false);
  meta_sink.set("emit-signals", true);
  meta_sink.set_buffer_consumer([](qti::Buffer buffer) {
    std::cout << "Metadata buffer received, size=" << buffer.size() << " bytes"
              << std::endl;
  });

  // Creates the pipeline, adds and links elements, and executes it.
  Pipeline pipeline("metadata-parser-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(decq)
          .add_stream_filter("vf", vf)
          .add(split)
          .add(q_video)
          .add(mlmuxer)
          .add(overlay)
          .add(display)
          .add(q_pre)
          .add(preprocessing)
          .add(q_infer)
          .add(inferencing)
          .add(q_post)
          .add(postprocessing)
          .add(meta_split)
          .add(q_meta_mux)
          .add_stream_filter("textf", textf)
          .add(q_meta_app)
          .add(meta_sink)
          .link("src", "demux", "parse", "decoder", "decq", "vf", "split")
          .link("split", "q_video", "obj_mux")
          .link("obj_mux", "overlay", "display")
          .link("split", "q_pre", "preprocessing", "q_infer", "inferencing", "q_post", "postprocessing", "meta_split")
          .link("meta_split", "q_meta_mux", "textf", "obj_mux")
          .link("meta_split", "q_meta_app", "meta_sink")
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
