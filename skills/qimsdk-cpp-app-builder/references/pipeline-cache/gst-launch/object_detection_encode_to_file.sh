#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t ! qtimetamux name=obj_mux ! qtivoverlay ! v4l2h264enc capture-io-mode=4 output-io-mode=4 ! h264parse ! mp4mux ! filesink location=$HOME/media/output/obj_detect_out.mp4 \
t. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=$HOME/labels/$LABELS_NAME settings="{\"confidence\": 51.0}" bbox-stabilization=true ! text/x-raw ! queue ! obj_mux.
