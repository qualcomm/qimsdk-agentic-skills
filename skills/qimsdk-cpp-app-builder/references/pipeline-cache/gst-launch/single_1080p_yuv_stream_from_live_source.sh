#!/bin/sh
gst-launch-1.0 \
  qticamsrc name=camsrc ! \
  video/x-raw,width=1920,height=1080,framerate=30/1 ! \
  waylandsink fullscreen=true
