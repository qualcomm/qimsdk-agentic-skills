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

const std::string INPUT_FILE =
    expand_home("/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4");
const std::string CLASS_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/inception_v3_w8a8.tflite");
const std::string CLASS_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/mobilenet.json");
const std::string POSE_STAGE1_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/person_foot_detection_w8a8.tflite");
const std::string POSE_STAGE1_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/foot_track_net.json");
const std::string POSE_STAGE1_SETTINGS =
    expand_home("/Downloads/qimsdk_samples/labels/foot_track_net_settings.json");
const std::string POSE_STAGE2_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/hrnetpose_w8a8.tflite");
const std::string POSE_STAGE2_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/hrnet.json");
const std::string POSE_STAGE2_SETTINGS =
    expand_home("/Downloads/qimsdk_samples/labels/hrnet_settings.json");
const std::string DET_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/yolox_w8a8.tflite");
const std::string DET_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/yolov8.json");
const std::string SEG_MODEL =
    expand_home("/Downloads/qimsdk_samples/models/deeplabv3_plus_mobilenet_w8a8.tflite");
const std::string SEG_LABELS =
    expand_home("/Downloads/qimsdk_samples/labels/dv3-argmax.json");

//  Example pipeline:

// External delegate options shared by every HTP/NPU inference stage.
// htp_performance_mode=2 is used because four models run concurrently on
// HTP/NPU from a single teed source (high-concurrency parallel inferencing).
static const std::string kHtpDelegateOptions =
    "QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,"
    "log_level=(string)1;";

//  Example pipeline:
//
//    src -> demux -> parse -> decoder -> [vf:NV12] -> split (tee, 8 pads)
//
//    Branch 1 (Classification - Inception-v3 w8a8):
//      split. -> class_mux (passthrough)
//      split. -> q -> class_pre -> q -> class_infer -> q -> class_post(mobilenet)
//             -> [class_mlf:text] -> class_mux -> q -> class_overlay -> q -> comp.sink_0
//
//    Branch 2 (Pose Estimation - two-stage HRNet cascade):
//      split. -> pose_mux1 (passthrough)
//      split. -> q -> pose_pre1 -> q -> pose_infer1(qpd) -> q -> pose_post1(qpd)
//             -> [pose_mlf1:text] -> pose_mux1 -> q -> pose_split2 (tee)
//        pose_split2. -> pose_mux2 (passthrough)
//        pose_split2. -> q -> pose_pre2(ROI) -> q -> pose_infer2(hrnet) -> q
//               -> pose_post2(hrnet) -> [pose_mlf2:text] -> pose_mux2
//             -> q -> pose_overlay -> q -> comp.sink_1
//
//    Branch 3 (Object Detection - YOLOX):
//      split. -> det_mux (passthrough)
//      split. -> q -> det_pre -> q -> det_infer -> q -> det_post(yolov8)
//             -> [det_mlf:text] -> det_mux -> q -> det_overlay -> q -> comp.sink_2
//
//    Branch 4 (Segmentation - DeepLabV3+ Mobilenet, direct-to-composer):
//      split. -> q -> comp.sink_3 (raw passthrough)
//      split. -> q -> seg_pre -> q -> seg_infer -> q -> seg_post(deeplab-argmax)
//             -> [seg_vf:RGBA] -> q -> comp.sink_4 (alpha=0.5, mask over sink_3)
//
//    comp (qtivcomposer, 2x2 grid, 1920x1080 canvas / 960x540 tiles)
//      -> q -> waylandsink
//
//  The pipeline reads a single MP4/H.264 file, decodes it once with the
//  hardware decoder, and tees the decoded NV12 stream into four independent
//  inference branches. Three branches (classification, pose, detection)
//  produce text metadata that is overlaid with qtimetamux/qtivoverlay before
//  reaching the composer; the segmentation branch produces a rendered RGBA
//  mask that feeds the composer directly, paired with its own passthrough
//  pad at the same tile position/dimensions. All four results are composited
//  into a fixed 2x2 grid and rendered to display.

void create_and_execute_pipeline() {

  // --- Shared source / decode ---

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", INPUT_FILE);

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

  // Parses the H.264 bitstream into a format suitable for decoding.
  Element parse("h264parse", "parse");

  // Decodes the compressed H.264 stream into raw video frames using DMA
  // buffers on both sides to avoid unnecessary buffer copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Single decode point, teed to all four inference branches below.
  Element split("tee", "split");

  // --- Branch 1: Image Classification (Inception-v3 w8a8) ---

  Element q1_pass("queue", "q1_pass");
  Element tf1_pass("qtivtransform", "tf1_pass");
  auto vf1_pass = VideoFilter().format("NV12");
  Element q1_ai("queue", "q1_ai");

  // Converts frames into tensor format for the classification model.
  Element class_pre("qtimlvconverter", "class_pre");

  Element q1_infer("queue", "q1_infer");

  // Executes Inception-v3 (w8a8) classification on HTP/NPU.
  Element class_infer("qtimltflite", "class_infer");
  class_infer.set("delegate", "external");
  class_infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  class_infer.set("external-delegate-options", kHtpDelegateOptions);
  class_infer.set("model", CLASS_MODEL);

  Element q1_post("queue", "q1_post");

  // Decodes classification output tensors into labeled results.
  // module=mobilenet per model-catalog.md (Inception-v3 w8a8 row).
  Element class_post("qtimlpostprocess", "class_post");
  class_post.set("module", "mobilenet");
  class_post.set("labels", CLASS_LABELS);
  class_post.set("settings", "{\"confidence\": 51.0}");
  class_post.set("results", 5);

  // Merges classification metadata with the passthrough video frame.
  Element class_mux("qtimetamux", "class_mux");

  Element q1_ovl("queue", "q1_ovl");

  // Overlays classification labels on the classification-branch video.
  Element class_overlay("qtivoverlay", "class_overlay");

  Element q1_disp("queue", "q1_disp");

  // --- Branch 2: Pose Estimation (two-stage HRNet cascade) ---
  //
  // HRNet is a top-down pose model: it must run on a person/foot-detection
  // ROI, not the full frame, so stage 1 (qpd) locates persons/feet and
  // stage 2 (hrnet) estimates keypoints within each detected ROI.

  Element q2_pass("queue", "q2_pass");
  Element tf2_pass("qtivtransform", "tf2_pass");
  auto vf2_pass = VideoFilter().format("NV12");
  Element q2_ai("queue", "q2_ai");

  // Stage 1: converts frames into tensor format for the person/foot detector.
  Element pose_pre1("qtimlvconverter", "pose_pre1");

  Element q2_infer1("queue", "q2_infer1");

  // Stage 1: person/foot detection (qpd) on HTP/NPU.
  // <MODEL_PATH_STAGE1_QPD>/<LABELS_PATH_STAGE1_QPD>/<SETTINGS_PATH_STAGE1_QPD>
  // are placeholders because the user provided only the HRNet (stage 2)
  // model; the stage-1 qpd detector model/labels/settings must be supplied.
  Element pose_infer1("qtimltflite", "pose_infer1");
  pose_infer1.set("delegate", "external");
  pose_infer1.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  pose_infer1.set("external-delegate-options", kHtpDelegateOptions);
  pose_infer1.set("model", POSE_STAGE1_MODEL);

  Element q2_post1("queue", "q2_post1");

  // Stage 1 postprocess: module=qpd. settings/results are mandatory per
  // model-catalog.md for the HRNet cascade (stage1 results=10).
  Element pose_post1("qtimlpostprocess", "pose_post1");
  pose_post1.set("module", "qpd");
  pose_post1.set("labels", POSE_STAGE1_LABELS);
  pose_post1.set("settings", POSE_STAGE1_SETTINGS);
  pose_post1.set("results", 10);

  // Merges stage-1 detection metadata with the passthrough frame.
  Element pose_mux1("qtimetamux", "pose_mux1");

  Element q2_split_pass("queue", "q2_split_pass");

  // Splits again ahead of stage 2 so the merged detection ROI stream feeds
  // both the stage-2 ROI preprocessing branch and its own passthrough leg.
  Element pose_split2("tee", "pose_split2");

  Element q2b_pass("queue", "q2b_pass");
  Element q2b_ai("queue", "q2b_ai");

  // Stage 2: ROI-based preprocessing driven by the stage-1 detection.
  Element pose_pre2("qtimlvconverter", "pose_pre2");
  pose_pre2.set("mode", "roi-batch-cumulative");

  Element q2_infer2("queue", "q2_infer2");

  // Stage 2: HRNet pose estimation (w8a8) on the detected ROI, HTP/NPU.
  Element pose_infer2("qtimltflite", "pose_infer2");
  pose_infer2.set("delegate", "external");
  pose_infer2.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  pose_infer2.set("external-delegate-options", kHtpDelegateOptions);
  pose_infer2.set("model", POSE_STAGE2_MODEL);

  Element q2_post2("queue", "q2_post2");

  // Stage 2 postprocess: module=hrnet. settings/results provided by user
  // (stage2 results=2, hrnet_settings.json).
  Element pose_post2("qtimlpostprocess", "pose_post2");
  pose_post2.set("module", "hrnet");
  pose_post2.set("labels", POSE_STAGE2_LABELS);
  pose_post2.set("settings", POSE_STAGE2_SETTINGS);
  pose_post2.set("results", 2);

  // Merges stage-2 keypoint metadata with the passthrough frame.
  Element pose_mux2("qtimetamux", "pose_mux2");

  Element q2_ovl("queue", "q2_ovl");

  // Overlays keypoints/skeleton on the pose-branch video.
  Element pose_overlay("qtivoverlay", "pose_overlay");

  Element q2_disp("queue", "q2_disp");

  // --- Branch 3: Object Detection (YOLOX) ---

  Element q3_pass("queue", "q3_pass");
  Element tf3_pass("qtivtransform", "tf3_pass");
  auto vf3_pass = VideoFilter().format("NV12");
  Element q3_ai("queue", "q3_ai");

  Element det_pre("qtimlvconverter", "det_pre");

  Element q3_infer("queue", "q3_infer");

  // Executes YOLOX (w8a8) object detection on HTP/NPU.
  Element det_infer("qtimltflite", "det_infer");
  det_infer.set("delegate", "external");
  det_infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  det_infer.set("external-delegate-options", kHtpDelegateOptions);
  det_infer.set("model", DET_MODEL);

  Element q3_post("queue", "q3_post");

  // module=yolov8 per model-catalog.md (YOLOX row uses the yolov8 module).
  Element det_post("qtimlpostprocess", "det_post");
  det_post.set("module", "yolov8");
  det_post.set("labels", DET_LABELS);
  det_post.set("settings", "{\"confidence\": 51.0}");

  // Merges detection metadata with the passthrough frame.
  Element det_mux("qtimetamux", "det_mux");

  Element q3_ovl("queue", "q3_ovl");

  // Overlays bounding boxes on the detection-branch video.
  Element det_overlay("qtivoverlay", "det_overlay");

  Element q3_disp("queue", "q3_disp");

  // --- Branch 4: Semantic Segmentation (DeepLabV3+ Mobilenet) ---
  //
  // deeplab-argmax is a video-output-only postprocess module (no text/x-raw
  // support), so this branch renders directly into the composer instead of
  // going through qtimetamux/qtivoverlay: a passthrough pad plus a rendered
  // RGBA mask pad, alpha-blended by qtivcomposer.

  Element q4_pass("queue", "q4_pass");
  Element q4_ai("queue", "q4_ai");
  Element tf4_pass("qtivtransform", "tf4_pass");
  auto vf4_pass = VideoFilter().format("NV12");
  Element q4_mask("queue", "q4_mask");
  Element seg_mix("qtivcomposer", "seg_mix");
  auto seg_mix_vf = VideoFilter().format("NV12");
  Element q4_out("queue", "q4_out");

  Element seg_pre("qtimlvconverter", "seg_pre");

  Element q4_infer("queue", "q4_infer");

  // Executes DeepLabV3+ Mobilenet (w8a8) segmentation on HTP/NPU.
  Element seg_infer("qtimltflite", "seg_infer");
  seg_infer.set("delegate", "external");
  seg_infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  seg_infer.set("external-delegate-options", kHtpDelegateOptions);
  seg_infer.set("model", SEG_MODEL);

  Element q4_post("queue", "q4_post");

  // module=deeplab-argmax per model-catalog.md (DeepLabV3-Plus-MobileNet row).
  Element seg_post("qtimlpostprocess", "seg_post");
  seg_post.set("module", "deeplab-argmax");
  seg_post.set("labels", SEG_LABELS);


  // --- Composer: 2x2 grid, 1920x1080 canvas / 960x540 tiles ---

  Element comp("qtivcomposer", "comp");

  Element q_disp_out("queue", "q_disp_out");

  // Renders the composed 2x2 grid on display. This is a large file-source
  // composer grid, so sync=false is used per skill display defaults.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", false);

  // Stream filters.
  // Fix the decoded frame dimensions before parallel branches so overlay
  // coordinates remain correct when each tile is composed at 960x540.
  auto vf = VideoFilter().format("NV12").resolution(1920, 1080);
  auto class_mlf = TextFilter();
  auto pose_mlf1 = TextFilter();
  auto pose_mlf2 = TextFilter();
  auto det_mlf = TextFilter();
  // Segmentation mask render filter: RGBA per qtimlpostprocess device-verified
  // caps (BGRA fails to link). No pinned resolution -- the composer sink-pad
  // dimensions scale the native rendered mask into its tile.
  auto seg_vf = VideoFilter().format("RGBA");

  Pipeline pipeline("qimsdk-cpp-parallel-inferencing");
  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add_stream_filter("vf", vf)
          .add(split)

          // Branch 1: classification
          .add(q1_pass)
          .add(tf1_pass)
          .add_stream_filter("vf1_pass", vf1_pass)
          .add(q1_ai)
          .add(class_pre)
          .add(q1_infer)
          .add(class_infer)
          .add(q1_post)
          .add(class_post)
          .add_stream_filter("class_mlf", class_mlf)
          .add(class_mux)
          .add(q1_ovl)
          .add(class_overlay)
          .add(q1_disp)

          // Branch 2: pose (two-stage HRNet cascade)
          .add(q2_pass)
          .add(tf2_pass)
          .add_stream_filter("vf2_pass", vf2_pass)
          .add(q2_ai)
          .add(pose_pre1)
          .add(q2_infer1)
          .add(pose_infer1)
          .add(q2_post1)
          .add(pose_post1)
          .add_stream_filter("pose_mlf1", pose_mlf1)
          .add(pose_mux1)
          .add(q2_split_pass)
          .add(pose_split2)
          .add(q2b_pass)
          .add(q2b_ai)
          .add(pose_pre2)
          .add(q2_infer2)
          .add(pose_infer2)
          .add(q2_post2)
          .add(pose_post2)
          .add_stream_filter("pose_mlf2", pose_mlf2)
          .add(pose_mux2)
          .add(q2_ovl)
          .add(pose_overlay)
          .add(q2_disp)

          // Branch 3: object detection
          .add(q3_pass)
          .add(tf3_pass)
          .add_stream_filter("vf3_pass", vf3_pass)
          .add(q3_ai)
          .add(det_pre)
          .add(q3_infer)
          .add(det_infer)
          .add(q3_post)
          .add(det_post)
          .add_stream_filter("det_mlf", det_mlf)
          .add(det_mux)
          .add(q3_ovl)
          .add(det_overlay)
          .add(q3_disp)

          // Branch 4: segmentation (direct-to-composer)
          .add(q4_pass)
          .add(tf4_pass)
          .add_stream_filter("vf4_pass", vf4_pass)
          .add(q4_ai)
          .add(seg_pre)
          .add(q4_infer)
          .add(seg_infer)
          .add(q4_post)
          .add(seg_post)
          .add_stream_filter("seg_vf", seg_vf)
          .add(q4_mask)
          .add(seg_mix)
          .add_stream_filter("seg_mix_vf", seg_mix_vf)
          .add(q4_out)

          // Composer + display
          .add(comp)
          .add(q_disp_out)
          .add(display)

          // Shared decode
          .link("src", "demux", "parse", "decoder", "vf", "split")

          // Branch 1 links
          .link("split", "q1_pass", "tf1_pass", "vf1_pass", "class_mux")
          .link("split", "q2_pass", "tf2_pass", "vf2_pass", "pose_mux1")
          .link("split", "q3_pass", "tf3_pass", "vf3_pass", "det_mux")
          .link("split", "q1_ai", "class_pre", "q1_infer", "class_infer",
                "q1_post", "class_post", "class_mlf", "class_mux")
          .link("class_mux", "q1_ovl", "class_overlay", "q1_disp")

          // Branch 2 links (stage 1)
          .link("split", "q2_ai", "pose_pre1", "q2_infer1", "pose_infer1",
                "q2_post1", "pose_post1", "pose_mlf1", "pose_mux1")
          .link("pose_mux1", "q2_split_pass", "pose_split2")

          // Branch 2 links (stage 2)
          .link("pose_split2", "q2b_pass", "pose_mux2")
          .link("pose_split2", "q2b_ai", "pose_pre2", "q2_infer2",
                "pose_infer2", "q2_post2", "pose_post2", "pose_mlf2",
                "pose_mux2")
          .link("pose_mux2", "q2_ovl", "pose_overlay", "q2_disp")

          // Branch 3 links
          .link("split", "q3_ai", "det_pre", "q3_infer", "det_infer",
                "q3_post", "det_post", "det_mlf", "det_mux")
          .link("det_mux", "q3_ovl", "det_overlay", "q3_disp")

          // Branch 4 links (direct-to-composer, passthrough + mask)
          .link("split", "q4_pass", "tf4_pass", "vf4_pass", "seg_mix")
          .link("split", "q4_ai", "seg_pre", "q4_infer", "seg_infer",
                "q4_post", "seg_post", "seg_vf", "q4_mask", "seg_mix")
          .link("seg_mix", "seg_mix_vf", "q4_out")


          // Composer + display
          .link("q1_disp", "comp")
          .link("q2_disp", "comp")
          .link("q3_disp", "comp")
          .link("q4_out", "comp")
          .link("comp", "q_disp_out", "display");

  pipeline.get("seg_mix").input(1).set("alpha", 0.5);

  // 2x2 grid layout on a 1920x1080 canvas, 960x540 tiles.
  // The local segmentation composer already combines its passthrough and
  // RGBA mask, so the top-level grid has exactly four inputs: class, pose,
  // detection, and the composed segmentation tile.
  pipeline.get("comp").input(0).set("position", std::vector<int>{0, 0});
  pipeline.get("comp").input(0).set("dimensions", std::vector<int>{960, 540});

  pipeline.get("comp").input(1).set("position", std::vector<int>{960, 0});
  pipeline.get("comp").input(1).set("dimensions", std::vector<int>{960, 540});

  pipeline.get("comp").input(2).set("position", std::vector<int>{0, 540});
  pipeline.get("comp").input(2).set("dimensions", std::vector<int>{960, 540});

  pipeline.get("comp").input(3).set("position", std::vector<int>{960, 540});
  pipeline.get("comp").input(3).set("dimensions", std::vector<int>{960, 540});

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
