#!/usr/bin/env python3
"""6-stream HRNet pose estimation: six independent decodes of the same
source file, each running full-frame HRNet pose inference directly (no
detection/ROI-crop stage), composited into one 3x2 grid, and encoded to a
single output file.

Each of the six streams runs the identical single-stage pipeline:

  source/decode -> NV12 -> qtimlqnn (HRNet pose, QNN native cached-binary
  runtime, backend=/usr/lib/libQnnHtp.so, system=/usr/lib/libQnnSystem.so) ->
  qtimlpostprocess (module=hrnet, results=2) -> qtivoverlay.

Per the model catalog (Pose Estimation section), HRNetPose exports a single
native output tensor on both the SNPE and QNN cached-binary runtimes, so the
qtimlqnn element intentionally carries NO `tensors=` filter -- unlike
multi-head/NMS models (e.g. YOLOv8) that require one.

All six overlaid outputs feed one shared qtivcomposer (3x2 grid, 640x360
cells), which is hardware-encoded and muxed to a single output MP4
(file-to-file; no display sink).
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

SRC_VIDEO = "/etc/ai_demo_sample.mp4"
OUTPUT_FILE = "/etc/6stream_hrnet_pose_qnn_output.mp4"

# HRNet pose estimation (qtimlqnn, native QNN cached-binary runtime).
MODEL = "/etc/hrnet_pose_w8a8.bin"
LABELS = "/etc/hrnet.json"
SETTINGS = "/etc/hrnet_settings.json"

QNN_BACKEND = "/usr/lib/libQnnHtp.so"
QNN_SYSTEM = "/usr/lib/libQnnSystem.so"

# Composed 3x2 grid, 640x360 cells (matches the reference script's
# explicit sink_0..sink_5 position/dimensions values).
GRID_COLS = 3
CELL_W = 640
CELL_H = 360


def build_stream(pipeline: Pipeline, i: int) -> str:
    """Add one full single-stage HRNet pipeline; return the overlay-output
    queue name so the caller can link it into the shared grid composer.
    """

    # --- Source / decode (independent decode of the same source file) ---
    source = Element("filesrc", f"source_{i}").set("location", SRC_VIDEO)
    demux = Element("qtdemux", f"demux_{i}")
    parser = Element("h264parse", f"parser_{i}")
    decoder = (
        Element("v4l2h264dec", f"decoder_{i}")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    q_dec = Element("queue", f"q_dec_{i}")
    vf = VideoFilter().format("NV12")

    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter(f"vf_{i}", vf)

    # --- Split: passthrough vs. HRNet pose branch ---
    t_split = Element("tee", f"t_split_{i}")
    q_pass = Element("queue", f"q_pass_{i}")
    q_ai_pre = Element("queue", f"q_ai_pre_{i}")

    preproc = Element("qtimlvconverter", f"preproc_{i}")
    q_ai_mid1 = Element("queue", f"q_ai_mid1_{i}")
    # HRNet pose on the QNN native cached-binary runtime. HRNetPose exports a
    # single native output tensor on this runtime (model-catalog.md, Pose
    # Estimation section) -- do NOT add a `tensors=` filter here.
    infer = (
        Element("qtimlqnn", f"inference_{i}")
        .set("model", MODEL)
        .set("backend", QNN_BACKEND)
        .set("system", QNN_SYSTEM)
    )
    q_ai_mid2 = Element("queue", f"q_ai_mid2_{i}")
    post = (
        Element("qtimlpostprocess", f"postproc_{i}")
        .set("module", "hrnet")
        .set("results", 2)
        .set("labels", LABELS)
        .set("settings", SETTINGS)
    )
    mlf = TextFilter()
    q_ai_post = Element("queue", f"q_ai_post_{i}")

    metamux = Element("qtimetamux", f"metamux_{i}")
    q_metamux_out = Element("queue", f"q_metamux_out_{i}")

    overlay = Element("qtivoverlay", f"ovl_{i}")
    q_ovl_out = Element("queue", f"q_ovl_out_{i}")

    pipeline.add(t_split)
    pipeline.add(q_pass)
    pipeline.add(q_ai_pre)
    pipeline.add(preproc)
    pipeline.add(q_ai_mid1)
    pipeline.add(infer)
    pipeline.add(q_ai_mid2)
    pipeline.add(post)
    pipeline.add_stream_filter(f"mlf_{i}", mlf)
    pipeline.add(q_ai_post)
    pipeline.add(metamux)
    pipeline.add(q_metamux_out)
    pipeline.add(overlay)
    pipeline.add(q_ovl_out)

    # --- Linking ---
    pipeline.link(
        f"source_{i}", f"demux_{i}", f"parser_{i}", f"decoder_{i}",
        f"q_dec_{i}", f"vf_{i}", f"t_split_{i}",
    )

    # Passthrough leg + HRNet pose leg merge through metamux.
    pipeline.link(f"t_split_{i}", f"q_pass_{i}", f"metamux_{i}")
    pipeline.link(
        f"t_split_{i}", f"q_ai_pre_{i}", f"preproc_{i}",
        f"q_ai_mid1_{i}", f"inference_{i}",
        f"q_ai_mid2_{i}", f"postproc_{i}",
        f"mlf_{i}", f"q_ai_post_{i}", f"metamux_{i}",
    )

    # Overlay the merged pose skeleton, then feed this stream's grid tile.
    pipeline.link(
        f"metamux_{i}", f"q_metamux_out_{i}", f"ovl_{i}", f"q_ovl_out_{i}",
    )

    return f"q_ovl_out_{i}"


def create_and_execute_pipeline() -> None:
    pipeline = Pipeline("sixstream-hrnet-pose-qnn")

    # Shared 3x2 grid composer that tiles all six overlaid streams.
    composer = Element("qtivcomposer", "comp")
    comp_vf = VideoFilter().format("NV12")
    q_comp_out = Element("queue", "q_comp_out")

    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    parse_out = Element("h264parse", "parse_out")
    mux = Element("mp4mux", "mux")
    sink = Element("filesink", "sink").set("location", OUTPUT_FILE)

    pipeline.add(composer)
    pipeline.add_stream_filter("comp_vf", comp_vf)
    pipeline.add(q_comp_out)
    pipeline.add(encoder)
    pipeline.add(parse_out)
    pipeline.add(mux)
    pipeline.add(sink)

    # Build the six independent per-stream pipelines and feed the composer.
    for i in range(NUM_STREAMS):
        stream_out = build_stream(pipeline, i)
        pipeline.link(stream_out, "comp")

    # Place each stream in its 3x2 grid cell (pad index == link order above).
    for i in range(NUM_STREAMS):
        col = i % GRID_COLS
        row = i // GRID_COLS
        composer.input(i).set("position", [col * CELL_W, row * CELL_H])
        composer.input(i).set("dimensions", [CELL_W, CELL_H])

    # Composed grid -> hardware encode -> mux -> single output file.
    pipeline.link("comp", "comp_vf", "q_comp_out", "encoder", "parse_out", "mux", "sink")

    # mp4mux needs EOS to finalize the file container.
    pipeline.eos(True)

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
