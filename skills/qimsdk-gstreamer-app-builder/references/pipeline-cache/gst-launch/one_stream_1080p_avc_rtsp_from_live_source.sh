#!/bin/sh
## 1. Start RTSP Server (in background or separate terminal)
gst-rtsp-server -p 8900 -m /live "( udpsrc name=pay0 port=8554 caps=\"application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96\" )" &

## 2. Run Pipeline
gst-launch-1.0 -e \
  qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 ! \
  v4l2h264enc capture-io-mode=4 output-io-mode=5 ! \
  h264parse config-interval=-1 ! rtph264pay pt=96 ! \
  udpsink host=127.0.0.1 port=8554
