// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <iostream>
#include <cstdlib>
#include <stdexcept>

#include <qti/qimsdk.h>

using namespace qti;

namespace {
std::string expand_home_path() {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return std::string(home);
}
}  // namespace

const std::string HOME_PATH = expand_home_path();

//  Example pipeline:
//
//    source -> [vf] -> encoder -> parser -> muxer -> sink
//
//  The pipeline reads camera frames, encodes them with H.264, muxes into MP4,
//  and writes the output to disk.

void create_and_execute_pipeline() {

  // Captures frames from the camera source.
  Element source("qtiqmmfsrc", "source");

  // Encodes raw video frames into H.264 stream.
  Element encoder("v4l2h264enc", "encoder");
  encoder.set("output-io-mode", "dmabuf-import");
  encoder.set("capture-io-mode", "dmabuf");

  // Parses H.264 bitstream for downstream muxing.
  Element parser("h264parse", "parser");

  // Muxes encoded stream into MP4 container.
  Element muxer("mp4mux", "muxer");

  // Writes output stream to a file.
  Element sink("filesink", "sink");
  sink.set("location", HOME_PATH + "/media/encoder_output.mp4");

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto vf = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied.
  Pipeline pipeline("cam-encoder-pipeline");
  pipeline.add(source)
          .add_stream_filter("vf", vf)
          .add(encoder)
          .add(parser)
          .add(muxer)
          .add(sink)
          .eos(true)
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
