/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

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
//    src → demux → parse → decoder → [videofilter] → mlbin1 → mlbin2 → overlay → display
//
//  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
//  overlays detected objects, and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/media/ppe_video.mp4");

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

  // Executes the ML model and attaches the results to the corresponding video frame.
  //
  // Configures the model, the hardware that executes it (delegate),
  // as well as the postprocessing algorithm and the label file.
  Element mlbin1("qtimlvideotflitebin", "mlbin1");
  mlbin1.set("inference-delegate", "external");
  mlbin1.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
  mlbin1.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  mlbin1.set("inference-model", HOME_PATH + "/models/foot_track_net-person-foot-detection-w8a8.tflite");
  mlbin1.set("postprocess-module", "qpd");
  mlbin1.set("postprocess-labels", HOME_PATH + "/labels/foot_track_net.json");
  mlbin1.set("postprocess-settings", HOME_PATH + "/labels/foot_track_net_settings.json");

  // Executes the ML model and attaches the results to the corresponding video frame.
  //
  // Configures the model, the hardware that executes it (delegate),
  // as well as the postprocessing algorithm and the label file.
  Element mlbin2("qtimlvideotflitebin", "mlbin2");
  mlbin2.set("preprocess-mode", "roi-batch-cumulative");
  mlbin2.set("inference-delegate", "external");
  mlbin2.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
  mlbin2.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  mlbin2.set("inference-model", HOME_PATH + "/models/gear_guard_net-ppe-detection-w8a8.tflite");
  mlbin2.set("postprocess-module", "yolov8");
  mlbin2.set("postprocess-labels", HOME_PATH + "/labels/gear_guard_net.json");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=true keeps rendering synchronized to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto videofilter = VideoFilter().format("NV12");

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Linking is implicit and follows the order in which elements are added.
  Pipeline pipeline("mlbin-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add_stream_filter("videofilter", videofilter)
          .add(mlbin1)
          .add(mlbin2)
          .add(overlay)
          .add(display)
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
