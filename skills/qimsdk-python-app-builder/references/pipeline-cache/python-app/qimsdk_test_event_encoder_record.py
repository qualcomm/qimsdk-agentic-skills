#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Event-triggered recording: run YOLOX object detection on a decoded MP4 file,
display the detection overlay, and record the overlaid stream to an MP4 file
only while a person is present — faithfully following the C reference
`gst-ai-event-encoder` pattern.

Pattern (mirrors the C sample):
  * Main pipeline: decode -> tee -> [AI: preprocess -> YOLOX -> postprocess ->
    metaparser(json) -> metadata AppSink] and [overlay -> tee -> waylandsink +
    video AppSink].
  * A separate recording pipeline stays resident (appsrc -> queue -> qtivtransform
    -> videoconvert -> NV12 -> v4l2h264enc -> h264parse -> mp4mux -> filesink) and
    is toggled PAUSED<->PLAYING by the person-presence gate.
  * The metadata AppSink parses detections; when a person appears the recording
    pipeline is set to PLAYING; after 150 consecutive person-free frames it is
    sent EOS and paused.
  * The video AppSink pushes a COPIED buffer into the recording appsrc only while
    the recording pipeline is RUNNING (exactly like the C ref's appsink_recording).
"""

import json
import os

from qimsdk import (
    AppSink,
    AppSrc,
    Buffer,
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    TextFilter,
    VideoFilter,
)

# gst() accessor for the underlying GStreamer bindings (used only to copy the
# video buffer and toggle the recording pipeline's state, matching the C ref).
from qimsdk._utils import gst

INPUT_FILE = os.path.expandvars("$HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4")
MODEL_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/models/yolox_w8a8.tflite")
LABELS_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/labels/yolov8.json")
OUTPUT_FILE = "/tmp/event-output.mp4"

CONFIDENCE = 51.0
NO_PERSON_STOP_FRAMES = 150
PERSON_LABEL = "person"


class EventRecordState:
    """Person-presence gate that toggles the resident recording pipeline between
    PAUSED and PLAYING and relays copied video buffers into its appsrc while
    recording — a direct port of the C ref's appsink_detection/appsink_recording."""

    def __init__(self, record_pipeline, record_src):
        self._Gst = gst()
        self.record_pipeline = record_pipeline
        self.record_src = record_src
        self.recording = False          # True == relaying frames to the recorder
        self.finished = False           # True once EOS has been sent
        self.no_person_frames = 0

    def _read_text(self, buffer):
        """Reads the metadata AppSink buffer payload as UTF-8 text via the
        qimsdk Buffer.data() memoryview accessor."""
        try:
            mv = buffer.data()
        except Exception:
            return None
        if mv is None:
            return None
        try:
            raw = bytes(mv)
        except Exception:
            return None
        if not raw:
            return None
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip() or None

    def _person_in(self, payload_text) -> bool:
        try:
            payload = json.loads(payload_text)
        except (ValueError, TypeError):
            return False
        # qtimlmetaparser(module=json) emits {"object_detection": [ {label, ...}, ... ]}
        entries = payload.get("object_detection", []) if isinstance(payload, dict) else payload
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                name = entry.get("label") or entry.get("name")
                if isinstance(name, str) and name.lower() == PERSON_LABEL:
                    return True
        return False

    def on_detection_json(self, buffer) -> bool:
        if self.finished:
            return True

        payload_text = self._read_text(buffer)
        person_present = self._person_in(payload_text) if payload_text else False

        if person_present:
            self.no_person_frames = 0
            if not self.recording:
                self.recording = True
                print("[event-encoder] person detected -> recording started", flush=True)
        elif self.recording:
            self.no_person_frames += 1
            if self.no_person_frames >= NO_PERSON_STOP_FRAMES:
                self.record_src.end_of_stream()
                self.recording = False
                self.finished = True
                print(
                    f"[event-encoder] no person for {NO_PERSON_STOP_FRAMES} frames -> "
                    "recording stopped",
                    flush=True,
                )
        return True

    def on_video_frame(self, buffer) -> bool:
        # Push a COPIED buffer into the recording appsrc only while recording is
        # active (the C ref copies the buffer and only pushes when RUNNING).
        if not self.recording:
            return True
        try:
            gst_buf = buffer.take_gst_buffer()
            if gst_buf is not None:
                self.record_src.push_buffer(Buffer(gst_buffer=gst_buf.copy()))
        except Exception:
            pass
        return True


def build_record_pipeline():
    """Resident recording pipeline, fed by appsrc with overlaid NV12 frames
    relayed from the main pipeline; encoded and muxed to OUTPUT_FILE.

    Kept PLAYING throughout; buffers are only pushed while a person is present,
    so nothing is encoded until recording is active. appsrc is is-live with
    do-timestamp so cross-pipeline buffers are re-stamped on this pipeline's
    clock (avoids clock/PTS mismatch), and its caps are FULLY fixed so the
    encoder negotiates without endless caps churn."""
    record_pipeline = Pipeline("event_recorder")

    record_src = AppSrc("appsrc")
    record_src.set_caps(
        VideoFilter().format("NV12").resolution(1920, 1080).framerate(30)
    )
    record_src.set("format", 3)          # GST_FORMAT_TIME
    record_src.set("is-live", True)      # don't block preroll waiting for data
    record_src.set("do-timestamp", True)  # stamp buffers on arrival (this clock)

    q_rec = Element("queue", "q_rec")
    encoder = Element("v4l2h264enc", "record_encoder").set(
        "capture-io-mode", 4, "output-io-mode", 4
    )
    parse_out = Element("h264parse", "record_parse")
    mux = Element("mp4mux", "record_mux")
    sink = Element("filesink", "filesink").set("location", OUTPUT_FILE)

    record_pipeline.add(record_src)
    record_pipeline.add(q_rec)
    record_pipeline.add(encoder)
    record_pipeline.add(parse_out)
    record_pipeline.add(mux)
    record_pipeline.add(sink)

    record_pipeline.link(
        "appsrc", "q_rec", "record_encoder", "record_parse", "record_mux", "filesink",
    )
    record_pipeline.eos(True)

    return record_pipeline, record_src


def build_main_pipeline(state_holder):
    pipeline = Pipeline("event_encoder")

    # Source / hardware decode
    source = Element("filesrc", "source").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")
    decoder = Element("v4l2h264dec", "decoder").set("capture-io-mode", 4, "output-io-mode", 4)
    q_dec = Element("queue", "q_dec")
    vf = VideoFilter().format("NV12")

    # Split raw video into passthrough (for overlay) and AI branches
    split1 = Element("tee", "split1")
    q_video = Element("queue", "q_video")
    q_ai = Element("queue", "q_ai")

    # AI: preprocess -> YOLOX inference -> postprocess
    pre = Element("qtimlvconverter", "preprocess")
    infer = Element("qtimltflite", "inference")
    infer.set("model", MODEL_PATH)
    infer.set("delegate", "external")
    infer.set("external-delegate-path", "libQnnTFLiteDelegate.so")
    infer.set(
        "external-delegate-options",
        "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
    )
    post = Element("qtimlpostprocess", "postprocess")
    post.set("module", "yolov8")
    post.set("labels", LABELS_PATH)
    post.set("settings", json.dumps({"confidence": CONFIDENCE}))

    # Fan the postprocess metadata out: one copy for overlay, one for JSON parse.
    split_meta = Element("tee", "split_meta")
    q_meta_overlay = Element("queue", "q_meta_overlay")
    q_meta_detect = Element("queue", "q_meta_detect")
    mlf = TextFilter()
    metaparser = Element("qtimlmetaparser", "metaparser").set("module", "json")
    detect_sink = AppSink("detect_sink")
    # Leaf appsinks must not gate on the clock or they can deadlock preroll
    # alongside waylandsink; consume asynchronously.
    detect_sink.set("sync", False)

    metamux = Element("qtimetamux", "metamux")
    overlay = Element("qtivoverlay", "overlay")

    # Split the overlaid frame into live display and record-candidate branches.
    split2 = Element("tee", "split2")
    q_disp = Element("queue", "q_disp")
    q_rec_src = Element("queue", "q_rec_src")
    display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)
    # qtivtransform before the video AppSink isolates the appsink's buffer-pool
    # allocation from the shared decode tee. Without it, the leaf appsink's
    # system-memory allocation query propagates back through the tee and forces
    # non-DMA buffers, breaking qtimlvconverter on the AI branch ("Buffer does
    # not have FD memory" → 0 inference frames).
    rec_tf = Element("qtivtransform", "rec_tf")
    rec_vf = VideoFilter().format("NV12")
    video_sink = AppSink("video_sink")
    video_sink.set("sync", False)

    for el in (source, demux, parser, decoder, q_dec):
        pipeline.add(el)
    pipeline.add_stream_filter("vf", vf)
    for el in (split1, q_video, q_ai, pre, infer, post, split_meta,
               q_meta_overlay, q_meta_detect):
        pipeline.add(el)
    pipeline.add_stream_filter("mlf", mlf)
    for el in (metaparser, detect_sink, metamux, overlay, split2,
               q_disp, q_rec_src, display, rec_tf, video_sink):
        pipeline.add(el)
    pipeline.add_stream_filter("rec_vf", rec_vf)

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split1")
    pipeline.link("split1", "q_video", "metamux")
    pipeline.link("split1", "q_ai", "preprocess", "inference", "postprocess", "split_meta")
    pipeline.link("split_meta", "q_meta_overlay", "mlf", "metamux")
    pipeline.link("split_meta", "q_meta_detect", "metaparser", "detect_sink")
    pipeline.link("metamux", "overlay", "split2")
    pipeline.link("split2", "q_disp", "display")
    pipeline.link("split2", "q_rec_src", "rec_tf", "rec_vf", "video_sink")

    detect_sink.set_buffer_consumer(state_holder.on_detection_json)
    video_sink.set_buffer_consumer(state_holder.on_video_frame)

    return pipeline


def create_and_execute_pipeline() -> None:
    record_pipeline, record_src = build_record_pipeline()

    # Bring the recording pipeline up and keep it PLAYING. It is appsrc-driven
    # and is-live, so it prerolls without data and simply idles until frames are
    # pushed (only while a person is present). No state toggling needed.
    record_pipeline.start()

    state = EventRecordState(record_pipeline, record_src)
    main_pipeline = build_main_pipeline(state)

    main_pipeline.execute()

    # Main stream ended; if still recording, finalize the MP4 container.
    if state.recording and not state.finished:
        record_src.end_of_stream()

    record_pipeline.stop()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
