/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <qti/qimsdk.h>

#include "qimsdk-test-utils.h"
#include <getopt.h>

using namespace qti;

namespace {
struct RootPoint {
  uint32_t id;
  float    x;
  float    y;
  float    confidence;

  RootPoint()
      : id(0), x(0), y(0), confidence(0.0) {};

  RootPoint(uint32_t id, float x, float y, float confidence)
      : id(id), x(x), y(y), confidence(confidence) {};
};

struct KeypointLinkIds {
  uint32_t s_kp_id;
  uint32_t d_kp_id;

  KeypointLinkIds()
      : s_kp_id(0), d_kp_id(0) {};

  KeypointLinkIds(uint32_t s_kp_id, uint32_t d_kp_id)
      : s_kp_id(s_kp_id), d_kp_id(d_kp_id) {};
};

int32_t NonMaxSuppression(MLPose &l_entry,
                          MLPoses &entries) {
  uint32_t n_overlaps = 0;
  uint32_t n_keypoints = l_entry.keypoints.size();
  const float kNMSTresholdRadius = 20.0f;

  // The threhold distance between 2 keypoints.
  double threshold = kNMSTresholdRadius * kNMSTresholdRadius;

  for (uint32_t idx = 0; idx < entries.size();  idx++, n_overlaps = 0) {
    MLPose& r_entry = entries[idx];

    // Find out how much overlap is between the keypoints of the predictons.
    for (uint32_t num = 0; num < n_keypoints; num++) {
      MLKeypoint& l_kp = l_entry.keypoints[num];
      MLKeypoint& r_kp = r_entry.keypoints[num];

      double distance = pow(l_kp.x - r_kp.x, 2) + pow(l_kp.y - r_kp.y, 2);

      // If the distance is below the threshold, increase the overlap score.
      if (distance <= threshold)
        n_overlaps += 1;
    }

    // If only half of the keypoints overlap then it's probably another pose.
    if (n_overlaps < (n_keypoints / 2))
      continue;

    // If confidence of current prediction is higher, remove the old entry.
    if (l_entry.confidence > r_entry.confidence)
      return idx;

    // If confidence of current prediction is lower, don't add it to the list.
    if (l_entry.confidence <= r_entry.confidence)
      return -2;
  }

  // If this point is reached then add current prediction to the list;
  return -1;
}

bool MlCompareRootpoints(RootPoint& a, RootPoint& b) {
  return a.confidence > b.confidence;
}

void ExtractRootpoints(const MLFrame& frame,
                       std::vector<RootPoint>& rootpoints,
                       float source_width,
                       float source_height,
                       float confidence_threshold) {

  bool is_five_tensor = (frame.tensors.size() == 5);

  // Radius in which to search for highest root keypoint of given type.
  const int32_t kLocalMaximumRadius = 1;

  int32_t n_keypoints, n_rows, n_columns;
  if (is_five_tensor) {
    n_keypoints = frame.tensors[0].dimensions[1];
    n_rows = frame.tensors[0].dimensions[2];
    n_columns = frame.tensors[0].dimensions[3];
  } else {
    n_rows = frame.tensors[0].dimensions[1];
    n_columns = frame.tensors[0].dimensions[2];
    n_keypoints = frame.tensors[0].dimensions[3];
  }

  // Convenient pointer to the keypoints heatmap inside the 1st tensor.
  const float *heatmap = static_cast<const float*>(frame.tensors[0].data);
  // Pointer to the keypoints coordinate offsets inside the 2nd tensor.
  const float *offsets = static_cast<const float*>(frame.tensors[1].data);

  // The width(position 0) and height(position 1) of the paxel block.
  uint32_t paxelsize[2] = {0, 0};
  paxelsize[0] = (source_width - 1) / (n_columns - 1);
  paxelsize[1] = (source_height - 1) / (n_rows - 1);

  // Confidence threshold represented as the exponent of sigmoid.
  float threshold = log(confidence_threshold / (1 - confidence_threshold));

  // Iterate the heatmap and find the keypoint with highest score for each block.
  for (int32_t kp_idx = 0; kp_idx < n_keypoints; kp_idx++) {
    for (int32_t row = 0; row < n_rows; row++) {
      for (int32_t column = 0; column < n_columns; column++) {
        RootPoint rootpoint;
        uint32_t x = 0, y = 0, xmin = 0, xmax = 0, ymin = 0, ymax = 0;
        float score = FLT_MIN;

        uint32_t idx;
        if (is_five_tensor)
          idx = (((kp_idx * n_rows) + row) * n_columns) + column;
        else
          idx = (((row * n_columns) + column) * n_keypoints) + kp_idx;

        // Extract the keypoint heatmap confidence.
        float confidence = heatmap[idx];

        // Discard results below the minimum confidence threshold.
        if (confidence < threshold)
          continue;

        // Calculate the X and Y range of the local window.
        ymin = std::max(row - kLocalMaximumRadius, 0);
        ymax = std::min(row + kLocalMaximumRadius + 1, n_rows);

        xmin = std::max(column - kLocalMaximumRadius, 0);
        xmax = std::min(column + kLocalMaximumRadius + 1, n_columns);

        // Check if this root point is the maximum in the local window.
        for (y = ymin; y < ymax; y++) {
          for (x = xmin; x < xmax; x++) {
            if (is_five_tensor)
              idx = (((kp_idx * n_rows) + y) * n_columns) + x;
            else
              idx = (((y * n_columns) + x) * n_keypoints) + kp_idx;

            float current_score = heatmap[idx];
            if (current_score > score)
              score = current_score;
          }
        }

        // Dicard keypoint if it is not the maximum in the local window.
        if (confidence < score)
          continue;

        // Apply a sigmoid function in order to normalize the heatmap confidence.
        confidence = 1.0 / (1.0 + expf(- confidence));

        rootpoint.id = kp_idx;
        rootpoint.confidence = confidence * 100.0;

        rootpoint.x = column * paxelsize[0];
        rootpoint.y = row * paxelsize[1];

        // Calculate offset indices based on tensor format
        if (is_five_tensor) {
          rootpoint.y += offsets[idx];
          idx = ((((kp_idx + n_keypoints) * n_rows) + row) * n_columns) + column;
          rootpoint.x += offsets[idx];
        } else {
          idx = (((row * n_columns) + column) * n_keypoints * 2) + kp_idx;
          // Dequantize the keypoint Y axis offset and add it to the end coordinate.
          rootpoint.y += offsets[idx];
          // Dequantize the keypoint X axis offset and add it to the end coordinate.
          rootpoint.x += offsets[idx + n_keypoints];
        }

        std::cout << "Root Keypoint " << rootpoint.id
                  << " [" << rootpoint.x
                  << " x " << rootpoint.y
                  << "], confidence: " << rootpoint.confidence
                  <<std::endl;

        rootpoints.emplace_back(std::move(rootpoint));
      }
    }
  }

  // Sort the hough keypoint scores map by the their confidences.
  std::sort(rootpoints.begin(), rootpoints.end(),
      MlCompareRootpoints);
}

void TraverseSkeletonLinks(const MLFrame& frame,
                           MLPose &entry,
                           const std::vector<LabelEntry>& labels,
                           float source_width,
                           float source_height,
                           bool backwards) {

  bool is_five_tensor = (frame.tensors.size() == 5);
  const uint32_t kNumRefinementSteps = 2;
  uint32_t num = 0;
  int32_t n_keypoints, n_rows, n_columns, n_edges;

  std::vector<KeypointLinkIds> links_ids = {
    {0, 1}, {0, 2}, {0, 5}, {0, 6},
    {1, 3},
    {2, 4},
    {5, 7}, {5, 11},
    {6, 8}, {6, 12},
    {7, 9},
    {8, 10},
    {11, 13},
    {12, 14},
    {13, 15},
    {14, 16}
  };

  if (is_five_tensor) {
    n_keypoints = frame.tensors[0].dimensions[1];
    n_rows = frame.tensors[0].dimensions[2];
    n_columns = frame.tensors[0].dimensions[3];
    n_edges = frame.tensors[2].dimensions[1] / 2;
  } else {
    n_rows = frame.tensors[0].dimensions[1];
    n_columns = frame.tensors[0].dimensions[2];
    n_keypoints = frame.tensors[0].dimensions[3];
    // Division by 4 due to X and Y coordinates and backwards and forward values.
    n_edges = frame.tensors[2].dimensions[3] / 4;
  }

  // Pointer to the keypoints heatmap inside the 1st tensor.
  const float *heatmap = static_cast<const float*>(frame.tensors[0].data);
  // Pointer to the keypoints coordinate offsets inside the 2nd tensor.
  const float *offsets = static_cast<const float*>(frame.tensors[1].data);
  // Pointer to the displacement data
  const float *displacements;
  if (is_five_tensor)
    displacements = backwards ?
        static_cast<const float*>(frame.tensors[3].data) :
        static_cast<const float*>(frame.tensors[2].data);
  else
    displacements = static_cast<const float*>(frame.tensors[2].data);

  // The width(position 0) and height(position 1) of the paxel block.
  uint32_t paxelsize[2] = {0, 0};
  paxelsize[0] = (source_width - 1) / (n_columns - 1);
  paxelsize[1] = (source_height - 1) / (n_rows - 1);

  int32_t base = backwards ?(n_edges - 1) : 0;

  for (int32_t edge = 0; edge < n_edges; edge++, num = 0) {
    uint32_t id = std::abs(base - edge);

    KeypointLinkIds& link = links_ids[id];

    uint32_t s_kp_id = backwards ? link.d_kp_id : link.s_kp_id;
    uint32_t d_kp_id = backwards ? link.s_kp_id : link.d_kp_id;

    MLKeypoint& s_kp = entry.keypoints[s_kp_id];
    MLKeypoint& d_kp = entry.keypoints[d_kp_id];

    // Skip if source is not present or destination is already populated.
    if ((s_kp.confidence == 0.0) || (d_kp.confidence != 0.0))
      continue;

    // Calculate original X & Y axis values in the matrix coordinate system.
    uint32_t row = std::clamp(round(s_kp.y / paxelsize[1]),
       (double)0,(double)(n_rows - 1));
    uint32_t column = std::clamp(round(s_kp.x / paxelsize[0]),
       (double)0,(double)(n_columns - 1));

    // Calculate the position of source keypoint inside the displacements tensor.
    uint32_t idx;
    if (is_five_tensor) {
      idx = (((id * n_rows) + row) * n_columns) + column;
      float displacement = displacements[idx];
      d_kp.y = s_kp.y + displacement;

      idx = ((((id + n_edges) * n_rows) + row) * n_columns) + column;
      displacement = displacements[idx];
      d_kp.x = s_kp.x + displacement;
    } else {
      idx = (((row * n_columns) + column) * (n_edges * 4)) + id;
      // For reverse iteration an additional offset by half of the edges is needed.
      idx += backwards ? (n_edges * 2) : 0;

      // Calculate the displaced Y axis value in the matrix coordinate system.
      float displacement = displacements[idx];
      d_kp.y = s_kp.y + displacement;

      // Calculate the displaced X axis value in the matrix coordinate system.
      displacement = displacements[idx + n_edges];
      d_kp.x = s_kp.x + displacement;
    }

    // Refine the destination keypoint coordinates.
    do {
      // Calculate original X & Y axis values in the matrix coordinate system.
      if (is_five_tensor) {
        row = std::clamp(round(d_kp.y / paxelsize[1]),
            (double)0, (double)(n_rows - 1));
        column = std::clamp(round(d_kp.x / paxelsize[0]),
            (double)0, (double)(n_columns - 1));

        uint32_t y_idx = (((d_kp_id * n_rows) + row) * n_columns) + column;
        uint32_t x_idx = ((((d_kp_id + n_keypoints) * n_rows) + row) *
            n_columns) + column;

        float y_offset = offsets[y_idx];
        d_kp.y = row * paxelsize[1] + y_offset;

        float x_offset = offsets[x_idx];
        d_kp.x = column * paxelsize[0] + x_offset;
      } else {
        row = std::clamp(round(d_kp.y / paxelsize[1]),
            (double)0, (double)(n_rows - 1));
        column = std::clamp(round(d_kp.x / paxelsize[0]),
            (double)0, (double)(n_columns - 1));

        // Calculate the position of target keypoint inside the offsets tensor.
        idx = (((row * n_columns) + column) * n_keypoints * 2) + d_kp_id;

        // Dequantize the keypoint Y axis offset and add it to the end coordinate.
        float offset = offsets[idx];
        d_kp.y = row * paxelsize[1] + offset;

        // Dequantize the keypoint X axis offset and add it to the end coordinate.
        offset = offsets[idx + n_keypoints];
        d_kp.x = column * paxelsize[0] + offset;
      }
    } while (++num < kNumRefinementSteps);

    // Clamp values outside the range.
    d_kp.y = std::clamp((double)d_kp.y,
       (double)0,(double)(source_height - 1));
    d_kp.x = std::clamp((double)d_kp.x,
       (double)0,(double)(source_width - 1));

    // Calculate original X & Y axis values in the matrix coordinate system.
    row = std::clamp(round(d_kp.y / paxelsize[1]),
       (double)0,(double)(n_rows - 1));
    column = std::clamp(round(d_kp.x / paxelsize[0]),
       (double)0,(double)(n_columns - 1));

    // Calculate the position of target keypoint inside the heatmap tensor.
    if (is_five_tensor)
      idx = (((d_kp_id * n_rows) + row) * n_columns) + column;
    else
      idx = (((row * n_columns) + column) * n_keypoints) + d_kp_id;

    // Extract the keypoint heatmap confidence.
    float confidence = heatmap[idx];
    // Apply a sigmoid function in order to normalize the heatmap confidence.
    confidence = 1.0 / (1.0 + expf(- confidence));

    d_kp.confidence = confidence * 100;

    // Extract info from labels and populate the coresponding keypoint params.
    if (d_kp_id < labels.size() && !labels[d_kp_id].name.empty()) {
      d_kp.name = labels[d_kp_id].name;
      d_kp.color = labels[d_kp_id].color;
    }

    std::cout << "Link[" << id
              << "]: '" << s_kp.name
              << "' [" << s_kp.x
              << " x " << s_kp.y
              << "], " << s_kp.confidence
              << " <---> '" << d_kp.name
              << "' [" << d_kp.x
              << " x " << d_kp.y
              << "], " << d_kp.confidence
              << std::endl;

    // Increase the overall prediction confidence with the found keypoint.
    entry.confidence += d_kp.confidence / n_keypoints;
  }
}

void KeypointTransformCoordinates(MLKeypoint& keypoint,
                                  Region& region) {
  keypoint.x = (keypoint.x - region.x) / region.width;
  keypoint.y = (keypoint.y - region.y) / region.height;
}

}  // namespace

bool decode_pose_estimation(const MLFrame& frame,
                            MLPoses& estimations,
                            const MLParam& mlparams,
                            const std::vector<LabelEntry>& labels,
                            float confidence_threshold) {
  estimations.clear();

  if (frame.tensors.size() != 5 && frame.tensors.size() != 3) {
    throw std::runtime_error("ML frame with unsupported post-processing procedure");
  }

  float source_width = 0.0f, source_height = 0.0f;

  std::vector<KeypointLinkIds> connections = {
    {5,  6},
    {6,  12},
    {7,  5},
    {8,  6},
    {9,  7},
    {10, 8},
    {11, 5},
    {12, 11},
    {13, 11},
    {14, 12},
    {15, 13},
    {16, 14}
  };

  std::vector<RootPoint> rootpoints;

  // Get region
  Region region = {};
  if (!mlparams.get("input-tensor-width", source_width) ||
      !mlparams.get("input-tensor-height", source_height) ||
      !mlparams.get("input-tensor-region", region)) {
    std::cerr << "Required mlparams not found for pose estimation parser."
              << std::endl;
    return false;
  }

  if (source_width <= 1.0f || source_height <= 1.0f ||
      region.width == 0 || region.height == 0) {
    std::cerr << "Invalid tensor or region dimensions for pose estimation parser."
              << std::endl;
    return false;
  }

  bool is_five_tensor = (frame.tensors.size() == 5);

  // Determine number of keypoints based on tensor format
  uint32_t n_keypoints;
  if (is_five_tensor)
    n_keypoints = frame.tensors[0].dimensions[1];
  else
    n_keypoints = frame.tensors[0].dimensions[3];

  // Find the keypoints with highest score for each block inside the heatmap.
  ExtractRootpoints(frame, rootpoints, source_width, source_height, confidence_threshold);

  // Iterate over the root keypoints and build up pose predictions.
  for (uint32_t idx = 0; idx < rootpoints.size(); idx++) {
    RootPoint& rootpoint = rootpoints[idx];
    MLPose entry;

    entry.keypoints.resize(n_keypoints);

    MLKeypoint& keypoint = entry.keypoints[rootpoint.id];
    keypoint.x = rootpoint.x;
    keypoint.y = rootpoint.y;
    keypoint.confidence = rootpoint.confidence;

    if (rootpoint.id < labels.size() && !labels[rootpoint.id].name.empty()) {
      keypoint.name = labels[rootpoint.id].name;
      keypoint.color = labels[rootpoint.id].color;
    }

    entry.confidence = keypoint.confidence / n_keypoints;

    std::cout << "Seed Keypoint: '" << keypoint.name
              << "' [" << keypoint.x
              << " x " << keypoint.y
              << "], confidence: " << keypoint.confidence
              << std::endl;

    // Iterate backwards over the skeleton links to find the seed keypoint.
    TraverseSkeletonLinks(frame, entry, labels,source_width, source_height, true);
    // Iterate forward over the skeleton links to find all other keypoints.
    TraverseSkeletonLinks(frame, entry, labels,source_width, source_height, false);

    // Non-Max Suppression (NMS) algorithm.
    // If the NMS result is below 0 don't create new pose prediction.
    int32_t nms = NonMaxSuppression(entry, estimations);

    // If the NMS result is -2 don't add the prediction to the list.
    if (nms == (-2))
      continue;

    std::vector<MLKeypointLink> links;
    for (uint32_t num = 0; num < connections.size(); num++) {
      KeypointLinkIds& lk = connections[num];
      links.push_back(MLKeypointLink {
                      entry.keypoints[lk.s_kp_id],
                      entry.keypoints[lk.d_kp_id]
                    });
    }

    if (is_five_tensor)
      entry.links = std::move(links);
    else
      entry.links = links;

    // If the NMS result is above -1 remove the entry with the nms index.
    if (nms >= 0)
      estimations.erase(estimations.begin() + nms);

    estimations.push_back(entry);
  }

  // Transform coordinates to relative with extracted source aspect ratio.
  for (uint32_t idx = 0; idx < estimations.size(); idx++) {
    MLPose& entry = estimations[idx];

    for (uint32_t num = 0; num < entry.keypoints.size(); num++) {
      MLKeypoint& keypoint = entry.keypoints[num];
      KeypointTransformCoordinates(keypoint, region);
    }
  }

  std::cout << "[external-postprocess][pose_estimation] called: "
            << "tensors=" << frame.tensors.size()
            << ", params=" << mlparams.fields.size()
            << ", poses(out)=" << estimations.size()
            << ", decode_ok=true";
  if (!estimations.empty()) {
    std::cout << ", decoded='" << estimations.front().name
              << "' @" << estimations.front().confidence;
  }
  std::cout << std::endl;

  return true;
}

static const std::string home_path =
    std::getenv("HOME") ? std::getenv("HOME") : "";

// Base path for sample assets (media/, models/, labels/ live under it).
// Set from the --model-base-path argument, or the default location.
static std::string model_base_path;

// Input source location, overridden via the --input-config argument.
static std::string input_config;

//  Example pipeline:
//
//    src → demux → parse → decoder → [vf] → split → mlmuxer → q4 → overlay → display
//                                             └──→ q1 → preprocessing → q2 → inferencing → q3 → postprocessing → [mlf] → mlmuxer
//
//  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
//  runs external postprocessing callback logic and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", input_config);

  // Extracts elementary streams from the MP4 container.
  Element demux("qtdemux", "demux");

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

  // Queues frames from tee into the ML branch.
  Element q1("queue", "q1");

  // Converts raw video frames into model input tensor format.
  Element preprocessing("qtimlvconverter", "preprocessing");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Executes the ML model and attaches tensor outputs to each frame.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options", "QNNExternalDelegate,backend_type=htp;");
  inferencing.set("model", model_base_path + "/models/posenet_mobilenet_v1_075_481_641_quant.tflite");

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

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
      load_labels(model_base_path + "/labels/posenet.json");

  // ML postprocessing element.
  MLPostprocess postprocessing("postprocessing");

  // ML postprocessing lambda function implementation.
  postprocessing.set("results", 2);
  // Attach the callback for external postprocessing.
  postprocessing.set_handler(
      [](const MLFrame& frame, const MLParam& params,
         MLPoses& poses) {
        return decode_pose_estimation(frame, poses, params, labels,
                                      /*confidence_threshold=*/0.70f);
      });

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto vf = VideoFilter().format("NV12");
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied
  Pipeline pipeline("posenet_pose_estimation");

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
          .add("queue", "q3")
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(q4)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "vf", "split")
          .link("split", "mlmuxer", "q4", "overlay", "display")
          .link("split", "q1", "preprocessing", "q2", "inferencing", "q3", "postprocessing", "mlf", "mlmuxer");

  pipeline.execute();
}

int main(int argc, char **argv) {
  if (home_path.empty()) {
    std::cerr << "Error: HOME environment variable is not set." << std::endl;
    return 1;
  }

  // Base path for sample assets; override via --model-base-path argument.
  model_base_path = home_path + "/Downloads/qimsdk_samples";
  // Path for sample input assets; override via --input-config argument.
  input_config = home_path + "/Downloads/qimsdk_samples/media/pose_sample.mp4";

  const std::string default_model_base_path = model_base_path;
  const std::string default_input_config = input_config;

  static struct option long_options[] = {
    {"model-base-path", required_argument, 0, 'm'},
    {"input-config", required_argument, 0, 'i'},
    {"help", no_argument, 0, 'h'},
    {0, 0, 0, 0}
  };

  auto print_usage = [&](std::ostream &out) {
    out << "Usage: " << argv[0] << " [OPTIONS]\n"
        << "\n"
        << "Options:\n"
        << "  -m, --model-base-path PATH   Base path for models and labels\n"
        << "                                (default: " << default_model_base_path << ")\n"
        << "  -i, --input-config VALUE     Input source configuration (camera number, device, or file path)\n"
        << "                                (default: " << default_input_config << ")\n"
        << "  -h, --help                   Show this help message and exit\n";
  };

  opterr = 0;  // Suppress getopt_long's own diagnostics; print_usage covers it.

  int option_index = 0;
  int c;
  while ((c = getopt_long(argc, argv, "m:i:h", long_options, &option_index)) != -1) {
    switch (c) {
      case 'm':
        model_base_path = optarg;
        break;
      case 'i':
        input_config = optarg;
        break;
      case 'h':
        print_usage(std::cout);
        return 0;
      case '?':
      default:
        print_usage(std::cerr);
        return 1;
    }
  }

  if (optind != argc) {
    std::cerr << "Error: unexpected argument '" << argv[optind] << "'\n\n";
    print_usage(std::cerr);
    return 1;
  }

  // Route GStreamer logs through the QIMSDK logger and enable debug output.
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
