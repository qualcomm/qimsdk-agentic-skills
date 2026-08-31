// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include <iostream>
#include <cstdlib>
#include <stdexcept>

#include <qti/qimsdk.h>

using namespace qti;

namespace {
std::string expand_home_path() {
  const char* home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME is not set; cannot resolve filesystem paths");
  }
  return std::string(home);
}
}  // namespace

const std::string HOME_PATH = expand_home_path();

//  Example pipeline:
//
//    src → [videostream] → split1 → q2 → stage_01_preprocessing → q3 → stage_01_inferencing → q1 → stage_01_postprocessing → [mlf_01] → mlmuxer1 → q11 → metatransform → split2 → q5 → stage_02_preprocessing → q6 → stage_02_inferencing → split3 → q7 → stage_02_1_postprocessing → [mlf_02] → mlmuxer2 → q12 → overlay → display
//                                                                                                                        └──→ q8 → stage_02_2_postprocessing → q9 → stage_03_1_inferencing → q10 → stage_03_2_inferencing → q4 → stage_03_postprocessing → [mlf_03] → mlmuxer2
//
//  The pipeline reads camera frames, runs ML inference and postprocessing,
//  and displays the result through Wayland.

void create_and_execute_pipeline() {

  // Captures frames from the camera source.
  Element source("qtiqmmfsrc", "source");

  // Splits decoded frames into display and ML branches.
  Element split1("tee", "split1");

  // Queues frames from tee into the ML branch.

  // Queues frames from tee into the ML branch.
  Element q1("queue", "q1");

  // Queues converted tensors before inference.
  Element q2("queue", "q2");

  // Converts raw video frames into model input tensor format.
  Element stage_01_preprocessing("qtimlvconverter", "stage_01_preprocessing");

  // Queues data between pipeline stages.
  Element q3("queue", "q3");

  // Executes the ML model and attaches tensor outputs to each frame.
  Element stage_01_inferencing("qtimltflite", "stage_01_inferencing");
  stage_01_inferencing.set("delegate", "gpu");
  stage_01_inferencing.set("model", HOME_PATH + "/models/palm_detection_full.tflite");

  // Decodes model output tensors into metadata for downstream overlay.
  Element stage_01_postprocessing("qtimlpostprocess", "stage_01_postprocessing");
  stage_01_postprocessing.set("module", "palmd");
  stage_01_postprocessing.set("labels", HOME_PATH + "/labels/palmd_labels.json");
  stage_01_postprocessing.set("settings", HOME_PATH + "/labels/palmd_settings.json");

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer1("qtimetamux", "mlmuxer1");

  // Queues data between pipeline stages.
  Element q11("queue", "q11");

  // Transforms metadata for downstream processing stages.
  Element metatransform("qtimetatransform", "metatransform");
  metatransform.set("module", "roi-palmd");

  // Splits decoded frames into display and ML branches.
  Element split2("tee", "split2");

  // Queues data between pipeline stages.

  // Queues frames from tee into the ML branch.
  Element q4("queue", "q4");

  // Queues data between pipeline stages.
  Element q5("queue", "q5");

  // Merges metadata produced by the ML branch with original video frames.
  Element mlmuxer2("qtimetamux", "mlmuxer2");

  // Queues data between pipeline stages.
  Element q12("queue", "q12");

  // Renders ML metadata over the video frame.
  Element overlay("qtivoverlay", "overlay");

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=false disables strict rendering synchronization to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("sync", false);
  display.set("fullscreen", true);

  // Converts raw video frames into model input tensor format.
  Element stage_02_preprocessing("qtimlvconverter", "stage_02_preprocessing");
  stage_02_preprocessing.set("mode", "roi-batch-cumulative");

  // Queues data between pipeline stages.
  Element q6("queue", "q6");

  // Executes the ML model and attaches tensor outputs to each frame.
  Element stage_02_inferencing("qtimltflite", "stage_02_inferencing");
  stage_02_inferencing.set("delegate", "xnnpack");
  stage_02_inferencing.set("model", HOME_PATH + "/models/hand_landmark_full.tflite");

  // Splits decoded frames into display and ML branches.
  Element split3("tee", "split3");

  // Queues data between pipeline stages.
  Element q7("queue", "q7");

  // Queues data between pipeline stages.
  Element q8("queue", "q8");

  // Queues data between pipeline stages.
  Element q9("queue", "q9");

  // Queues data between pipeline stages.
  Element q10("queue", "q10");

  // Decodes model output tensors into metadata for downstream overlay.
  Element stage_02_1_postprocessing("qtimlpostprocess", "stage_02_1_postprocessing");
  stage_02_1_postprocessing.set("module", "hlandmark");
  stage_02_1_postprocessing.set("labels", HOME_PATH + "/labels/hlandmarks.json");
  stage_02_1_postprocessing.set("settings", HOME_PATH + "/labels/hlandmark_settings.json");

  // Decodes model output tensors into metadata for downstream overlay.
  Element stage_02_2_postprocessing("qtimlpostprocess", "stage_02_2_postprocessing");
  stage_02_2_postprocessing.set("module", "tensor");

  // Executes the ML model and attaches tensor outputs to each frame.
  Element stage_03_1_inferencing("qtimltflite", "stage_03_1_inferencing");
  stage_03_1_inferencing.set("delegate", "gpu");
  stage_03_1_inferencing.set("model", HOME_PATH + "/models/gesture_embedder.tflite");

  // Executes the ML model and attaches tensor outputs to each frame.
  Element stage_03_2_inferencing("qtimltflite", "stage_03_2_inferencing");
  stage_03_2_inferencing.set("delegate", "gpu");
  stage_03_2_inferencing.set("model", HOME_PATH + "/models/canned_gesture_classifier.tflite");

  // Decodes model output tensors into metadata for downstream overlay.
  Element stage_03_postprocessing("qtimlpostprocess", "stage_03_postprocessing");
  stage_03_postprocessing.set("module", "mobilenet");
  stage_03_postprocessing.set("labels", HOME_PATH + "/labels/gesture_rec.json");

  // Stream filters used in branch links.
  // They define specific stream characteristics from the supported options.
  auto videostream = qti::VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);
  auto mlf_01 = TextFilter();
  auto mlf_02 = TextFilter();
  auto mlf_03 = TextFilter();

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied
  Pipeline pipeline("ml-pipeline");
  pipeline.add(source)
          .add_stream_filter("videostream", videostream)
          .add(split1)
          .add(q1)
          .add(q2)
          .add(stage_01_preprocessing)
          .add(q3)
          .add(stage_01_inferencing)
          .add(stage_01_postprocessing)
          .add_stream_filter("mlf_01", mlf_01)
          .add(mlmuxer1)
          .add(q11)
          .add(metatransform)
          .add(split2)
          .add(q4)
          .add(q5)
          .add(mlmuxer2)
          .add(q12)
          .add(overlay)
          .add(display)
          .add(stage_02_preprocessing)
          .add(q6)
          .add(stage_02_inferencing)
          .add(split3)
          .add(q7)
          .add(q8)
          .add(q9)
          .add(q10)
          .add(stage_02_1_postprocessing)
          .add_stream_filter("mlf_02", mlf_02)
          .add(stage_02_2_postprocessing)
          .add(stage_03_1_inferencing)
          .add(stage_03_2_inferencing)
          .add(stage_03_postprocessing)
          .add_stream_filter("mlf_03", mlf_03)

          .link("source", "videostream", "split1")
          .link("split1", "mlmuxer1", "q11", "metatransform", "split2")
          .link("split1", "q2", "stage_01_preprocessing", "q3", "stage_01_inferencing", "q1", "stage_01_postprocessing", "mlf_01", "mlmuxer1")
          .link("split2", "mlmuxer2", "q12", "overlay", "display")
          .link("split2", "q5", "stage_02_preprocessing", "q6", "stage_02_inferencing", "split3")
          .link("split3", "q7", "stage_02_1_postprocessing", "mlf_02", "mlmuxer2")
          .link("split3", "q8", "stage_02_2_postprocessing", "q9", "stage_03_1_inferencing", "q10",
                "stage_03_2_inferencing", "q4", "stage_03_postprocessing", "mlf_03", "mlmuxer2")
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
