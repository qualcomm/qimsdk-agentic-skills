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

//  Example pipeline (three tee/qtimetamux stages, file-to-file):
//
//    source -> demux -> parse -> decoder -> q_dec -> [vf:NV12] -> split1(tee)
//      split1. -> metamux_1                                   (passthrough)
//      split1. -> q_stage1 -> stage_01_preproc -> stage_01_inference
//        -> stage_01_postproc -> [stage1_text] -> metamux_1.
//    metamux_1 -> split2(tee)
//      split2. -> metamux_2                                   (passthrough)
//      split2. -> q_stage2 -> stage_02_preproc -> stage_02_inference
//        -> stage_02_postproc -> [stage2_text] -> metamux_2.
//    metamux_2 -> split3(tee)
//      split3. -> metamux_3                                   (passthrough)
//      split3. -> q_stage3 -> stage_03_preproc -> stage_03_inference
//        -> stage_03_postproc -> [stage3_text] -> metamux_3.
//    metamux_3 -> overlay -> q_enc -> [enc_vf:NV12] -> encoder
//              -> parse_out -> mux -> sink
//
//  Reads an MP4/H.264 file, decodes it through the hardware decoder, and runs
//  the face recognition chain on ONNX Runtime (qtimlonnx) with the CPU
//  execution provider: Stage 1 Face Detection Lite (module=qfd) locates the
//  face, Stage 2 facemap-3dmm (module=lite-3dmm, roi-batch-cumulative) aligns
//  it, Stage 3 face recognition (module=qfr, roi-batch-cumulative) emits the
//  identity match. Each stage's metadata is merged back with its own
//  qtimetamux; qtivoverlay draws the box/landmarks/label, and the annotated
//  stream is hardware-encoded to an output MP4 file.
//
//  This is the qtimlonnx/.onnx sibling of the QNN (qtimlqnn/.bin) and SNPE
//  (qtimlsnpe/.dlc) face-recognition daisy-chains: identical topology and the
//  same built-in qfr Stage-3 postprocess, with qtimlqnn/qtimlsnpe swapped for
//  qtimlonnx.
//
//  Known SDK fact (execution provider): the CPU execution provider is
//  compiled into the ONNX Runtime core (libonnxruntime.so), so it runs on any
//  device with no extra install. The QNN/HTP execution provider instead needs
//  ONNX Runtime's own QNN provider plugin on-device
//  (libonnxruntime_providers_qnn.so + libonnxruntime_providers_shared.so), a
//  separate install from the QNN backend libs and not expressible in the
//  pipeline. CPU is therefore the portable, device-independent default used
//  here. To move onto HTP, install the ORT QNN provider plugin on the device,
//  then set execution-provider=qnn + backend-path=/usr/lib/libQnnHtp.so; the
//  topology is otherwise unchanged.
//
//  Known SDK fact (optimization level): optimization-level=disable-all keeps
//  the model graph intact. qtimlonnx detects NCHW/NHWC layout by inspecting
//  Transpose nodes to set its src caps; aggressive optimization can fold
//  those nodes away, which breaks layout detection and surfaces as
//  empty-caps gst_caps_get_structure assertions at preroll. Disabling
//  optimization keeps the Transpose nodes present so layout/caps negotiate.
//
//  Known SDK fact (no tensors= property): unlike qtimlsnpe/qtimlqnn,
//  qtimlonnx has NO tensors= property to filter or reorder model outputs.
//  ONNX Runtime emits all native model outputs in order, so Stage 1's qfd
//  3-tensor contract and Stage 3's qfr 6-tensor contract must be satisfied
//  directly by each model's native ONNX output order. Do not add a tensors=
//  property to any qtimlonnx element below.
//
//  Known SDK fact (Stage 3 model identity): the recognition model is the
//  genuine multi-head model.onnx, whose graph carries all six recognition
//  head names (id_feature[1,512], liveness_feature[1,32], eye_closeness[1,2],
//  glasses[1,2], mask[1,2], sunglasses[1,2]) that module=qfr requires. It
//  stores its weights externally in model.data beside the .onnx, so BOTH
//  files must live in the same directory for qtimlonnx/ORT to load it.
//
//  Stage 2 (lite-3dmm) requires /etc/data/{blendShape,meanFace,shapeBasis}.bin
//  on the device (paths hard-coded inside facemap_3dmm_settings.json);
//  without them Stage 2 fails "Failed to open /etc/data/meanFace.bin". See
//  README "Assumptions" for this device prerequisite.

// ONNX Runtime execution provider. CPU is built into the ORT core and runs on
// any device; the QNN/HTP provider plugin is a separate device install, so
// CPU is the portable choice here. See header note.
static const std::string ONNX_EP = "cpu";

// ONNX Runtime graph optimization level. disable-all keeps Transpose nodes
// intact so qtimlonnx's layout/caps detection works. See header note.
static const std::string ONNX_OPT_LEVEL = "disable-all";

void create_and_execute_pipeline() {
  // -- source / decode -----------------------------------------------------
  Element source("filesrc", "source");
  source.set("location", "/etc/ai_demo_sample.mp4");

  // Demultiplexes the MP4 container; SDK handles the dynamic pad-added link
  // internally.
  Element demux("qtdemux", "demux");

  // Prepares the H.264 elementary stream before hardware decode.
  Element parser("h264parse", "parser");

  // Hardware-accelerated H.264 decode with zero-copy DMA buffers on both
  // sides.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  // Queue required immediately after the hardware decoder.
  Element q_dec("queue", "q_dec");

  // Normalizes decoder output to NV12 before branching/AI preprocessing.
  auto vf = VideoFilter().format("NV12");

  // -- stage 1: face detection (qfd) ---------------------------------------
  Element split1("tee", "split1");
  Element q_stage1("queue", "q_stage1");

  Element stage_01_preproc("qtimlvconverter", "stage_01_preproc");
  stage_01_preproc.set("mode", "image-batch-non-cumulative");

  // CPU EP: no backend-path (meaningful only for the QNN EP). qtimlonnx has
  // no tensors= property, so the model's 3 native outputs must already match
  // qfd's 3-tensor contract in order. See header note.
  Element stage_01_inference("qtimlonnx", "stage_01_inference");
  stage_01_inference.set("model", "/etc/face-det-lite-w8a8-w8a8.onnx");
  stage_01_inference.set("execution-provider", ONNX_EP);
  stage_01_inference.set("optimization-level", ONNX_OPT_LEVEL);

  Element stage_01_postproc("qtimlpostprocess", "stage_01_postproc");
  stage_01_postproc.set("module", "qfd");
  stage_01_postproc.set("labels", "/etc/face_detection.json");
  stage_01_postproc.set("settings", "{\"confidence\": 51.0}");
  stage_01_postproc.set("results", 6);

  auto stage1_text = TextFilter();
  Element metamux_1("qtimetamux", "metamux_1");

  // -- stage 2: face landmark / 3DMM alignment (lite-3dmm) -----------------
  Element split2("tee", "split2");
  Element q_stage2("queue", "q_stage2");

  Element stage_02_preproc("qtimlvconverter", "stage_02_preproc");
  stage_02_preproc.set("mode", "roi-batch-cumulative");
  stage_02_preproc.set("image-disposition", "centre");

  Element stage_02_inference("qtimlonnx", "stage_02_inference");
  stage_02_inference.set("model", "/etc/facemap-3dmm-w8a8-w8a8.onnx");
  stage_02_inference.set("execution-provider", ONNX_EP);
  stage_02_inference.set("optimization-level", ONNX_OPT_LEVEL);

  Element stage_02_postproc("qtimlpostprocess", "stage_02_postproc");
  stage_02_postproc.set("module", "lite-3dmm");
  stage_02_postproc.set("settings", "/etc/facemap_3dmm_settings.json");
  stage_02_postproc.set("results", 6);

  auto stage2_text = TextFilter();
  Element metamux_2("qtimetamux", "metamux_2");

  // -- stage 3: face recognition (qfr) -------------------------------------
  Element split3("tee", "split3");
  Element q_stage3("queue", "q_stage3");

  Element stage_03_preproc("qtimlvconverter", "stage_03_preproc");
  stage_03_preproc.set("mode", "roi-batch-cumulative");
  stage_03_preproc.set("image-disposition", "centre");

  // CPU EP: no backend-path. This .onnx is the multi-head recognition model
  // exporting all six outputs [1,512][1,32][1,2][1,2][1,2][1,2] in native
  // order; qtimlonnx has no tensors= to filter/reorder, so qfr's 6-tensor
  // caps negotiation relies on the model's native output order. External
  // weights live in model.data beside the .onnx (same directory). See
  // header note.
  Element stage_03_inference("qtimlonnx", "stage_03_inference");
  stage_03_inference.set("model", "/etc/model.onnx");
  stage_03_inference.set("execution-provider", ONNX_EP);
  stage_03_inference.set("optimization-level", ONNX_OPT_LEVEL);

  Element stage_03_postproc("qtimlpostprocess", "stage_03_postproc");
  stage_03_postproc.set("module", "qfr");
  stage_03_postproc.set("labels", "/etc/face_recognition.json");
  stage_03_postproc.set("settings", "/etc/face_recognition_settings.json");
  stage_03_postproc.set("results", 6);

  auto stage3_text = TextFilter();
  Element metamux_3("qtimetamux", "metamux_3");

  // -- overlay + encode to file ---------------------------------------------
  Element overlay("qtivoverlay", "overlay");

  Element q_enc("queue", "q_enc");
  auto enc_vf = VideoFilter().format("NV12");

  // File/decode source path: driver manages both encoder input and output
  // buffers via dmabuf.
  Element encoder("v4l2h264enc", "encoder");
  encoder.set("capture-io-mode", 4);
  encoder.set("output-io-mode", 4);

  Element parse_out("h264parse", "parse_out");
  Element mux("mp4mux", "mux");

  Element sink("filesink", "sink");
  sink.set("location", "/etc/face_recognition_onnx_out.mp4");

  // -- assemble pipeline -----------------------------------------------------
  Pipeline pipeline("qimsdk-cpp-face-recognition-onnx-qfr");

  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("vf", vf)
          .add(split1)
          .add(q_stage1)
          .add(stage_01_preproc)
          .add(stage_01_inference)
          .add(stage_01_postproc)
          .add_stream_filter("stage1_text", stage1_text)
          .add(metamux_1)
          .add(split2)
          .add(q_stage2)
          .add(stage_02_preproc)
          .add(stage_02_inference)
          .add(stage_02_postproc)
          .add_stream_filter("stage2_text", stage2_text)
          .add(metamux_2)
          .add(split3)
          .add(q_stage3)
          .add(stage_03_preproc)
          .add(stage_03_inference)
          .add(stage_03_postproc)
          .add_stream_filter("stage3_text", stage3_text)
          .add(metamux_3)
          .add(overlay)
          .add(q_enc)
          .add_stream_filter("enc_vf", enc_vf)
          .add(encoder)
          .add(parse_out)
          .add(mux)
          .add(sink);

  // source -> decode -> NV12 -> stage 1 split
  pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split1");

  // stage 1: passthrough + detection branches merge at metamux_1
  pipeline.link("split1", "metamux_1");
  pipeline.link("split1", "q_stage1", "stage_01_preproc", "stage_01_inference",
                "stage_01_postproc", "stage1_text", "metamux_1");

  // stage 2: metamux_1 output feeds stage 2 split
  pipeline.link("metamux_1", "split2");
  pipeline.link("split2", "metamux_2");
  pipeline.link("split2", "q_stage2", "stage_02_preproc", "stage_02_inference",
                "stage_02_postproc", "stage2_text", "metamux_2");

  // stage 3: metamux_2 output feeds stage 3 split
  pipeline.link("metamux_2", "split3");
  pipeline.link("split3", "metamux_3");
  pipeline.link("split3", "q_stage3", "stage_03_preproc", "stage_03_inference",
                "stage_03_postproc", "stage3_text", "metamux_3");

  // overlay + encode to file
  pipeline.link("metamux_3", "overlay", "q_enc", "enc_vf", "encoder",
                "parse_out", "mux", "sink");

  // Finalize the MP4 container on EOS.
  pipeline.eos(true);
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
