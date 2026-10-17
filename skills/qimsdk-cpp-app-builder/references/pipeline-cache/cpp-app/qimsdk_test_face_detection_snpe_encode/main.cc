#include <iostream>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

#include <qti/qimsdk.h>

using namespace qti;

//  Example pipeline (matches the verified Python source
//  qimsdk_test_face_detection_snpe_encode.py exactly):
//
//    source -> demux -> parser -> decoder -> q_dec -> [vf:NV12] -> split(tee)
//      split. -> facemux -> overlay -> encoder -> parse_out -> mux -> sink
//      split. -> q1 -> preprocessing -> q2 -> inferencing -> q3
//             -> postprocessing -> [mlf:text] -> facemux.
//
//  Reads an MP4/H.264 file, decodes it through the hardware decoder, runs
//  Face Detection Lite (face_det_lite, qfd postprocess module) on full frames
//  using the SNPE runtime on the DSP (Hexagon) delegate, overlays the
//  detected face bounding boxes on each frame, and hardware-encodes the
//  annotated stream to an output MP4 file.
//
//  Known SDK fact (carried over from the Python source's own comment and
//  confirmed in references/model-catalog.md "Lightweight-Face-Detection
//  (face_det_lite, SNPE)"): this .dlc exports 3 separate named output tensors
//  (heatmap/1ch, bbox/4ch, landmark/10ch). Without an explicit `tensors=`
//  filter on qtimlsnpe, only the first tensor is forwarded and
//  qtimlpostprocess module=qfd fails caps negotiation.
void create_and_execute_pipeline() {

  const std::string input_file = "/etc/ai_demo_sample.mp4";
  const std::string model_path = "/etc/face-det-lite-w8a8.dlc";
  const std::string labels_path = "/etc/face_detection.json";
  const std::string output_file = "/tmp/face_detect_snpe_out.mp4";

  // Reads the input MP4 file as raw bytes.
  Element source("filesrc", "source");
  source.set("location", input_file);

  // Extracts the elementary H.264 stream from the MP4 container. Dynamic
  // pad-added linking is handled internally by the SDK.
  Element demux("qtdemux", "demux");

  // Prepares the H.264 bitstream for the decoder.
  Element parser("h264parse", "parser");

  // Decodes the compressed H.264 stream into raw video frames.
  //
  // File source decoded through the hardware decoder, so both io-modes are 4.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("capture-io-mode", 4);
  decoder.set("output-io-mode", 4);

  // Queue immediately after hardware decode to decouple the decoder thread.
  Element q_dec("queue", "q_dec");

  // Restricts the decoded stream to NV12 before branching for AI/overlay.
  auto videofilter = VideoFilter().format("NV12");

  // Splits decoded frames into a video/overlay branch and an AI branch.
  Element split("tee", "split");

  // Queues frames from the tee into the AI branch.
  Element q1("queue", "q1");

  // Converts raw NV12 video frames into the model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Runs Face Detection Lite on full frames using the SNPE runtime on the
  // DSP (Hexagon) delegate.
  //
  // tensors= is mandatory here: the .dlc exports 3 named output tensors
  // (heatmap, bbox, landmark) and qtimlsnpe only forwards the first one
  // unless told otherwise, starving qtimlpostprocess module=qfd of the
  // remaining 2 required tensors and failing caps negotiation.
  Element inferencing("qtimlsnpe", "inferencing");
  inferencing.set("model", model_path);
  inferencing.set("delegate", "dsp");
  inferencing.set("tensors", "<heatmap,bbox,landmark>");

  // Queues tensor outputs before postprocessing.
  Element q3("queue", "q3");

  // Decodes face_det_lite model output tensors into face-detection metadata.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "qfd");
  postprocessing.set("labels", labels_path);
  postprocessing.set("settings", "{\"confidence\": 51.0}");

  // Stream filter marking the AI branch output as text metadata.
  auto mlf = TextFilter();

  // Merges detection metadata produced by the AI branch with the original
  // video frames from the passthrough branch.
  Element facemux("qtimetamux", "facemux");

  // Renders detected face bounding boxes on top of the frame.
  Element overlay("qtivoverlay", "overlay");

  // Hardware-encodes the annotated NV12 stream back to H.264.
  //
  // File source path decoded via hardware decoder: capture/output io-mode 4.
  Element encoder("v4l2h264enc", "encoder");
  encoder.set("capture-io-mode", 4);
  encoder.set("output-io-mode", 4);

  // Prepares the encoded H.264 stream for muxing.
  Element parse_out("h264parse", "parse_out");

  // Multiplexes the H.264 stream into an MP4 container.
  Element mux("mp4mux", "mux");

  // Writes the final annotated video to the output MP4 file.
  Element sink("filesink", "sink");
  sink.set("location", output_file);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Each tee branch is terminated by its own complete link() chain; the
  // facemux -> overlay/encoder/mux chain is linked separately.
  Pipeline pipeline("qimsdk-cpp-face-detection-snpe-encode");
  pipeline.add(source)
          .add(demux)
          .add(parser)
          .add(decoder)
          .add(q_dec)
          .add_stream_filter("videofilter", videofilter)
          .add(split)
          .add(q1)
          .add(preprocessing)
          .add(q2)
          .add(inferencing)
          .add(q3)
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(facemux)
          .add(overlay)
          .add(encoder)
          .add(parse_out)
          .add(mux)
          .add(sink)
          .eos(true)
          .link("source", "demux", "parser", "decoder", "q_dec", "videofilter", "split")
          .link("split", "facemux")
          .link("split", "q1", "preprocessing", "q2", "inferencing", "q3",
                "postprocessing", "mlf", "facemux")
          .link("facemux", "overlay", "encoder", "parse_out", "mux", "sink")
          .execute();
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
