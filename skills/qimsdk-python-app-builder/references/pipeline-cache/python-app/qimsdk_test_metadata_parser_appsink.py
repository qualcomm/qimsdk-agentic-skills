"""QIM SDK Python app: YOLOv8 object-detection metadata parser from an mp4-file source.

Runs YOLOv8 detection on an mp4-file, renders the overlay on a wayland display,
and simultaneously taps the raw detection metadata (post `qtimlpostprocess`) into
an AppSink so application code can parse/consume it directly, separate from the
display-overlay branch.
"""

import os

from qimsdk import (
    AppSink,
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    TextFilter,
    VideoFilter,
)

INPUT_FILE = os.path.expandvars("$HOME/Downloads/qimsdk_samples/media/object_detection.mp4")
MODEL_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/models/yolov8_det_w8a8.tflite")
LABELS_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/labels/yolov8.json")
CONFIDENCE = 51.0


def on_detection_buffer(buffer) -> bool:
    """Consume parsed detection metadata (text/x-raw utf8 GstStructure list).

    TODO:
    - read/decode `buffer` payload (utf8-serialized ObjectDetection structures)
    - map into the application's own metadata model
    """
    return True


def create_and_execute_pipeline() -> None:
    pipeline = Pipeline("metadata_parser_yolov8")

    # Source / demux / decode
    source = Element("filesrc", "source")
    source.set("location", INPUT_FILE)

    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")

    decoder = Element("v4l2h264dec", "decoder")
    decoder.set("capture-io-mode", 4)
    decoder.set("output-io-mode", 4)

    q_dec = Element("queue", "q_dec")

    vf = VideoFilter().format("NV12")

    # Split: one branch overlays for display, one branch runs AI + metadata tap
    split = Element("tee", "split")

    q_video = Element("queue", "q_video")
    metamux = Element("qtimetamux", "metamux")

    q_ai = Element("queue", "q_ai")
    pre = Element("qtimlvconverter", "preproc")

    infer = Element("qtimltflite", "inference")
    infer.set("model", MODEL_PATH)
    infer.set("delegate", "external")
    infer.set("external-delegate-path", "libQnnTFLiteDelegate.so")
    infer.set(
        "external-delegate-options",
        "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
    )

    post = Element("qtimlpostprocess", "postproc")
    post.set("module", "yolov8")
    post.set("labels", LABELS_PATH)
    post.set("settings", f'{{"confidence": {CONFIDENCE}}}')

    # Detection output fans out: one copy for display overlay, one copy to AppSink
    detection_split = Element("tee", "detection_split")

    q_ovl = Element("queue", "q_ovl")
    mlf = TextFilter()

    q_app = Element("queue", "q_app")
    appsink = AppSink("metadata_sink")
    appsink.set_buffer_consumer(on_detection_buffer)

    overlay = Element("qtivoverlay", "overlay")
    display = Element("waylandsink", "display")
    display.set("fullscreen", True)
    display.set("sync", True)

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(split)
    pipeline.add(q_video)
    pipeline.add(metamux)
    pipeline.add(q_ai)
    pipeline.add(pre)
    pipeline.add(infer)
    pipeline.add(post)
    pipeline.add(detection_split)
    pipeline.add(q_ovl)
    pipeline.add_stream_filter("mlf", mlf)
    pipeline.add(q_app)
    pipeline.add(appsink)
    pipeline.add(overlay)
    pipeline.add(display)

    # qtdemux exposes a dynamic pad; link the static chain up to it here,
    # the dynamic video pad connects to "parser" via the pipeline's pad-added handling.
    pipeline.link("source", "demux")
    pipeline.link("demux", "parser", "decoder", "q_dec", "vf", "split")

    # Video passthrough branch -> overlay -> display
    pipeline.link("split", "q_video", "metamux")
    pipeline.link("metamux", "overlay", "display")

    # AI branch -> postprocess -> detection metadata fan-out
    pipeline.link("split", "q_ai", "preproc", "inference", "postproc", "detection_split")

    # Detection fan-out branch 1: overlay text merge
    pipeline.link("detection_split", "q_ovl", "mlf", "metamux")

    # Detection fan-out branch 2: app-side metadata consumption
    pipeline.link("detection_split", "q_app", "metadata_sink")

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
