/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>
#include <iostream>
#include <string>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

inline uint8_t clamp_u8(int value) {
  if (value < 0) return 0;
  if (value > 255) return 255;
  return static_cast<uint8_t>(value);
}

// BT.601 limited-range YUV to full-range RGB, in fixed point (8-bit shift).
inline void nv12_to_rgb(uint8_t y, uint8_t u, uint8_t v,
                        uint8_t& r, uint8_t& g, uint8_t& b) {
  const int c = static_cast<int>(y) - 16;
  const int d = static_cast<int>(u) - 128;
  const int e = static_cast<int>(v) - 128;

  const int c_scaled = std::max(0, c) * 298;
  r = clamp_u8((c_scaled + 409 * e + 128) >> 8);
  g = clamp_u8((c_scaled - 100 * d - 208 * e + 128) >> 8);
  b = clamp_u8((c_scaled + 516 * d + 128) >> 8);
}


// Converts one NV12 blit into the model's [1, H, W, 3] int8 input tensor.
//
// yolov8_det_quantized.tflite uses asymmetric int8 quantization with a
// zero-point of 128, so each [0, 255] RGB sample is rebased by -128 into
// [-128, 127] rather than normalized to a float range.
//
// Writes in place into `tensor.data`, which points directly at the preprocess
// element's output GstMLFrame block. The SDK re-validates the tensor's data
// pointer and size after the callback returns, so the block must be filled
// in place - repointing tensor.data at another buffer makes the frame fail.
//
// Scaling is nearest-neighbor, chosen to keep the example dependency-free.
// A model trained with bilinear or letterbox preprocessing will lose some
// accuracy here; match the model's training-time resize for best results.
//
// Returns false and logs the offending contract if the input image or output
// tensor does not match what this conversion supports.
bool convert_nv12_to_nhwc_i8(const MLVideoImage& image,
                             const MLVideoBlit* blit,
                             MLTensor& tensor) {
  if (tensor.type != MLTensorType::Int8 || tensor.data == nullptr ||
      tensor.dimensions.size() != 4 || tensor.dimensions[0] != 1 ||
      tensor.dimensions[3] != 3 || image.format != "NV12" ||
      image.planes.size() < 2 || image.width == 0 || image.height == 0) {
    std::cout << "preprocess failed: invalid input/output contract"
              << " | tensor.type=" << static_cast<int>(tensor.type)
              << "(expect Int8)"
              << " | tensor.data=" << tensor.data
              << " | tensor.dims.size=" << tensor.dimensions.size();
    if (!tensor.dimensions.empty()) {
      std::cout << " | dims=";
      for (size_t i = 0; i < tensor.dimensions.size(); ++i) {
        std::cout << tensor.dimensions[i]
                  << (i + 1 < tensor.dimensions.size() ? "x" : "");
      }
    }
    std::cout << " | image.format=" << image.format
              << " | image.planes=" << image.planes.size()
              << " | image.wh=" << image.width << "x" << image.height
              << std::endl;
    return false;
  }

  const uint32_t out_h = tensor.dimensions[1];
  const uint32_t out_w = tensor.dimensions[2];
  if (out_h == 0 || out_w == 0) {
    std::cout << "preprocess failed: zero output size"
              << " | out_wh=" << out_w << "x" << out_h << std::endl;
    return false;
  }

  // tensor.size is a byte count. For this int8 tensor one element is one byte,
  // so the element count doubles as the required byte count.
  const size_t needed = static_cast<size_t>(out_h) * out_w * 3;
  if (tensor.size < needed) {
    std::cout << "preprocess failed: output tensor too small"
              << " | tensor.size=" << tensor.size
              << " | needed=" << needed << std::endl;
    return false;
  }

  const auto* y_plane = static_cast<const uint8_t*>(image.planes[0].data);
  const auto* uv_plane = static_cast<const uint8_t*>(image.planes[1].data);
  const int y_stride = image.planes[0].stride;
  const int uv_stride = image.planes[1].stride;
  if (!y_plane || !uv_plane || y_stride <= 0 || uv_stride <= 0) {
    std::cout << "preprocess failed: invalid NV12 planes"
              << " | y_plane=" << static_cast<const void*>(y_plane)
              << " | uv_plane=" << static_cast<const void*>(uv_plane)
              << " | y_stride=" << y_stride
              << " | uv_stride=" << uv_stride << std::endl;
    return false;
  }

  // Letterbox padding: any destination area the blit does not cover stays at
  // the quantized zero-point (-128), which is RGB 0 for this model.
  auto* dst_i8 = static_cast<int8_t*>(tensor.data);
  std::fill(dst_i8, dst_i8 + needed, static_cast<int8_t>(-128));

  int dst_x = 0;
  int dst_y = 0;
  int dst_w = static_cast<int>(out_w);
  int dst_h = static_cast<int>(out_h);

  if (blit) {
    dst_x = std::max(0, blit->destination.x);
    dst_y = std::max(0, blit->destination.y);
    dst_w = std::max(1, std::min<int>(blit->destination.w, static_cast<int>(out_w) - dst_x));
    dst_h = std::max(1, std::min<int>(blit->destination.h, static_cast<int>(out_h) - dst_y));
  }

  const int src_w = static_cast<int>(image.width);
  const int src_h = static_cast<int>(image.height);

  for (int y = 0; y < dst_h; ++y) {
    const int out_y = dst_y + y;
    const int src_y = std::min(src_h - 1, (y * src_h) / dst_h);

    for (int x = 0; x < dst_w; ++x) {
      const int out_x = dst_x + x;
      const int src_x = std::min(src_w - 1, (x * src_w) / dst_w);

      const uint8_t yv = y_plane[src_y * y_stride + src_x];
      // NV12 interleaves U and V at half resolution, so a chroma pair starts
      // at an even column. On an odd-width frame the last column's pair would
      // read one byte past the visible row, so clamp the V index.
      const int uv_y = src_y / 2;
      const int uv_x = (src_x / 2) * 2;
      const uint8_t u = uv_plane[uv_y * uv_stride + uv_x];
      const uint8_t v =
          uv_plane[uv_y * uv_stride + std::min(uv_x + 1, src_w - 1)];

      uint8_t r = 0;
      uint8_t g = 0;
      uint8_t b = 0;
      nv12_to_rgb(yv, u, v, r, g, b);

      const size_t out_idx = (static_cast<size_t>(out_y) * out_w + out_x) * 3;
      dst_i8[out_idx + 0] = static_cast<int8_t>(static_cast<int>(r) - 128);
      dst_i8[out_idx + 1] = static_cast<int8_t>(static_cast<int>(g) - 128);
      dst_i8[out_idx + 2] = static_cast<int8_t>(static_cast<int>(b) - 128);
    }
  }

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
//      split. → qtimetamux → qtivoverlay → waylandsink
//      split. → queue → qtimlvconverter(custom callback)
//             → queue → qtimltflite → qtimlpostprocess
//             → [mlf:text] → qtimetamux
//
//  The pipeline reads an MP4/H.264 file, decodes it through the hardware decoder,
//  runs YOLOv8 object detection with external preprocessing callback logic,
//  overlays detected objects, and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Reads the input media file as raw bytes.
  Element src("filesrc", "src");
  src.set("location", HOME_PATH + "/media/video.mp4");

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

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer("qtimetamux", "mlmuxer");

  // Queues frames from tee into the ML branch.
  // Runs the downstream element in a separate thread for improved performance.
  Element q1("queue", "q1");

  // Converts raw video frames into model input tensor format.
  //
  // engine=none disables the internal preprocessing path in qtimlvconverter,
  // handing the conversion to the callback registered below. Unlike the Python
  // binding, the C++ set_handler() does not set this implicitly.
  MLVConverter preprocessing("preprocessing");
  preprocessing.set("engine", "none");
  // ML preprocessing lambda function implementation.
  // Attach the callback for external preprocessing.
  preprocessing.set_handler(
      [](const MLVideoBlits& blits, MLFrame& output) {
        std::cout << "[external-preprocess][detection] called: "
                  << "blits=" << blits.entries.size()
                  << ", tensors=" << output.tensors.size() << std::endl;

        if (blits.entries.empty() || output.tensors.empty()) {
          std::cout << "preprocess failed: empty blits or tensors"
                    << " | blits=" << blits.entries.size()
                    << " | tensors=" << output.tensors.size() << std::endl;
          return false;
        }

        // Only the first blit is converted; this example assumes the
        // single-frame batching that qtimlvconverter uses by default.
        const MLVideoBlit& first_blit = blits.entries.front();
        const MLVideoImage& image = first_blit.image;
        if (image.planes.empty()) {
          std::cout << "preprocess failed: image has no planes"
                    << " | format=" << image.format
                    << " | wh=" << image.width << "x" << image.height << std::endl;
          return false;
        }

        const bool ok = convert_nv12_to_nhwc_i8(
            image, &first_blit, output.tensors.front());
        if (!ok) {
          std::cout << "preprocess failed: conversion routine failed" << std::endl;
        }
        return ok;
      });

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Executes the ML model and attaches tensor outputs to each frame.
  //
  // Configures the model and the hardware delegate used for execution.
  Element inferencing("qtimltflite", "inferencing");
  inferencing.set("delegate", "external");
  inferencing.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  inferencing.set("external-delegate-options",
                  "QNNExternalDelegate,backend_type=htp;");
  inferencing.set("model", HOME_PATH + "/models/yolov8_det_quantized.tflite");

  // Queues tensor outputs before postprocessing.
  Element q4("queue", "q4");

  // Postprocesses model outputs into detection metadata.
  Element postprocessing("qtimlpostprocess", "postprocessing");
  postprocessing.set("module", "yolov8");
  postprocessing.set("labels", HOME_PATH + "/labels/yolov8.json");

  // Queues data between pipeline stages.
  Element q5("queue", "q5");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto vf = VideoFilter().format("NV12");
  auto mlf = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied to branch the tee into the display and ML
  // metadata branches, and to merge them back at the metamux.
  Pipeline pipeline("ml-external-preprocess-detection");
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
          .add(q4)
          .add(postprocessing)
          .add_stream_filter("mlf", mlf)
          .add(mlmuxer)
          .add(q5)
          .add(overlay)
          .add(display)
          .link("src", "demux", "parse", "decoder", "vf", "split")
          .link("split", "mlmuxer")
          .link("split", "q1", "preprocessing", "q2", "inferencing",
                "q4", "postprocessing", "mlf", "mlmuxer", "q5", "overlay",
                "display");

  pipeline.execute();
}

}  // namespace

int main() {
  // Route GStreamer logs through IMSDK logger and enable debug output.
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
