/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

// qimsdk-cpp-batched-multistream-yolov8-grid
//
// 12-stream batched YOLOv8 object detection AI wall.
// One MP4 file (/etc/mahendra/office.mp4) is replicated into 12 independent
// decode branches. Streams are grouped 4-at-a-time (3 groups of 4, matching
// the batch-4 compiled model) and each group shares ONE qtimltflite HTP/NPU
// inference instance via qtibatch (mux 4 streams in) / qtimldemux (split
// back to 4 per-stream results) -- this avoids instantiating 12 independent
// HTP graphs (which exhausts HTP memory, error 6020). Every stream still
// gets its own qtimlpostprocess and its own pair of qtivcomposer sink pads
// (raw passthrough + detection-mask), composited into a 4x3 grid on a single
// Wayland display.
//
// Two device-verified requirements this file encodes (both were real bugs
// in an earlier draft):
//   1. The per-group qtimldemux MUST NOT reuse the "demux_" name prefix that
//      the per-stream qtdemux elements use -- "demux_<stream 0..11>" and a
//      group's "demux_<0..2>" collide, and gst_bin_add() fails on a
//      duplicate element name ("Failed to add external element"). This file
//      names the batch-group demux "mldemux_<g>".
//   2. A tee has multiple request src pads; a single chained pipeline.link()
//      call only takes ONE path off it. BOTH tee legs (passthrough -> qpass,
//      AI -> qai) must be linked explicitly, or the passthrough leg is left
//      dangling (NOT_LINKED: composer gets no video, preroll never
//      completes -> hang / nondeterministic crash).
//
// Construction is split into three phases so qtivcomposer sink-pad CREATION
// ORDER matches the interleaved (2*i, 2*i+1) indexing scheme: pad
// "position"/"dimensions" are addressed via Port::input(id), where id is the
// pad's request-creation order. Building every stream's decode/tee first,
// then every group's shared inference, then linking ALL composer pads in a
// final interleaved pass guarantees pads are created in order 0,1,...,23 ==
// (pass_0, mask_0, pass_1, mask_1, ..., pass_11, mask_11).

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <sys/resource.h>
#include <unistd.h>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// --- Fixed configuration ---
constexpr int kNumStreams = 12;
constexpr int kBatchGroupSize = 4;  // model is compiled for batch depth 4
constexpr int kNumGroups = kNumStreams / kBatchGroupSize;  // 3 groups of 4

// Media/model/label paths are resolved under $HOME at startup. C++ string
// literals do NOT expand shell variables, so a literal "$HOME/..." would be
// passed byte-for-byte to open() and fail; resolve HOME in code with an
// explicit unset check. Replace these relative paths with the actual
// on-device locations if they differ.
std::string home_path() {
  const char *home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return home;
}

std::string input_file()  { return home_path() + "/Downloads/qimsdk_samples/media/office.mp4"; }
std::string model_path()  { return home_path() + "/Downloads/qimsdk_samples/models/yolov8_det_quantized_batch_4.tflite"; }
std::string labels_path() { return home_path() + "/Downloads/qimsdk_samples/labels/yolov8.json"; }

// Grid layout: 4 columns x 3 rows (one tile per stream).
constexpr int kGridCols = 4;
constexpr int kGridRows = 3;
constexpr int kCanvasWidth = 1920;
constexpr int kCanvasHeight = 810;
constexpr int kTileWidth = kCanvasWidth / kGridCols;    // 480
constexpr int kTileHeight = kCanvasHeight / kGridRows;  // 270

// Raise the open-file-descriptor limit before constructing the pipeline.
// 12 independent file-decode branches each open several fds (filesrc,
// v4l2 decoder instances, DMA buffers); the default per-process limit can be
// exhausted at this stream count. If this cannot raise the limit (e.g.
// insufficient privilege), run 'ulimit -n 10000' in the launching shell
// before starting this app -- see README "Steps to Run".
void raise_fd_limit() {
  struct rlimit rl{10000, 10000};
  if (setrlimit(RLIMIT_NOFILE, &rl) != 0) {
    std::cerr << "Warning: failed to raise fd limit programmatically; "
                 "run 'ulimit -n 10000' before launching this app.\n";
  }
}

// Detect a second HTP/NPU device (dual-HTP SKUs expose a second cdsp fastrpc
// node) so batch inference groups can round-robin across available HTP
// devices instead of contending a single HTP graph slot.
int detect_htp_count() {
  return (access("/dev/fastrpc-cdsp1", F_OK) == 0) ? 2 : 1;
}

// Phase 1: builds the per-stream source/decode/tee chain for stream `idx`.
// Leaves the AI leg (queued "qai_<idx>") ready for Phase 2 to link into its
// batch group, and the passthrough leg (queued "qpass_<idx>") ready for
// Phase 3 to link into the composer.
void build_stream_source(Pipeline &pipeline, int idx) {
  const std::string sfx = std::to_string(idx);

  // Reads the input media file as raw bytes. Same file replicated across
  // all 12 streams per the request (single source, fanned out).
  Element source("filesrc", "src_" + sfx);
  source.set("location", input_file());

  // Extracts elementary streams from the MP4 container. Dynamic pad-added
  // linking is handled internally by the SDK.
  Element demux("qtdemux", "demux_" + sfx);

  // Prepares the H.264 bitstream for the decoder.
  Element parse("h264parse", "parse_" + sfx);

  // Decodes the compressed H.264 stream into raw video frames using DMA
  // buffers on both sides (file-source decode: driver manages both ends).
  Element decoder("v4l2h264dec", "dec_" + sfx);
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  // Queue immediately after the hardware decoder, before any filter/tee.
  Element q_dec("queue", "qdec_" + sfx);

  pipeline.add(source).add(demux).add(parse).add(decoder).add(q_dec);
  pipeline.add_stream_filter("vf_" + sfx, VideoFilter().format("NV12"));

  // Splits the normalized video into a passthrough (raw video) branch and
  // an AI (batched inference) branch.
  Element split("tee", "split_" + sfx);
  Element q_pass("queue", "qpass_" + sfx);
  Element q_ai("queue", "qai_" + sfx);
  pipeline.add(split).add(q_pass).add(q_ai);

  pipeline.link("src_" + sfx, "demux_" + sfx, "parse_" + sfx, "dec_" + sfx,
                "qdec_" + sfx, "vf_" + sfx, "split_" + sfx);

  // REQUIREMENT #2 (see file header): link BOTH tee legs explicitly. A tee
  // has multiple request src pads; a single chained link() only takes one.
  // Omitting the passthrough link leaves qpass_<idx> NOT_LINKED -> no video
  // to the composer + preroll hang.
  pipeline.link("split_" + sfx, "qpass_" + sfx);
  pipeline.link("split_" + sfx, "qai_" + sfx);
}

// Phase 2: builds one shared-inference batch group covering streams
// [group_idx * kBatchGroupSize, group_idx * kBatchGroupSize + kBatchGroupSize).
//
// qtibatch stacks one buffer from each of its linked sink_%u pads (in link
// order) into a single batched buffer and tags each stacked frame with a
// stream-id equal to that pad's link-order position. qtimldemux reverses
// this: its request src pads are auto-numbered in link order, and routes
// each batched channel to the output whose position matches the stream-id
// tag. So the k-th stream linked into qtibatch here MUST come out of the
// k-th output linked out of qtimldemux -- the per-member loop below
// preserves that by using the same loop index `k` for both.
void build_batch_group(Pipeline &pipeline, int group_idx, int htp_device_id) {
  const std::string gfx = std::to_string(group_idx);

  // Aggregates one buffer from each of this group's 4 member streams into a
  // single batch-4 buffer. Batch depth comes from linked sink pad count, not
  // a batch-size property.
  Element batch("qtibatch", "batch_" + gfx);
  Element q_batch("queue", "qbatch_" + gfx);

  // Converts the batched NV12 buffer into normalized tensors; writes
  // views=4 into caps based on the batch depth above.
  Element converter("qtimlvconverter", "mlv_" + gfx);
  Element q_mlv("queue", "qmlv_" + gfx);

  // Shared TFLite HTP/NPU inference for this 4-stream group -- one HTP
  // graph serves all 4 member streams. htp_device_id round-robins groups
  // across available HTP devices (see detect_htp_count()); performance mode
  // 2 keeps throughput up under concurrent multi-group HTP load.
  Element infer("qtimltflite", "infer_" + gfx);
  infer.set("model", model_path());
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,htp_device_id=(string)" +
                std::to_string(htp_device_id) +
                ",htp_performance_mode=(string)2,log_level=(string)1;");

  Element q_infer("queue", "qinfer_" + gfx);

  // REQUIREMENT #1 (see file header): named "mldemux_" (not "demux_") to
  // avoid a pipeline-wide element name collision with the per-stream qtdemux
  // elements named "demux_0".."demux_11".
  Element demux("qtimldemux", "mldemux_" + gfx);

  pipeline.add(batch)
      .add(q_batch)
      .add(converter)
      .add(q_mlv)
      .add(infer)
      .add(q_infer)
      .add(demux);

  pipeline.link("batch_" + gfx, "qbatch_" + gfx, "mlv_" + gfx, "qmlv_" + gfx,
                "infer_" + gfx);
  pipeline.link("infer_" + gfx, "qinfer_" + gfx, "mldemux_" + gfx);

  for (int k = 0; k < kBatchGroupSize; ++k) {
    const int stream_idx = group_idx * kBatchGroupSize + k;
    const std::string sfx = std::to_string(stream_idx);

    // Link this stream's already-queued AI leg (split_<idx> -> qai_<idx> was
    // linked in build_stream_source()) into the batch group's k-th request
    // sink pad.
    pipeline.link("qai_" + sfx, "batch_" + gfx);

    Element q_post("queue", "qpost_" + sfx);

    // Decodes this stream's tensor output into bounding boxes + class
    // labels. Confidence threshold per request: 51.0.
    Element post("qtimlpostprocess", "post_" + sfx);
    post.set("module", "yolov8");
    post.set("labels", labels_path());
    post.set("settings", "{\"confidence\": 51.0}");

    Element q_mask("queue", "qmask_" + sfx);

    pipeline.add(q_post).add(post).add(q_mask);
    // Rendered detection mask: RGBA per qtimlpostprocess device-verified src
    // caps (BGRA fails to link). No pinned resolution -- the composer
    // sink-pad dimensions (set in Phase 3) scale the mask into its tile.
    pipeline.add_stream_filter("render_vf_" + sfx,
                                VideoFilter().format("RGBA"));

    // k-th qtimldemux request src pad -- same order as the k-th qtibatch
    // link above, so this stream's own detections land on its own tile.
    // Stops at "qmask_<sfx>"; not yet linked to the composer (Phase 3).
    pipeline.link("mldemux_" + gfx, "qpost_" + sfx, "post_" + sfx,
                  "render_vf_" + sfx, "qmask_" + sfx);
  }
}

// Phase 3: links each stream's passthrough pad immediately followed by its
// own detection-mask pad into the composer, stream by stream. This is the
// step that actually creates qtivcomposer's request sink pads, in order
// 0,1,2,...,23 -- i.e. (pass_0, mask_0, pass_1, mask_1, ..., pass_11,
// mask_11). Linking passthrough before mask for a given stream is also what
// makes the mask paint on top of the video by default creation-order
// z-order, with no explicit "zorder" property needed.
void link_composer_pads(Pipeline &pipeline) {
  for (int i = 0; i < kNumStreams; ++i) {
    const std::string sfx = std::to_string(i);
    const int col = i % kGridCols;
    const int row = i / kGridCols;
    const std::vector<int> position{col * kTileWidth, row * kTileHeight};
    const std::vector<int> dimensions{kTileWidth, kTileHeight};

    // Passthrough pad: creation index 2*i.
    pipeline.link("qpass_" + sfx, "composer");
    pipeline.get("composer")
        .input(2 * i)
        .set("position", position)
        .set("dimensions", dimensions);

    // Detection-mask pad: creation index 2*i + 1, same tile as passthrough.
    pipeline.link("qmask_" + sfx, "composer");
    pipeline.get("composer")
        .input(2 * i + 1)
        .set("position", position)
        .set("dimensions", dimensions);
  }
}

void create_and_execute_pipeline() {
  Pipeline pipeline("qimsdk-cpp-batched-multistream-yolov8-grid");

  // Composer must be added before any phase links into its "composer" sink
  // pads.
  Element composer("qtivcomposer", "composer");
  pipeline.add(composer);

  // Phase 1: every stream's independent source/decode/tee.
  for (int i = 0; i < kNumStreams; ++i) {
    build_stream_source(pipeline, i);
  }

  // Phase 2: every batch group's shared inference + per-stream postprocess.
  const int htp_count = detect_htp_count();
  for (int g = 0; g < kNumGroups; ++g) {
    build_batch_group(pipeline, g, g % htp_count);
  }

  // Phase 3: interleaved composer pad linking (creation order == pad index).
  link_composer_pads(pipeline);

  // Large batched multistream composer grid -> sync=false (avoids clock
  // stalls; each group's HTP graph can take ~10s to prepare before frames
  // appear -- do not cap the run short).
  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", false);

  pipeline.add(display);
  pipeline.link("composer", "display");

  pipeline.execute();
}

}  // namespace

int main() {
  // Route GStreamer logs through the IMSDK logger and enable debug output.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  raise_fd_limit();

  try {
    create_and_execute_pipeline();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
