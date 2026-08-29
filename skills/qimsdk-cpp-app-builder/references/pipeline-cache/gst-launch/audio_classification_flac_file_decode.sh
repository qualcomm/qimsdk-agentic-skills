#!/bin/sh
gst-launch-1.0 -e filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux name=demux demux. ! queue ! h264parse ! \
v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw, format=NV12 ! qtivcomposer name=mixer sink_1::position="<50, 50>" sink_1::dimensions="<368, 64>" ! \
queue ! waylandsink fullscreen=true demux. ! queue ! flacparse ! flacdec ! queue ! audioconvert ! audioresample ! \
audiobuffersplit output-buffer-size=31200 ! queue ! qtimlaconverter  sample-rate=16000 feature=lmfe params="params,nfft=96,nhop=160,nmels=64,chunklen=0.96;" ! \
queue ! qtimltflite name=infeng model=$HOME/models/$MODEL_NAME ! qtimlpostprocess settings="{\"confidence\": 10.0}" results=3 module=yamnet \
labels=$HOME/labels/$LABELS_NAME ! queue ! mixer.
