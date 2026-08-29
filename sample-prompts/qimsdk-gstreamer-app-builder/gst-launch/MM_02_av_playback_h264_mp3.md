Create a QIM SDK gst-launch-1.0 command for an audio-video playback pipeline that plays back an MP4 file containing H.264 video and MP3 audio, displaying video on screen and playing audio through the speaker.

## Pipeline Behavior
- Read an MP4 file containing H.264 video and MP3 audio tracks
- Demux into separate video and audio streams using a named demuxer
- Decode H.264 video using Qualcomm hardware decoder and display fullscreen
- Decode MP3 audio using software decoder and play through PulseAudio speaker

## Configuration
- Input:    $HOME/Downloads/qimsdk_samples/media/<AV_file_with_H264_video_and_MP3_audio>.mp4
- Video:    H.264 decode → Wayland display, fullscreen
- Audio:    MP3 decode → PulseAudio speaker, volume 10

## Prerequisites
Run before executing: wpctl set-default <audio-node-number>
