#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// C++ string literals do not shell-expand ($HOME/...). Resolve HOME once at
// startup and build absolute paths from it instead of a literal "$HOME/...".
std::string expand_home_path() {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return std::string(home);
}

}  // namespace

const std::string HOME_PATH = expand_home_path();

const std::string MODEL_PATH = "/etc/yolov8_det_w8a8.bin";
const std::string LABELS_PATH = "/etc/yolov8.json";
const std::string QNN_BACKEND = "/usr/lib/libQnnHtp.so";
const std::string QNN_SYSTEM = "/usr/lib/libQnnSystem.so";

//  Example pipeline:
//
//    source -> [videostream:NV12,1920x1080@30] -> split name=split
//      split. -> mlmuxer
//      split. -> q1 -> preprocessing -> q2 -> inferencing -> q3
//             -> postprocessing -> [mlf:text] -> q4 -> mlmuxer
//    mlmuxer -> overlay -> display
//
//  Captures the default ISP camera, runs YOLOv8 object detection directly on
//  the QNN runtime (qtimlqnn) against a pre-converted QNN cached-binary model
//  (no TFLite external-delegate indirection), overlays detected bounding
//  boxes and class labels, and renders fullscreen on a Wayland display.
//
//  NOTE on `tensors`: this cached QNN binary bakes NMS in and exports 3
//  separate output tensors (boxes, scores, class_idx). qtimlqnn's `tensors`
//  property is set to the plain angle-bracket string
//  "<boxes,scores,class_idx>" via Element::set(...) -- the same mechanism
//  used in the sibling SNPE/QNN face-detection/face-recognition
//  and six-stream-daisychain artifacts in this cache. Without it qtimlqnn
//  forwards only the first tensor and qtimlpostprocess module=yolov8 fails
//  caps negotiation.

void create_and_execute_pipeline() {

  // Captures frames from the default ISP camera.
  Element source("qtiqmmfsrc", "source");
  source.set("camera", 0);

  // Constrains the camera stream to NV12 at a fixed resolution/framerate
  // before branching for display and AI preprocessing.
  auto videostream = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);

  // Splits the camera stream into a passthrough/overlay branch and an AI branch.
  // Added directly via Pipeline::add("tee", "split") -- tee needs no
  // properties, so there is no separate Element to pre-construct here.

  // Queues frames from the tee into the AI branch.
  Element q1("queue", "q1");

  // Converts raw NV12 video frames into the model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Runs YOLOv8 detection directly on the QNN runtime against a cached QNN
  // binary (no TFLite external-delegate indirection). This cached binary
  // bakes NMS in and exports 3 separate output tensors (boxes, scores,
  // class_idx) -- requires tensors="<boxes,scores,class_idx>"; without it
  // qtimlqnn forwards only the first tensor and qtimlpostprocess
  // module=yolov8 fails caps negotiation. The plain angle-bracket string
  // form is passed directly to Element::set(...), the same mechanism
  // used in the sibling SNPE/QNN face-detection/face-recognition
  // and six-stream-daisychain artifacts in this cache.
  Element inferencing("qtimlqnn", "inferencing");
  inferencing.set("model", MODEL_PATH);
  inferencing.set("backend", QNN_BACKEND);
  inferencing.set("system", QNN_SYSTEM);
  inferencing.set("tensors", "<boxes,scores,class_idx>");

  // Queues tensor outputs before postprocessing.
  Element q3("queue", "q3");

  // Decodes YOLOv8 model output tensors into detection metadata.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("results", 5);
  postprocessing.set("module", "yolov8");
  postprocessing.set("labels", LABELS_PATH);
  postprocessing.set("settings", "{\"confidence\": 51.0}");
  postprocessing.set("bbox-stabilization", true);

  // Stream filter marking the AI branch output as text metadata.
  auto mlf = TextFilter();

  // Queues metadata before it is merged back with the video branch.
  Element q4("queue", "q4");

  // Merges detection metadata produced by the AI branch with the original
  // video frames from the passthrough branch.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Renders detected bounding boxes and class labels on top of the frame.
  Element overlay("qtivoverlay", "overlay");

  // Displays the annotated stream fullscreen on the Wayland compositor.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", true);

  // Creates the pipeline, adds and links elements, and executes it.
  Pipeline pipeline("camera-qnn-yolov8-pipeline");
  pipeline.add(source);
  pipeline.add_stream_filter("videostream", videostream);
  pipeline.add("tee", "split");
  pipeline.add(q1);
  pipeline.add(preprocessing);
  pipeline.add(q2);
  pipeline.add(inferencing);
  pipeline.add(q3);
  pipeline.add(postprocessing);
  pipeline.add_stream_filter("mlf", mlf);
  pipeline.add(q4);
  pipeline.add(mlmuxer);
  pipeline.add(overlay);
  pipeline.add(display);

  pipeline.link("source", "videostream", "split");
  pipeline.link("split", "mlmuxer");
  pipeline.link("split", "q1", "preprocessing", "q2", "inferencing", "q3",
                "postprocessing", "mlf", "q4", "mlmuxer");
  pipeline.link("mlmuxer", "overlay", "display");

  pipeline.execute();
}

int main() {
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
