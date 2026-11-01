#!/usr/bin/env python3
"""Six-stream, four-stage AI daisy-chain to a single file, all stages on SNPE/DSP.

This is the 6-stream version of the verified single-stream four-stage daisy-chain.
The exact same per-stream topology (Stage 1 YOLOv8 detection, Stage 2 DeepLabV3
segmentation, Stage 3 HRNet pose on Stage-1 ROIs, Stage 4 Inception-v3
classification on Stage-1 ROIs) is replicated across six independent input
streams. Each stream produces its own overlay+segmentation-composited video; the
six results are then tiled into one 3x2 grid by a final qtivcomposer, encoded
once, and written to a single output MP4.

Per-stream topology (identical to the single-stream reference):

  filesrc -> qtdemux -> h264parse -> v4l2h264dec -> queue -> VideoFilter(NV12)
    -> main_split (tee)
       |- overlay leg: private buffer copy (qtivtransform+NV12) -> split1
       |     Stage 1 (full frame): YOLOv8 det -> metamux_1
       |     Stage 3 (ROI of Stage 1): HRNet pose -> metamux_2
       |     Stage 4 (ROI of Stage 1): Inception-v3 class -> metamux_3
       |     -> qtivoverlay (boxes+pose+labels) -> seg_mix (sink_0)
       |- seg leg: Stage 2 DeepLabV3 argmax RGBA mask -> seg_mix (sink_1, alpha)
    -> seg_mix (per-stream composited NV12) -> grid_composer.input(i)

  grid_composer (3x2 tiles) -> VideoFilter(NV12) -> v4l2h264enc -> h264parse
    -> mp4mux -> filesink

Load note: six streams x four SNPE/DSP inference stages = 24 concurrent DSP
inferences plus six HW decode+encode legs. This is a deliberately heavy
stress topology; it is file-to-file so sync=... display rules do not apply.
"""

from qimsdk import (
    Element,
    ImsdkGstLogMode,
    ImsdkLogLevel,
    Pipeline,
    SetImsdkGstLogMode,
    SetImsdkLogLevel,
    TextFilter,
    VideoFilter,
)

NUM_STREAMS = 6

INPUT_FILE = "/etc/mahendra/ai_demo_sample.mp4"
OUTPUT_FILE = "/etc/mahendra/sixstream_daisychain_four_stage_out.mp4"

# Stage 1: object detection (YOLOv8, SNPE DLC)
DET_MODEL = "/etc/mahendra/yolov8_det_quantized.dlc"
DET_LABELS = "/etc/mahendra/yolov8.json"
DET_CONFIDENCE = 51.0

# Stage 2: semantic segmentation (DeepLabV3-Plus-MobileNet, SNPE DLC)
SEG_MODEL = "/etc/mahendra/deeplabv3_plus_mobilenet.dlc"
SEG_LABELS = "/etc/mahendra/deeplab-labels.txt"
SEG_ALPHA = 0.5

# Stage 3: pose estimation on Stage 1 ROIs (HRNet, SNPE DLC)
POSE_MODEL = "/etc/mahendra/hrnet_pose.dlc"
POSE_LABELS = "/etc/mahendra/hrnet.json"
POSE_SETTINGS = "/etc/mahendra/hrnet_settings.json"

# Stage 4: classification on Stage 1 ROIs (Inception-v3, SNPE DLC)
CLASS_MODEL = "/etc/mahendra/inception_v3.dlc"
CLASS_LABELS = "/etc/mahendra/classification.txt"

# Final 3x2 grid geometry (output canvas 1920x1080, cell 640x540).
GRID_COLS = 3
GRID_ROWS = 2
CELL_W = 640
CELL_H = 540


def build_stream(pipeline: Pipeline, i: int) -> str:
    """Add one full per-stream four-stage chain; return its output queue name.

    Every element and stream-filter name is suffixed with the stream index so
    the six copies never collide on a shared default name.
    """

    s = f"_{i}"

    # --- Source / decode ---

    source = Element("filesrc", f"source{s}").set("location", INPUT_FILE)
    demux = Element("qtdemux", f"demux{s}")
    parser = Element("h264parse", f"parser{s}")
    decoder = (
        Element("v4l2h264dec", f"decoder{s}")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    q_dec = Element("queue", f"q_dec{s}")
    vf = VideoFilter().format("NV12")
    main_split = Element("tee", f"main_split{s}")

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter(f"vf{s}", vf)
    pipeline.add(main_split)

    # --- Overlay-chain private buffer copy (shared tee feeds a composer) ---
    q_ov = Element("queue", f"q_ov{s}")
    tf_ov = Element("qtivtransform", f"tf_ov{s}")
    vf_ov = VideoFilter().format("NV12")

    pipeline.add(q_ov)
    pipeline.add(tf_ov)
    pipeline.add_stream_filter(f"vf_ov{s}", vf_ov)

    # --- Stage 1: object detection (YOLOv8, SNPE DSP) ---
    split1 = Element("tee", f"split1{s}")
    q_det = Element("queue", f"q_det{s}")
    det_preproc = (
        Element("qtimlvconverter", f"det_preproc{s}")
        .set("mode", "image-batch-non-cumulative")
    )
    det_infer = (
        Element("qtimlsnpe", f"det_infer{s}")
        .set("model", DET_MODEL)
        .set("delegate", "dsp")
        .set("tensors", ["boxes", "scores", "class_idx"])
    )
    det_post = (
        Element("qtimlpostprocess", f"det_post{s}")
        .set("module", "yolov8")
        .set("labels", DET_LABELS)
        .set("settings", f'{{"confidence": {DET_CONFIDENCE}}}')
    )
    mlf_det = TextFilter()
    metamux1 = Element("qtimetamux", f"metamux_1{s}")

    pipeline.add(split1)
    pipeline.add(q_det)
    pipeline.add(det_preproc)
    pipeline.add(det_infer)
    pipeline.add(det_post)
    pipeline.add_stream_filter(f"mlf_det{s}", mlf_det)
    pipeline.add(metamux1)

    # --- Stage 3: pose estimation on Stage 1 ROIs (HRNet, SNPE DSP) ---
    split2 = Element("tee", f"split2{s}")
    q_pose = Element("queue", f"q_pose{s}")
    pose_preproc = (
        Element("qtimlvconverter", f"pose_preproc{s}")
        .set("mode", "roi-batch-cumulative")
    )
    pose_infer = (
        Element("qtimlsnpe", f"pose_infer{s}")
        .set("model", POSE_MODEL)
        .set("delegate", "dsp")
    )
    pose_post = (
        Element("qtimlpostprocess", f"pose_post{s}")
        .set("module", "hrnet")
        .set("labels", POSE_LABELS)
        .set("settings", POSE_SETTINGS)
        .set("results", 2)
    )
    mlf_pose = TextFilter()
    metamux2 = Element("qtimetamux", f"metamux_2{s}")

    pipeline.add(split2)
    pipeline.add(q_pose)
    pipeline.add(pose_preproc)
    pipeline.add(pose_infer)
    pipeline.add(pose_post)
    pipeline.add_stream_filter(f"mlf_pose{s}", mlf_pose)
    pipeline.add(metamux2)

    # --- Stage 4: classification on Stage 1 ROIs (Inception-v3, SNPE DSP) ---
    split3 = Element("tee", f"split3{s}")
    q_class = Element("queue", f"q_class{s}")
    class_preproc = (
        Element("qtimlvconverter", f"class_preproc{s}")
        .set("mode", "roi-batch-cumulative")
    )
    class_infer = (
        Element("qtimlsnpe", f"class_infer{s}")
        .set("model", CLASS_MODEL)
        .set("delegate", "dsp")
    )
    class_post = (
        Element("qtimlpostprocess", f"class_post{s}")
        .set("module", "mobilenet")
        .set("labels", CLASS_LABELS)
    )
    mlf_class = TextFilter()
    metamux3 = Element("qtimetamux", f"metamux_3{s}")

    pipeline.add(split3)
    pipeline.add(q_class)
    pipeline.add(class_preproc)
    pipeline.add(class_infer)
    pipeline.add(class_post)
    pipeline.add_stream_filter(f"mlf_class{s}", mlf_class)
    pipeline.add(metamux3)

    overlay = Element("qtivoverlay", f"overlay{s}")
    q_ov_out = Element("queue", f"q_ov_out{s}")

    pipeline.add(overlay)
    pipeline.add(q_ov_out)

    # --- Stage 2: semantic segmentation (DeepLabV3-Plus-MobileNet, SNPE DSP) ---
    q_seg = Element("queue", f"q_seg{s}")
    seg_preproc = (
        Element("qtimlvconverter", f"seg_preproc{s}")
        .set("mode", "image-batch-non-cumulative")
    )
    seg_infer = (
        Element("qtimlsnpe", f"seg_infer{s}")
        .set("model", SEG_MODEL)
        .set("delegate", "dsp")
    )
    seg_post = (
        Element("qtimlpostprocess", f"seg_post{s}")
        .set("module", "deeplab-argmax")
        .set("labels", SEG_LABELS)
    )
    seg_mask_vf = VideoFilter().format("RGBA")
    q_seg_mask = Element("queue", f"q_seg_mask{s}")

    pipeline.add(q_seg)
    pipeline.add(seg_preproc)
    pipeline.add(seg_infer)
    pipeline.add(seg_post)
    pipeline.add_stream_filter(f"seg_mask_vf{s}", seg_mask_vf)
    pipeline.add(q_seg_mask)

    # Per-stream composite: alpha-blends the seg mask over the stage 1/3/4 overlay.
    seg_mix = Element("qtivcomposer", f"seg_mix{s}")
    seg_mix_vf = VideoFilter().format("NV12")
    q_seg_out = Element("queue", f"q_seg_out{s}")

    pipeline.add(seg_mix)
    pipeline.add_stream_filter(f"seg_mix_vf{s}", seg_mix_vf)
    pipeline.add(q_seg_out)

    # --- Per-stream linking (identical structure to the single-stream app) ---
    pipeline.link(
        f"source{s}", f"demux{s}", f"parser{s}", f"decoder{s}",
        f"q_dec{s}", f"vf{s}", f"main_split{s}",
    )

    # Private buffer copy for the overlay chain.
    pipeline.link(f"main_split{s}", f"q_ov{s}", f"tf_ov{s}", f"vf_ov{s}", f"split1{s}")

    # Stage 1: passthrough + detection leg merge through metamux_1.
    pipeline.link(f"split1{s}", f"metamux_1{s}")
    pipeline.link(
        f"split1{s}", f"q_det{s}", f"det_preproc{s}", f"det_infer{s}",
        f"det_post{s}", f"mlf_det{s}", f"metamux_1{s}",
    )

    # Stage 3: passthrough + pose ROI leg merge through metamux_2.
    pipeline.link(f"metamux_1{s}", f"split2{s}")
    pipeline.link(f"split2{s}", f"metamux_2{s}")
    pipeline.link(
        f"split2{s}", f"q_pose{s}", f"pose_preproc{s}", f"pose_infer{s}",
        f"pose_post{s}", f"mlf_pose{s}", f"metamux_2{s}",
    )

    # Stage 4: passthrough + classification ROI leg merge through metamux_3.
    pipeline.link(f"metamux_2{s}", f"split3{s}")
    pipeline.link(f"split3{s}", f"metamux_3{s}")
    pipeline.link(
        f"split3{s}", f"q_class{s}", f"class_preproc{s}", f"class_infer{s}",
        f"class_post{s}", f"mlf_class{s}", f"metamux_3{s}",
    )

    # Overlay all three discrete metadata stages, then into seg_mix (sink_0).
    pipeline.link(f"metamux_3{s}", f"overlay{s}", f"q_ov_out{s}", f"seg_mix{s}")

    # Stage 2: segmentation mask renders directly into seg_mix (sink_1).
    pipeline.link(
        f"main_split{s}", f"q_seg{s}", f"seg_preproc{s}", f"seg_infer{s}",
        f"seg_post{s}", f"seg_mask_vf{s}", f"q_seg_mask{s}", f"seg_mix{s}",
    )

    # Per-stream composited output tail (stops at the queue; grid link is global).
    pipeline.link(f"seg_mix{s}", f"seg_mix_vf{s}", f"q_seg_out{s}")

    # Alpha-blend the seg mask (sink_1) over the overlay (sink_0).
    pipeline.get(f"seg_mix{s}").input(1).set("alpha", SEG_ALPHA)

    return f"q_seg_out{s}"


def create_and_execute_pipeline() -> None:
    pipeline = Pipeline("sixstream-daisychain-four-stage")

    # Final grid composer that tiles the six per-stream composited outputs.
    grid = Element("qtivcomposer", "grid_composer")
    grid_vf = VideoFilter().format("NV12")
    q_grid_out = Element("queue", "q_grid_out")

    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    parse_out = Element("h264parse", "parse_out")
    mux = Element("mp4mux", "mux")
    sink = Element("filesink", "sink").set("location", OUTPUT_FILE)

    pipeline.add(grid)
    pipeline.add_stream_filter("grid_vf", grid_vf)
    pipeline.add(q_grid_out)
    pipeline.add(encoder)
    pipeline.add(parse_out)
    pipeline.add(mux)
    pipeline.add(sink)

    # Build the six per-stream chains and link each into the grid composer.
    for i in range(NUM_STREAMS):
        stream_out = build_stream(pipeline, i)
        pipeline.link(stream_out, "grid_composer")

    # Place each stream in its 3x2 grid cell (pad index == link order above).
    for i in range(NUM_STREAMS):
        col = i % GRID_COLS
        row = i // GRID_COLS
        grid.input(i).set("position", [col * CELL_W, row * CELL_H])
        grid.input(i).set("dimensions", [CELL_W, CELL_H])

    # Grid -> encode -> mux -> single output file.
    pipeline.link("grid_composer", "grid_vf", "q_grid_out", "encoder", "parse_out", "mux", "sink")

    # mp4mux needs EOS to finalize the file container.
    pipeline.eos(True)

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
