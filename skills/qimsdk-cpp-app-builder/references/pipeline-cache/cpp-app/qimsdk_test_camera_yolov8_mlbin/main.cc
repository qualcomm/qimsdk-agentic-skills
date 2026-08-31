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
//    source → [videofilter] → qtimlvideotflitebin → qtivoverlay → waylandsink
//
//  The pipeline captures camera frames, runs YOLOv8 inference and postprocessing
//  in the ML bin, overlays detected objects, and displays the result.

void create_and_execute_pipeline() {

  // Captures frames from the camera source.
  Element source("qtiqmmfsrc", "source");
  source.set("camera", 0);

  // Executes ML inference and postprocessing.
  //
  // Configures the model, the execution delegate and postprocessing labels.
  Element mlbin("qtimlvideotflitebin", "mlbin");
  mlbin.set("inference-delegate", "external");
  mlbin.set("inference-external-delegate-path", "libQnnTFLiteDelegate.so");
  mlbin.set("inference-external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  mlbin.set("inference-model", HOME_PATH + "/models/yolov8_det_quantized.tflite");
  mlbin.set("postprocess-module", "yolov8");
  mlbin.set("postprocess-labels", HOME_PATH + "/labels/yolov8.json");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=false disables strict rendering synchronization to the pipeline clock.
  Element display("waylandsink", "display");
  display.set("sync", false);
  display.set("fullscreen", true);

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto videofilter = qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Linking is implicit and follows the order in which elements are added.
  Pipeline pipeline("camera-yolov8-mlbin-pipeline");
  pipeline.add(source)
          .add_stream_filter("videofilter", videofilter)
          .add(mlbin)
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
