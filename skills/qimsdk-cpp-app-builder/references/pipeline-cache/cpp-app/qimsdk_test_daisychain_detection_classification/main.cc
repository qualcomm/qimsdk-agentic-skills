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

//  Example pipeline (two-stage daisy-chain, two-mux topology):
//
//    src -> demux -> parse -> decoder -> [NV12] -> split1
//      split1 (passthrough) -----------------------------> metamux_1
//      split1 (stage 1: YOLOX detection) -> stage_01_preproc -> stage_01_inference
//        -> stage_01_postproc -> [TextFilter] -----------> metamux_1
//      metamux_1 -> split2
//      split2 (passthrough) -----------------------------> metamux_2
//      split2 (stage 2: MobileNet classification on ROI) -> stage_02_preproc
//        -> stage_02_inference -> stage_02_postproc -> [TextFilter] -> metamux_2
//      metamux_2 -> overlay -> display
//
//  Stage 1 runs YOLOX object detection (module=yolov8) on full frames.
//  Stage 2 runs MobileNet classification on the ROIs detected by stage 1.
//  Both stages' metadata are muxed back onto the original video before a
//  single qtivoverlay draws both bounding boxes and classification labels.

void create_and_execute_pipeline() {

  // Reads the input MP4 file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/Downloads/qimsdk_samples/media/15s.mp4");

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

  // Prepares the H.264 bitstream for the decoder.
  Element parse("h264parse", "parse");

  // Decodes the compressed H.264 stream into raw video frames using the
  // hardware decoder. DMA I/O mode avoids unnecessary buffer copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue required immediately after the hardware decoder.
  Element q_dec("queue", "q_dec");

  // Normalizes decoder output to NV12 before branching/AI preprocessing.
  auto videofilter = VideoFilter().format("NV12");

  // First split: one branch passes the raw video through to metamux_1,
  // the other runs stage-1 detection.
  Element split1("tee", "split1");
  Element q_video_1("queue", "q_video_1");
  Element q_stage1("queue", "q_stage1");

  // Stage 1 - YOLOX object detection on full frames.
  Element stage_01_preproc("qtimlvconverter", "stage_01_preproc");
  stage_01_preproc.set("mode", "image-batch-non-cumulative");

  Element stage_01_inference("qtimltflite", "stage_01_inference");
  stage_01_inference.set("delegate", "external");
  stage_01_inference.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  stage_01_inference.set("external-delegate-options",
                          "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  stage_01_inference.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/yolox_w8a8.tflite");

  // YOLOX detection uses postprocess module "yolov8" (documented compatibility mapping).
  Element stage_01_postproc("qtimlpostprocess", "stage_01_postproc");
  stage_01_postproc.set("module", "yolov8");
  stage_01_postproc.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/yolov8.json");
  stage_01_postproc.set("settings", "{\"confidence\": 51.0}");

  // Metadata bus for stage-1 detection results into metamux_1.
  auto stage1_text = TextFilter();

  Element metamux_1("qtimetamux", "metamux_1");

  // Second split: after stage-1 metadata is muxed back onto the video,
  // split again for stage-2 ROI-based classification.
  Element split2("tee", "split2");
  Element q_video_2("queue", "q_video_2");
  Element q_stage2("queue", "q_stage2");

  // Stage 2 - MobileNet classification on stage-1 detected object ROIs.
  Element stage_02_preproc("qtimlvconverter", "stage_02_preproc");
  stage_02_preproc.set("mode", "roi-batch-cumulative");

  Element stage_02_inference("qtimltflite", "stage_02_inference");
  stage_02_inference.set("delegate", "external");
  stage_02_inference.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  stage_02_inference.set("external-delegate-options",
                          "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");
  stage_02_inference.set("model", HOME_PATH + "/Downloads/qimsdk_samples/models/mobilenet_v2_w8a8.tflite");

  Element stage_02_postproc("qtimlpostprocess", "stage_02_postproc");
  stage_02_postproc.set("module", "mobilenet");
  stage_02_postproc.set("labels", HOME_PATH + "/Downloads/qimsdk_samples/labels/mobilenet.json");
  stage_02_postproc.set("settings", "{\"confidence\": 51.0}");

  // Metadata bus for stage-2 classification results into metamux_2.
  auto stage2_text = TextFilter();

  Element metamux_2("qtimetamux", "metamux_2");

  // Draws both stage-1 bounding boxes and stage-2 classification labels
  // over the original video.
  Element overlay("qtivoverlay", "overlay");

  // Renders the composited output fullscreen on Wayland.
  Element display("waylandsink", "display");
  display.set("sync", true);
  display.set("fullscreen", true);

  Pipeline pipeline("daisychain-detection-classification");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("vf", videofilter)
          .add(split1)
          .add(q_video_1)
          .add(q_stage1)
          .add(stage_01_preproc)
          .add(stage_01_inference)
          .add(stage_01_postproc)
          .add_stream_filter("mlf_s1", stage1_text)
          .add(metamux_1)
          .add(split2)
          .add(q_video_2)
          .add(q_stage2)
          .add(stage_02_preproc)
          .add(stage_02_inference)
          .add(stage_02_postproc)
          .add_stream_filter("mlf_s2", stage2_text)
          .add(metamux_2)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split1")
          .link("split1", "q_video_1", "metamux_1")
          .link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
                "stage_01_postproc", "mlf_s1", "metamux_1")
          .link("metamux_1", "split2")
          .link("split2", "q_video_2", "metamux_2")
          .link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference",
                "stage_02_postproc", "mlf_s2", "metamux_2")
          .link("metamux_2", "overlay", "display");

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
