#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
qtimlvconverter name=stage_01_preproc mode=image-batch-non-cumulative \
qtimltflite name=stage_01_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp;" model=$HOME/models/$MODEL_NAME_1 \
qtimlpostprocess name=stage_01_postproc results=10 module=qpd labels=$HOME/labels/$LABELS_NAME_1 settings=$HOME/labels/$LABELS_NAME_2 \
qtimlvconverter name=stage_02_preproc image-disposition=centre mode=roi-batch-cumulative \
qtimltflite name=stage_02_inference delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,htp_performance_mode=(string)2;" model=$HOME/models/$MODEL_NAME_2 \
qtimlpostprocess name=stage_02_postproc results=1 module=hrnet labels=$HOME/labels/$LABELS_NAME_2 settings=$HOME/labels/$LABELS_NAME_4 \
qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! tee name=t_split_1 \
t_split_1. ! queue ! metamux_1. \
t_split_1. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! metamux_1. \
qtimetamux name=metamux_1 ! queue ! qtiobjtracker algo=bytetrack ! queue ! tee name=t_split_2 \
t_split_2. ! queue ! metamux_2. \
t_split_2. ! queue ! stage_02_preproc. stage_02_preproc. ! queue ! stage_02_inference. stage_02_inference. ! queue ! stage_02_postproc. stage_02_postproc. ! text/x-raw ! queue ! metamux_2. \
qtimetamux name=metamux_2 ! queue ! qtivoverlay ! queue ! waylandsink fullscreen=true sync=false async=false
