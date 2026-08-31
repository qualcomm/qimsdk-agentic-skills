#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

set -euo pipefail

artifact_dir="${1:-}"

if [[ -z "$artifact_dir" ]]; then
  echo "usage: verify-python-app.sh <artifact-dir>" >&2
  exit 2
fi

app="$artifact_dir/main.py"
readme="$artifact_dir/README.md"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

count_matches() {
  grep -c "$1" "$app" || true
}


has_multistream_topology() {
  grep -Eiq 'multistream|multi[-_ ]stream|ai[[:space:]_-]*wall|num[_-]?streams?|stream[_-]?count|source[_-]?count|streams?[[:space:]]*[=:][[:space:]]*[2-9]' "$app" && return 0
  grep -Eiq '\b(if|else|elif|switch|case)\b' "$app" && return 1
  local source_count
  source_count="$(grep -Eo '"(filesrc|rtspsrc|qticamsrc|qtiqmmfsrc|v4l2src)"' "$app" | wc -l || true)"
  (( source_count > 1 ))
}

readme_setup_block_valid() {
  local pulse="$1" gpu="$2" limit="$3" wayland="$4"
  awk -v pulse="$pulse" -v gpu="$gpu" -v limit="$limit" -v wayland="$wayland" '
    function valid(    p,q,g,u,w,applicable) {
      if (pulse && block !~ /wpctl[[:space:]]+status/) return 0
      if (pulse && block !~ /wpctl[[:space:]]+set-default[[:space:]]+<node_no[.]>/) return 0
      if (gpu && block !~ /OCL_ICD_FILENAMES/) return 0
      if (limit && block !~ /ulimit[[:space:]]+-n[[:space:]]+10000/) return 0
      if (wayland && block !~ /WS=\$\(find/) return 0
      if (wayland && (block !~ /XDG_RUNTIME_DIR/ || block !~ /WAYLAND_DISPLAY/)) return 0
      applicable = pulse + gpu + limit + wayland
      if (applicable > 1 && block !~ /&&/) return 0
      p=index(block,"wpctl status"); q=index(block,"wpctl set-default"); g=index(block,"OCL_ICD_FILENAMES"); u=index(block,"ulimit -n 10000"); w=index(block,"WS=$(find")
      if (pulse && (q <= p || p == 0)) return 0
      if (gpu && g <= (pulse ? q : 0)) return 0
      if (limit && u <= (gpu ? g : (pulse ? q : 0))) return 0
      if (wayland && w <= (limit ? u : (gpu ? g : (pulse ? q : 0)))) return 0
      return 1
    }
    /^```bash[[:space:]]*$/ { in_block=1; block=""; next }
    in_block && /^```[[:space:]]*$/ { if (valid()) found=1; in_block=0; next }
    in_block { block=block $0 "\n" }
    END { exit(found ? 0 : 1) }
  ' "$readme"
}

[[ -d "$artifact_dir" ]] || fail "artifact directory does not exist: $artifact_dir"
[[ -f "$app" ]] || fail "missing main.py"
[[ -f "$readme" ]] || fail "missing README.md"
[[ ! -f "$artifact_dir/CMakeLists.txt" ]] || fail "Python artifact must not include CMakeLists.txt"

grep -q "from qimsdk import" "$app" || fail "main.py must import from public qimsdk surface"
! grep -q "from qimsdk\\._" "$app" || fail "main.py must not import qimsdk internals"
! grep -q "import qimsdk\\._" "$app" || fail "main.py must not import qimsdk internals"
! grep -q "gi.repository.Gst" "$app" || fail "generated app should not bypass qimsdk with raw Gst imports"

grep -q "^## Pipeline Flow" "$readme" || fail "README.md missing '## Pipeline Flow' section"
grep -q "^### Text Summary" "$readme" || fail "README.md missing '### Text Summary' heading"
grep -q "^### Mermaid Diagram" "$readme" || fail "README.md missing '### Mermaid Diagram' heading"
grep -q "^## Steps to Run on QLI" "$readme" || fail "README.md missing '## Steps to Run on QLI' section"
! grep -q "PIPELINE_FLOW" "$readme" || fail "README.md should use 'Pipeline Flow', not 'PIPELINE_FLOW'"
! grep -q "^## Run[[:space:]]*$" "$readme" || fail "README.md should use '## Steps to Run on QLI', not '## Run'"
! grep -Eq "^## Steps to Run[[:space:]]*$" "$readme" || fail "README.md should use '## Steps to Run on QLI', not '## Steps to Run'"
grep -q '```mermaid' "$readme" || fail "README.md Mermaid Diagram must include a fenced Mermaid diagram"
grep -Eq "flowchart (LR|TD)" "$readme" || fail "README.md Mermaid diagram must declare flowchart LR or flowchart TD"
grep -Eq "^def create_and_execute_pipeline\\(" "$app" || fail "main.py must define create_and_execute_pipeline(...)"
grep -Eq "^def main\\(\\) -> None:|^def main\\(\\):" "$app" || fail "main.py must define main()"
grep -q 'if __name__ == "__main__"' "$app" || fail "main.py must include __main__ guard"
grep -Eq "^[[:space:]]+pipeline\\.execute\\(" "$app" || fail "pipeline.execute() should be inside create_and_execute_pipeline(...)"
grep -q "SetImsdkLogLevel(ImsdkLogLevel.Debug)" "$app" \
  || fail "generated apps should default to ImsdkLogLevel.Debug unless user overrides"

if grep -q "filesink\\|mp4mux" "$app"; then
  grep -q "\\.eos(True)" "$app" || fail "file/mux output should call pipeline.eos(True)"
fi

need_pulse=0
need_gpu=0
need_ulimit=0
need_wayland=0
grep -q "pulsesrc\|pulsesink" "$app" && need_pulse=1
grep -Eq '\.set\("delegate", *"gpu"\)|\.set\("inference-delegate", *"gpu"\)|delegate=gpu' "$app" && need_gpu=1
has_multistream_topology && need_ulimit=1
grep -q "waylandsink" "$app" && need_wayland=1
if (( need_pulse )); then
  grep -q "wpctl status" "$readme" \
    || fail "README.md Steps to Run on QLI must include 'wpctl status' for pulsesrc/pulsesink apps"
  grep -q "wpctl set-default <node_no.>" "$readme" \
    || fail "README.md Steps to Run on QLI must include 'wpctl set-default <node_no.>' for pulsesrc/pulsesink apps"
fi
if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  readme_setup_block_valid "$need_pulse" "$need_gpu" "$need_ulimit" "$need_wayland" || fail "README.md must put applicable runtime setup in one ordered &&-chained bash block"
fi

grep -q "scp" "$readme" || fail "README.md Steps to Run on QLI must include an 'scp' command to copy the app to device"
grep -q "ssh" "$readme" || fail "README.md Steps to Run on QLI must include an 'ssh' command to reach the device"
grep -Eq "python3[[:space:]].*main\\.py" "$readme" \
  || fail "README.md Steps to Run on QLI must include a 'python3 .../main.py' run command"
grep -q "present on the device" "$readme" \
  || fail "README.md Steps to Run on QLI must state that models/labels/media files are present on the device"

# Deployment step ordering: scp (host) -> ssh (enter device) -> env-setup
# block (on-device, if applicable) -> python3 run. The env-setup block must
# never appear before scp/ssh, since it has to execute in the device's shell.
scp_line="$(grep -n "^scp " "$readme" | head -1 | cut -d: -f1 || true)"
ssh_line="$(grep -n "^ssh " "$readme" | head -1 | cut -d: -f1 || true)"
run_line="$(grep -nE "python3[[:space:]].*main\\.py" "$readme" | head -1 | cut -d: -f1 || true)"
[[ -n "$scp_line" && -n "$ssh_line" && "$ssh_line" -gt "$scp_line" ]] \
  || fail "README.md Steps to Run on QLI must ssh after scp, not before"
[[ -n "$ssh_line" && -n "$run_line" && "$run_line" -gt "$ssh_line" ]] \
  || fail "README.md Steps to Run on QLI must run python3 after ssh (on the device), not before"
if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  env_marker_line="$(grep -nE "OCL_ICD_FILENAMES|ulimit[[:space:]]+-n[[:space:]]+10000|wpctl[[:space:]]+status|WS=\\\$\\(find" "$readme" | head -1 | cut -d: -f1 || true)"
  [[ -n "$env_marker_line" && -n "$ssh_line" && "$env_marker_line" -gt "$ssh_line" ]] \
    || fail "README.md Steps to Run on QLI env-setup block must run on the device (after ssh), not on the host before scp"
  [[ -n "$env_marker_line" && -n "$run_line" && "$env_marker_line" -lt "$run_line" ]] \
    || fail "README.md Steps to Run on QLI env-setup block must run before the python3 run command"
fi

if grep -q "Pipeline\\.from_yaml" "$app"; then
  yaml_files="$(find "$artifact_dir" -type f \( -name '*.yaml' -o -name '*.yml' \) | sort)"
  if [[ -z "$yaml_files" ]]; then
    grep -q "External YAML provided by user" "$readme" \
      || fail "Pipeline.from_yaml artifacts must include a generated YAML file, unless README says 'External YAML provided by user'"
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

if grep -q "qtimetamux" "$app"; then
  grep -q "TextFilter()" "$app" || fail "qtimetamux metadata flows should include TextFilter()"
  ! grep -Eq "MLVideo[A-Za-z]*Bin|qtimlvideo[a-z]*bin" "$app" || ! grep -q "tee" "$app" || ! grep -q "TextFilter()" "$app" \
    || fail "ML-bin stages must not be wired into tee/TextFilter/qtimetamux metadata fan-in topologies; keep ML-bin directly in the media path, or use discrete qtimlvconverter/qtimltflite/qtimlpostprocess stages for metadata-merge flows"
  app_compact="$(tr '\n' ' ' < "$app")"
  folded_branch_re='pipeline\.link\([^)]*"(split|tee)[^"]*"[^)]*"metamux[^"]*"[^)]*"overlay"'
  if [[ "$app_compact" =~ $folded_branch_re ]]; then
    fail "tee/qtimetamux fan-in branch links must stop at qtimetamux; link qtimetamux -> overlay/display separately"
  fi
fi

! grep -Eq "pipeline\\.add\\([[:space:]]*(VideoFilter|TextFilter|TensorFilter|ImageFilter|H264Filter|AudioFilter)\\(" "$app" \
  || fail "stream filter objects must be added with pipeline.add_stream_filter(...), not pipeline.add(...)"
! grep -Eq "pipeline\\.add\\([[:space:]]*[A-Za-z_][A-Za-z0-9_]*(filter|vf|mlf|text|tensor|caps)[A-Za-z0-9_]*[[:space:]]*\\)" "$app" \
  || fail "stream filter variables must be added with pipeline.add_stream_filter(...), not pipeline.add(...)"
! grep -Eq "pipeline\\.add_stream_filter\\([[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\\)" "$app" \
  || fail "generated apps must use named add_stream_filter(\"name\", filter_obj) to avoid default-name collisions"

if grep -q "qtivoverlay" "$app"; then
  converter_count="$(count_matches "qtimlvconverter")"
  inference_count="$(count_matches "qtimltflite\\|qtimlsnpe\\|qtimlqnn\\|qtimlonnx")"
  post_count="$(count_matches "qtimlpostprocess")"
  metamux_count="$(count_matches "qtimetamux")"
  text_filter_count="$(count_matches "TextFilter()")"

  if (( converter_count >= 1 && inference_count >= 1 && post_count >= 1 )); then
    grep -Eq "Element\\([\"']tee[\"']" "$app" \
      || fail "discrete AI overlay must preserve a main video branch with tee"
    (( metamux_count >= 1 )) \
      || fail "discrete AI overlay must merge video and metadata with qtimetamux"
    (( text_filter_count >= 1 )) \
      || fail "discrete AI overlay must use TextFilter() before qtimetamux metadata input"
  fi

  if (( converter_count >= 2 && inference_count >= 2 && post_count >= 2 )); then
    (( metamux_count >= 2 )) \
      || fail "two-stage discrete overlay must use two qtimetamux stages to merge stage-1 and stage-2 metadata"
    (( text_filter_count >= 2 )) \
      || fail "two-stage discrete overlay must use TextFilter() before each qtimetamux metadata input"
  fi
fi

if grep -Eq "palmd|hand_detector|hlandmark|hand_landmarks|gesture_embedder|canned_gesture_classifier" "$app"; then
  grep -q "qtimetatransform" "$app" \
    || fail "gesture-recognition pipelines must include qtimetatransform between palm metadata and landmark ROI processing"
  grep -q "roi-palmd" "$app" \
    || fail "gesture-recognition pipelines must set qtimetatransform module roi-palmd"
  grep -q "hlandmark" "$app" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module hlandmark for hand landmarks"
  grep -q "tensor" "$app" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module tensor before gesture embedding"
  grep -q "mobilenet" "$app" \
    || fail "gesture-recognition pipelines must use qtimlpostprocess module mobilenet for gesture classification output"
  grep -Eq "stage_03_1_inference|gesture_embedder" "$app" \
    || fail "gesture-recognition pipelines must include the gesture embedder inference stage"
  grep -Eq "stage_03_2_inference|canned_gesture_classifier" "$app" \
    || fail "gesture-recognition pipelines must include the gesture classifier inference stage"
  gesture_metamux_count="$(count_matches "qtimetamux")"
  (( gesture_metamux_count == 2 )) \
    || fail "gesture-recognition pipelines must use exactly two qtimetamux stages, not a generic per-stage mux cascade"
fi

! grep -Eq "QNNExecurorBackend:HTP|QNNExecutorBackend:HTP|QNNExternalDelegateBackend:HTP" "$app" "$readme" \
  || fail "invalid QNN delegate option string; use QNNExternalDelegate,backend_type=htp,log_level=(string)1;"
! grep -q "QNNExternalDelegate,backend_type=htp;" "$app" "$readme" \
  || fail "HTP delegate options must include log_level=(string)1"
! grep -Eq "confidence_threshold|confidence-threshold|confidence=[0-9]|confidence=\\{" "$app" "$readme" \
  || fail "confidence settings must use JSON key confidence, for example {\"confidence\": 50.0}"
! grep -Eq "settings.*confidence=.*;" "$app" "$readme" \
  || fail "qtimlpostprocess settings must use JSON, not semicolon-delimited confidence settings"

if grep -Eq "qticamsrc|qtiqmmfsrc|rtspsrc" "$app" && grep -Eq "module\".*, *\"(yolov8|yolov5|yolo-nas|qfd|palmd)\"" "$app"; then
  grep -q "bbox-stabilization" "$app" \
    || fail "live-source object/face/palm detection should set bbox-stabilization"
fi

if grep -q "MLVConverter\\|set_preprocess_handler(" "$app"; then
  grep -Eq "TODO:|Placeholder" "$app" "$readme" \
    || fail "custom preprocess placeholders must include TODOs in app or README"
  if grep -q "MLVConverter" "$app"; then
    grep -q "engine.*none\\|engine=\\\"none\\\"\\|engine='none'" "$app" \
      || fail "custom preprocess MLVConverter must explicitly set engine=\"none\" before set_handler"
  fi
  if grep -q "set_preprocess_handler(" "$app"; then
    grep -q "preprocess-engine" "$app" \
      || fail "ML-bin custom preprocess must set preprocess-engine=\"none\" before set_preprocess_handler"
  fi
  if grep -Eq "TODO:|Placeholder" "$app" && grep -q "return False" "$app"; then
    grep -q "not functionally runnable for inference until real tensor-write logic is implemented" "$readme" \
      || fail "README.md must state custom preprocess placeholders are not functionally runnable until tensor-write logic is implemented"
    grep -Eq "return True|returns True|return `True`|must return True" "$app" "$readme" \
      || fail "custom preprocess placeholders must explain changing return False to return True after writing a valid tensor"
  fi
  ! grep -q "blit\\.unmap" "$app" "$readme" \
    || fail "custom preprocess placeholders should not call or instruct manual blit.unmap(); qimsdk unmaps after callback return"
fi

! grep -Eq "AppSrc\\([^\\n]*\\)\\.set_handler|appsrc\\.set_handler" "$app" \
  || fail "AppSrc has no set_handler; use set_buffer_producer(...) or get_raw().connect(\"need-data\", ...)"
! grep -Eq "AppSink\\([^\\n]*\\)\\.set_handler|appsink\\.set_handler" "$app" \
  || fail "AppSink has no set_handler; use set_buffer_consumer(...)"

if { grep -q "set_postprocess_handler(" "$app" || { grep -q "set_handler(" "$app" && ! grep -q "MLVConverter" "$app"; }; }; then
  grep -Eq "ObjectDetections|Poses|DepthMaps|Segmentations|Tensors|ImageClassifications|AudioClassifications" "$app" \
    || fail "custom postprocess callback must use a valid marker annotation"
  grep -q "GstQtiML" "$app" \
    || fail "custom postprocess app must import/check GstQtiML for handler registration and functional output objects"
  grep -q "GstQtiML-1.0.typelib" "$readme" \
    || fail "README.md must mention GstQtiML-1.0.typelib for custom postprocess apps"
  grep -Eq "TODO:|Placeholder" "$app" "$readme" \
    || fail "custom postprocess placeholders must include TODOs in app or README"
  if grep -Eq "TODO:|Placeholder" "$app" && grep -Eq "^[[:space:]]*return[[:space:]]*$" "$app"; then
    fail "custom postprocess placeholder callbacks must not use bare return; return True with an empty output"
  fi
  if grep -Eq "TODO:|Placeholder" "$app" && ! grep -Eq "^[[:space:]]*return[[:space:]]+True[[:space:]]*$" "$app"; then
    fail "custom postprocess placeholder callbacks must return True with an empty typed output"
  fi
  if grep -Eq "TODO:|Placeholder" "$app"; then
    grep -Eq "empty metadata is valid|downstream flow continues|TextFilter|qtimetamux|Use False only for real" "$app" "$readme" \
      || fail "custom postprocess placeholders must explain why empty outputs return True and False is only for real errors"
  fi
fi

! grep -Eq "MLVideo[A-Za-z]*Bin\\([^\\n]*\\)\\.set_handler|mlbin\\.set_handler" "$app" \
  || fail "ML-bin custom postprocess must use set_postprocess_handler(...), not set_handler(...)"

if grep -q "Element(" "$app" && grep -q "\\.add(\"" "$app"; then
  grep -q "mixed construction style" "$readme" \
    || fail "mixed explicit/implicit construction requires README note"
fi

! grep -q "\\.width(" "$app" || fail "VideoFilter.width() is not a documented API"
! grep -q "\\.height(" "$app" || fail "VideoFilter.height() is not a documented API"
! grep -q "app.py" "$readme" "$app" || fail "Python artifacts must use main.py, not app.py"

python3 -m py_compile "$app"

echo "PASS: qimsdk Python artifact checks passed"
