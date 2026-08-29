#!/bin/sh
gst-launch-1.0 -e -v \
  qtivcomposer name=mixer \
    sink_0::position="<0,0>" sink_0::dimensions="<1280,720>" \
    sink_1::position="<590,310>" sink_1::dimensions="<640,360>" \
  mixer. ! queue ! tee name=t_split \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 \
      ! queue ! h264parse ! mp4mux ! filesink location=$HOME/media/pip.mp4 \
    t_split. ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 \
      ! queue ! h264parse config-interval=-1 ! rtph264pay pt=96 \
      ! udpsink host=127.0.0.1 port=8554 \
  qticamsrc name=camsrc_0 camera=0 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_0 \
  qticamsrc name=camsrc_1 camera=1 \
    ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
    ! queue ! mixer.sink_1
