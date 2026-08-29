#!/bin/sh
gst-launch-1.0 -e --gst-debug=3 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<480, 270>" \
    sink_1::position="<480, 0>" sink_1::dimensions="<480, 270>" \
    sink_2::position="<960, 0>" sink_2::dimensions="<480, 270>" \
    sink_3::position="<1440, 0>" sink_3::dimensions="<480, 270>" \
    sink_4::position="<0, 270>" sink_4::dimensions="<480, 270>" \
    sink_5::position="<480, 270>" sink_5::dimensions="<480, 270>" \
    sink_6::position="<960, 270>" sink_6::dimensions="<480, 270>" \
    sink_7::position="<1440, 270>" sink_7::dimensions="<480, 270>" \
    sink_8::position="<0, 540>" sink_8::dimensions="<480, 270>" \
    sink_9::position="<480, 540>" sink_9::dimensions="<480, 270>" \
    sink_10::position="<960, 540>" sink_10::dimensions="<480, 270>" \
    sink_11::position="<1440, 540>" sink_11::dimensions="<480, 270>" \
    sink_12::position="<0, 810>" sink_12::dimensions="<480, 270>" \
    sink_13::position="<480, 810>" sink_13::dimensions="<480, 270>" \
    sink_14::position="<960, 810>" sink_14::dimensions="<480, 270>" \
    sink_15::position="<1440, 810>" sink_15::dimensions="<480, 270>" ! \
  queue ! waylandsink fullscreen=true \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_0 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_1 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_3 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_4 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_5 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_6 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_7 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_8 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_9 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_10 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_11 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_12 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_13 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_14 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_15
