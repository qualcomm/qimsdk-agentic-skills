#!/usr/bin/env python3
"""Single-stream, four-stage AI daisy-chain to a file, all stages on SNPE/DSP.

Stage 1 (full frame): object detection, YOLOv8 (SNPE DLC) on the DSP delegate,
qtimlpostprocess module=yolov8, confidence 51.0.

Stage 2 (full frame, parallel to the ROI stages): semantic segmentation,
DeepLabV3-Plus-MobileNet (SNPE DLC) on the DSP delegate, qtimlpostprocess
module=deeplab-argmax. The rendered RGBA mask is alpha-blended directly onto
a qtivcomposer over Stage 1's (plus Stage 3/4's) detection overlay -- it does
NOT flow through TextFilter/qtimetamux, because it is rendered video, not
text metadata.

Stage 3 (ROI from Stage 1): pose estimation, HRNet (SNPE DLC) on the DSP
delegate, qtimlpostprocess module=hrnet. Consumes Stage 1's YOLOv8 detection
ROIs directly (top-down/ROI mode) -- Stage 1's detector IS the upstream
detector for this stage; no extra person-detector stage is inserted.

Stage 4 (ROI from Stage 1, parallel to Stage 3): image classification,
Inception-v3 (SNPE DLC) on the DSP delegate, qtimlpostprocess module=mobilenet.
Also consumes Stage 1's ROIs directly; does not consume Stage 3's output.

Stages 3 and 4 are independent ROI consumers of Stage 1, not a strict linear
chain. They are implemented as two sequential tee/qtimetamux metadata-merge
stages chained after Stage 1's metadata merge (each stage only reads Stage 1's
ROI metadata off the shared stream; neither reads the other's output), then a
single qtivoverlay renders all three discrete metadata stages (boxes, pose
keypoints, classification labels) together before the segmentation blend.

All four stage outputs are composited into one final video, encoded, and
written to a single output file.
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


def create_and_execute_pipeline() -> None:

    # --- Source / decode ---

    source = Element("filesrc", "source").set("location", INPUT_FILE)
    demux = Element("qtdemux", "demux")
    parser = Element("h264parse", "parser")

    # Decodes H.264 using the Qualcomm hardware decoder with DMA IO modes.
    decoder = (
        Element("v4l2h264dec", "decoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )

    # Queue immediately after hardware decode, required before any filter/tee.
    q_dec = Element("queue", "q_dec")

    # Normalizes decoded frames to NV12 before the single input is teed out.
    vf = VideoFilter().format("NV12")

    # Single-input tee shared by the discrete-overlay chain (stages 1/3/4)
    # and the segmentation-to-composer chain (stage 2).
    main_split = Element("tee", "main_split")

    pipeline = Pipeline("sixstream-daisychain-four-stage")
    pipeline.add(source)
    pipeline.add(demux)
    pipeline.add(parser)
    pipeline.add(decoder)
    pipeline.add(q_dec)
    pipeline.add_stream_filter("vf", vf)
    pipeline.add(main_split)

    # --- Overlay-chain passthrough copy ---
    #
    # main_split is shared with the segmentation branch, which ultimately
    # feeds the seg_mix qtivcomposer below. A qtivcomposer anywhere downstream
    # of a shared tee holds that tee's buffers for stream sync, which makes
    # qtimetamux's in-place metadata write below silently draw nothing unless
    # this leg gets its own private buffer copy first.
    q_ov = Element("queue", "q_ov")
    tf_ov = Element("qtivtransform", "tf_ov")
    vf_ov = VideoFilter().format("NV12")

    pipeline.add(q_ov)
    pipeline.add(tf_ov)
    pipeline.add_stream_filter("vf_ov", vf_ov)

    # --- Stage 1: object detection (YOLOv8, SNPE DSP) ---

    split1 = Element("tee", "split1")
    q_det = Element("queue", "q_det")
    det_preproc = (
        Element("qtimlvconverter", "det_preproc")
        .set("mode", "image-batch-non-cumulative")
    )
    det_infer = (
        Element("qtimlsnpe", "det_infer")
        .set("model", DET_MODEL)
        .set("delegate", "dsp")
        # This DLC bakes NMS in and exports 3 separate output tensors; without
        # this filter qtimlsnpe forwards only the first tensor and
        # qtimlpostprocess module=yolov8 fails caps negotiation.
        .set("tensors", ["boxes", "scores", "class_idx"])
    )
    det_post = (
        Element("qtimlpostprocess", "det_post")
        .set("module", "yolov8")
        .set("labels", DET_LABELS)
        .set("settings", f'{{"confidence": {DET_CONFIDENCE}}}')
    )
    mlf_det = TextFilter()
    metamux1 = Element("qtimetamux", "metamux_1")

    pipeline.add(split1)
    pipeline.add(q_det)
    pipeline.add(det_preproc)
    pipeline.add(det_infer)
    pipeline.add(det_post)
    pipeline.add_stream_filter("mlf_det", mlf_det)
    pipeline.add(metamux1)

    # --- Stage 3: pose estimation on Stage 1 ROIs (HRNet, SNPE DSP) ---
    #
    # Independent ROI consumer of Stage 1's detections, not a stage-2 of a
    # linear chain; wired as its own tee/qtimetamux layer so its metadata
    # merges back into the shared stream without touching Stage 4's branch.

    split2 = Element("tee", "split2")
    q_pose = Element("queue", "q_pose")
    pose_preproc = (
        Element("qtimlvconverter", "pose_preproc")
        .set("mode", "roi-batch-cumulative")
    )
    pose_infer = (
        Element("qtimlsnpe", "pose_infer")
        .set("model", POSE_MODEL)
        .set("delegate", "dsp")
    )
    pose_post = (
        Element("qtimlpostprocess", "pose_post")
        .set("module", "hrnet")
        .set("labels", POSE_LABELS)
        .set("settings", POSE_SETTINGS)
        .set("results", 2)
    )
    mlf_pose = TextFilter()
    metamux2 = Element("qtimetamux", "metamux_2")

    pipeline.add(split2)
    pipeline.add(q_pose)
    pipeline.add(pose_preproc)
    pipeline.add(pose_infer)
    pipeline.add(pose_post)
    pipeline.add_stream_filter("mlf_pose", mlf_pose)
    pipeline.add(metamux2)

    # --- Stage 4: classification on Stage 1 ROIs (Inception-v3, SNPE DSP) ---
    #
    # Also an independent ROI consumer of Stage 1's detections (parallel to
    # Stage 3, not fed by Stage 3's output); its own tee/qtimetamux layer.

    split3 = Element("tee", "split3")
    q_class = Element("queue", "q_class")
    class_preproc = (
        Element("qtimlvconverter", "class_preproc")
        .set("mode", "roi-batch-cumulative")
    )
    class_infer = (
        Element("qtimlsnpe", "class_infer")
        .set("model", CLASS_MODEL)
        .set("delegate", "dsp")
    )
    class_post = (
        Element("qtimlpostprocess", "class_post")
        .set("module", "mobilenet")
        .set("labels", CLASS_LABELS)
    )
    mlf_class = TextFilter()
    metamux3 = Element("qtimetamux", "metamux_3")

    pipeline.add(split3)
    pipeline.add(q_class)
    pipeline.add(class_preproc)
    pipeline.add(class_infer)
    pipeline.add(class_post)
    pipeline.add_stream_filter("mlf_class", mlf_class)
    pipeline.add(metamux3)

    # Renders Stage 1 boxes, Stage 3 pose keypoints, and Stage 4 classification
    # labels together over the video frame.
    overlay = Element("qtivoverlay", "overlay")
    q_ov_out = Element("queue", "q_ov_out")

    pipeline.add(overlay)
    pipeline.add(q_ov_out)

    # --- Stage 2: semantic segmentation (DeepLabV3-Plus-MobileNet, SNPE DSP) ---
    #
    # Segmentation renders an RGBA mask, not text metadata, so it uses the
    # direct-to-composer topology instead of qtimetamux/qtivoverlay.

    q_seg = Element("queue", "q_seg")
    seg_preproc = (
        Element("qtimlvconverter", "seg_preproc")
        .set("mode", "image-batch-non-cumulative")
    )
    seg_infer = (
        Element("qtimlsnpe", "seg_infer")
        .set("model", SEG_MODEL)
        .set("delegate", "dsp")
    )
    seg_post = (
        Element("qtimlpostprocess", "seg_post")
        .set("module", "deeplab-argmax")
        .set("labels", SEG_LABELS)
    )
    # qtimlpostprocess only emits {RGBA, RGBx} for image-mask output (BGRA
    # fails to link). Resolution is left unpinned: the seg_mix composer below
    # has no per-pad `dimensions` sizing this branch (it is a full-frame
    # alpha blend, not a tile), so pinning would be unnecessary; if it did
    # have pad geometry, pinning here would fail caps fixation.
    seg_mask_vf = VideoFilter().format("RGBA")
    q_seg_mask = Element("queue", "q_seg_mask")

    pipeline.add(q_seg)
    pipeline.add(seg_preproc)
    pipeline.add(seg_infer)
    pipeline.add(seg_post)
    pipeline.add_stream_filter("seg_mask_vf", seg_mask_vf)
    pipeline.add(q_seg_mask)

    # Alpha-blends the segmentation mask over the stage 1/3/4 overlay result.
    seg_mix = Element("qtivcomposer", "seg_mix")
    seg_mix_vf = VideoFilter().format("NV12")
    q_seg_out = Element("queue", "q_seg_out")

    pipeline.add(seg_mix)
    pipeline.add_stream_filter("seg_mix_vf", seg_mix_vf)
    pipeline.add(q_seg_out)

    # --- Encode and write the single composited output file ---

    encoder = (
        Element("v4l2h264enc", "encoder")
        .set("capture-io-mode", 4)
        .set("output-io-mode", 4)
    )
    parse_out = Element("h264parse", "parse_out")
    mux = Element("mp4mux", "mux")
    sink = Element("filesink", "sink").set("location", OUTPUT_FILE)

    pipeline.add(encoder)
    pipeline.add(parse_out)
    pipeline.add(mux)
    pipeline.add(sink)

    # --- Linking ---

    pipeline.link("source", "demux", "parser", "decoder", "q_dec", "vf", "main_split")

    # Private buffer copy for the overlay chain (see comment above).
    pipeline.link("main_split", "q_ov", "tf_ov", "vf_ov", "split1")

    # Stage 1: passthrough + detection leg merge through metamux_1.
    pipeline.link("split1", "metamux_1")
    pipeline.link(
        "split1", "q_det", "det_preproc", "det_infer", "det_post",
        "mlf_det", "metamux_1",
    )

    # Stage 3: passthrough + pose ROI leg (from Stage 1 ROIs) merge through
    # metamux_2.
    pipeline.link("metamux_1", "split2")
    pipeline.link("split2", "metamux_2")
    pipeline.link(
        "split2", "q_pose", "pose_preproc", "pose_infer", "pose_post",
        "mlf_pose", "metamux_2",
    )

    # Stage 4: passthrough + classification ROI leg (from Stage 1 ROIs,
    # independent of Stage 3) merge through metamux_3.
    pipeline.link("metamux_2", "split3")
    pipeline.link("split3", "metamux_3")
    pipeline.link(
        "split3", "q_class", "class_preproc", "class_infer", "class_post",
        "mlf_class", "metamux_3",
    )

    # Overlay boxes + pose keypoints + classification labels, then hand off
    # to the segmentation composer as the base/passthrough layer.
    pipeline.link("metamux_3", "overlay", "q_ov_out", "seg_mix")

    # Stage 2: segmentation branch renders its own mask directly into seg_mix.
    pipeline.link(
        "main_split", "q_seg", "seg_preproc", "seg_infer", "seg_post",
        "seg_mask_vf", "q_seg_mask", "seg_mix",
    )

    # Composited output -> encode -> mux -> file.
    pipeline.link("seg_mix", "seg_mix_vf", "q_seg_out", "encoder", "parse_out", "mux", "sink")

    # Alpha-blends the segmentation mask (sink_1) over the stage 1/3/4
    # overlay (sink_0) on seg_mix.
    seg_mix_el = pipeline.get("seg_mix")
    seg_mix_el.input(1).set("alpha", SEG_ALPHA)

    # mp4mux needs EOS to finalize the file container.
    pipeline.eos(True)

    pipeline.execute()


def main() -> None:
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
