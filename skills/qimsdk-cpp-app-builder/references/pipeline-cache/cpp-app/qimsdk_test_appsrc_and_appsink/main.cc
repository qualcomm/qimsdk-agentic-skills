/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#include <iostream>
#include <utility>

#include <qti/qimsdk.h>

using namespace qti;

//  Example pipeline:
//
//    testsrc → [videostream] → appsink
//
//  The pipeline creates and executes the configured media/ML graph.
//

void create_and_execute_pipeline() {

  // Generates synthetic test video frames.
  Element testsrc("videotestsrc", "testsrc");
  testsrc.set("is-live", true);
  testsrc.set("pattern", "ball");

  VideoFilter videostream =
      VideoFilter().format("NV12").resolution(1920, 1080).framerate(30);

  // Receives buffers from the upstream pipeline branch.
  AppSink appsink("sink");
  appsink.set("emit-signals", true);
  appsink.set("max-buffers", 5);
  appsink.set("drop", true);

  // Creates the pipeline, adds and links elements, and executes it.
  //
  // Explicit linking is applied
  Pipeline pipeline1("p1");
  pipeline1.add(testsrc)
      .add_stream_filter("videostream", videostream)
      .add(appsink);

  // Pushes buffers into the downstream pipeline branch.
  AppSrc appsrc("src");
  appsrc.set("is-live", true);
  appsrc.set("block", true);
  appsrc.set("stream-type", 0);
  appsrc.set("format", AppSrc::Format::TIME);
  appsrc.set("do-timestamp", true);
  appsrc.set("caps", VideoFilter().format("NV12").resolution(1920, 1080).framerate(30));

  // Render video stream on display.
  //
  // async=false enforce state transition to ensure the buffers are returned on time.
  // sync=true keeps rendering synchronized to the pipeline clock.
  // fullscreen=true renders the output fullscreen on the target display.
  Element display("waylandsink", "display");
  display.set("fullscreen", true);

  appsink.set_buffer_consumer([&](Buffer b) {
    appsrc.push_buffer(std::move(b));
  });

  Pipeline pipeline2("p2");
  pipeline2.add(appsrc)
      .add(display);

  pipeline2.start();
  pipeline1.start();
  pipeline2.wait();
  pipeline1.wait();

  pipeline2.stop();
  pipeline1.stop();
}

int main() {
  // Route GStreamer logs through the IMSDK logger and enable debug output.
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
