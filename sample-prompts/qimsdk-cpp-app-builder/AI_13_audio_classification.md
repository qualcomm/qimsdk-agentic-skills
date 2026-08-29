Create a QIM SDK C++ app for an audio classification pipeline that classifies audio events from a video file and overlays results on the video.

## Pipeline Behavior
- Decode an MP4 file containing H.264 video and FLAC audio using Qualcomm hardware decoder
- Process video and audio in parallel:
  - Video path: decode and pass through to display
  - Audio path: decode FLAC audio, convert to feature representation, run YAMNet classification
- Overlay audio classification labels on the video and render to display

## Configuration
- Input:        /root/Downloads/qimsdk_samples/media/H264_720p_30fps_FLAC.mp4
- Model:        /root/Downloads/qimsdk_samples/models/yamnet.tflite
- Labels:       /root/Downloads/qimsdk_samples/labels/yamnet.json
- Confidence:   10.0
- Results:      3
- Sample rate:  16000
- Feature:      lmfe
- Audio params: nfft=96, nhop=160, nmels=64, chunklen=0.96
- Buffer size:  31200
