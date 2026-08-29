#!/bin/sh
gst-launch-1.0 -v pulsesrc volume=10 ! audioconvert ! wavenc ! filesink location=$HOME/media/<Audio_PCM>.wav
