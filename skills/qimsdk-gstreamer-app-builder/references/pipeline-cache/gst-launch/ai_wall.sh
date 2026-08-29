#!/bin/sh
gst-launch-1.0 -e --gst-debug=2 \
qtimlvconverter name=class_pre \
qtimltflite name=class_infer model=$HOME/models/$MODEL_NAME_1 delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=class_post results=5 module=mobilenet labels=$HOME/labels/$LABELS_NAME_1 settings="{\"confidence\": 51.0}" \
qtimetamux name=class_mux \
qtivoverlay name=class_overlay \
qtimlvconverter name=face_pre \
qtimltflite name=face_infer model=$HOME/models/$MODEL_NAME_2 delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=face_post module=qfd results=6 labels=$HOME/labels/$LABELS_NAME_2 \
qtimetamux name=face_mux \
qtivoverlay name=face_overlay \
qtimlvconverter name=seg_pre \
qtimltflite name=seg_infer model=$HOME/models/$MODEL_NAME_3 delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=seg_post module=deeplab-argmax labels=$HOME/labels/$LABELS_NAME_3 \
qtivcomposer name=seg_mix sink_1::alpha=0.5 \
qtimlvconverter name=obj_pre \
qtimltflite name=obj_infer model=$HOME/models/$MODEL_NAME_4 delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp,log_level=(string)1;" \
qtimlpostprocess name=obj_post module=yolov8 labels=$HOME/labels/$LABELS_NAME_4 settings="{\"confidence\": 51.0}" \
qtimetamux name=obj_mux \
qtivcomposer name=comp \
  sink_0::position="<0, 0>" sink_0::dimensions="<960, 540>" \
  sink_1::position="<960, 0>" sink_1::dimensions="<960, 540>" \
  sink_2::position="<0, 540>" sink_2::dimensions="<960, 540>" \
  sink_3::position="<960, 540>" sink_3::dimensions="<960, 540>" ! \
queue ! waylandsink fullscreen=true sync=true \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=class_tee \
class_tee. ! queue ! class_mux. \
class_tee. ! queue ! class_pre. class_pre. ! queue ! class_infer. class_infer. ! queue ! class_post. class_post. ! text/x-raw ! queue ! class_mux. \
class_mux. ! queue ! class_overlay. class_overlay. ! queue ! comp.sink_0 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=face_tee \
face_tee. ! queue ! face_mux. \
face_tee. ! queue ! face_pre. face_pre. ! queue ! face_infer. face_infer. ! queue ! face_post. face_post. ! text/x-raw ! queue ! face_mux. \
face_mux. ! queue ! face_overlay. face_overlay. ! queue ! comp.sink_1 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=seg_tee \
seg_tee. ! queue ! seg_mix. \
seg_tee. ! queue ! seg_pre. seg_pre. ! queue ! seg_infer. seg_infer. ! queue ! seg_post. seg_post. ! queue ! seg_mix. \
seg_mix. ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
tee name=obj_tee \
obj_tee. ! queue ! obj_mux. \
obj_tee. ! queue ! obj_pre. obj_pre. ! queue ! obj_infer. obj_infer. ! queue ! obj_post. obj_post. ! text/x-raw ! queue ! obj_mux. \
obj_mux. ! queue ! qtivoverlay ! queue ! comp.sink_3
