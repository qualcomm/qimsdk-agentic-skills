#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# 6-stream pose estimation: same source file decoded 6 times, each stream runs
# the full person-detect -> ROI-crop -> HRNet two-stage cascade independently,
# all 6 overlaid results composed into a single 3x2 grid and encoded to one
# output file. Stage 2 uses the QNN native .bin runtime (qtimlqnn) rather than
# qtimltflite external-delegate — use this form when the user supplies a
# pre-compiled QNN .bin model for the pose stage instead of a .tflite.
#
# Running HRNet directly on a full, uncropped frame (no stage-1 detector) is
# a documented alternative (see model-catalog.md's HRNetPose row) but on
# device produces near-noise keypoints at ~5-8% confidence that a correctly
# configured settings/confidence threshold silently discards to zero overlay
# output — "0 errors, correct geometry, nothing drawn" is the symptom of that
# mismatch, not a plumbing bug. The two-stage cascade below is required for
# usable keypoints; see ai-pipeline-patterns.md "## 2b) Daisy-Chain Pose
# Estimation" for when to pick this over the single-stage Route A form.

gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc_0 \
  qtimltflite name=stage_01_inference_0 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_0 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_0 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_0 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_0 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_0 \
  qtimetamux name=metamux_2_0 \
  qtivoverlay name=ovl_0 \
  qtimlvconverter name=stage_01_preproc_1 \
  qtimltflite name=stage_01_inference_1 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_1 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_1 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_1 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_1 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_1 \
  qtimetamux name=metamux_2_1 \
  qtivoverlay name=ovl_1 \
  qtimlvconverter name=stage_01_preproc_2 \
  qtimltflite name=stage_01_inference_2 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_2 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_2 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_2 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_2 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_2 \
  qtimetamux name=metamux_2_2 \
  qtivoverlay name=ovl_2 \
  qtimlvconverter name=stage_01_preproc_3 \
  qtimltflite name=stage_01_inference_3 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_3 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_3 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_3 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_3 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_3 \
  qtimetamux name=metamux_2_3 \
  qtivoverlay name=ovl_3 \
  qtimlvconverter name=stage_01_preproc_4 \
  qtimltflite name=stage_01_inference_4 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_4 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_4 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_4 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_4 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_4 \
  qtimetamux name=metamux_2_4 \
  qtivoverlay name=ovl_4 \
  qtimlvconverter name=stage_01_preproc_5 \
  qtimltflite name=stage_01_inference_5 delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
    external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
    model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc_5 results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
    settings=$HOME/labels/$SETTINGS_NAME_1 \
  qtimlvconverter name=stage_02_preproc_5 mode=roi-batch-cumulative image-disposition=centre \
  qtimlqnn name=stage_02_inference_5 model=$HOME/models/$MODEL_NAME_2 \
    backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so \
  qtimlpostprocess name=stage_02_postproc_5 results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
    settings=$HOME/labels/$SETTINGS_NAME_2 \
  qtimetamux name=metamux_1_5 \
  qtimetamux name=metamux_2_5 \
  qtivoverlay name=ovl_5 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<640, 360>" \
    sink_1::position="<640, 0>" sink_1::dimensions="<640, 360>" \
    sink_2::position="<1280, 0>" sink_2::dimensions="<640, 360>" \
    sink_3::position="<0, 360>" sink_3::dimensions="<640, 360>" \
    sink_4::position="<640, 360>" sink_4::dimensions="<640, 360>" \
    sink_5::position="<1280, 360>" sink_5::dimensions="<640, 360>" ! \
  video/x-raw,format=NV12 ! queue ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! \
  filesink location=$HOME/media/output/$OUTPUT_NAME \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_0 \
  t_split_1_0. ! queue ! metamux_1_0. \
  t_split_1_0. ! queue ! stage_01_preproc_0. stage_01_preproc_0. ! queue ! stage_01_inference_0. stage_01_inference_0. ! queue ! \
    stage_01_postproc_0. stage_01_postproc_0. ! text/x-raw ! queue ! metamux_1_0. \
  metamux_1_0. ! queue ! tee name=t_split_2_0 \
  t_split_2_0. ! queue ! metamux_2_0. \
  t_split_2_0. ! queue ! stage_02_preproc_0. stage_02_preproc_0. ! queue ! stage_02_inference_0. stage_02_inference_0. ! queue ! \
    stage_02_postproc_0. stage_02_postproc_0. ! text/x-raw ! queue ! metamux_2_0. \
  metamux_2_0. ! queue ! ovl_0. ovl_0. ! queue ! comp.sink_0 \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_1 \
  t_split_1_1. ! queue ! metamux_1_1. \
  t_split_1_1. ! queue ! stage_01_preproc_1. stage_01_preproc_1. ! queue ! stage_01_inference_1. stage_01_inference_1. ! queue ! \
    stage_01_postproc_1. stage_01_postproc_1. ! text/x-raw ! queue ! metamux_1_1. \
  metamux_1_1. ! queue ! tee name=t_split_2_1 \
  t_split_2_1. ! queue ! metamux_2_1. \
  t_split_2_1. ! queue ! stage_02_preproc_1. stage_02_preproc_1. ! queue ! stage_02_inference_1. stage_02_inference_1. ! queue ! \
    stage_02_postproc_1. stage_02_postproc_1. ! text/x-raw ! queue ! metamux_2_1. \
  metamux_2_1. ! queue ! ovl_1. ovl_1. ! queue ! comp.sink_1 \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_2 \
  t_split_1_2. ! queue ! metamux_1_2. \
  t_split_1_2. ! queue ! stage_01_preproc_2. stage_01_preproc_2. ! queue ! stage_01_inference_2. stage_01_inference_2. ! queue ! \
    stage_01_postproc_2. stage_01_postproc_2. ! text/x-raw ! queue ! metamux_1_2. \
  metamux_1_2. ! queue ! tee name=t_split_2_2 \
  t_split_2_2. ! queue ! metamux_2_2. \
  t_split_2_2. ! queue ! stage_02_preproc_2. stage_02_preproc_2. ! queue ! stage_02_inference_2. stage_02_inference_2. ! queue ! \
    stage_02_postproc_2. stage_02_postproc_2. ! text/x-raw ! queue ! metamux_2_2. \
  metamux_2_2. ! queue ! ovl_2. ovl_2. ! queue ! comp.sink_2 \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_3 \
  t_split_1_3. ! queue ! metamux_1_3. \
  t_split_1_3. ! queue ! stage_01_preproc_3. stage_01_preproc_3. ! queue ! stage_01_inference_3. stage_01_inference_3. ! queue ! \
    stage_01_postproc_3. stage_01_postproc_3. ! text/x-raw ! queue ! metamux_1_3. \
  metamux_1_3. ! queue ! tee name=t_split_2_3 \
  t_split_2_3. ! queue ! metamux_2_3. \
  t_split_2_3. ! queue ! stage_02_preproc_3. stage_02_preproc_3. ! queue ! stage_02_inference_3. stage_02_inference_3. ! queue ! \
    stage_02_postproc_3. stage_02_postproc_3. ! text/x-raw ! queue ! metamux_2_3. \
  metamux_2_3. ! queue ! ovl_3. ovl_3. ! queue ! comp.sink_3 \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_4 \
  t_split_1_4. ! queue ! metamux_1_4. \
  t_split_1_4. ! queue ! stage_01_preproc_4. stage_01_preproc_4. ! queue ! stage_01_inference_4. stage_01_inference_4. ! queue ! \
    stage_01_postproc_4. stage_01_postproc_4. ! text/x-raw ! queue ! metamux_1_4. \
  metamux_1_4. ! queue ! tee name=t_split_2_4 \
  t_split_2_4. ! queue ! metamux_2_4. \
  t_split_2_4. ! queue ! stage_02_preproc_4. stage_02_preproc_4. ! queue ! stage_02_inference_4. stage_02_inference_4. ! queue ! \
    stage_02_postproc_4. stage_02_postproc_4. ! text/x-raw ! queue ! metamux_2_4. \
  metamux_2_4. ! queue ! ovl_4. ovl_4. ! queue ! comp.sink_4 \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
    v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
    tee name=t_split_1_5 \
  t_split_1_5. ! queue ! metamux_1_5. \
  t_split_1_5. ! queue ! stage_01_preproc_5. stage_01_preproc_5. ! queue ! stage_01_inference_5. stage_01_inference_5. ! queue ! \
    stage_01_postproc_5. stage_01_postproc_5. ! text/x-raw ! queue ! metamux_1_5. \
  metamux_1_5. ! queue ! tee name=t_split_2_5 \
  t_split_2_5. ! queue ! metamux_2_5. \
  t_split_2_5. ! queue ! stage_02_preproc_5. stage_02_preproc_5. ! queue ! stage_02_inference_5. stage_02_inference_5. ! queue ! \
    stage_02_postproc_5. stage_02_postproc_5. ! text/x-raw ! queue ! metamux_2_5. \
  metamux_2_5. ! queue ! ovl_5. ovl_5. ! queue ! comp.sink_5
