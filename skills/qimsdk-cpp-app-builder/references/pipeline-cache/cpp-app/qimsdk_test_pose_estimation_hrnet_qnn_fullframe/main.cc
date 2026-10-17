#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// Number of parallel decode streams and final grid geometry.
constexpr int kNumStreams = 6;
constexpr int kGridCols = 3;
constexpr int kGridRows = 2;
constexpr int kCellWidth = 640;
constexpr int kCellHeight = 360;

}  // namespace

// HRNet pose estimation, QNN native cached-binary runtime (qtimlqnn).
const std::string kPoseModel = "/etc/hrnet_pose_w8a8.bin";
const std::string kPoseLabels = "/etc/hrnet.json";
const std::string kPoseSettings = "/etc/hrnet_settings.json";
const std::string kQnnBackend = "/usr/lib/libQnnHtp.so";
const std::string kQnnSystem = "/usr/lib/libQnnSystem.so";

const std::string kSrcVideo = "/etc/ai_demo_sample.mp4";
const std::string kOutputFile = "/etc/6stream_hrnet_pose_qnn_output.mp4";

// Builds one full single-stage HRNet pipeline and returns the name of the
// queue that carries this stream's overlaid NV12 output, ready to link into
// the top-level grid composer.
//
// Per-stream topology:
//
//   source_i -> demux_i -> parser_i -> decoder_i -> q_dec_i
//     -> tee t_split_i
//        |- queue -> metamux_i (data pad)
//        |- queue -> preproc_i -> inference_i (qtimlqnn HRNet)
//              -> postproc_i -> TextFilter -> queue -> metamux_i
//     -> metamux_i -> ovl_i -> queue -> comp.sink_i
//
// Every element/stream-filter name is suffixed with the stream index so the
// six copies never collide on a shared default name.
std::string build_stream(Pipeline& pipeline, int i) {
  const std::string s = "_" + std::to_string(i);

  // --- Source / decode ---

  Element source("filesrc", "source" + s);
  source.set("location", kSrcVideo);

  Element demux("qtdemux", "demux" + s);
  Element parser("h264parse", "parser" + s);

  Element decoder("v4l2h264dec", "decoder" + s);
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  Element q_dec("queue", "q_dec" + s);
  auto vf = VideoFilter().format("NV12");

  Element t_split("tee", "t_split" + s);

  pipeline.add(source);
  pipeline.add(demux);
  pipeline.add(parser);
  pipeline.add(decoder);
  pipeline.add(q_dec);
  pipeline.add_stream_filter("vf" + s, vf);
  pipeline.add(t_split);

  // --- HRNet pose estimation directly on the full frame (qtimlqnn) ---
  //
  // This QNN cached-binary HRNet model exports a single native output
  // tensor (per model-catalog.md "Pose Estimation" row) -- unlike YOLOv8,
  // it needs no `tensors=` output-filter on qtimlqnn.

  Element q_meta("queue", "q_meta" + s);
  Element q_pose("queue", "q_pose" + s);

  Element preproc("qtimlvconverter", "preproc" + s);

  Element inference("qtimlqnn", "inference" + s);
  inference.set("model", kPoseModel);
  inference.set("backend", kQnnBackend);
  inference.set("system", kQnnSystem);

  Element postproc("qtimlpostprocess", "postproc" + s);
  postproc.set("results", 2);
  postproc.set("module", "hrnet");
  postproc.set("labels", kPoseLabels);
  postproc.set("settings", kPoseSettings);

  auto mlf = TextFilter();
  Element q_metamux("queue", "q_metamux" + s);
  Element metamux("qtimetamux", "metamux" + s);

  pipeline.add(q_meta);
  pipeline.add(q_pose);
  pipeline.add(preproc);
  pipeline.add(inference);
  pipeline.add(postproc);
  pipeline.add_stream_filter("mlf" + s, mlf);
  pipeline.add(q_metamux);
  pipeline.add(metamux);

  // --- Overlay + queue into the shared grid composer ---

  Element ovl("qtivoverlay", "ovl" + s);
  Element q_comp_in("queue", "q_comp_in" + s);

  pipeline.add(ovl);
  pipeline.add(q_comp_in);

  // --- Per-stream linking ---

  pipeline.link("source" + s, "demux" + s, "parser" + s, "decoder" + s,
                "q_dec" + s, "vf" + s, "t_split" + s);

  // Passthrough leg + HRNet pose leg merge through metamux.
  pipeline.link("t_split" + s, "q_meta" + s, "metamux" + s);
  pipeline.link("t_split" + s, "q_pose" + s, "preproc" + s,
                "inference" + s, "postproc" + s,
                "mlf" + s, "q_metamux" + s, "metamux" + s);

  // Overlay the merged skeleton keypoints, then queue into the grid
  // composer (grid link is global, performed by the caller).
  pipeline.link("metamux" + s, "ovl" + s, "q_comp_in" + s);

  return "q_comp_in" + s;
}

void create_and_execute_pipeline() {
  Pipeline pipeline("sixstream-hrnet-pose-qnn");

  // Final grid composer that tiles the six per-stream overlaid outputs into
  // a 3x2 grid of 640x360 cells.
  Element comp("qtivcomposer", "comp");
  auto comp_vf = VideoFilter().format("NV12");
  Element q_comp_out("queue", "q_comp_out");

  Element encoder("v4l2h264enc", "encoder");
  encoder.set("capture-io-mode", 4);
  encoder.set("output-io-mode", 4);

  Element parse_out("h264parse", "parse_out");
  Element mux("mp4mux", "mux");
  Element sink("filesink", "sink");
  sink.set("location", kOutputFile);

  pipeline.add(comp);
  pipeline.add_stream_filter("comp_vf", comp_vf);
  pipeline.add(q_comp_out);
  pipeline.add(encoder);
  pipeline.add(parse_out);
  pipeline.add(mux);
  pipeline.add(sink);

  // Build the six per-stream pipelines and link each into the grid composer.
  std::vector<std::string> stream_outputs;
  for (int i = 0; i < kNumStreams; ++i) {
    stream_outputs.push_back(build_stream(pipeline, i));
  }
  for (const auto& out_name : stream_outputs) {
    pipeline.link(out_name, "comp");
  }

  // Place each stream in its 3x2 grid cell (pad index == link order above).
  for (int i = 0; i < kNumStreams; ++i) {
    const int col = i % kGridCols;
    const int row = i / kGridCols;
    pipeline.get("comp")
        .input(i)
        .set("position", std::vector<int>{col * kCellWidth, row * kCellHeight})
        .set("dimensions", std::vector<int>{kCellWidth, kCellHeight});
  }

  // Grid -> encode -> mux -> single output file (file-to-file, no display).
  pipeline.link("comp", "comp_vf", "q_comp_out", "encoder", "parse_out",
                "mux", "sink");

  // mp4mux needs EOS to finalize the file container.
  pipeline.eos(true);

  pipeline.execute();
}

int main() {
  // Mandatory logging setup before constructing any Pipeline.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  try {
    create_and_execute_pipeline();
  } catch (const std::exception& ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
