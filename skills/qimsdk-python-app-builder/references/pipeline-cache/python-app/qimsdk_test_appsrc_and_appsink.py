#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Basic AppSink -> AppSrc forwarding sample"""

from qimsdk import AppSink, AppSrc, Element, Pipeline, VideoFilter

#  Example pipeline:
#
#    producer: testsrc -> [videofilter] -> appsink
#    consumer: appsrc -> display
#
#  The producer pipeline generates synthetic frames and hands them to the
#  consumer pipeline through an AppSink -> AppSrc buffer bridge, which then
#  displays the result through Wayland.


def create_and_execute_pipeline() -> None:

    # Generates synthetic test video frames.
    testsrc = (
        Element("videotestsrc", "testsrc")
        .set("is-live", True)
        .set("pattern", "ball")
    )

    # Restricts the generated stream to a fixed NV12/1080p/30fps format.
    videofilter = VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)

    # Pushes buffers into the downstream pipeline branch.
    appsrc = (
        AppSrc("src")
        .set("is-live", True, "block", True, "stream-type", 0)
        .set("format", AppSrc.Format.TIME, "do-timestamp", True)
        .set("caps", VideoFilter().format("NV12").resolution(1920, 1080).framerate(30))
    )

    # Receives buffers from the upstream pipeline branch and forwards each
    # one into appsrc, bridging the producer and consumer pipelines.
    appsink = (
        AppSink("sink")
        .set("emit-signals", True, "max-buffers", 5, "drop", True)
        .set_buffer_consumer(lambda buffer: appsrc.push_buffer(buffer))
    )

    # Render video stream on display.
    display = (
        Element("waylandsink", "display")
        .set("fullscreen", True)
    )

    # Creates the producer pipeline, adds and links elements.
    #
    # Linking is implicit and follows the order in which elements are added.
    producer = (
        Pipeline("producer")
        .add(testsrc)
        .add_stream_filter("videofilter", videofilter)
        .add(appsink)
    )

    # Creates the consumer pipeline, adds and links elements.
    consumer = (
        Pipeline("consumer")
        .add(appsrc)
        .add(display)
    )

    consumer.start()
    producer.start()
    consumer.wait()
    producer.wait()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
