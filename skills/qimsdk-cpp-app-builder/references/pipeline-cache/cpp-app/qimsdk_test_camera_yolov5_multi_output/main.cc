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
//    source(camera=0) → [videostream:NV12 1920x1080@30] → tee name=split
//      split. → mlmuxer
//      split. → q_ai → qtimlvconverter → q_infer → qtimltflite(yolov5_float, HTP external delegate)
//             → q_post → qtimlpostprocess(module=yolov8, bbox-stabilization) → [mlf:text] → mlmuxer
//      mlmuxer → q_ovl → qtivoverlay → tee name=out
//        out. → q_disp → waylandsink
//        out. → q_file → v4l2h264enc → h264parse → mp4mux → filesink
//        out. → q_rtsp → v4l2h264enc → h264parse(config-interval=-1) → qtirtspbin
//
//  The pipeline reads ISP camera frames, runs YOLOv5 (mapped to the yolov8
//  postprocess module per model-catalog.md) object detection on HTP/NPU,
//  overlays bounding boxes, then fans the single overlaid stream out to
//  three simultaneous sinks: Wayland display, an MP4 file, and an RTSP
//  server mount point.

void create_and_execute_pipeline() {

  // Captures frames from the ISP camera source (camera=0).
  Element source("qticamsrc", "source");
  source.set("camera", 0);

  // Splits the camera feed into the passthrough branch and the ML branch.
  Element split("tee", "split");

  // Queue on the AI branch, isolating it from the passthrough branch.
  Element q_ai("queue", "q_ai");

  // Converts raw video frames into model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queue before inference.
  Element q_infer("queue", "q_infer");

  // Executes the YOLOv5 model on HTP/NPU via the TFLite external delegate.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  inferencing.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/yolov5_float.tflite");

  // Queue before postprocessing.
  Element q_post("queue", "q_post");

  // Decodes model output tensors into detection metadata.
  // module=yolov8 per model-catalog.md (yolov5_float.tflite maps to the
  // yolov8 postprocess module); bbox-stabilization is enabled for the live
  // camera source.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "yolov8");
  postprocessing.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/yolov8.json");
  postprocessing.set("settings", "{\"confidence\": 51.0}");
  postprocessing.set("bbox-stabilization", true);

  // Merges detection metadata with the original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Queue between metadata mux and overlay.
  Element q_ovl("queue", "q_ovl");

  // Renders bounding boxes over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Splits the single overlaid stream into three simultaneous output branches.
  Element out_split("tee", "out_split");

  // --- Display branch ---
  Element q_disp("queue", "q_disp");

  // Render video stream on display.
  //
  // sync=false and enable-last-sample=false avoid clock stalls since display
  // runs alongside the file and RTSP encode branches.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("sync", false);
  display.set("fullscreen", true);
  display.set("enable-last-sample", false);

  // --- MP4 file branch ---
  Element q_file("queue", "q_file");

  // Hardware H.264 encoder for the file branch.
  // output-io-mode=5 (dmabuf-import) imports the camera-derived DMA buffer;
  // capture-io-mode=4 (dmabuf) lets the driver manage encoder output.
  Element file_encoder("v4l2h264enc", "file_encoder");
  file_encoder.set("capture-io-mode", 4);
  file_encoder.set("output-io-mode", 5);

  Element file_parser("h264parse", "file_parser");

  Element mp4_mux("mp4mux", "mp4_mux");

  Element filesink("filesink", "filesink");
  filesink.set("location", HOME_PATH + "/Downloads/qimsdk_samples/output/15s.mp4");

  // --- RTSP branch ---
  Element q_rtsp("queue", "q_rtsp");

  // Hardware H.264 encoder for the RTSP branch.
  Element rtsp_encoder("v4l2h264enc", "rtsp_encoder");
  rtsp_encoder.set("capture-io-mode", 4);
  rtsp_encoder.set("output-io-mode", 5);

  // config-interval=-1 inserts SPS/PPS before every IDR frame, required for
  // RTSP streaming.
  Element rtsp_parser("h264parse", "rtsp_parser");
  rtsp_parser.set("config-interval", -1);

  // RTSP server sink; clients connect via rtsp://<device-ip>:8900/live.
  Element rtsp_server("qtirtspbin", "rtsp_server");
  rtsp_server.set("address", "0.0.0.0");
  rtsp_server.set("port", "8900");
  rtsp_server.set("mpoint", "/live");

  // Stream filters used in branch links.
  auto videostream = qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  // eos(true) ensures mp4mux finalizes the MP4 file correctly on shutdown.
  Pipeline pipeline("cam-yolov5-multi-output-pipeline");
  pipeline.eos(true);
  pipeline.add(source)
          .add_stream_filter("videostream", videostream)
          .add(split)
          .add(q_ai)
          .add(preprocessing)
          .add(q_infer)
          .add(inferencing)
          .add(q_post)
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(q_ovl)
          .add(overlay)
          .add(out_split)
          .add(q_disp)
          .add(display)
          .add(q_file)
          .add(file_encoder)
          .add(file_parser)
          .add(mp4_mux)
          .add(filesink)
          .add(q_rtsp)
          .add(rtsp_encoder)
          .add(rtsp_parser)
          .add(rtsp_server)
          .link("source", "videostream", "split")
          .link("split", "mlmuxer")
          .link("split", "q_ai", "preprocessing", "q_infer", "inferencing", "q_post", "postprocessing", "mlf", "mlmuxer")
          .link("mlmuxer", "q_ovl", "overlay", "out_split")
          .link("out_split", "q_disp", "display")
          .link("out_split", "q_file", "file_encoder", "file_parser", "mp4_mux", "filesink")
          .link("out_split", "q_rtsp", "rtsp_encoder", "rtsp_parser", "rtsp_server")
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
