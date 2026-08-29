#include <iostream>
#include <cstdlib>
#include <stdexcept>
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

void create_and_execute_pipeline() {
  Pipeline pipeline("qimsdk-cpp-image-segmentation");

  // Source / decode: MP4 file -> H.264 hardware decode
  Element source("filesrc", "src");
  source.set("location", expand_home("/Downloads/qimsdk_samples/media/15s.mp4"));

  Element demux("qtdemux", "demux");
  Element parser("h264parse", "parse");

  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  Element q_dec("queue", "q_dec");

  // Normalize decoded output to NV12 before branching
  auto video_filter = VideoFilter().format("NV12");

  Element split("tee", "split");

  // Passthrough branch: raw video feeds composer sink_0
  Element q_video("queue", "q_video");

  // AI branch: preprocess -> HTP inference -> segmentation postprocess
  Element q_ai("queue", "q_ai");
  Element preprocess("qtimlvconverter", "pre");

  Element q_infer("queue", "q_infer");
  Element infer("qtimltflite", "infer");
  infer.set("delegate", "external");
  infer.set("model", expand_home("/Downloads/qimsdk_samples/models/deeplabv3_plus_mobilenet_w8a8.tflite"));
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  Element q_post("queue", "q_post");
  Element postprocess("qtimlpostprocess", "post");
  postprocess.set("module", "deeplab-argmax");
  postprocess.set("labels", expand_home("/Downloads/qimsdk_samples/labels/dv3-argmax.json"));

  // Segmentation mask render caps: RGBA is mandatory (BGRA fails to link);
  // no pinned resolution so the composer sink-pad dimensions size the tile.
  auto render_filter = VideoFilter().format("RGBA");

  Element composer("qtivcomposer", "composer");
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("vf", video_filter)
          .add(split)
          .add(q_video)
          .add(q_ai)
          .add(preprocess)
          .add(q_infer)
          .add(infer)
          .add(q_post)
          .add(postprocess)
          .add_stream_filter("render_filter", render_filter)
          .add(composer)
          .add(display)
          .link("src", "demux", "parse", "decoder", "q_dec", "vf", "split")
          .link("split", "q_video", "composer")
          .link("split", "q_ai", "pre", "q_infer", "infer", "q_post", "post", "render_filter", "composer");

  // Blend the segmentation mask (composer input 1) over the raw video (input 0).
  pipeline.get("composer").input(1).set("alpha", 0.5);

  pipeline.link("composer", "display");

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
