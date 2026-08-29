#!/bin/sh
gst-launch-1.0 -e \
pulsesrc do-timestamp=true provide-clock=false volume=10 ! audio/x-raw,format=S16LE,channels=1,rate=48000 ! audioconvert ! queue ! lamemp3enc ! queue ! mpegaudioparse ! queue ! mp4mux name=muxer ! queue ! filesink location=$HOME/media/1080p_AVC_MP3.mp4 \
qticamsrc ! video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1,interlace-mode=progressive,colorimetry=bt601 ! queue ! v4l2h264enc capture-io-mode=4 output-io-mode=5 extra-controls="controls,video_bitrate=1000000,video_gop_size=29;" ! queue ! h264parse ! muxer.
