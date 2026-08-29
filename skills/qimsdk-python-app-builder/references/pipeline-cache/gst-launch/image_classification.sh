#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=class_mux ! qtivoverlay ! waylandsink fullscreen=true sync=true \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=mobilenet labels=$HOME/labels/$LABELS_NAME settings="{\"confidence\": 51.0}" ! text/x-raw ! queue ! class_mux.
