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

// Toggle: enable/disable YOLOv8 object-detection overlay on the USB feed.
// The SDK provides no CLI-argument parsing, so this is a source-level
// config switch — flip it and rebuild to change behavior.
static const bool kEnableObjectDetection = true;

//  Example pipeline (detection enabled):
//
//    v4l2src → [YUY2] → transform → [NV12] → tee name=split
//      split. → q_pass → qtimetamux.sink
//      split. → q_pre → qtimlvconverter → q_infer → qtimltflite → q_post
//             → qtimlpostprocess → [mlf:text] → qtimetamux.data_0
//    qtimetamux → q_ovl → qtivoverlay → q_tee2 → tee name=out_split
//      out_split. → q_disp → waylandsink
//      out_split. → q_file → v4l2h264enc → h264parse → mp4mux → filesink
//      out_split. → q_rtsp → v4l2h264enc → h264parse → qtirtspbin
//
//  When detection is disabled, the source feeds the output tee directly
//  (no AI branch, no metadata mux, no overlay).
//
//  The pipeline captures a UVC USB camera, optionally runs YOLOv8 object
//  detection on NPU/HTP and overlays bounding boxes, then simultaneously
//  renders to Wayland display, records an MP4 file, and serves an RTSP
//  stream — all three outputs active at once.

void create_and_execute_pipeline(const std::string &device) {
  // Captures frames from the USB (UVC) camera source. Discover the correct
  // /dev/videoN node with `v4l2-ctl --list-devices` if it differs on your
  // device.
  Element source("v4l2src", "source");
  source.set("device", device);

  // USB cameras typically expose YUY2; pin that before format conversion.
  auto usb_caps = VideoFilter().format("YUY2");

  // Converts YUY2 to NV12 for downstream branching/AI preprocessing.
  Element transform("qtivtransform", "transform");

  // Normalizes the camera stream ahead of branching. No resolution/framerate
  // was provided, so this assumes 1920x1080 @ 30fps (see README assumptions).
  auto videofilter = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);

  Pipeline pipeline("qimsdk-cpp-usb-camera-multi-output");
  pipeline.add(source)
      .add_stream_filter("usb_caps", usb_caps)
      .add(transform)
      .add_stream_filter("videofilter", videofilter);

  // Splits the normalized camera stream into the (optional) AI branch and
  // the passthrough path feeding all outputs.
  Element split("tee", "split");
  pipeline.add(split);
  pipeline.link("source", "usb_caps", "transform", "videofilter", "split");

  // Name of the element whose output feeds the final output-fanout tee.
  // With detection enabled this is the overlay element; otherwise it is
  // the source tee itself.
  std::string pre_output_stage = "split";

  if (kEnableObjectDetection) {
    // Queue decoupling the tee's passthrough branch.
    Element q_pass("queue", "q_pass");

    // Queue decoupling the tee's AI branch before preprocessing.
    Element q_pre("queue", "q_pre");

    // Converts raw video frames into model input tensor format.
    Element preprocessing("qtimlvconverter", "preprocessing");

    // Queue between preprocessing and inference.
    Element q_infer("queue", "q_infer");

    // Executes YOLOv8 on NPU/HTP via the TFLite external (QNN) delegate.
    Element inferencing("qtimltflite", "inferencing");
    inferencing.set("delegate", "external");
    inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
    inferencing.set("external-delegate-options",
                     "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
    inferencing.set("model", expand_home("/Downloads/qimsdk_samples/models/yolov8_det_w8a8.tflite"));

    // Queue between inference and postprocessing.
    Element q_post("queue", "q_post");

    // Decodes model output tensors into detection metadata.
    // bbox-stabilization=true reduces jitter for live camera detection.
    Element postprocessing("qtimlpostprocess", "postprocessing");
    postprocessing.set("module", "yolov8");
    postprocessing.set("labels", expand_home("/Downloads/qimsdk_samples/labels/yolov8.json"));
    postprocessing.set("settings", "{\"confidence\": 51.0}");
    postprocessing.set("bbox-stabilization", true);

    auto mlf = TextFilter();

    // Merges detection metadata with the original video frames.
    Element mlmuxer("qtimetamux", "mlmuxer");

    // Queue before overlay rendering.
    Element q_ovl("queue", "q_ovl");

    // Draws bounding boxes on the live feed.
    Element overlay("qtivoverlay", "overlay");

    pipeline.add(q_pass)
        .add(q_pre)
        .add(preprocessing)
        .add(q_infer)
        .add(inferencing)
        .add(q_post)
        .add(postprocessing)
        .add_stream_filter("mlf", mlf)
        .add(mlmuxer)
        .add(q_ovl)
        .add(overlay);

    pipeline.link("split", "q_pass", "mlmuxer");
    pipeline.link("split", "q_pre", "preprocessing", "q_infer", "inferencing", "q_post",
                  "postprocessing", "mlf", "mlmuxer");
    pipeline.link("mlmuxer", "q_ovl", "overlay");

    pre_output_stage = "overlay";
  }

  // Fans the (optionally overlaid) stream out to all three outputs at once.
  Element out_split("tee", "out_split");
  pipeline.add(out_split);
  pipeline.link(pre_output_stage, "out_split");

  // --- Output 1: Wayland display ---
  Element q_disp("queue", "q_disp");

  // Render video stream on display. sync=false is used for live camera
  // sources per skill defaults; fullscreen=true renders full-screen.
  Element display("waylandsink", "display");
  display.set("sync", false);
  display.set("fullscreen", true);

  pipeline.add(q_disp).add(display);
  pipeline.link("out_split", "q_disp", "display");

  // --- Output 2: MP4 file recording ---
  Element q_file("queue", "q_file");

  // The transformed USB branch supplies a normal NV12 stream; use the
  // driver-managed file-source encoder pairing on this device.
  Element encoder_file("v4l2h264enc", "encoder_file");
  encoder_file.set("capture-io-mode", 4);
  encoder_file.set("output-io-mode", 4);

  Element parser_file("h264parse", "parser_file");

  Element muxer("mp4mux", "muxer");

  Element filesink("filesink", "filesink");
  filesink.set("location", "<OUTPUT_FILE>");

  pipeline.add(q_file).add(encoder_file).add(parser_file).add(muxer).add(filesink);
  pipeline.link("out_split", "q_file", "encoder_file", "parser_file", "muxer", "filesink");

  // --- Output 3: RTSP streaming ---
  Element q_rtsp("queue", "q_rtsp");

  Element encoder_rtsp("v4l2h264enc", "encoder_rtsp");
  encoder_rtsp.set("capture-io-mode", 4);
  encoder_rtsp.set("output-io-mode", 4);

  // config-interval=-1 inserts SPS/PPS into every RTP packet, required for
  // RTSP streaming.
  Element parser_rtsp("h264parse", "parser_rtsp");
  parser_rtsp.set("config-interval", -1);

  // RTSP server sink. address=0.0.0.0 binds all interfaces so remote
  // clients can connect.
  Element rtspbin("qtirtspbin", "rtspbin");
  rtspbin.set("port", "8900");
  rtspbin.set("mpoint", "/live");
  rtspbin.set("address", "0.0.0.0");

  pipeline.add(q_rtsp).add(encoder_rtsp).add(parser_rtsp).add(rtspbin);
  pipeline.link("out_split", "q_rtsp", "encoder_rtsp", "parser_rtsp", "rtspbin");

  // Ensures mp4mux finalizes the MP4 file correctly on shutdown/EOS.
  pipeline.eos(true);

  pipeline.execute();
}

int main(int argc, char **argv) {
  // Route GStreamer logs through the IMSDK logger and enable debug output.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  // V4L2 device node to capture from, defaults to /dev/video2. Discover the
  // correct UVC node with `v4l2-ctl --list-devices` if it differs.
  std::string device = (argc > 1) ? argv[1] : "/dev/video2";

  try {
    create_and_execute_pipeline(device);
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
