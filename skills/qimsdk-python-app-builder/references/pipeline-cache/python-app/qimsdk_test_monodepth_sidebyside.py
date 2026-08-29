"""QIM SDK Python app: monocular depth estimation with side-by-side comparison display."""

import os

from qimsdk import (
    Element,
    Pipeline,
    VideoFilter,
    ImsdkLogLevel,
    ImsdkGstLogMode,
    SetImsdkLogLevel,
    SetImsdkGstLogMode,
)

INPUT_FILE = os.path.expandvars("$HOME/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4")
MODEL_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/models/midas_v2_w8a8.tflite")
LABELS_PATH = os.path.expandvars("$HOME/Downloads/qimsdk_samples/labels/monodepth.json")


def create_and_execute_pipeline() -> None:
    pipeline = Pipeline("monodepth_side_by_side")

    # Source / hardware decode
    source = Element("filesrc", "source").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")
    decoder = Element("v4l2h264dec", "decoder").set("capture-io-mode", 4, "output-io-mode", 4)
    q_dec = Element("queue", "q_dec")
    vf = VideoFilter().format("NV12")

    # Split into a passthrough branch (left pane) and an AI branch (right pane)
    split = Element("tee", "split")
    q_video = Element("queue", "q_video")
    q_ai = Element("queue", "q_ai")

    # Depth-estimation AI branch: preprocess -> inference -> postprocess
    pre = Element("qtimlvconverter", "preproc")
    q_infer = Element("queue", "q_infer")
    infer = Element("qtimltflite", "inference")
    infer.set("model", MODEL_PATH)
    infer.set("delegate", "external")
    infer.set("external-delegate-path", "libQnnTFLiteDelegate.so")
    infer.set(
        "external-delegate-options",
        "QNNExternalDelegate,backend_type=htp,log_level=(string)1;",
    )
    q_post = Element("queue", "q_post")
    post = Element("qtimlpostprocess", "postproc")
    post.set("module", "midas-v2")
    post.set("labels", LABELS_PATH)

    # Depth-map postprocess renders RGBA; let it emit at its native size and rely
    # on the composer sink-pad dimensions to scale into the display tile. NOTE:
    # qtimlpostprocess only emits video/x-raw {RGBA, RGBx} (BGRA fails to link),
    # and pinning width/height here makes postproc caps fixation fail (NULL caps).
    render_filter = VideoFilter().format("RGBA")

    # Composer: passthrough on the left, depth-overlay render on the right, each 960x1080
    composer = Element("qtivcomposer", "composer")
    display = Element("waylandsink", "display").set("fullscreen", True, "sync", True)

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(split)
    pipeline.add(q_video)
    pipeline.add(q_ai)
    pipeline.add(pre)
    pipeline.add(q_infer)
    pipeline.add(infer)
    pipeline.add(q_post)
    pipeline.add(post)
    pipeline.add_stream_filter("render_filter", render_filter)
    pipeline.add(composer)
    pipeline.add(display)

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "split")
    pipeline.link("split", "q_video", "composer")
    pipeline.link(
        "split", "q_ai", "preproc", "q_infer", "inference",
        "q_post", "postproc", "render_filter", "composer",
    )
    pipeline.link("composer", "display")

    # Left pane (passthrough, sink_0): full-left position, 960x1080
    composer.input(0).set("position", [0, 0])
    composer.input(0).set("dimensions", [960, 1080])
    # Right pane (depth overlay, sink_1): full-right position, 960x1080
    composer.input(1).set("position", [960, 0])
    composer.input(1).set("dimensions", [960, 1080])

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
