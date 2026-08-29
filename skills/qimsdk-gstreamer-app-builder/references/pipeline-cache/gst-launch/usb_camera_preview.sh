#!/bin/sh
gst-launch-1.0 \
  v4l2src device=/dev/video0 ! \
  video/x-raw,format=YUY2 ! \
  qtivtransform ! video/x-raw,format=NV12 ! \
  waylandsink fullscreen=true
