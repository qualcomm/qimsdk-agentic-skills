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
//    source -> demux -> parser -> decoder -> q_dec -> [NV12 vf] -> tee name=split
//      split. -> metamux                                   (passthrough video)
//      split. -> q_cls -> cls_preproc -> cls_inference -> cls_postproc -> [TextFilter] -> metamux.
//      metamux -> overlay -> q_enc -> [NV12 vf] -> encoder -> parse_out
//              -> mux -> sink
//
//  The pipeline decodes an MP4/H.264 file with the hardware decoder, runs
//  Inception-v3 classification on full frames via ONNX Runtime on the CPU
//  execution provider, overlays the top label with qtivoverlay, and
//  hardware-encodes the annotated stream to one output MP4 file.

void create_and_execute_pipeline() {

  // -- source / decode ----------------------------------------------------

  // Reads the encoded MP4 file from disk.
  Element source("filesrc", "source");
  source.set("location", "/etc/ai_demo_sample.mp4");

  // Demuxes the MP4 container to extract the H.264 elementary stream.
  Element demux("qtdemux", "demux");

  // Parses the H.264 stream for the hardware decoder.
  Element parser("h264parse", "parser");

  // Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  // Queue immediately after hardware decode, required to decouple the decoder.
  Element q_dec("queue", "q_dec");

  // Normalizes decoded frames to NV12 before branching.
  auto vf = VideoFilter().format("NV12");

  // Single-input tee: one leg is the passthrough video, the other runs
  // classification; their metadata merges at qtimetamux. tee has no
  // properties to set, so it is added directly via the factory-string
  // overload of pipeline.add(...) below.

  // -- classification stage (Inception-v3, ONNX Runtime CPU) --------------

  // Queues frames from the tee into the classification branch.
  Element q_cls("queue", "q_cls");

  // Converts raw video frames into the model input tensor format.
  Element cls_preproc("qtimlvconverter", "cls_preproc");
  cls_preproc.set("mode", "image-batch-non-cumulative");

  // Runs Inception-v3 classification on ONNX Runtime.
  //
  // Known SDK fact (execution provider): the CPU execution provider is
  // compiled into the ONNX Runtime core (libonnxruntime.so), so it runs on
  // any device with no extra install, unlike the QNN/HTP provider plugin.
  //
  // Known SDK fact (optimization level): optimization-level=disable-all keeps
  // the model graph intact. qtimlonnx inspects Transpose nodes to detect
  // NCHW/NHWC layout for its src caps; aggressive optimization can fold those
  // away and break layout/caps negotiation at preroll.
  //
  // Known SDK fact (no tensors= property): qtimlonnx has no tensors= property
  // to filter/reorder model outputs, so the model's native ONNX output order
  // must satisfy the module=mobilenet classification contract directly.
  Element cls_inference("qtimlonnx", "cls_inference");
  cls_inference.set("model", "/etc/inception_v3.onnx");
  cls_inference.set("execution-provider", "cpu");
  cls_inference.set("optimization-level", "disable-all");

  // Decodes classification tensor output into label metadata.
  Element cls_postproc("qtimlpostprocess", "cls_postproc");
  cls_postproc.set("module", "mobilenet");
  cls_postproc.set("labels", "/etc/classification.txt");

  // Stream filter marking the classification branch output as text metadata.
  auto cls_text = TextFilter();

  // Merges classification metadata with the passthrough video frames.
  Element metamux("qtimetamux", "metamux");

  // -- overlay + encode to file -------------------------------------------

  // Renders the classification label overlay on the frame.
  Element overlay("qtivoverlay", "overlay");

  // Queues frames before the hardware encoder.
  Element q_enc("queue", "q_enc");

  // Normalizes to NV12 for the hardware encoder.
  auto enc_vf = VideoFilter().format("NV12");

  // Hardware-encodes the annotated stream to H.264 with DMA IO modes.
  // File/decode source path: driver manages both encoder input and output
  // buffers via dmabuf.
  Element encoder("v4l2h264enc", "encoder");
  encoder.set("capture-io-mode", 4);
  encoder.set("output-io-mode", 4);

  // Parses the encoded H.264 stream for the muxer.
  Element enc_parser("h264parse", "parse_out");

  // Muxes the encoded stream into an MP4 container.
  Element mux("mp4mux", "mux");

  // Writes the output MP4 file to disk.
  Element sink("filesink", "sink");
  sink.set("location", "/etc/image_classification_onnx_cpu_out.mp4");

  // -- assemble pipeline --------------------------------------------------
  Pipeline pipeline("qimsdk-cpp-image-classification-onnx-cpu");

  pipeline.add(source);
  pipeline.add(demux);
  pipeline.add(parser);
  pipeline.add(decoder);
  pipeline.add(q_dec);
  pipeline.add_stream_filter("vf", vf);
  pipeline.add("tee", "split");

  pipeline.add(q_cls);
  pipeline.add(cls_preproc);
  pipeline.add(cls_inference);
  pipeline.add(cls_postproc);
  pipeline.add_stream_filter("cls_text", cls_text);
  pipeline.add(metamux);

  pipeline.add(overlay);
  pipeline.add(q_enc);
  pipeline.add_stream_filter("enc_vf", enc_vf);
  pipeline.add(encoder);
  pipeline.add(enc_parser);
  pipeline.add(mux);
  pipeline.add(sink);

  // source -> decode -> NV12 -> tee
  pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split");

  // passthrough video leg + classification leg merge at metamux
  pipeline.link("split", "metamux");
  pipeline.link(
      "split", "q_cls", "cls_preproc", "cls_inference",
      "cls_postproc", "cls_text", "metamux");

  // overlay the label, then encode to the output file
  pipeline.link(
      "metamux", "overlay", "q_enc", "enc_vf", "encoder",
      "parse_out", "mux", "sink");

  // Finalize the MP4 container on EOS. Required: CPU ONNX inference runs well
  // below realtime, so the file-to-file run must drain to natural EOS for
  // mp4mux to write the moov atom and produce a playable output file.
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
