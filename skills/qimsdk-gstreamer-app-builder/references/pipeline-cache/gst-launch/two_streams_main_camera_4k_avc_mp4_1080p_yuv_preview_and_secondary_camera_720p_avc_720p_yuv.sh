#!/bin/sh
gst-launch-1.0 -e \
  qticamsrc name=camsrc_0 camera=0 video_0::type=video video_1::type=preview \
  camsrc_0. ! video/x-raw,format=NV12,width=3840,height=2160,framerate=30/1 \
      ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 \
      ! queue ! h264parse ! mp4mux ! filesink location=$HOME/media/main_4k.mp4 \
  camsrc_0. ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1 \
      ! queue ! waylandsink sync=false \
  qticamsrc name=camsrc_1 camera=1 video_0::type=video video_1::type=preview \
  camsrc_1. ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
      ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 \
      ! queue ! h264parse ! mp4mux ! filesink location=$HOME/media/secondary_720p.mp4 \
  camsrc_1. ! video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 \
      ! queue ! filesink location=$HOME/media/secondary_720p.yuv
