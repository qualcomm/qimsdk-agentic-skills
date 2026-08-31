#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 filesrc location=$HOME/media/$SRC_VIDEO_NAME ! qtdemux ! h264parse ! v4l2h264dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! qtimlvconverter ! queue ! qtimltflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so external-delegate-options="QNNExternalDelegate,backend_type=htp;" model=$HOME/models/$MODEL_NAME ! queue ! qtimlpostprocess results=5 module=yolov8 labels=$HOME/labels/$LABELS_NAME ! text/x-raw ! queue ! qtiredissink host=127.0.0.1 port=6379 channel=detections
