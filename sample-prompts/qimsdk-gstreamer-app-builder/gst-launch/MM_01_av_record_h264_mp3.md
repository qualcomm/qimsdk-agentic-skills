Create a QIM SDK gst-launch-1.0 command for an audio-video record pipeline that captures 1080p video from the ISP camera and audio from the microphone and muxes them into a single MP4 file.

## Pipeline Behavior
- Capture 1080p NV12 video from ISP camera
- Capture audio from the system microphone via PulseAudio
- Encode video to H.264 using hardware encoder
- Encode audio to MP3 using software encoder
- Mux both streams into a single MP4 container

## Configuration
- Camera:        ISP camera 0 (qtiqmmfsrc), 1920x1080 @ 30fps, interlace-mode=progressive, colorimetry=bt601
- Audio source:  PulseAudio microphone (pulsesrc), do-timestamp=true, provide-clock=false, volume 10
- Audio format:  S16LE, channels=1, rate=48000
- Video codec:   H.264 (v4l2h264enc), capture-io-mode=4, output-io-mode=5, bitrate=1000000 bps, GOP=29
- Audio codec:   MP3 (lamemp3enc)
- Output:        $HOME/Downloads/qimsdk_samples/media/1080p_AVC_MP3.mp4

## Prerequisites
Run before executing: wpctl set-default <audio-node-number>
