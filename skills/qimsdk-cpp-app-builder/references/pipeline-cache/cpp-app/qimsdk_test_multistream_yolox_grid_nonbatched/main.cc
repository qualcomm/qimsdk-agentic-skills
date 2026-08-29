// qimsdk-cpp-multistream-yolox-grid
//
// 32-stream, non-batched YOLOX object-detection AI wall.
// One MP4 file is replicated into 32 independent decode/inference branches
// (each stream owns its own qtimlvconverter -> qtimltflite -> qtimlpostprocess
// chain -- no qtibatch/qtimldemux sharing, per the non-batched-variant request).
// Every stream's own qtimetamux/qtivoverlay result feeds a dedicated
// qtivcomposer sink pad; the composer tiles all 32 streams into a 6x6 grid
// (ceil(sqrt(32)) = 6) and renders on waylandsink.
//
// See README.md "Pipeline Flow" for the full per-stream topology diagram and
// the grid-sizing formula.

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// Number of AI-wall streams and derived grid geometry.
constexpr int kNumStreams = 32;
constexpr int kGridCols = 6;   // ceil(sqrt(32)) = 6
constexpr int kGridRows = 6;   // ceil(32 / 6)  = 6 (last row has 4 empty cells)
constexpr int kCanvasWidth = 1920;
constexpr int kCanvasHeight = 1080;
constexpr int kTileWidth = kCanvasWidth / kGridCols;   // 320
constexpr int kTileHeight = kCanvasHeight / kGridRows; // 180

// Raise the open-file-descriptor limit before constructing the pipeline.
// 32 independent file-decode branches each open several fds (filesrc,
// v4l2 decoder instances, DMA buffers); the default per-process limit can be
// exhausted at this stream count.
void raise_fd_limit() {
  struct rlimit rl{10000, 10000};
  if (setrlimit(RLIMIT_NOFILE, &rl) != 0) {
    std::cerr << "Warning: failed to raise fd limit programmatically; "
                 "run 'ulimit -n 10000' before launching this app.\n";
  }
}

// Builds one independent decode -> tee -> (passthrough + AI) -> metamux ->
// overlay branch for stream index `idx`, and wires its output into
// qtivcomposer sink pad `idx` at its grid tile position/dimensions.
//
// Each stream is fully independent: its own filesrc/decoder, its own
// qtimlvconverter/qtimltflite/qtimlpostprocess instance (non-batched -- no
// shared/batched inference call across streams).
void build_stream(Pipeline &pipeline, const std::string &home_path,
                   const std::string &input_file, const std::string &model_path,
                   const std::string &labels_path, int idx) {
  const std::string sfx = std::to_string(idx);

  // --- Source / decode chain (replicated file source) ---
  Element source("filesrc", "src_" + sfx);
  source.set("location", input_file);

  Element demux("qtdemux", "demux_" + sfx);
  Element parse("h264parse", "parse_" + sfx);

  Element decoder("v4l2h264dec", "dec_" + sfx);
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  Element q_dec("queue", "qdec_" + sfx);

  pipeline.add(source).add(demux).add(parse).add(decoder).add(q_dec);
  pipeline.add_stream_filter("vf_" + sfx, VideoFilter().format("NV12"));

  // --- Per-stream tee: passthrough branch + independent AI branch ---
  Element split("tee", "split_" + sfx);
  Element q_video("queue", "qvid_" + sfx);
  Element q_ai("queue", "qai_" + sfx);

  pipeline.add(split).add(q_video).add(q_ai);

  // --- Independent AI branch: own qtimlvconverter -> qtimltflite ->
  //     qtimlpostprocess per stream (non-batched variant) ---
  Element preproc("qtimlvconverter", "pre_" + sfx);

  Element infer("qtimltflite", "infer_" + sfx);
  infer.set("model", model_path);
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  Element postproc("qtimlpostprocess", "post_" + sfx);
  postproc.set("module", "yolov8"); // YOLOX detection maps to yolov8 module
  postproc.set("labels", labels_path);
  postproc.set("settings", "{\"confidence\": 51.0}");

  pipeline.add(preproc).add(infer).add(postproc);
  pipeline.add_stream_filter("mlf_" + sfx, TextFilter());

  // --- Metadata merge + bounding-box overlay for this stream ---
  Element metamux("qtimetamux", "metamux_" + sfx);
  Element overlay("qtivoverlay", "overlay_" + sfx);
  Element q_comp("queue", "qcomp_" + sfx);

  pipeline.add(metamux).add(overlay).add(q_comp);

  pipeline.link("src_" + sfx, "demux_" + sfx, "parse_" + sfx, "dec_" + sfx,
                "qdec_" + sfx, "vf_" + sfx, "split_" + sfx);
  pipeline.link("split_" + sfx, "qvid_" + sfx, "metamux_" + sfx);
  pipeline.link("split_" + sfx, "qai_" + sfx, "pre_" + sfx, "infer_" + sfx,
                "post_" + sfx, "mlf_" + sfx, "metamux_" + sfx);
  pipeline.link("metamux_" + sfx, "overlay_" + sfx, "qcomp_" + sfx,
                "composer");

  // Place this stream's tile in the 6x6 grid: row-major, ceil(sqrt(32))=6
  // columns. See README "Grid Sizing" for the full derivation.
  const int col = idx % kGridCols;
  const int row = idx / kGridCols;
  pipeline.get("composer")
      .input(idx)
      .set("position", std::vector<int>{col * kTileWidth, row * kTileHeight})
      .set("dimensions", std::vector<int>{kTileWidth, kTileHeight});
}

void create_and_execute_pipeline() {
  const char *home_env = std::getenv("HOME");
  if (home_env == nullptr || *home_env == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  const std::string home_path = home_env;

  const std::string input_file =
      home_path + "/Downloads/qimsdk_samples/media/15s.mp4";
  const std::string model_path =
      home_path + "/Downloads/qimsdk_samples/models/yolox_w8a8.tflite";
  const std::string labels_path =
      home_path + "/Downloads/qimsdk_samples/labels/yolov8.json";

  Pipeline pipeline("qimsdk-cpp-multistream-yolox-grid");

  // Composer must be added before per-stream build_stream() calls link into
  // its "composer" sink pads.
  Element composer("qtivcomposer", "composer");
  pipeline.add(composer);

  for (int i = 0; i < kNumStreams; ++i) {
    build_stream(pipeline, home_path, input_file, model_path, labels_path, i);
  }

  // Large multistream/file-source composer grid -> sync=false per skill
  // default (avoids clock stalls waiting on 32 independently-scheduled
  // decode/inference branches).
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", false);

  pipeline.add(display);
  pipeline.link("composer", "display");

  pipeline.execute();
}

} // namespace

int main() {
  // Mandatory logging setup before constructing any Pipeline.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  raise_fd_limit();

  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
