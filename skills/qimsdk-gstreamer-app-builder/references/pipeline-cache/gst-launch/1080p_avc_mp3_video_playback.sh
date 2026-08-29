#!/bin/sh
gst-launch-1.0 -e filesrc location=$HOME/media/<AV_file_with_H264_video_and_MP3_audio>.mp4 ! qtdemux name=demux demux. ! queue ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! waylandsink fullscreen=true demux. ! queue ! mpegaudioparse ! mpg123audiodec ! pulsesink volume=10
