# Sample Prompts

Skill-specific prompt sets:

- `sample-prompts/qimsdk-gstreamer-app-builder/`
- `sample-prompts/qimsdk-cpp-app-builder/`
- `sample-prompts/qimsdk-python-app-builder/`

## Current structure

### qimsdk-gstreamer-app-builder

- `gst-launch/` (flat, no subfolders: 8 `AI_NN_*.md` prompts + 2 `MM_NN_*.md` prompts)
- `c-app/` (flat, no subfolders: 9 `AI_NN_*.md` prompts + 1 `MM_NN_*.md` prompt)
- scope note: `qimsdk-gstreamer-app-builder` is `gst-launch + C only`

### qimsdk-cpp-app-builder

- flat, no subfolders: 9 `AI_NN_*.md` prompts covering file/camera detection, ML-bin, daisy-chain, PPE, custom preprocess/postprocess placeholders, tensor callback, and YAML config generated/external modes, plus 1 `MM_NN_*.md` prompt (AppSrc/AppSink bridge)

### qimsdk-python-app-builder

- flat, no subfolders: 9 `AI_NN_*.md` prompts covering file/camera detection, ML-bin, daisy-chain, PPE, custom preprocess/postprocess placeholders, tensor callback, and YAML config generated/external modes, plus 1 `MM_NN_*.md` prompt (AppSrc/AppSink bridge)
