#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference model=$HOME/models/$MODEL_NAME_1 delegate=gpu \
  qtimlpostprocess name=stage_01_postproc results=1 module=palmd \
  labels=$HOME/labels/$LABELS_NAME_1 settings=$HOME/labels/$LABELS_NAME_2 \
  qtimlvconverter name=stage_02_preproc mode=roi-batch-non-cumulative \
  qtimltflite name=stage_02_inference model=$HOME/models/$MODEL_NAME_2 delegate=gpu \
  qtimlpostprocess name=stage_02_1_postproc results=6 module=hlandmark \
  labels=$HOME/labels/$LABELS_NAME_3 settings=$HOME/labels/$LABELS_NAME_4 \
  qtimlpostprocess name=stage_02_2_postproc results=6 module=tensor \
  qtimltflite name=stage_03_1_inference model=$HOME/models/$MODEL_NAME_3 delegate=gpu \
  qtimltflite name=stage_03_2_inference model=$HOME/models/$MODEL_NAME_4 delegate=gpu \
  qtimlpostprocess name=stage_03_postproc results=8 module=mobilenet labels=$HOME/labels/$LABELS_NAME_5 \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! qtimetamux name=metamux_1 ! queue ! qtimetatransform module=roi-palmd ! \
  queue ! tee name=t_split_2 \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. \
  stage_01_inference. ! queue ! stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  t_split_2. ! queue ! qtimetamux name=metamux_2 ! queue ! qtivoverlay ! waylandsink fullscreen=true sync=false \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. \
  stage_02_inference. ! queue ! tee name=t_split_3 \
  t_split_3. ! queue ! stage_02_1_postproc. stage_02_1_postproc. ! text/x-raw ! metamux_2. \
  t_split_3. ! queue ! stage_02_2_postproc. stage_02_2_postproc. ! queue ! \
  stage_03_1_inference. stage_03_1_inference. ! stage_03_2_inference. \
  stage_03_2_inference. ! stage_03_postproc. stage_03_postproc. ! text/x-raw ! metamux_2.
