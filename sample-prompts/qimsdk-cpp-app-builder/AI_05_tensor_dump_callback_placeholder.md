Create a QIM SDK C++ app that runs inference and exposes tensor output through a custom tensor callback placeholder.

## Pipeline Behavior
- Decode an MP4 file using Qualcomm hardware decoder
- Run TFLite inference using external delegate on HTP/NPU
- Use custom C++ postprocess with tensor output placeholder logic
- Leave tensor inspection/dump logic as explicit TODO comments
- Write no fake tensor values

## Configuration
- Input:   /root/Downloads/qimsdk_samples/media/Draw_1080p_180s_30FPS.mp4
- Model:   /root/Downloads/qimsdk_samples/models/tensor_debug_model_w8a8.tflite
- Backend: TFLite external delegate, HTP/NPU
- Output:  App-level tensor inspection placeholder
