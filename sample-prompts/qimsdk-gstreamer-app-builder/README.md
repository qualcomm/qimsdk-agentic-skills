# QIM SDK GStreamer Sample Prompts

These prompts are for the `qimsdk-gstreamer-app-builder` skill and generate `gst-launch-1.0` commands or C app artifacts.

## Prompt files

Prompt files are stored directly in `c-app/` and `gst-launch/`. The filename prefix identifies the category, and numbering starts at `01` within each category.

### gst-launch/

AI prompts:
- `AI_01_single_stream_object_detection.md`: single-stream object detection
- `AI_02_ai_wall.md`: AI wall (multistream detection, composed view)
- `AI_03_detection_classification_daisy_chain.md`: detection + classification daisy-chain
- `AI_04_gesture_recognition.md`: gesture recognition
- `AI_05_audio_classification.md`: audio classification
- `AI_06_snpe_dsp_object_detection.md`: SNPE DSP object detection
- `AI_07_rtsp_input_yolov8_meta_over_rtsp.md`: RTSP input, YOLOv8 metadata over RTSP
- `AI_08_multistream_face_detection.md`: 2-stream face detection

Multimedia prompts:
- `MM_01_av_record_h264_mp3.md`: AV record, H.264 + MP3
- `MM_02_av_playback_h264_mp3.md`: AV playback, H.264 + MP3

### c-app/

AI prompts:
- `AI_01_single_stream_object_detection.md`: single-stream object detection
- `AI_02_ai_wall.md`: AI wall (multistream detection, composed view)
- `AI_03_detection_classification_daisy_chain.md`: detection + classification daisy-chain
- `AI_04_event_triggered_recording.md`: event-triggered recording
- `AI_05_gesture_recognition.md`: gesture recognition
- `AI_06_audio_classification.md`: audio classification
- `AI_07_smartcodec_detection.md`: smartcodec detection
- `AI_08_event_encoder_conditional_recording.md`: event encoder, conditional recording
- `AI_09_multistream_face_detection.md`: 2-stream face detection

Multimedia prompt:
- `MM_01_av_playback_h264_mp3.md`: AV playback, H.264 + MP3

Prompt generation source material is not included in this repository.

> Scope note: `qimsdk-gstreamer-app-builder` supports `gst-launch-1.0` commands and C apps only.
