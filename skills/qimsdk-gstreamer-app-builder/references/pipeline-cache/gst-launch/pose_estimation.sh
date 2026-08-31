#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 \
  settings=$HOME/labels/foot_track_net_settings.json \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-cumulative image-disposition=centre \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2,log_level=(string)1;" \
  model=$HOME/models/$MODEL_NAME_2 \
  qtimlpostprocess name=stage_02_postproc results=2 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 \
  settings=$HOME/labels/hrnet_settings.json \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_1 \
  t_split_1. ! queue ! metamux_1. metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! qtimetamux name=metamux_2 \
  metamux_2. ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=true \
  t_split_2. ! queue ! metamux_2.
