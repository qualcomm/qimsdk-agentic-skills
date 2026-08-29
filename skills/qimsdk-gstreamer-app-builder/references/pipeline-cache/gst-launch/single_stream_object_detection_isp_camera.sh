#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
qticamsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t ! qtimetamux name=obj_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=$HOME/labels/$LABELS_NAME settings="{\"confidence\": 51.0}" bbox-stabilization=true ! text/x-raw ! queue ! obj_mux.
