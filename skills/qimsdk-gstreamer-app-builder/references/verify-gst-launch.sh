#!/usr/bin/env bash
# verify-gst-launch.sh — QIM SDK gst-launch artifact verification
#
# Usage: bash verify-gst-launch.sh <path/to/pipeline.sh>
#
# Hard checks fail the script.
# Advisory checks are warnings and do not fail the script.

PIPELINE="$1"
PASS=0
FAIL=0
WARN=0

check() {
  local name="$1"
  local ok="$2"
  if [ "$ok" -eq 0 ]; then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
  fi
}

warn_check() {
  local name="$1"
  local ok="$2"
  if [ "$ok" -eq 0 ]; then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[WARN] $name"
    WARN=$((WARN + 1))
  fi
}

if [ -z "$PIPELINE" ]; then
  echo "Usage: $0 <pipeline.sh>"
  exit 1
fi

# Normalize multiline gst-launch into one logical line for regex checks.
CONTENT=$(sed -e ':a;N;$!ba;s/\\\n/ /g' "$PIPELINE" | tr '\n' ' ')

contains() {
  echo "$CONTENT" | grep -Eq "$1"
}

# Sibling README.md — generated artifacts always place pipeline.sh and README.md
# in the same directory (see artifact-contract.md "Required Files by Request Type").
ARTIFACT_DIR=$(dirname "$PIPELINE")
README="$ARTIFACT_DIR/README.md"
README_CONTENT=""
if [ -f "$README" ]; then
  README_CONTENT=$(cat "$README")
fi

readme_contains() {
  printf '%s' "$README_CONTENT" | grep -Eq "$1"
}

# Validates that the applicable runtime-prerequisite rows are emitted as ONE
# fenced bash block, ordered GPU export -> ulimit -> Wayland discovery, with
# the PulseAudio commands (when applicable) appearing before that chain in
# the same block. Only rows flagged by the $need_* args are required.
readme_setup_block_valid() {
  local need_pulse="$1" need_gpu="$2" need_ulimit="$3" need_wayland="$4"
  awk -v pulse="$need_pulse" -v gpu="$need_gpu" -v limit="$need_ulimit" -v wayland="$need_wayland" '
    function block_ok(    pulse_status_pos, pulse_default_pos, gpu_pos, ulimit_pos, wayland_pos, last) {
      if (pulse && block !~ /wpctl[[:space:]]+status/) return 0
      if (pulse && block !~ /wpctl[[:space:]]+set-default[[:space:]]+<node_no[.]>/) return 0
      if (gpu && block !~ /OCL_ICD_FILENAMES/) return 0
      if (limit && block !~ /ulimit[[:space:]]+-n[[:space:]]+10000/) return 0
      if (wayland && block !~ /WS=\$\(find/) return 0
      if (wayland && (block !~ /XDG_RUNTIME_DIR/ || block !~ /WAYLAND_DISPLAY/)) return 0

      last = 0
      if (pulse) {
        pulse_status_pos = index(block, "wpctl status")
        pulse_default_pos = index(block, "wpctl set-default")
        if (pulse_status_pos == 0 || pulse_default_pos <= pulse_status_pos) return 0
        last = pulse_default_pos
      }
      if (gpu) {
        gpu_pos = index(block, "OCL_ICD_FILENAMES")
        if (gpu_pos <= last) return 0
        last = gpu_pos
      }
      if (limit) {
        ulimit_pos = index(block, "ulimit -n 10000")
        if (ulimit_pos <= last) return 0
        last = ulimit_pos
      }
      if (wayland) {
        wayland_pos = index(block, "WS=$(find")
        if (wayland_pos <= last) return 0
      }
      return 1
    }
    /^```bash[[:space:]]*$/ { in_block = 1; block = ""; next }
    in_block && /^```[[:space:]]*$/ { if (block_ok()) found = 1; in_block = 0; next }
    in_block { block = block $0 "\n" }
    END { exit(found ? 0 : 1) }
  ' "$README"
}

# High-scale/concurrency criterion: multiple genuinely independent, concurrently
# active sources (multistream/AI-wall/grid/batched fan-out), not a single source
# selected at runtime among mutually exclusive if/else alternatives.
has_multistream_topology() {
  if printf '%s' "$CONTENT" | grep -Eiq 'multistream|multi[-_ ]stream|ai[[:space:]_-]*wall|num[_-]?streams?|stream[_-]?count|source[_-]?count|streams?[[:space:]]*[=:][[:space:]]*[2-9]'; then
    return 0
  fi
  if grep -Eiq '\b(if|else|elif|switch|case)\b' "$PIPELINE"; then
    # Runtime-selectable single-source alternatives are not multistream.
    return 1
  fi
  local source_count
  source_count="$(printf '%s' "$CONTENT" | grep -Eo 'filesrc|rtspsrc|qticamsrc|qtiqmmfsrc|v4l2src' | wc -l || true)"
  (( source_count > 1 ))
}

echo "=== gst-launch Verification: $PIPELINE ==="
echo ""

# ── Header checks ─────────────────────────────────────────────────────────

# 1. Shebang must be #!/usr/bin/env bash (not #!/bin/bash)
head -1 "$PIPELINE" | grep -q '^#!/usr/bin/env bash' && RESULT=0 || RESULT=1
check "Shebang is #!/usr/bin/env bash" $RESULT

# ── Plugin name checks ────────────────────────────────────────────────────

# 2. No invented plugin names
INVENTED="qtivdec|qtimlinference|qtioverlay|qtivenc|qtmux|qtijpegdec|qtimlpostprocessing"
contains "$INVENTED" && RESULT=1 || RESULT=0
check "No invented plugin names" $RESULT

# ── Topology correctness ──────────────────────────────────────────────────

# 3. composer-to-encoder path should stay on direct NV12 capsfilter
contains 'qtivcomposer.*qtivtransform.*v4l2h264enc' && RESULT=1 || RESULT=0
check "composer-to-encoder path uses direct NV12 capsfilter sequence" $RESULT

if contains 'qtirtspbin'; then
  # RTSP-specific checks
  contains 'mpoint=' && RESULT=0 || RESULT=1
  check "qtirtspbin uses mpoint= (not mount-point=)" $RESULT

  contains 'address=0\.0\.0\.0' && RESULT=0 || RESULT=1
  check "qtirtspbin has address=0.0.0.0 (default 127.0.0.1 is unreachable remotely)" $RESULT

  contains 'mount-point=' && RESULT=1 || RESULT=0
  check "qtirtspbin does not use mount-point= (wrong property name)" $RESULT
fi

# ── File-sink specific ────────────────────────────────────────────────────

if contains 'filesink'; then
  # 4. For composer->encode file output, accept either explicit NV12 caps or direct queue->encoder pattern.
  # Apply this only when the encode branch is actually fed from qtivcomposer.
  if contains 'qtivcomposer'; then
    if contains 'qtivcomposer.*v4l2h264enc|qtivcomposer.*video/x-raw,format=NV12.*v4l2h264enc|qtivcomposer.*queue.*v4l2h264enc'; then
      RESULT=0
      check "composer-to-encode path is valid (explicit NV12 caps or direct negotiated path)" $RESULT
    else
      # Not all filesink pipelines encode composer output; skip this rule when encoder branch is elsewhere.
      RESULT=0
      check "composer-to-encode path check not applicable (encoder branch not from composer output)" $RESULT
    fi
  fi

  # 5. v4l2h264enc must have IO mode properties
  contains 'v4l2h264enc.*capture-io-mode|v4l2h264enc.*output-io-mode' && RESULT=0 || RESULT=1
  check "v4l2h264enc has capture-io-mode and output-io-mode properties" $RESULT
fi

# ── Display-sink specific ─────────────────────────────────────────────────

if contains 'waylandsink'; then
  # 6. waylandsink should have fullscreen=true by default
  contains 'waylandsink.*fullscreen=true' && RESULT=0 || RESULT=1
  check "waylandsink has fullscreen=true" $RESULT
fi

# ── delegate options completeness ─────────────────────────────────────────

if contains 'delegate=external'; then
  # 7. external delegate must have path and options
  contains 'external-delegate-path' && RESULT=0 || RESULT=1
  check "external-delegate-path present when delegate=external" $RESULT

  contains 'external-delegate-options' && RESULT=0 || RESULT=1
  check "external-delegate-options present when delegate=external" $RESULT

  # 8. delegate path should use bare filename (not absolute path)
  contains 'external-delegate-path=/|external-delegate-path=/usr/' && RESULT=1 || RESULT=0
  check "external-delegate-path uses bare filename (not absolute path)" $RESULT
fi

# ── README runtime setup and run steps ────────────────────────────────────

need_pulse=0
need_gpu=0
need_ulimit=0
need_wayland=0
contains 'pulsesrc|pulsesink' && need_pulse=1
contains '\bdelegate=gpu\b|\binference-delegate=gpu\b' && need_gpu=1
has_multistream_topology && need_ulimit=1
contains 'waylandsink' && need_wayland=1

# 9. When one or more rows apply, README must combine them into one ordered
# &&-chained bash block; artifacts with no applicable row are not required
# to include this block at all.
if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  readme_setup_block_valid "$need_pulse" "$need_gpu" "$need_ulimit" "$need_wayland" && RESULT=0 || RESULT=1
  check "README uses one ordered &&-chained runtime setup block" $RESULT
fi

# 10. README must document the "Steps to Run on QLI" section with an explicit
# scp/ssh/run sequence (see artifact-contract.md "Steps to Run on QLI").
readme_contains '^## Steps to Run on QLI[[:space:]]*$' && RESULT=0 || RESULT=1
check "README includes '## Steps to Run on QLI' heading" $RESULT

readme_contains '\bscp\b' && readme_contains '\bssh\b' && (readme_contains '\bbash\b' || readme_contains '\./pipeline\.sh') && RESULT=0 || RESULT=1
check "README Steps to Run on QLI includes scp, ssh, and a run command" $RESULT

readme_contains 'present on the device' && RESULT=0 || RESULT=1
check "README Steps to Run on QLI states models/labels/media files must be present on the device" $RESULT

# 11. Deployment step ordering: scp (host) -> ssh (enter device) -> env-setup
# block (on-device, if applicable) -> run. The env-setup block must never
# appear before scp/ssh, since it has to execute in the device's shell.
scp_line="$(grep -n "^scp " "$README" | head -1 | cut -d: -f1 || true)"
ssh_line="$(grep -n "^ssh " "$README" | head -1 | cut -d: -f1 || true)"
[[ -n "$scp_line" && -n "$ssh_line" && "$ssh_line" -gt "$scp_line" ]] \
  && RESULT=0 || RESULT=1
check "README Steps to Run on QLI runs ssh after scp, not before" $RESULT

if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
  env_marker_line="$(grep -nE "OCL_ICD_FILENAMES|ulimit[[:space:]]+-n[[:space:]]+10000|wpctl[[:space:]]+status|WS=\\\$\\(find" "$README" | head -1 | cut -d: -f1 || true)"
  [[ -n "$env_marker_line" && -n "$ssh_line" && "$env_marker_line" -gt "$ssh_line" ]] \
    && RESULT=0 || RESULT=1
  check "README Steps to Run on QLI env-setup block runs on the device (after ssh), not on the host before scp" $RESULT
fi

echo ""
echo "=== Result: $PASS passed, $FAIL failed, $WARN warnings ==="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
