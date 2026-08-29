#!/bin/sh
gst-launch-1.0 --gst-debug=2 \
qtimlvconverter name=stage_01_preproc \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" name=stage_01_inference \
qtimlpostprocess name=stage_01_postproc results=10 module=yolov8 labels=$HOME/labels/$LABELS_NAME \
qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! tee name=t \
t. ! queue ! metamux. \
t. ! queue ! stage_01_preproc. stage_01_preproc. ! queue ! stage_01_inference. stage_01_inference. ! queue ! stage_01_postproc. stage_01_postproc. ! text/x-raw ! queue ! metamux. \
qtimetamux name=metamux ! queue ! qtiobjtracker algo=bytetrack ! queue ! qtimlmetaparser module=json ! queue ! qtiredissink host=127.0.0.1 port=6379 channel=ml_results
