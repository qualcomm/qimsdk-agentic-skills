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
//    source → [videostream] → tee name=split
//      split. → qtimetamux
//      split. → q2 → qtimlvconverter → q3 → qtimltflite → q4
//             → qtimlpostprocess → [mlf:text] → qtimetamux → q5 → qtivoverlay → waylandsink
//
//  The pipeline reads camera frames, runs YOLOv8 inference and postprocessing,
//  overlays detected objects, and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Captures frames from the camera source.
  Element source("qtiqmmfsrc", "source");
  source.set("camera", 0);

  // Splits decoded frames into display and ML branches.
  Element split("tee", "split");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Converts raw video frames into model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q3("queue", "q3");

  // Executes the ML model and attaches tensor outputs to each frame.
  //
  // Configures the model and the hardware delegate used for execution.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  inferencing.set("model", HOME_PATH + "/models/yolov8_det_quantized.tflite");

  // Decodes model output tensors into metadata for downstream overlay.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("results", 5);
  postprocessing.set("module", "yolov8");
  postprocessing.set("labels", HOME_PATH + "/labels/yolov8.json");
  postprocessing.set("settings", "{\"confidence\": 70.0}");

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Queues data between pipeline stages.
  Element q5("queue", "q5");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=false disables strict rendering synchronization to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("sync", false);
  display.set("fullscreen", true);

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto videostream = qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied
  Pipeline pipeline("ml-cam-pipeline");
  pipeline.add(source)
          .add_stream_filter("videostream", videostream)
          .add(split)
          .add(q2)
          .add(preprocessing)
          .add(q3)
          .add(inferencing)
          .add("queue", "q4")
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(q5)
          .add(overlay)
          .add(display)
          .link("split", "mlmuxer")
          .link("source", "videostream", "split", "q2", "preprocessing", "q3", "inferencing", "q4", "postprocessing", "mlf", "mlmuxer", "q5", "overlay", "display")
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
