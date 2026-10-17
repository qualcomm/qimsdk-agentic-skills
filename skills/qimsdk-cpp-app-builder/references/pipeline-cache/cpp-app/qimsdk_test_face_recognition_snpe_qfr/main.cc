#include <iostream>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

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
//  the face recognition chain on the SNPE DSP (Hexagon) delegate (qtimlsnpe):
//  Stage 1 Face Detection Lite (module=qfd) locates the face, Stage 2
//  facemap-3dmm (module=lite-3dmm, roi-batch-cumulative) aligns it, Stage 3
//  face recognition (module=qfr, roi-batch-cumulative) emits the identity
//  match. Each stage's metadata is merged back with its own qtimetamux;
//  qtivoverlay draws the box/landmarks/label, and the annotated stream is
//  hardware-encoded to an output MP4 file.
//
//  This is the C++ equivalent of the verified Python app
//  qimsdk_test_face_recognition_snpe_qfr.py, and the qtimlsnpe/.dlc sibling
//  of the ONNX (qtimlonnx/.onnx) and QNN (qtimlqnn/.bin) face-recognition
//  daisy-chains: identical topology and the same built-in qfr Stage-3
//  postprocess, with qtimlonnx/qtimlqnn swapped for qtimlsnpe + delegate=dsp.
//
//  Known SDK fact (Stage 1): the face_det_lite .dlc exports 3 separate named
//  output tensors (heatmap/1ch, bbox/4ch, landmark/10ch). Without an explicit
//  tensors= list, qtimlsnpe forwards only the first and module=qfd fails caps
//  negotiation. See model-catalog.md "Lightweight-Face-Detection (SNPE)".
//
//  Known SDK fact (Stage 3): the recognition .dlc is a MULTI-HEAD model with
//  six output heads (id_feature[1,512], liveness_feature[1,32],
//  eye_closeness[1,2], glasses[1,2], mask[1,2], sunglasses[1,2]), but SNPE's
//  DEFAULT output set is only the last leaf (sunglasses[1,2]). module=qfr
//  requires all six, in order, so tensors= must name every head; without it
//  one tensor is offered against qfr's six-tensor contract and caps
//  negotiation fails. See model-catalog.md "Facial-Attribute-Detection (SNPE)"
//  and plugin-catalog.md "Tensor Filter - Decision Rule" (multi-head case).
//
//  Stage 2 (lite-3dmm) needs no tensors= (single native output tensor) but
//  DOES require /etc/data/{blendShape,meanFace,shapeBasis}.bin on the device
//  (paths hard-coded inside facemap_3dmm_settings.json); without them Stage 2
//  fails "Failed to open /etc/data/meanFace.bin". See README "Assumptions".
//
//  Known SDK fact (tensors= in native C++): `tensors` on qtimlsnpe is a
//  GstValueArray of output tensor names. The SDK reference docs
//  (inference-runtimes.md / plugin-catalog.md) describe constructing it via
//  the raw GStreamer API (g_value_init/gst_value_array_append_value/
//  g_object_set_property), but qti::Element exposes no raw GObject*/GstElement*
//  handle to call that API on. The only documented array-valued set() overload
//  in this SDK is qti::Port::set(...), which explicitly accepts a
//  std::vector<...>/initializer-list for array-valued pad properties (e.g.
//  port.set("position", std::vector<int>{0, 0})). Element::set(...) below is
//  given a std::vector<std::string> for "tensors" as the closest documented,
//  non-invented array-value convention in this SDK; this is an explicit
//  interpretive assumption, flagged again in README "Assumptions". (Same
//  resolution already used for the qtimlqnn sibling app
//  qimsdk-cpp-face-recognition-qnn-qfr.)

// Confidence threshold (percent) applied to Stage 1 detection, encoded into
// the qtimlpostprocess settings JSON below with the canonical "confidence"
// key.

void create_and_execute_pipeline() {
  // -- source / decode -------------------------------------------------------
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

  // -- stage 1: face detection (qfd) -----------------------------------------
  Element split1("tee", "split1");
  Element q_stage1("queue", "q_stage1");

  Element stage_01_preproc("qtimlvconverter", "stage_01_preproc");
  stage_01_preproc.set("mode", "image-batch-non-cumulative");

  Element stage_01_inference("qtimlsnpe", "stage_01_inference");
  stage_01_inference.set("model", "/etc/face-det-lite-w8a8.dlc");
  stage_01_inference.set("delegate", "dsp");
  // 3 separate named output tensors; see "Known SDK fact (Stage 1)" above.
  stage_01_inference.set("tensors", "<heatmap,bbox,landmark>");

  Element stage_01_postproc("qtimlpostprocess", "stage_01_postproc");
  stage_01_postproc.set("module", "qfd");
  stage_01_postproc.set("labels", "/etc/face_detection.json");
  stage_01_postproc.set("settings", "{\"confidence\": 51.0}");
  stage_01_postproc.set("results", 6);

  auto stage1_text = TextFilter();
  Element metamux_1("qtimetamux", "metamux_1");

  // -- stage 2: face landmark / 3DMM alignment (lite-3dmm) -------------------
  Element split2("tee", "split2");
  Element q_stage2("queue", "q_stage2");

  Element stage_02_preproc("qtimlvconverter", "stage_02_preproc");
  stage_02_preproc.set("mode", "roi-batch-cumulative");
  stage_02_preproc.set("image-disposition", "centre");

  Element stage_02_inference("qtimlsnpe", "stage_02_inference");
  stage_02_inference.set("model", "/etc/facemap-3dmm-w8a8.dlc");
  stage_02_inference.set("delegate", "dsp");
  // Single native output tensor -> no tensors= needed (see header note).

  Element stage_02_postproc("qtimlpostprocess", "stage_02_postproc");
  stage_02_postproc.set("module", "lite-3dmm");
  stage_02_postproc.set("settings", "/etc/facemap_3dmm_settings.json");
  stage_02_postproc.set("results", 6);

  auto stage2_text = TextFilter();
  Element metamux_2("qtimetamux", "metamux_2");

  // -- stage 3: face recognition (qfr) ---------------------------------------
  Element split3("tee", "split3");
  Element q_stage3("queue", "q_stage3");

  Element stage_03_preproc("qtimlvconverter", "stage_03_preproc");
  stage_03_preproc.set("mode", "roi-batch-cumulative");
  stage_03_preproc.set("image-disposition", "centre");

  Element stage_03_inference("qtimlsnpe", "stage_03_inference");
  stage_03_inference.set("model", "/etc/Facial-Attribute-Detection_w8a8.dlc");
  stage_03_inference.set("delegate", "dsp");
  // Multi-head model, SNPE default emits only the last leaf -> name all six
  // outputs in qfr's expected order. See "Known SDK fact (Stage 3)" above.
  stage_03_inference.set("tensors",
      "<id_feature,liveness_feature,eye_closeness,glasses,mask,sunglasses>");

  Element stage_03_postproc("qtimlpostprocess", "stage_03_postproc");
  stage_03_postproc.set("module", "qfr");
  stage_03_postproc.set("labels", "/etc/face_recognition.json");
  stage_03_postproc.set("settings", "/etc/face_recognition_settings.json");
  stage_03_postproc.set("results", 6);

  auto stage3_text = TextFilter();
  Element metamux_3("qtimetamux", "metamux_3");

  // -- overlay + encode to file -----------------------------------------------
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
  sink.set("location", "/tmp/face_recognition_snpe_out.mp4");

  // -- assemble pipeline -------------------------------------------------------
  Pipeline pipeline("qimsdk-cpp-face-recognition-snpe-qfr");

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
