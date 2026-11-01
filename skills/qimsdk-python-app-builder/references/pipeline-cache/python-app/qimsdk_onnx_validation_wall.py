#!/usr/bin/env python3
"""Create a five-tile ONNX Runtime validation wall and encode it to MP4."""

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    VideoFilter,
)

INPUT_FILE = "/root/Downloads/qimsdk_samples/media/Short-Draw.mp4"
OUTPUT_FILE = "/root/Downloads/qimsdk_samples/media/output/onnx_validation_wall.mp4"
YOLOX_MODEL = "/root/Downloads/qimsdk_samples/models/yolox-onnx-w8a8/yolox.onnx"
YOLOX_LABELS = "/root/Downloads/qimsdk_samples/labels/yolov8.json"
DEEPLAB_MODEL = "/root/Downloads/qimsdk_samples/models/deeplab_xception-onnx-w8a8/deeplab_xception.onnx"
DEEPLAB_LABELS = "/root/Downloads/qimsdk_samples/labels/dv3-argmax.json"
SR_MODEL = "/root/Downloads/qimsdk_samples/models/quicksrnetlarge-onnx-float/quicksrnetlarge.onnx"

# Five 16:9 tiles occupy the first five slots of a 3x2, 640x360 grid.
TILE_WIDTH = 640
TILE_HEIGHT = 360
TILE_POSITIONS = [(0, 0), (640, 0), (1280, 0), (0, 360), (640, 360)]


def create_and_execute_pipeline() -> None:
    # Read and decode one MP4, then normalize decoded frames to NV12.
    source = Element("filesrc", "source").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")
    decoder = Element("v4l2h264dec", "decoder").set("capture-io-mode", 4).set("output-io-mode", 4)
    q_dec = Element("queue", "q_dec")
    nv12 = VideoFilter().format("NV12")
    split = Element("tee", "split")

    # The composer receives a raw tile and a rendered mask for each AI branch.
    original_queue = Element("queue", "original_queue")
    original_transform = Element("qtivtransform", "original_transform")
    original_nv12 = VideoFilter().format("NV12")

    cpu_raw_queue = Element("queue", "cpu_raw_queue")
    cpu_raw_transform = Element("qtivtransform", "cpu_raw_transform")
    cpu_raw_nv12 = VideoFilter().format("NV12")
    cpu_ai_queue = Element("queue", "cpu_ai_queue")
    cpu_pre = Element("qtimlvconverter", "cpu_pre")
    cpu_infer = Element("qtimlonnx", "cpu_infer").set("model", YOLOX_MODEL).set("execution-provider", "cpu").set("threads", 8)
    cpu_post_queue = Element("queue", "cpu_post_queue")
    cpu_post = (
        Element("qtimlpostprocess", "cpu_post")
        .set("module", "yolov8")
        .set("labels", YOLOX_LABELS)
        .set("results", 10)
    )
    cpu_render = VideoFilter().format("RGBA")
    cpu_render_queue = Element("queue", "cpu_render_queue")

    qnn_raw_queue = Element("queue", "qnn_raw_queue")
    qnn_raw_transform = Element("qtivtransform", "qnn_raw_transform")
    qnn_raw_nv12 = VideoFilter().format("NV12")
    qnn_ai_queue = Element("queue", "qnn_ai_queue")
    qnn_pre = Element("qtimlvconverter", "qnn_pre")
    qnn_infer = (
        Element("qtimlonnx", "qnn_infer")
        .set("model", YOLOX_MODEL)
        .set("execution-provider", "qnn")
        .set("backend-path", "/usr/lib/libQnnHtp.so")
    )
    qnn_post_queue = Element("queue", "qnn_post_queue")
    qnn_post = (
        Element("qtimlpostprocess", "qnn_post")
        .set("module", "yolov8")
        .set("labels", YOLOX_LABELS)
        .set("results", 10)
    )
    qnn_render = VideoFilter().format("RGBA")
    qnn_render_queue = Element("queue", "qnn_render_queue")

    # Segmentation is a rendered RGBA mask paired with its raw passthrough pad.
    seg_raw_queue = Element("queue", "seg_raw_queue")
    seg_raw_transform = Element("qtivtransform", "seg_raw_transform")
    seg_raw_nv12 = VideoFilter().format("NV12")
    seg_ai_queue = Element("queue", "seg_ai_queue")
    seg_pre = Element("qtimlvconverter", "seg_pre")
    seg_infer = (
        Element("qtimlonnx", "seg_infer")
        .set("model", DEEPLAB_MODEL)
        .set("execution-provider", "qnn")
        .set("backend-path", "/usr/lib/libQnnHtp.so")
    )
    seg_post_queue = Element("queue", "seg_post_queue")
    seg_post = (
        Element("qtimlpostprocess", "seg_post")
        .set("module", "deeplab-argmax")
        .set("labels", DEEPLAB_LABELS)
    )
    seg_render = VideoFilter().format("RGBA")
    seg_render_queue = Element("queue", "seg_render_queue")

    sr_raw_queue = Element("queue", "sr_raw_queue")
    sr_raw_transform = Element("qtivtransform", "sr_raw_transform")
    sr_raw_nv12 = VideoFilter().format("NV12")
    sr_ai_queue = Element("queue", "sr_ai_queue")
    sr_pre = Element("qtimlvconverter", "sr_pre")
    sr_infer = (
        Element("qtimlonnx", "sr_infer")
        .set("model", SR_MODEL)
        .set("execution-provider", "qnn")
        .set("backend-path", "/usr/lib/libQnnHtp.so")
        .set("htp-performance-mode", "burst")
    )
    sr_post_queue = Element("queue", "sr_post_queue")
    sr_post = Element("qtimlpostprocess", "sr_post").set("module", "srnet")
    sr_render = VideoFilter().format("RGBA")
    sr_render_queue = Element("queue", "sr_render_queue")

    # Compose the five tiles, then encode the wall to the requested MP4.
    composer = Element("qtivcomposer", "wall")
    output_nv12 = VideoFilter().format("NV12")
    encoder = Element("v4l2h264enc", "encoder").set("capture-io-mode", 4).set("output-io-mode", 4)
    output_parser = Element("h264parse", "output_parser")
    mux = Element("mp4mux", "mux")
    sink = Element("filesink", "sink").set("location", OUTPUT_FILE)

    pipeline = Pipeline("onnx-validation-wall")
    for element in (source, demux, parser, decoder, q_dec, split,
                    original_queue, original_transform,
                    cpu_raw_queue, cpu_raw_transform, cpu_ai_queue, cpu_pre, cpu_infer, cpu_post_queue, cpu_post, cpu_render_queue,
                    qnn_raw_queue, qnn_raw_transform, qnn_ai_queue, qnn_pre, qnn_infer, qnn_post_queue, qnn_post, qnn_render_queue,
                    seg_raw_queue, seg_raw_transform, seg_ai_queue, seg_pre, seg_infer, seg_post_queue, seg_post, seg_render_queue,
                    sr_raw_queue, sr_raw_transform, sr_ai_queue, sr_pre, sr_infer, sr_post_queue, sr_post, sr_render_queue,
                    composer, encoder, output_parser, mux, sink):
        pipeline.add(element)

    for name, stream_filter in (
        ("nv12", nv12), ("original_nv12", original_nv12),
        ("cpu_raw_nv12", cpu_raw_nv12), ("cpu_render", cpu_render),
        ("qnn_raw_nv12", qnn_raw_nv12), ("qnn_render", qnn_render),
        ("seg_raw_nv12", seg_raw_nv12), ("seg_render", seg_render),
        ("sr_raw_nv12", sr_raw_nv12), ("sr_render", sr_render),
        ("output_nv12", output_nv12)):
        pipeline.add_stream_filter(name, stream_filter)

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "nv12", "split")
    pipeline.link("split", "original_queue", "original_transform", "original_nv12", "wall")

    # YOLOX CPU tile: raw frame plus RGBA detection mask.
    pipeline.link("split", "cpu_raw_queue", "cpu_raw_transform", "cpu_raw_nv12", "wall")
    pipeline.link("split", "cpu_ai_queue", "cpu_pre", "cpu_infer", "cpu_post_queue", "cpu_post", "cpu_render", "cpu_render_queue", "wall")

    # YOLOX QNN HTP tile: raw frame plus RGBA detection mask.
    pipeline.link("split", "qnn_raw_queue", "qnn_raw_transform", "qnn_raw_nv12", "wall")
    pipeline.link("split", "qnn_ai_queue", "qnn_pre", "qnn_infer", "qnn_post_queue", "qnn_post", "qnn_render", "qnn_render_queue", "wall")

    # DeepLab QNN HTP tile: raw frame plus RGBA segmentation mask.
    pipeline.link("split", "seg_raw_queue", "seg_raw_transform", "seg_raw_nv12", "wall")
    pipeline.link("split", "seg_ai_queue", "seg_pre", "seg_infer", "seg_post_queue", "seg_post", "seg_render", "seg_render_queue", "wall")

    # QuickSRNet Large QNN HTP burst tile: raw frame plus RGBA SR output.
    pipeline.link("split", "sr_raw_queue", "sr_raw_transform", "sr_raw_nv12", "wall")
    pipeline.link("split", "sr_ai_queue", "sr_pre", "sr_infer", "sr_post_queue", "sr_post", "sr_render", "sr_render_queue", "wall")

    pipeline.link("wall", "output_nv12", "encoder", "output_parser", "mux", "sink")

    # Composer input order is original, then raw/mask pairs for CPU, QNN,
    # segmentation, and SR. Raw and mask pads share each tile's geometry.
    wall = pipeline.get("wall")
    wall.input(0).set("position", [0, 0]).set("dimensions", [TILE_WIDTH, TILE_HEIGHT])
    for pair_index, (raw_pad, mask_pad) in enumerate(((1, 2), (3, 4), (5, 6), (7, 8)), start=1):
        x, y = TILE_POSITIONS[pair_index]
        for pad in (raw_pad, mask_pad):
            wall.input(pad).set("position", [x, y]).set("dimensions", [TILE_WIDTH, TILE_HEIGHT])

    pipeline.eos(True)
    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)
    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
