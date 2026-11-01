#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// Number of parallel decode/inference streams and final grid geometry.
constexpr int kNumStreams = 6;
constexpr int kGridCols = 3;
constexpr int kGridRows = 2;
constexpr int kCellWidth = 640;
constexpr int kCellHeight = 540;

const std::string kInputFile = "/etc/ai_demo_sample.mp4";
const std::string kOutputFile =
    "/etc/sixstream_daisychain_four_stage_out.mp4";

// Stage 1: object detection (YOLOv8, SNPE DLC).
const std::string kDetModel = "/etc/yolov8_det_quantized.dlc";
const std::string kDetLabels = "/etc/yolov8.json";
const std::string kDetConfidence = "51.0";

// Stage 2: semantic segmentation (DeepLabV3-Plus-MobileNet, SNPE DLC).
const std::string kSegModel = "/etc/deeplabv3_plus_mobilenet.dlc";
const std::string kSegLabels = "/etc/deeplab-labels.txt";
constexpr double kSegAlpha = 0.5;

// Stage 3: pose estimation on Stage-1 ROIs (HRNet, SNPE DLC).
const std::string kPoseModel = "/etc/hrnet_pose.dlc";
const std::string kPoseLabels = "/etc/hrnet.json";
const std::string kPoseSettings = "/etc/hrnet_settings.json";

// Stage 4: classification on Stage-1 ROIs (Inception-v3, SNPE DLC).
const std::string kClassModel = "/etc/inception_v3.dlc";
const std::string kClassLabels = "/etc/classification.txt";

// Builds one full per-stream four-stage daisy-chain (identical topology to
// the verified single-stream Python reference) and returns the name of the
// queue that carries this stream's fully-composited NV12 output, ready to
// link into the top-level grid_composer.
//
// Per-stream topology:
//
//   source_i -> demux_i -> parser_i -> decoder_i -> q_dec_i -> vf_i(NV12)
//     -> main_split_i (tee)
//        |- private buffer copy: q_ov_i -> tf_ov_i(qtivtransform)
//        |     -> vf_ov_i(NV12) -> split1_i (tee)
//        |        Stage 1 (full frame): YOLOv8 det -> metamux_1_i
//        |        Stage 3 (ROI of Stage 1): HRNet pose -> metamux_2_i
//        |        Stage 4 (ROI of Stage 1): Inception-v3 class -> metamux_3_i
//        |        -> overlay_i -> q_ov_out_i -> seg_mix_i (sink_0)
//        |- seg leg: Stage 2 DeepLabV3 argmax RGBA mask -> seg_mix_i
//              (sink_1, alpha)
//     -> seg_mix_i (per-stream composited NV12) -> seg_mix_vf_i -> q_seg_out_i
//
// Every element/stream-filter name is suffixed with the stream index so the
// six copies never collide on a shared default name.
std::string build_stream(Pipeline &pipeline, int i) {
  const std::string s = "_" + std::to_string(i);

  // --- Source / decode ---

  Element source("filesrc", "source" + s);
  source.set("location", kInputFile);

  Element demux("qtdemux", "demux" + s);
  Element parser("h264parse", "parser" + s);

  Element decoder("v4l2h264dec", "decoder" + s);
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  Element q_dec("queue", "q_dec" + s);
  auto vf = VideoFilter().format("NV12");

  Element main_split("tee", "main_split" + s);

  pipeline.add(source);
  pipeline.add(demux);
  pipeline.add(parser);
  pipeline.add(decoder);
  pipeline.add(q_dec);
  pipeline.add_stream_filter("vf" + s, vf);
  pipeline.add(main_split);

  // --- Overlay-chain private buffer copy (shared tee feeds a composer) ---

  Element q_ov("queue", "q_ov" + s);
  Element tf_ov("qtivtransform", "tf_ov" + s);
  auto vf_ov = VideoFilter().format("NV12");

  pipeline.add(q_ov);
  pipeline.add(tf_ov);
  pipeline.add_stream_filter("vf_ov" + s, vf_ov);

  // --- Stage 1: object detection (YOLOv8, SNPE DSP) ---

  Element split1("tee", "split1" + s);
  Element q_det("queue", "q_det" + s);

  Element det_preproc("qtimlvconverter", "det_preproc" + s);
  det_preproc.set("mode", "image-batch-non-cumulative");

  // This YOLOv8 SNPE DLC bakes NMS in and exports 3 separate output tensors
  // (boxes, scores, class_idx) — requires tensors="<boxes,scores,class_idx>"
  // (per model-catalog.md, the same mechanism used in the sibling
  // face-detection/face-recognition SNPE artifacts); without it qtimlsnpe
  // forwards only the first tensor and qtimlpostprocess module=yolov8 fails
  // caps negotiation.
  Element det_infer("qtimlsnpe", "det_infer" + s);
  det_infer.set("model", kDetModel);
  det_infer.set("delegate", "dsp");
  det_infer.set("tensors", "<boxes,scores,class_idx>");

  Element det_post("qtimlpostprocess", "det_post" + s);
  det_post.set("module", "yolov8");
  det_post.set("labels", kDetLabels);
  det_post.set("settings", "{\"confidence\": " + kDetConfidence + "}");

  auto mlf_det = TextFilter();
  Element metamux1("qtimetamux", "metamux_1" + s);

  pipeline.add(split1);
  pipeline.add(q_det);
  pipeline.add(det_preproc);
  pipeline.add(det_infer);
  pipeline.add(det_post);
  pipeline.add_stream_filter("mlf_det" + s, mlf_det);
  pipeline.add(metamux1);

  // --- Stage 3: pose estimation on Stage-1 ROIs (HRNet, SNPE DSP) ---

  Element split2("tee", "split2" + s);
  Element q_pose("queue", "q_pose" + s);

  Element pose_preproc("qtimlvconverter", "pose_preproc" + s);
  pose_preproc.set("mode", "roi-batch-cumulative");

  Element pose_infer("qtimlsnpe", "pose_infer" + s);
  pose_infer.set("model", kPoseModel);
  pose_infer.set("delegate", "dsp");

  Element pose_post("qtimlpostprocess", "pose_post" + s);
  pose_post.set("module", "hrnet");
  pose_post.set("labels", kPoseLabels);
  pose_post.set("settings", kPoseSettings);
  pose_post.set("results", 2);

  auto mlf_pose = TextFilter();
  Element metamux2("qtimetamux", "metamux_2" + s);

  pipeline.add(split2);
  pipeline.add(q_pose);
  pipeline.add(pose_preproc);
  pipeline.add(pose_infer);
  pipeline.add(pose_post);
  pipeline.add_stream_filter("mlf_pose" + s, mlf_pose);
  pipeline.add(metamux2);

  // --- Stage 4: classification on Stage-1 ROIs (Inception-v3, SNPE DSP) ---

  Element split3("tee", "split3" + s);
  Element q_class("queue", "q_class" + s);

  Element class_preproc("qtimlvconverter", "class_preproc" + s);
  class_preproc.set("mode", "roi-batch-cumulative");

  Element class_infer("qtimlsnpe", "class_infer" + s);
  class_infer.set("model", kClassModel);
  class_infer.set("delegate", "dsp");

  Element class_post("qtimlpostprocess", "class_post" + s);
  class_post.set("module", "mobilenet");
  class_post.set("labels", kClassLabels);

  auto mlf_class = TextFilter();
  Element metamux3("qtimetamux", "metamux_3" + s);

  pipeline.add(split3);
  pipeline.add(q_class);
  pipeline.add(class_preproc);
  pipeline.add(class_infer);
  pipeline.add(class_post);
  pipeline.add_stream_filter("mlf_class" + s, mlf_class);
  pipeline.add(metamux3);

  Element overlay("qtivoverlay", "overlay" + s);
  Element q_ov_out("queue", "q_ov_out" + s);

  pipeline.add(overlay);
  pipeline.add(q_ov_out);

  // --- Stage 2: semantic segmentation (DeepLabV3-Plus-MobileNet, SNPE DSP) ---

  Element q_seg("queue", "q_seg" + s);

  Element seg_preproc("qtimlvconverter", "seg_preproc" + s);
  seg_preproc.set("mode", "image-batch-non-cumulative");

  Element seg_infer("qtimlsnpe", "seg_infer" + s);
  seg_infer.set("model", kSegModel);
  seg_infer.set("delegate", "dsp");

  Element seg_post("qtimlpostprocess", "seg_post" + s);
  seg_post.set("module", "deeplab-argmax");
  seg_post.set("labels", kSegLabels);

  // deeplab-argmax is a video-output-only postprocess module; RGBA is the
  // required qtimlpostprocess render format for this build.
  auto seg_mask_vf = VideoFilter().format("RGBA");
  Element q_seg_mask("queue", "q_seg_mask" + s);

  pipeline.add(q_seg);
  pipeline.add(seg_preproc);
  pipeline.add(seg_infer);
  pipeline.add(seg_post);
  pipeline.add_stream_filter("seg_mask_vf" + s, seg_mask_vf);
  pipeline.add(q_seg_mask);

  // Per-stream composite: alpha-blends the seg mask over the stage 1/3/4
  // overlay.
  Element seg_mix("qtivcomposer", "seg_mix" + s);
  auto seg_mix_vf = VideoFilter().format("NV12");
  Element q_seg_out("queue", "q_seg_out" + s);

  pipeline.add(seg_mix);
  pipeline.add_stream_filter("seg_mix_vf" + s, seg_mix_vf);
  pipeline.add(q_seg_out);

  // --- Per-stream linking (identical structure to the single-stream app) ---

  pipeline.link("source" + s, "demux" + s, "parser" + s, "decoder" + s,
                "q_dec" + s, "vf" + s, "main_split" + s);

  // Private buffer copy for the overlay chain.
  pipeline.link("main_split" + s, "q_ov" + s, "tf_ov" + s, "vf_ov" + s,
                "split1" + s);

  // Stage 1: passthrough + detection leg merge through metamux_1.
  pipeline.link("split1" + s, "metamux_1" + s);
  pipeline.link("split1" + s, "q_det" + s, "det_preproc" + s, "det_infer" + s,
                "det_post" + s, "mlf_det" + s, "metamux_1" + s);

  // Stage 3: passthrough + pose ROI leg merge through metamux_2.
  pipeline.link("metamux_1" + s, "split2" + s);
  pipeline.link("split2" + s, "metamux_2" + s);
  pipeline.link("split2" + s, "q_pose" + s, "pose_preproc" + s,
                "pose_infer" + s, "pose_post" + s, "mlf_pose" + s,
                "metamux_2" + s);

  // Stage 4: passthrough + classification ROI leg merge through metamux_3.
  pipeline.link("metamux_2" + s, "split3" + s);
  pipeline.link("split3" + s, "metamux_3" + s);
  pipeline.link("split3" + s, "q_class" + s, "class_preproc" + s,
                "class_infer" + s, "class_post" + s, "mlf_class" + s,
                "metamux_3" + s);

  // Overlay all three discrete metadata stages, then into seg_mix (sink_0).
  pipeline.link("metamux_3" + s, "overlay" + s, "q_ov_out" + s,
                "seg_mix" + s);

  // Stage 2: segmentation mask renders directly into seg_mix (sink_1).
  pipeline.link("main_split" + s, "q_seg" + s, "seg_preproc" + s,
                "seg_infer" + s, "seg_post" + s, "seg_mask_vf" + s,
                "q_seg_mask" + s, "seg_mix" + s);

  // Per-stream composited output tail (stops at the queue; grid link is
  // global).
  pipeline.link("seg_mix" + s, "seg_mix_vf" + s, "q_seg_out" + s);

  // Alpha-blend the seg mask (sink_1) over the overlay (sink_0).
  pipeline.get("seg_mix" + s).input(1).set("alpha", kSegAlpha);

  return "q_seg_out" + s;
}

void create_and_execute_pipeline() {
  Pipeline pipeline("sixstream-daisychain-four-stage");

  // Final grid composer that tiles the six per-stream composited outputs.
  Element grid("qtivcomposer", "grid_composer");
  auto grid_vf = VideoFilter().format("NV12");
  Element q_grid_out("queue", "q_grid_out");

  Element encoder("v4l2h264enc", "encoder");
  encoder.set("capture-io-mode", 4);
  encoder.set("output-io-mode", 4);

  Element parse_out("h264parse", "parse_out");
  Element mux("mp4mux", "mux");
  Element sink("filesink", "sink");
  sink.set("location", kOutputFile);

  pipeline.add(grid);
  pipeline.add_stream_filter("grid_vf", grid_vf);
  pipeline.add(q_grid_out);
  pipeline.add(encoder);
  pipeline.add(parse_out);
  pipeline.add(mux);
  pipeline.add(sink);

  // Build the six per-stream chains and link each into the grid composer.
  std::vector<std::string> stream_outputs;
  for (int i = 0; i < kNumStreams; ++i) {
    stream_outputs.push_back(build_stream(pipeline, i));
  }
  for (const auto &out_name : stream_outputs) {
    pipeline.link(out_name, "grid_composer");
  }

  // Place each stream in its 3x2 grid cell (pad index == link order above).
  for (int i = 0; i < kNumStreams; ++i) {
    const int col = i % kGridCols;
    const int row = i / kGridCols;
    pipeline.get("grid_composer")
        .input(i)
        .set("position", std::vector<int>{col * kCellWidth, row * kCellHeight})
        .set("dimensions", std::vector<int>{kCellWidth, kCellHeight});
  }

  // Grid -> encode -> mux -> single output file.
  pipeline.link("grid_composer", "grid_vf", "q_grid_out", "encoder",
                "parse_out", "mux", "sink");

  // mp4mux needs EOS to finalize the file container.
  pipeline.eos(true);

  pipeline.execute();
}

} // namespace

int main() {
  // Mandatory logging setup before constructing any Pipeline.
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
