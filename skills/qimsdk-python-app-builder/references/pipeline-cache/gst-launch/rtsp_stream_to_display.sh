#!/bin/sh
gst-launch-1.0 \
  rtspsrc location={file_path} latency=200 ! rtph264depay ! h264parse ! \
  v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! \
  waylandsink fullscreen=true
