// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

// Event-triggered recording: YOLOX object detection on an MP4 file, with
// display overlay, that starts recording an annotated MP4 output only while
// a "person" is detected, and stops after a run of consecutive frames with
// no person detected.
//
// Two qti::Pipeline instances are used:
//   pipeline_main:      decode -> AI (yolox/yolov8 postproc) -> compose ->
//                        display tap + recording tap (AppSink)
//   pipeline_recording: AppSrc -> encode -> mp4mux -> filesink
//
// pipeline_recording is built once at startup and left un-started. It is
// started/stopped from the detection-metadata AppSink callback based on
// whether "person" is currently being detected.
//
// NOTE (per generation-rules.md "API-Validated Feature Routes"): the IMSDK
// C++ API has no dedicated event-triggered-recording control hook or
// structured-metadata parser. This app therefore consumes qtimlpostprocess's
// text/x-raw metadata output via a raw qti::AppSink buffer (bytes only) and
// does a best-effort substring scan for the detection label, instead of a
// full GstStructure-list deserialization (that API is not exposed by
// <qti/imsdk.h> and pulling in raw GStreamer headers would violate this
// skill's "qti/imsdk.h only" rule). See README "Assumptions and Limitations".

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>

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

const std::string kInputFile =
    expand_home("/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4");
const std::string kModelPath =
    expand_home("/Downloads/qimsdk_samples/models/yolox_w8a8.tflite");
const std::string kLabelsPath =
    expand_home("/Downloads/qimsdk_samples/labels/yolov8.json");
const std::string kOutputFile = "/tmp/event-output.mp4";

constexpr float kConfidenceThreshold = 51.0f;
constexpr const char *kDetectionLabel = "person";
constexpr int kNoDetectionFrameThreshold = 150;

// Assumed decode/record resolution — the filename ("...1080p_...") implies
// 1920x1080; update if the real source resolution differs, or the composer
// output caps and recording encoder chain below will fail to negotiate.
constexpr int kDecodedWidth = 1920;
constexpr int kDecodedHeight = 1080;
constexpr int kRecordingFramerate = 30;

// Shared mutable state driving the recording start/stop state machine.
// Accessed from the AppSink callback threads only (no concurrent producer).
struct EventState {
  bool recording_active = false;
  int no_detection_frame_count = 0;
} g_state;

qti::Pipeline *g_pipeline_recording = nullptr;

// Best-effort textual scan for the detection label inside the raw
// qtimlpostprocess text/x-raw metadata buffer. The exact serialized
// GstStructure-list format (structure names such as "ObjectDetection" /
// "bounding-boxes") is documented in plugin-catalog.md, but this SDK's C++
// API does not expose a structure deserializer — TODO: replace with a real
// parser if the target application needs exact per-box fields rather than
// "was <label> present in this frame".
bool FrameContainsLabel(const qti::Buffer &buffer, const char *label) {
  if (buffer.size() == 0) {
    return false;
  }
  const char *data = reinterpret_cast<const char *>(buffer.data());
  std::string text(data, buffer.size());
  return text.find(label) != std::string::npos;
}

}  // namespace

void CreateAndExecutePipelines() {
  // ---- Recording pipeline (built once and kept PLAYING) ----
  Pipeline pipeline_recording("event-encoder-recording");
  g_pipeline_recording = &pipeline_recording;

  AppSrc event_appsrc("event_appsrc");
  event_appsrc.set("is-live", true);
  event_appsrc.set("format", qti::AppSrc::Format::TIME);
  // Keep the recorder resident and timestamp buffers on its own clock.
  event_appsrc.set("do-timestamp", true);
  event_appsrc.set(
      "caps",
      qti::VideoFilter()
          .format("NV12")
          .resolution(kDecodedWidth, kDecodedHeight)
          .framerate(kRecordingFramerate));

  Element q_appsrc_to_enc("queue", "q_appsrc_to_enc");

  // The recorder receives copied AppSrc buffers, not camera-native DMA buffers
  // and not an AV dual-input mux. Use the file-source encoder pairing so the
  // driver manages both sides of the encoder allocation.
  Element rec_encoder("v4l2h264enc", "event_v4l2h264enc");
  rec_encoder.set("capture-io-mode", 4);
  rec_encoder.set("output-io-mode", 4);

  Element rec_parse("h264parse", "event_rec_h264parse");
  Element q_enc_to_mux("queue", "q_enc_to_mux");
  Element rec_mux("mp4mux", "event_mp4mux");

  Element rec_filesink("filesink", "event_filesink");
  rec_filesink.set("location", kOutputFile);
  rec_filesink.set("enable-last-sample", false);
  rec_filesink.set("async", false);

  pipeline_recording.add(event_appsrc)
      .add(q_appsrc_to_enc)
      .add(rec_encoder)
      .add(rec_parse)
      .add(q_enc_to_mux)
      .add(rec_mux)
      .add(rec_filesink);
  // No explicit .link(): straight-line chain auto-links in insertion order.
  pipeline_recording.eos(true);

  // ---- Main pipeline: decode -> AI -> compose -> display + recording tap ----
  Pipeline pipeline_main("event-encoder-main");

  Element source("filesrc", "event_filesrc");
  source.set("location", kInputFile);

  Element demux("qtdemux", "event_qtdemux");
  Element parse("h264parse", "event_h264parse");

  Element decoder("v4l2h264dec", "event_v4l2h264dec");
  decoder.set("output-io-mode", 4);
  decoder.set("capture-io-mode", 4);

  Element q_dec_to_vf("queue", "q_dec_to_vf");

  Element split("tee", "split");
  Element q_passthrough("queue", "q_passthrough");
  Element q_pre_convert("queue", "q_pre_convert");

  Element preproc("qtimlvconverter", "event_preproc");

  Element infer("qtimltflite", "event_infer");
  infer.set("model", kModelPath);
  infer.set("delegate", "external");
  infer.set("external-delegate-path", "libQnnTFLiteDelegate.so");
  infer.set("external-delegate-options",
            "QNNExternalDelegate,backend_type=htp,log_level=(string)1;");

  Element q_infer_to_dtee("queue", "q_infer_to_dtee");
  Element detection_tee("tee", "detection_tee");

  // Two independent qtimlpostprocess instances on the same detection tensors
  // — one negotiates to an RGBA overlay mask for the composer branch, the
  // other negotiates to text/x-raw metadata for the AppSink branch. A single
  // qtimlpostprocess src pad only carries one negotiated format at a time.
  const std::string settings_json =
      "{\"confidence\": " + std::to_string(kConfidenceThreshold) + "}";

  Element post_mask("qtimlpostprocess", "post_mask");
  post_mask.set("module", "yolov8");
  post_mask.set("labels", kLabelsPath);
  post_mask.set("settings", settings_json);

  Element post_meta("qtimlpostprocess", "post_meta");
  post_meta.set("module", "yolov8");
  post_meta.set("labels", kLabelsPath);
  post_meta.set("settings", settings_json);

  Element q_mask_to_composer("queue", "q_mask_to_composer");
  // Force the event-control branch to consume the postprocess text metadata,
  // rather than allowing the postprocess src pad to negotiate rendered video.
  auto metadata_filter = qti::TextFilter();
  Element q_meta_to_appsink("queue", "q_meta_to_appsink");

  AppSink detection_appsink("detection_appsink");
  detection_appsink.set("sync", false);

  Element composer("qtivcomposer", "event_composer");

  auto composer_out_filter =
      qti::VideoFilter().format("NV12").resolution(kDecodedWidth, kDecodedHeight);
  auto mask_render_filter = qti::VideoFilter().format("RGBA");

  Element composer_tee("tee", "composer_tee");
  Element q_display("queue", "q_display");

  Element display("waylandsink", "display");
  display.set("fullscreen", true);
  display.set("sync", false);
  display.set("enable-last-sample", false);

  Element q_composer_tap("queue", "q_composer_tap");
  AppSink composer_appsink("composer_appsink");
  composer_appsink.set("sync", false);

  pipeline_main.add(source)
      .add(demux)
      .add(parse)
      .add(decoder)
      .add(q_dec_to_vf)
      .add_stream_filter("vf", qti::VideoFilter().format("NV12"))
      .add(split)
      .add(q_passthrough)
      .add(q_pre_convert)
      .add(preproc)
      .add(infer)
      .add(q_infer_to_dtee)
      .add(detection_tee)
      .add(post_mask)
      .add_stream_filter("mask_filter", mask_render_filter)
      .add(q_mask_to_composer)
      .add(post_meta)
      .add_stream_filter("metadata_filter", metadata_filter)
      .add(q_meta_to_appsink)
      .add(detection_appsink)
      .add(composer)
      .add_stream_filter("composer_out", composer_out_filter)
      .add(composer_tee)
      .add(q_display)
      .add(display)
      .add(q_composer_tap)
      .add(composer_appsink)
      // Decode chain into the tee.
      .link("event_filesrc", "event_qtdemux", "event_h264parse",
            "event_v4l2h264dec", "q_dec_to_vf", "vf", "split")
      // Passthrough branch -> composer sink_0.
      .link("split", "q_passthrough", "event_composer")
      // AI branch -> detection_tee (tensors).
      .link("split", "q_pre_convert", "event_preproc", "event_infer",
            "q_infer_to_dtee", "detection_tee")
      // Mask branch -> composer sink_1.
      .link("detection_tee", "post_mask", "mask_filter", "q_mask_to_composer",
            "event_composer")
      // Metadata branch -> detection AppSink.
      .link("detection_tee", "post_meta", "metadata_filter",
            "q_meta_to_appsink", "detection_appsink")
      // Composer output -> display tap + recording tap.
      .link("event_composer", "composer_out", "composer_tee")
      .link("composer_tee", "q_display", "display")
      .link("composer_tee", "q_composer_tap", "composer_appsink");

  // Single full-screen stream: passthrough pad pinned to the decoded frame
  // size; mask pad intentionally left unset (scales to the same tile).
  pipeline_main.get("event_composer")
      .input(0)
      .set("position", std::vector<int>{0, 0})
      .set("dimensions", std::vector<int>{kDecodedWidth, kDecodedHeight});

  // ---- Detection-metadata callback: drives the recording state machine ----
  detection_appsink.set_buffer_consumer([](qti::Buffer buffer) {
    bool person_detected = FrameContainsLabel(buffer, kDetectionLabel);
    std::cout << "Metadata callback: bytes=" << buffer.size()
              << ", person=" << (person_detected ? "yes" : "no") << std::endl;

    if (person_detected) {
      g_state.no_detection_frame_count = 0;
      if (!g_state.recording_active) {
        g_state.recording_active = true;
        std::cout << "Recording started" << std::endl;
      }
    } else if (g_state.recording_active) {
      g_state.no_detection_frame_count++;
      if (g_state.no_detection_frame_count >= kNoDetectionFrameThreshold) {
        // eos(true) lets mp4mux flush its moov atom before we tear the
        // recording pipeline down.
        g_state.recording_active = false;
        g_state.no_detection_frame_count = 0;
        std::cout << "Recording stopped" << std::endl;
      }
    }
  });

  // ---- Composed-frame callback: feeds the recording pipeline while active ----
  composer_appsink.set_buffer_consumer([&event_appsrc](qti::Buffer buffer) {
    if (g_state.recording_active && buffer.valid() && buffer.size() > 0) {
      // Transfer the DMA-backed GstBuffer directly. AppSrc takes ownership of
      // the moved wrapper; allocating a system-memory copy breaks the hardware
      // encoder's buffer contract on this device.
      event_appsrc.push_buffer(std::move(buffer));
    } else if (g_state.recording_active) {
      std::cout << "Recording buffer unavailable" << std::endl;
    }
  });

  pipeline_recording.start();
  pipeline_main.execute();

  // Finalize the resident recorder once the input file reaches EOS. This
  // cleanly writes the MP4 moov atom even if the final detection was a person.
  pipeline_recording.eos(true);
  pipeline_recording.stop();
}

int main() {
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  try {
    CreateAndExecutePipelines();
  } catch (const std::exception &ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
