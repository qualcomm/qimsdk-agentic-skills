Create a QIM SDK C++ app for a four-stage gesture recognition daisy-chain pipeline.

## Pipeline Behavior
- Capture live video from ISP camera
- Stage 1: Run palm detection on full frames using GPU
- Stage 2: Run hand landmark detection on palm ROIs from Stage 1 using GPU
- Stage 3: Run gesture embedding on hand landmarks from Stage 2 using GPU
- Stage 4: Run gesture classification on embeddings from Stage 3 using GPU
- Overlay hand keypoints and recognized gesture labels on the video and render to display

## Configuration
- Input:   ISP camera (qtiqmmfsrc), 1920x1080 @ 30fps
- Backend: GPU delegate (all stages)
- Output:  Wayland display, fullscreen

Stage 1 — Palm Detection:
- Model:    /root/Downloads/qimsdk_samples/models/hand_detector.tflite
- Labels:   /root/Downloads/qimsdk_samples/labels/palmd_labels.json
- Settings: /root/Downloads/qimsdk_samples/labels/palmd_settings.json
- Results:  1

Stage 2 — Hand Landmark Detection (ROI-based):
- Model:    /root/Downloads/qimsdk_samples/models/hand_landmarks_detector.tflite
- Labels:   /root/Downloads/qimsdk_samples/labels/hlandmark_labels.json
- Settings: /root/Downloads/qimsdk_samples/labels/hlandmark_settings.json
- Results:  6
- Mode:     roi-batch-non-cumulative

Stage 3 — Gesture Embedding:
- Model:    /root/Downloads/qimsdk_samples/models/gesture_embedder.tflite

Stage 4 — Gesture Classification:
- Model:    /root/Downloads/qimsdk_samples/models/canned_gesture_classifier.tflite
- Labels:   /root/Downloads/qimsdk_samples/labels/gesture_labels.json
- Results:  8
