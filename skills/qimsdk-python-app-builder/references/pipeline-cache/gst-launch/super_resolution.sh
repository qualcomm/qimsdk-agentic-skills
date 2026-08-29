#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=t \
t. ! queue ! qtivcomposer name=mixer sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! queue ! waylandsink fullscreen=true sync=true \
t. ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=srnet ! video/x-raw,format=RGB ! queue ! mixer.
