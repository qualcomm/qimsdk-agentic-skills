#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

set -euo pipefail

artifact_dir="${1:-}"

if [[ -z "$artifact_dir" ]]; then
  echo "usage: verify-cpp-app.sh <artifact-dir>" >&2
  exit 2
fi

main="$artifact_dir/main.cc"
cmake="$artifact_dir/CMakeLists.txt"
readme="$artifact_dir/README.md"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

count_matches() {
  grep -c "$1" "$main" || true
}

has_multistream_topology() {
  grep -Eiq 'multistream|multi[-_ ]stream|ai[[:space:]_-]*wall|num[_-]?streams?|stream[_-]?count|source[_-]?count|streams?[[:space:]]*[=:][[:space:]]*[2-9]' "$main" && return 0
  # Do not infer concurrency from source literals in runtime if/else alternatives.
  grep -Eiq '\b(if|else|elif|switch|case)\b' "$main" && return 1
  local source_count
  source_count="$(grep -Eo '"(filesrc|rtspsrc|qticamsrc|qtiqmmfsrc|v4l2src)"' "$main" | wc -l || true)"
  (( source_count > 1 ))
}

readme_setup_block_valid() {
  local need_pulse="$1" need_gpu="$2" need_ulimit="$3" need_wayland="$4"
  awk -v pulse="$need_pulse" -v gpu="$need_gpu" -v limit="$need_ulimit" -v wayland="$need_wayland" '
    function check_block(    p, q, g, u, w, last, applicable) {
      if (pulse && block !~ /wpctl[[:space:]]+status/) return 0
      if (pulse && block !~ /wpctl[[:space:]]+set-default[[:space:]]+<node_no[.]>/) return 0
      if (gpu && block !~ /OCL_ICD_FILENAMES/) return 0
      if (limit && block !~ /ulimit[[:space:]]+-n[[:space:]]+10000/) return 0
      if (wayland && block !~ /WS=\$\(find/) return 0
      if (wayland && (block !~ /XDG_RUNTIME_DIR/ || block !~ /WAYLAND_DISPLAY/)) return 0
      applicable = pulse + gpu + limit + wayland
      if (applicable > 1 && block !~ /&&/) return 0
      last = 0
      if (pulse) { p = index(block, "wpctl status"); q = index(block, "wpctl set-default"); if (p == 0 || q <= p) return 0; last = q }
      if (gpu) { g = index(block, "OCL_ICD_FILENAMES"); if (g <= last) return 0; last = g }
      if (limit) { u = index(block, "ulimit -n 10000"); if (u <= last) return 0; last = u }
      if (wayland) { w = index(block, "WS=$(find"); if (w <= last) return 0 }
      return 1
    }
    /^```bash[[:space:]]*$/ { in_block = 1; block = ""; next }
    in_block && /^```[[:space:]]*$/ { if (check_block()) found = 1; in_block = 0; next }
    in_block { block = block $0 "\n" }
    END { exit(found ? 0 : 1) }
  ' "$readme"
}

[[ -d "$artifact_dir" ]] || fail "artifact directory does not exist: $artifact_dir"
[[ -f "$main" ]] || fail "missing main.cc"
[[ -f "$cmake" ]] || fail "missing CMakeLists.txt"
[[ -f "$readme" ]] || fail "missing README.md"

grep -q "#include <qti/qimsdk.h>" "$main" || fail "main.cc must include umbrella header <qti/qimsdk.h>"
! grep -q "#include <qti/qimsdk-" "$main" || fail "main.cc must not include individual qti qimsdk headers"
! grep -q "gst_element_factory_make\\|gst_sample_apps_utils" "$main" || fail "C++ app must not use raw C-app GStreamer scaffolding"

grep -q "^## Pipeline Flow" "$readme" || fail "README.md missing '## Pipeline Flow' section"
grep -q "^### Text Summary" "$readme" || fail "README.md missing '### Text Summary' heading"
grep -q "^### Mermaid Diagram" "$readme" || fail "README.md missing '### Mermaid Diagram' heading"
grep -q "^## Steps to Compile" "$readme" || fail "README.md missing '## Steps to Compile' section"
grep -q "Yocto: https://imsdkdocs.qualcomm.com/advanced/yocto-build#steps-to-build-custom-application" "$readme" \
  || fail "README.md Steps to Compile must contain the exact Yocto build link"
grep -q "^## Steps to Run on QLI" "$readme" || fail "README.md missing '## Steps to Run on QLI' section"
! grep -q "PIPELINE_FLOW" "$readme" || fail "README.md should use 'Pipeline Flow', not 'PIPELINE_FLOW'"
! grep -q "^## Run[[:space:]]*$" "$readme" || fail "README.md should use '## Steps to Run on QLI', not '## Run'"
! grep -Eq "^## Steps to Run[[:space:]]*$" "$readme" || fail "README.md should use '## Steps to Run on QLI', not '## Steps to Run'"
grep -q '```mermaid' "$readme" || fail "README.md Mermaid Diagram must include a fenced Mermaid diagram"
grep -Eq "flowchart (LR|TD)" "$readme" || fail "README.md Mermaid diagram must declare flowchart LR or flowchart TD"
grep -q "qimsdk-app-builder" "$cmake" || fail "CMakeLists.txt must link qimsdk-app-builder"
grep -q "main.cc" "$cmake" || fail "CMakeLists.txt must build main.cc"

if grep -q "filesink\\|mp4mux" "$main"; then
  grep -q "\\.eos(true)" "$main" || fail "file/mux output should call pipeline.eos(true)"
fi

grep -q "^scp .*root@<device-ip>:/root/" "$readme" \
  || fail "README.md Steps to Run on QLI must include an scp command copying the binary to root@<device-ip>:/root/"
grep -q "^ssh root@<device-ip>" "$readme" \
  || fail "README.md Steps to Run on QLI must include an ssh command into root@<device-ip>"
grep -q "chmod +x /root/" "$readme" \
  || fail "README.md Steps to Run on QLI must include a chmod +x command for the deployed binary"
grep -Eq "^\\./[A-Za-z0-9_.-]+[[:space:]]*$" "$readme" \
  || fail "README.md Steps to Run on QLI must include a ./<binary-name> run command line"
grep -q "present on the device" "$readme" \
  || fail "README.md Steps to Run on QLI must state that models/labels/media files are present on the device"

need_pulse=0
need_gpu=0
need_ulimit=0
need_wayland=0
grep -q "pulsesrc\|pulsesink" "$main" && need_pulse=1
grep -Eq '\.set\("delegate", *"gpu"\)|\.set\("inference-delegate", *"gpu"\)|delegate=gpu' "$main" && need_gpu=1
has_multistream_topology && need_ulimit=1
grep -q "waylandsink" "$main" && need_wayland=1
if (( need_pulse )); then
  grep -q "wpctl status" "$readme" \
    || fail "README.md Steps to Run on QLI must include 'wpctl status' for pulsesrc/pulsesink apps"
  grep -q "wpctl set-default <node_no.>" "$readme" \
    || fail "README.md Steps to Run on QLI must include 'wpctl set-default <node_no.>' for pulsesrc/pulsesink apps"
fi
if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  readme_setup_block_valid "$need_pulse" "$need_gpu" "$need_ulimit" "$need_wayland" || fail "README.md must put applicable runtime setup in one ordered &&-chained bash block"
fi

# Deployment step ordering: scp (host) -> ssh (enter device) -> env-setup block
# (on-device, if applicable) -> chmod +x -> run. The env-setup block must never
# appear before scp/ssh, since it has to execute in the device's shell.
scp_line="$(grep -n "^scp .*root@<device-ip>:/root/" "$readme" | head -1 | cut -d: -f1 || true)"
ssh_line="$(grep -n "^ssh root@<device-ip>" "$readme" | head -1 | cut -d: -f1 || true)"
chmod_line="$(grep -n "chmod +x /root/" "$readme" | head -1 | cut -d: -f1 || true)"
[[ -n "$scp_line" && -n "$ssh_line" && "$ssh_line" -gt "$scp_line" ]] \
  || fail "README.md Steps to Run on QLI must ssh after scp, not before"
[[ -n "$ssh_line" && -n "$chmod_line" && "$chmod_line" -gt "$ssh_line" ]] \
  || fail "README.md Steps to Run on QLI must chmod +x after ssh (on the device), not before"
if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  env_marker_line="$(grep -nE "OCL_ICD_FILENAMES|ulimit[[:space:]]+-n[[:space:]]+10000|wpctl[[:space:]]+status|WS=\\\$\\(find" "$readme" | head -1 | cut -d: -f1 || true)"
  [[ -n "$env_marker_line" && "$env_marker_line" -gt "$ssh_line" ]] \
    || fail "README.md Steps to Run on QLI env-setup block must run on the device (after ssh), not on the host before scp"
  [[ -n "$env_marker_line" && "$env_marker_line" -lt "$chmod_line" ]] \
    || fail "README.md Steps to Run on QLI env-setup block must run before chmod +x/the binary run command"
fi

if grep -Eq "Pipeline[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\\([^,]+,[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\\)|qti::Pipeline[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\\([^,]+,[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\\)" "$main"; then
  yaml_files="$(find "$artifact_dir" -type f \( -name '*.yaml' -o -name '*.yml' \) | sort)"
  if [[ -z "$yaml_files" ]]; then
    grep -q "External YAML provided by user" "$readme" \
      || fail "YAML config constructor artifacts must include a generated YAML file, unless README says 'External YAML provided by user'"
  else
    while IFS= read -r yaml_file; do
      grep -q "^pipeline:" "$yaml_file" \
        || fail "generated YAML file must have a top-level pipeline: mapping: $yaml_file"
      ! grep -q "^[[:space:]]*factory:" "$yaml_file" \
        || fail "generated YAML must use element type: keys, not factory: keys: $yaml_file"
      ! grep -q "^[[:space:]]*properties:" "$yaml_file" \
        || fail "generated YAML must use flat element properties, not nested properties: mappings: $yaml_file"
    done <<< "$yaml_files"
    grep -Eq "\\.ya?ml" "$readme" \
      || fail "README.md must list the generated YAML config file"
  fi
fi

if grep -q "qtimetamux" "$main"; then
  grep -q "TextFilter" "$main" || fail "qtimetamux metadata flows should include TextFilter"
  ! grep -Eq "MLVideo[A-Za-z]*Bin|qtimlvideo[a-z]*bin" "$main" || ! grep -q "tee" "$main" || ! grep -q "TextFilter" "$main" \
    || fail "ML-bin stages must not be wired into tee/TextFilter/qtimetamux metadata fan-in topologies; keep ML-bin directly in the media path, or use discrete qtimlvconverter/qtimltflite/qtimlpostprocess stages for metadata-merge flows"
  main_compact="$(tr '\n' ' ' < "$main")"
  folded_branch_re='\.link\([^)]*"(split|tee)[^"]*"[^)]*"metamux[^"]*"[^)]*"overlay"'
  if [[ "$main_compact" =~ $folded_branch_re ]]; then
    fail "tee/qtimetamux fan-in branch links must stop at qtimetamux; link qtimetamux -> overlay/display separately"
  fi
fi

! grep -Eq "\\.add\\([[:space:]]*(qti::)?(VideoFilter|TextFilter|TensorFilter|ImageFilter|H264Filter|AudioFilter|StreamFilter)\\(" "$main" \
  || fail "stream filter objects must be added with add_stream_filter(\"name\", filter_obj), not add(...)"
! grep -Eq "\\.add\\([[:space:]]*(vf[0-9_]*|mlf[0-9_]*|tf[0-9_]*|[A-Za-z_][A-Za-z0-9_]*(filter|text|tensor|caps|stream)[A-Za-z0-9_]*)[[:space:]]*\\)" "$main" \
  || fail "stream filter variables must be added with add_stream_filter(\"name\", filter_obj), not add(...)"
! grep -Eq "\\.add_stream_filter\\([[:space:]]*[A-Za-z_:][A-Za-z0-9_:]*[[:space:]]*\\)" "$main" \
  || fail "C++ add_stream_filter requires a unique name and filter instance: add_stream_filter(\"name\", filter_obj)"

if grep -q "qtivoverlay" "$main"; then
  converter_count="$(count_matches "qtimlvconverter")"
  inference_count="$(count_matches "qtimltflite\\|qtimlsnpe\\|qtimlqnn\\|qtimlonnx")"
  post_count="$(count_matches "qtimlpostprocess")"
  metamux_count="$(count_matches "qtimetamux")"
  text_filter_count="$(count_matches "TextFilter")"

  if (( converter_count >= 1 && inference_count >= 1 && post_count >= 1 )); then
    grep -Eq "\\.add\\([\"']tee[\"']" "$main" \
      || fail "discrete AI overlay must preserve a main video branch with tee"
    (( metamux_count >= 1 )) \
      || fail "discrete AI overlay must merge video and metadata with qtimetamux"
    (( text_filter_count >= 1 )) \
      || fail "discrete AI overlay must use TextFilter before qtimetamux metadata input"
  fi

  if (( converter_count >= 2 && inference_count >= 2 && post_count >= 2 )); then
    (( metamux_count >= 2 )) \
      || fail "two-stage discrete overlay must use two qtimetamux stages to merge stage-1 and stage-2 metadata"
    (( text_filter_count >= 2 )) \
      || fail "two-stage discrete overlay must use TextFilter before each qtimetamux metadata input"
  fi
fi

if grep -Eq "palmd|hand_detector|hlandmark|hand_landmarks|gesture_embedder|canned_gesture_classifier" "$main"; then
  grep -q "qtimetatransform" "$main" \
    || fail "gesture-recognition pipelines must include qtimetatransform between palm metadata and landmark ROI processing"
  grep -q "roi-palmd" "$main" \
    || fail "gesture-recognition pipelines must set qtimetatransform module roi-palmd"
  grep -q "hlandmark" "$main" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module hlandmark for hand landmarks"
  grep -q "tensor" "$main" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module tensor before gesture embedding"
  grep -q "mobilenet" "$main" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module mobilenet for gesture classification output"
  grep -Eq "stage_03_1_inference|gesture_embedder" "$main" \
    || fail "gesture-recognition pipelines must include the gesture embedder inference stage"
  grep -Eq "stage_03_2_inference|canned_gesture_classifier" "$main" \
    || fail "gesture-recognition pipelines must include the gesture classifier inference stage"
  gesture_metamux_count="$(count_matches "qtimetamux")"
  (( gesture_metamux_count == 2 )) \
    || fail "gesture-recognition pipelines must use exactly two qtimetamux stages, not a generic per-stage mux cascade"
fi

! grep -Eq "QNNExecurorBackend:HTP|QNNExecutorBackend:HTP|QNNExternalDelegateBackend:HTP" "$main" "$readme" \
  || fail "invalid QNN delegate option string; use QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
! grep -q "QNNExternalDelegate,backend_type=htp;" "$main" "$readme" \
  || fail "HTP delegate options must include log_level=(string)1"
! grep -Eq "confidence_threshold|confidence-threshold|confidence=[0-9]|confidence=\\{" "$main" "$readme" \
  || fail "confidence settings must use JSON key confidence, for example {\"confidence\": 50.0}"
! grep -Eq "settings.*confidence=.*;" "$main" "$readme" \
  || fail "qtimlpostprocess settings must use JSON, not semicolon-delimited confidence settings"

if grep -Eq "qticamsrc|qtiqmmfsrc|rtspsrc" "$main" && grep -Eq "module\".*, *\"(yolov8|yolov5|yolo-nas|qfd|palmd)\"" "$main"; then
  grep -q "bbox-stabilization" "$main" \
    || fail "live-source object/face/palm detection should set bbox-stabilization"
fi

if grep -q "MLVConverter\\|set_preprocess_handler" "$main"; then
  grep -Eq "TODO:|Placeholder" "$main" "$readme" \
    || fail "custom preprocess placeholders must include TODOs in main.cc or README.md"
  if grep -q "MLVConverter" "$main"; then
    grep -Eq "engine.*none|engine\", *\"none\"" "$main" \
      || fail "custom preprocess MLVConverter must explicitly set engine=\"none\" before set_handler"
  fi
  if grep -q "set_preprocess_handler" "$main"; then
    grep -q "preprocess-engine" "$main" \
      || fail "ML-bin custom preprocess must set preprocess-engine=\"none\" before set_preprocess_handler"
  fi
  if grep -Eq "TODO:|Placeholder" "$main" && grep -q "return false;" "$main"; then
    grep -Eq "return true|returns true|must return true" "$main" "$readme" \
      || fail "custom preprocess placeholders must explain changing return false to return true after writing a valid tensor"
  fi
fi

! grep -Eq "\\b(appsrc|src)\\.set_handler\\(" "$main" \
  || fail "AppSrc has no set_handler; use set_buffer_producer(...)"
! grep -Eq "\\b(appsink|sink)\\.set_handler\\(" "$main" \
  || fail "AppSink has no set_handler; use set_buffer_consumer(...)"
! grep -Eq "setBufferProducer|setBufferConsumer|setEnoughHandler|setPrerollHandler|setEosHandler|pushBuffer|endOfStream" "$main" \
  || fail "C++ AppSrc/AppSink APIs use snake_case names such as set_buffer_consumer and push_buffer"

if grep -q "MLPostprocess\\|set_postprocess_handler" "$main"; then
  grep -Eq "MLDetections|MLPoses|MLDepthMaps|MLSegmentations|MLClassifications|MLFrame" "$main" \
    || fail "custom postprocess callback should use SDK ML output types"
  grep -Eq "return true;|return false;" "$main" \
    || fail "custom postprocess callbacks must return bool explicitly"
fi

! grep -q "\\.width(" "$main" || fail "VideoFilter.width() is not a documented API"
! grep -q "\\.height(" "$main" || fail "VideoFilter.height() is not a documented API"

echo "PASS: qimsdk C++ artifact checks passed"
