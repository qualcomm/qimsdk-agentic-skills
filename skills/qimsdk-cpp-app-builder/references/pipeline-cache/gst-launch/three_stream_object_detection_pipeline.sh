#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 1080>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 1080>" ! \
queue ! waylandsink fullscreen=true sync=true \
qtimetamux name=obj_mux ! queue ! tee name=ai_tee \
ai_tee. ! queue ! qtivoverlay ! queue ! comp.sink_1 \
ai_tee. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 ! h264parse ! mp4mux ! \
filesink location=$HOME/media/output/obj_detect_out.mp4 sync=false \
qticamsrc name=camsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! queue ! \
tee name=t_src \
t_src. ! queue ! comp.sink_0 \
t_src. ! queue ! qtimlvconverter ! queue ! \
qtimltflite model=$HOME/models/$MODEL_NAME delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" ! queue ! \
qtimlpostprocess module=yolov8 labels=$HOME/labels/$LABELS_NAME settings="{\"confidence\": 51.0}" bbox-stabilization=true ! text/x-raw ! queue ! obj_mux. \
t_src. ! queue ! obj_mux.
