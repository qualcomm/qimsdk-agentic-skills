#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
v4l2src device=/dev/video0 ! video/x-raw,format=YUY2 ! qtivtransform ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=obj_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=$HOME/labels/$LABELS_NAME settings="{\"confidence\": 51.0}" bbox-stabilization=true ! text/x-raw ! queue ! obj_mux.
