#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=/etc/mahendra/palm_detector-w8a8-w8a8-w8a8.tflite \
  qtimlpostprocess name=stage_01_postproc results=2 bbox-stabilization=true module=palmd \
  labels=/etc/mahendra/palmd_labels.json settings=/etc/mahendra/palmd_settings.json \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative \
  qtimltflite name=stage_02_inference delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  model=/etc/mahendra/hand_landmark_detector-w8a8-w8a8-w8a8.tflite \
  qtimlpostprocess name=stage_02_1_postproc module=hlandmark \
  labels=/etc/mahendra/hlandmark_labels.json settings=/etc/mahendra/hlandmark_settings.json \
  qtimlpostprocess name=stage_02_2_postproc module=tensor \
  qtimltflite name=stage_03_1_inference delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  model=/etc/mahendra/gesture_embedder.tflite \
  qtimltflite name=stage_03_2_inference delegate=external \
  external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  model=/etc/mahendra/canned_gesture_classifier.tflite \
  qtimlpostprocess name=stage_03_postproc module=mobilenet \
  labels=/etc/mahendra/gesture_labels.json \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! queue ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! qtimetamux name=metamux_1 ! queue ! qtimetatransform module=roi-palmd ! \
  queue ! tee name=t_split_2 \
  t_split_1. ! queue ! stage_01_preproc. \
  stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. \
  stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  t_split_2. ! queue ! qtimetamux name=metamux_2 ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! stage_02_preproc. \
  stage_02_preproc. ! queue ! stage_02_inference. \
  stage_02_inference. ! queue ! tee name=t_split_4 \
  t_split_4. ! queue ! stage_02_1_postproc. stage_02_1_postproc. ! text/x-raw ! metamux_2. \
  t_split_4. ! queue ! stage_02_2_postproc. stage_02_2_postproc. ! queue ! \
  stage_03_1_inference. stage_03_1_inference. ! stage_03_2_inference. \
  stage_03_2_inference. ! stage_03_postproc. stage_03_postproc. ! text/x-raw ! metamux_2.
