#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
  qtimlvconverter name=stage_01_preproc \
  qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=$HOME/models/$MODEL_NAME_1 \
  qtimlpostprocess name=stage_01_postproc module=yolov8 labels=$HOME/labels/$LABELS_NAME_1 \
  settings="{\"confidence\": 51.0}" \
  qtimetamux name=metamux_1 \
  qtivoverlay name=main_overlay \
  qtimlvconverter name=stage_02_preproc \
  qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
  external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
  model=$HOME/models/$MODEL_NAME_2 \
  qtimlpostprocess name=stage_02_postproc module=mobilenet labels=$HOME/labels/$LABELS_NAME_2 \
  settings="{\"confidence\": 51.0}" \
  qtimetamux name=metamux_2 \
  qtivoverlay name=cls_overlay \
  filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t_split_1 \
  t_split_1. ! queue ! metamux_1. \
  t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! \
  stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
  metamux_1. ! queue ! tee name=t_split_2 \
  t_split_2. ! queue ! metamux_2. \
  t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! \
  stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! metamux_2. \
  metamux_2. ! queue ! cls_overlay. cls_overlay. ! queue ! waylandsink sync=true fullscreen=true
