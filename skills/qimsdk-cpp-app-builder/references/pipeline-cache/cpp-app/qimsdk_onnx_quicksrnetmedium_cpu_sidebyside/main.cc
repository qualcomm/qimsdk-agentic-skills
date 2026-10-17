#include <iostream>
#include <stdexcept>
#include <vector>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// Device paths for input media and the ONNX model (absolute, device-specific).
const char* kInputFile = "/etc/super.mp4";
const char* kModelPath = "/etc/quicksrnetmedium.onnx";
// NOTE: ONNX Runtime auto-discovers this external-data sidecar by convention
// (same directory, same basename as the .onnx) when the model exceeds the
// protobuf 2GB inline-weight limit. It is not a settable pipeline property,
// so it has no corresponding Element::set(...) call below; it must simply be
// present on-device next to kModelPath. See README "Assumptions".
const char* kModelDataPath = "/etc/quicksrnetmedium.data";

// The composer uses two 960x1080 panes: original on the left and SR output on
// the right. Adjust these only when the source display layout must change.
const int kTileWidth = 960;
const int kTileHeight = 1080;

}  // namespace

void create_and_execute_pipeline() {
  Pipeline pipeline("qimsdk-cpp-onnx-quicksrnetmedium-cpu-sidebyside");

  // Source and hardware H.264 decode.
  Element source("filesrc", "source");
  source.set("location", kInputFile);

  Element demux("qtdemux", "demux");
  Element parser("h264parse", "parser");

  Element decoder("v4l2h264dec", "decoder");
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  Element q_dec("queue", "q_dec");
  auto nv12 = qti::VideoFilter().format("NV12");

  // Split decoded video into original and super-resolution render paths.
  Element split("tee", "split");
  Element q_original("queue", "q_original");

  // This device's ONNX Runtime rejects this model with duplicate `node_conv2d`
  // node names at the default optimization level; disable graph optimization.
  Element q_sr("queue", "q_sr");
  Element preprocess("qtimlvconverter", "preprocess");
  Element q_infer("queue", "q_infer");

  Element infer("qtimlonnx", "infer");
  infer.set("model", kModelPath);
  infer.set("execution-provider", "cpu");
  infer.set("optimization-level", "disable-all");

  Element q_post("queue", "q_post");
  Element postprocess("qtimlpostprocess", "postprocess");
  postprocess.set("module", "srnet");

  // srnet emits rendered video; RGBA is required by qtimlpostprocess caps.
  // Do not pin its resolution: composer pad geometry sizes the output pane.
  auto sr_render = qti::VideoFilter().format("RGBA");
  Element q_render("queue", "q_render");

  // Compose original and upscaled frames into a side-by-side Wayland display.
  Element composer("qtivcomposer", "composer");

  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add(split)
          .add(q_original)
          .add(q_sr)
          .add(preprocess)
          .add(q_infer)
          .add(infer)
          .add(q_post)
          .add(postprocess)
          .add(q_render)
          .add(composer)
          .add(display);

  pipeline.add_stream_filter("nv12", nv12);
  pipeline.add_stream_filter("sr_render", sr_render);

  pipeline.link("source", "demux");
  pipeline.link("demux", "parser", "decoder", "q_dec", "nv12", "split");
  pipeline.link("split", "q_original", "composer");
  pipeline.link(
      "split", "q_sr", "preprocess", "q_infer", "infer", "q_post",
      "postprocess", "sr_render", "q_render", "composer");
  pipeline.link("composer", "display");

  // Original is left; QuickSRNet's upscaled output is right.
  pipeline.get("composer").input(0).set("position", std::vector<int>{0, 0});
  pipeline.get("composer").input(0).set("dimensions", std::vector<int>{kTileWidth, kTileHeight});
  pipeline.get("composer").input(1).set("position", std::vector<int>{kTileWidth, 0});
  pipeline.get("composer").input(1).set("dimensions", std::vector<int>{kTileWidth, kTileHeight});

  pipeline.execute();
}

int main() {
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
