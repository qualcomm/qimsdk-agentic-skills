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
//    filesrc → qtdemux → h264parse → v4l2h264dec
//            → qtimlvideotflitebin → qtivoverlay → waylandsink
//
//  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
//  runs YOLOv8 object detection with the Qualcomm TFLite delegate, overlays the
//  detected objects, and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/media/video.mp4");

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

  auto vf = VideoFilter().format("NV12");

  // Executes the ML model and attaches the results to the corresponding video frame.
  //
  // Configures the model, the hardware that executes it (delegate),
  // as well as the postprocessing algorithm and the label file.
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
  // sync=true keeps rendering synchronized to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Linking is implicit and follows the order in which elements are added.
  Pipeline pipeline("mlbin-pipeline");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add_stream_filter("vf", vf)
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
