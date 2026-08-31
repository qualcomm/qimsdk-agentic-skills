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
//    filesrc -> qtdemux -> h264parse -> v4l2h264dec -> [vf NV12] -> tee
//      tee (passthrough) -> qtimetamux -> qtivoverlay -> [render vf NV12] -> encoder -> parser -> muxer -> filesink
//      tee (AI branch)   -> qtimlvconverter -> qtimltflite (YOLOX, HTP external delegate)
//                        -> qtimlpostprocess (module=yolov8, labels, confidence=51.0)
//                        -> [TextFilter] -> qtimetamux
//
//  The pipeline reads an MP4/H.264 file, decodes it with the hardware decoder,
//  runs YOLOX object detection on full frames via the HTP/NPU (TFLite external
//  delegate), overlays bounding boxes and class labels on each frame, encodes
//  the overlaid video with the hardware H.264 encoder, and writes the result
//  to an output MP4 file.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/Downloads/qimsdk_samples/media/15s.mp4");

  // Extracts elementary streams from the MP4 container.
  // Dynamic pad-added linking is handled internally by the SDK.
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

  // Queue immediately after the hardware decoder before any filter/tee.
  Element q_dec("queue", "q_dec");

  // Normalizes decoded output to NV12 before branching/AI preprocessing.
  auto vf = VideoFilter().format("NV12");

  // Splits the normalized video into a passthrough (main video) branch and
  // an AI inference branch.
  Element split("tee", "split");

  // Queue to isolate the AI branch from the tee.
  Element q_ai("queue", "q_ai");

  // Converts raw video frames into normalized tensors for inference.
  Element preprocess("qtimlvconverter", "pre");

  // Runs YOLOX object detection via the TFLite HTP/NPU external delegate.
  Element infer("qtimltflite", "infer");
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
             "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  infer.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/yolox_w8a8.tflite");

  // Decodes YOLOX output tensors into bounding boxes + class labels.
  // module=yolov8 is the documented compatibility mapping for YOLOX detection.
  Element post("qtimlpostprocess", "post");
  post.set("module", "yolov8");
  post.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/yolov8.json");
  post.set("settings", "{\"confidence\": 51.0}");

  // Serialized detection metadata bus feeding qtimetamux.
  auto mlf = TextFilter();

  // Synchronizes AI detection metadata with the original video buffer.
  Element metamux("qtimetamux", "metamux");

  // Draws bounding boxes and class labels on the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Encoder requires NV12 raw video input.
  auto render_vf = VideoFilter().format("NV12");

  // Encodes the overlaid raw video frames into H.264.
  //
  // File/decode source path: driver manages both encoder input and output
  // buffers via dmabuf.
  Element encoder("v4l2h264enc", "encoder");
  encoder.set("output-io-mode", 4);
  encoder.set("capture-io-mode", 4);

  // Parses the encoded H.264 bitstream for muxing.
  Element h264parser("h264parse", "h264parser");

  // Muxes the encoded stream into an MP4 container.
  Element muxer("mp4mux", "muxer");

  // Writes the muxed MP4 stream to the output file.
  Element sink("filesink", "sink");
  sink.set("location", "/tmp/obj_detect_out.mp4");

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied for the tee/metamux branches.
  Pipeline pipeline("qimsdk-cpp-obj-detect-file");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("vf", vf)
          .add(split)
          .add(metamux)
          .add(q_ai)
          .add(preprocess)
          .add(infer)
          .add(post)
          .add_stream_filter("mlf", mlf)
          .add(overlay)
          .add_stream_filter("render_vf", render_vf)
          .add(encoder)
          .add(h264parser)
          .add(muxer)
          .add(sink)
          .eos(true)
          .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split")
          .link("split", "metamux")
          .link("split", "q_ai", "pre", "infer", "post", "mlf", "metamux")
          .link("metamux", "overlay", "render_vf", "encoder", "h264parser", "muxer", "sink")
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
