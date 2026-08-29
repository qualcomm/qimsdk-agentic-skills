# QIM SDK C++ Sample Prompts

These prompts are for the `qimsdk-cpp-app-builder` skill and generate C++ `qti::Pipeline` app artifacts.

## Prompt files

Prompt files are stored directly in this directory. The filename prefix identifies the category, and numbering starts at `01` within each category:

### AI prompts

- `AI_01_mlbin_yolov8_camera_overlay.md`: ML-bin YOLOv8 camera overlay
- `AI_02_mlbin_ppe_daisy_chain_display.md`: ML-bin PPE daisy-chain display
- `AI_03_custom_postprocess_detection_placeholder.md`: custom object-detection postprocess placeholder
- `AI_04_custom_postprocess_pose_placeholder.md`: custom pose postprocess placeholder
- `AI_05_tensor_dump_callback_placeholder.md`: tensor dump / tensor callback placeholder
- `AI_06_yaml_yolov8_generated_config.md`: YAML-driven YOLOv8 app with generated YAML config
- `AI_07_custom_preprocess_detection_placeholder.md`: custom object-detection preprocess placeholder
- `AI_08_mlbin_custom_preprocess_detection_placeholder.md`: ML-bin custom object-detection preprocess placeholder
- `AI_09_yaml_yolov8_external_config.md`: YAML-driven YOLOv8 app with externally provided YAML config
- `AI_10_single_stream_object_detection.md`: single-stream object detection, encode to file
- `AI_11_ai_wall.md`: AI wall (four parallel AI streams composed into a 2x2 grid)
- `AI_12_gesture_recognition.md`: four-stage gesture recognition daisy-chain
- `AI_13_audio_classification.md`: audio classification (YAMNet) overlaid on video

### Multimedia prompt

- `MM_01_appsrc_appsink_bridge.md`: AppSrc/AppSink bridge

Scope note: `qimsdk-cpp-app-builder` supports C++ `qti::Pipeline` apps only.
