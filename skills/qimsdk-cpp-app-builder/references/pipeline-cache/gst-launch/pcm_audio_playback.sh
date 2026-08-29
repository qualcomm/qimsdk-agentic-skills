#!/bin/sh
gst-launch-1.0 -e filesrc location=$HOME/media/<wav_file>.wav ! wavparse ! audioconvert ! pulsesink volume=10
