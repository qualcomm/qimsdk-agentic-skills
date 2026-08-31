// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

#include <qti/qimsdk.h>

#include "imsdk-test-utils.h"

using namespace qti;

namespace {

void TransformDimensions(MLDetection &box,
                         const Region& region) {

  box.top = (box.top - region.y) / region.height;
  box.bottom = (box.bottom - region.y) / region.height;
  box.left = (box.left - region.x) / region.width;
  box.right = (box.right - region.x) / region.width;
}

float IntersectionScore(const MLDetection &l_box,
                        const MLDetection &r_box) {

  // Figure out the width of the intersecting rectangle.
  // 1st: Find out the X axis coordinate of left most Top-Right point.
  float width = std::min(l_box.right, r_box.right);
  // 2nd: Find out the X axis coordinate of right most Top-Left point
  // and substract from the previously found value.
  width -= std::max(l_box.left, r_box.left);

  // Negative width means that there is no overlapping.
  if (width <= 0.0F)
    return 0.0F;

  // Figure out the height of the intersecting rectangle.
  // 1st: Find out the Y axis coordinate of bottom most Left-Top point.
  float height = std::min(l_box.bottom, r_box.bottom);
  // 2nd: Find out the Y axis coordinate of top most Left-Bottom point
  // and substract from the previously found value.
  height -= std::max(l_box.top, r_box.top);

  // Negative height means that there is no overlapping.
  if (height <= 0.0F)
    return 0.0F;

  // Calculate intersection area.
  float intersection = width * height;

  // Calculate the area of the 2 objects.
  float l_area = (l_box.right - l_box.left) * (l_box.bottom - l_box.top);
  float r_area = (r_box.right - r_box.left) * (r_box.bottom - r_box.top);

  // Intersection over Union score.
  return intersection / (l_area + r_area - intersection);
}

int32_t NonMaxSuppression(const MLDetection &l_box,
                          const MLDetections &boxes) {
  const float kNMSIntersectionTreshold = 0.5;

  for (uint32_t idx = 0; idx < boxes.size();  idx++) {
    MLDetection r_box = boxes[idx];

    // If labels do not match, continue with next list entry.
    if (l_box.name != r_box.name)
      continue;

    double score = IntersectionScore(l_box, r_box);

    // If the score is below the threshold, continue with next list entry.
    if (score <= kNMSIntersectionTreshold)
      continue;

    // If confidence of current box is higher, remove the old entry.
    if (l_box.confidence > r_box.confidence)
      return idx;

    // If confidence of current box is lower, don't add it to the list.
    if (l_box.confidence <= r_box.confidence)
      return -2;
  }

  // If this point is reached then add current box to the list;
  return -1;
}

}  // namespace

bool decode_detection(const MLFrame& frame,
                      MLDetections& detections,
                      const MLParam& mlparams,
                      const std::vector<LabelEntry>& labels,
                      float confidence_threshold) {
  detections.clear();

  if (frame.tensors.empty()) {
    std::cout << "[external-postprocess][detection] called: "
              << "tensors=" << frame.tensors.size()
              << ", params=" << mlparams.fields.size()
              << ", detections(out)=" << detections.size()
              << ", decode_ok=false"
              << std::endl;
    return false;
  }

  Region region;
  mlparams.get("input-tensor-region", region);

  uint32_t n_paxels = frame.tensors[0].dimensions[1];

  const float* bboxes = static_cast<const float*>(frame.tensors[0].data);
  const float* scores = static_cast<const float*>(frame.tensors[1].data);
  const float* classes = static_cast<const float*>(frame.tensors[2].data);

  for (uint32_t idx = 0; idx < n_paxels; idx++) {
    double confidence = scores[idx];
    uint32_t class_idx = static_cast<uint32_t>(classes[idx]);

    // Discard results below the minimum score threshold.
    if (confidence < confidence_threshold)
      continue;

    MLDetection entry;
    entry.left   = bboxes[idx * 4];
    entry.top    = bboxes[idx * 4 + 1];
    entry.right  = bboxes[idx * 4 + 2];
    entry.bottom = bboxes[idx * 4 + 3];

    std::cout << "Class: " << class_idx
              << " Confidence: " << confidence
              << " Box[" << entry.top
              << ", " << entry.left
              << ", " << entry.bottom
              << ", " << entry.right << "]"
              << std::endl;

     // Adjust bounding box dimensions with extracted source tensor region.
    TransformDimensions(entry, region);

    // Discard results with out of region coordinates.
    if ((entry.top > 1.0)   || (entry.left > 1.0) ||
       (entry.bottom > 1.0) || (entry.right > 1.0) ||
       (entry.top < 0.0)    || (entry.left < 0.0) ||
       (entry.bottom < 0.0) || (entry.right < 0.0))
      continue;

    entry.confidence = confidence * 100.0f;

    if (class_idx < labels.size() && !labels[class_idx].name.empty()) {
      entry.name  = labels[class_idx].name;
      entry.color = labels[class_idx].color;
    }

    int32_t nms = NonMaxSuppression(entry, detections);

    // If the NMS result is -2 don't add the prediction
    if (nms == -2)
      continue;

    std::cout << "Label: " << entry.name
              << " Confidence: " << entry.confidence
              << " Box[" << entry.top
              << ", " << entry.left
              << ", " << entry.bottom
              << ", " << entry.right << "]"
              << std::endl;

    // If the NMS result is above -1 remove the existing entry
    if (nms >= 0)
      detections.erase(detections.begin() + nms);

    detections.emplace_back(std::move(entry));
  }

  std::cout << "[external-postprocess][detection] called: "
            << "tensors=" << frame.tensors.size()
            << ", params=" << mlparams.fields.size()
            << ", detections(out)=" << detections.size()
            << ", decode_ok=true";
  if (!detections.empty()) {
    std::cout << ", top1='" << detections.front().name
              << "'@" << detections.front().confidence;
  }
  std::cout << std::endl;

  return true;
}

std::string expand_home_path() {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return std::string(home);
}

const std::string HOME_PATH = expand_home_path();

//  Example pipeline:
//
//    filesrc → qtdemux → h264parse → v4l2h264dec
//            → [vf:NV12] → tee name=split
//      split. → qtimetamux
//      split. → q1 → qtimlvconverter → q2 → qtimltflite → q3
//             → qtimlpostprocess(custom callback) → [mlf:text] → qtimetamux → q4 → qtivoverlay → waylandsink
//
//  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
//  runs YOLOv8 object detection with external postprocessing callback logic,
//  overlays detected objects, and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/media/video.mp4");

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

  // Queues data between pipeline stages.

  // Prepares the H.264 bitstream for the decoder.
  Element parse("h264parse", "parse");

  // Decodes the compressed H.264 stream into raw video frames.
  //
  // The I/O mode is configured to enforce DMA buffer usage,
  // avoiding unnecessary buffer copies.
  Element decoder("v4l2h264dec", "decoder");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Splits decoded frames into display and ML branches.
  Element split("tee", "split");

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Queues frames from tee into the ML branch.
  // Runs the downstream element in a separate thread for improved performance.
  Element q1("queue", "q1");

  // Converts raw video frames into model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Executes the ML model and attaches tensor outputs to each frame.
  //
  // Configures the model and the hardware delegate used for execution.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  inferencing.set("model", HOME_PATH + "/models/yolov8_det_quantized.tflite");

  // Queues tensors between inference and postprocessing.
  Element q3("queue", "q3");

  // Queues data between pipeline stages.
  Element q4("queue", "q4");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=true keeps rendering synchronized to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  // Read Labels used by the external postprocess callback for class decoding.
  static const std::vector<LabelEntry> labels =
      load_labels(HOME_PATH + "/labels/yolov8.json");

  // ML postprocessing element.
  MLPostprocess postprocessing("postprocessing");

  // ML postprocessing lambda function implementation.
  // Attach the callback for external postprocessing.
  postprocessing.set_handler(
      [](const MLFrame& frame, const MLParam& params,
         MLDetections& detections) {
        return decode_detection(frame, detections, params, labels,
                                /*confidence_threshold=*/0.70f);
      });

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto vf = VideoFilter().format("NV12");
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied
  Pipeline pipeline("ml-external-detection");

  pipeline.add(src)
          .add(demux)
          .add(parse)
          .add(decoder)
          .add_stream_filter("vf", vf)
          .add(split)
          .add(q1)
          .add(preprocessing)
          .add(q2)
          .add(inferencing)
          .add(q3)
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(q4)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "vf", "split")
          .link("split", "mlmuxer")
          .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf", "mlmuxer", "q4", "overlay", "display");

  pipeline.execute();
}

int main() {
  // Route GStreamer logs through IMSDK logger and enable debug output.
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
