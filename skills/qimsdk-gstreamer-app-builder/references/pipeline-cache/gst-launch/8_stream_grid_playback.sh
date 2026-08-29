#!/bin/sh
gst-launch-1.0 -e --gst-debug=3 \
  qtivcomposer name=comp \
    sink_0::position="<0, 0>" sink_0::dimensions="<480, 540>" \
    sink_1::position="<480, 0>" sink_1::dimensions="<480, 540>" \
    sink_2::position="<960, 0>" sink_2::dimensions="<480, 540>" \
    sink_3::position="<1440, 0>" sink_3::dimensions="<480, 540>" \
    sink_4::position="<0, 540>" sink_4::dimensions="<480, 540>" \
    sink_5::position="<480, 540>" sink_5::dimensions="<480, 540>" \
    sink_6::position="<960, 540>" sink_6::dimensions="<480, 540>" \
    sink_7::position="<1440, 540>" sink_7::dimensions="<480, 540>" ! \
  queue ! waylandsink fullscreen=true \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_0 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_1 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_2 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_3 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_4 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_5 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_6 \
  filesrc location=$HOME/media/video.mp4 ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! comp.sink_7
