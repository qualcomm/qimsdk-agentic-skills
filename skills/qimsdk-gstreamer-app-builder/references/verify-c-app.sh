#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

# verify-c-app.sh — QIM SDK C app artifact verification
#
# Usage: bash verify-c-app.sh <path/to/main.c> <path/to/CMakeLists.txt>
#
# Checks things that are ALWAYS wrong regardless of pipeline type.
# Exits 0 if all pass, 1 if any fail.

MAIN_C="$1"
CMAKE="$2"
PASS=0
FAIL=0

check() {
  local name="$1"
  local ok="$2"   # 0 = pass, nonzero = fail
  if [ "$ok" -eq 0 ]; then
    echo "[PASS] $name"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $name"
    FAIL=$((FAIL + 1))
  fi
}

if [ -z "$MAIN_C" ] || [ -z "$CMAKE" ]; then
  echo "Usage: $0 <main.c> <CMakeLists.txt>"
  exit 1
fi

echo "=== C App Verification: $MAIN_C ==="
echo ""

# Sibling README.md — generated C-app artifacts always place main.c,
# CMakeLists.txt, and README.md in the same directory (see
# artifact-contract.md "Required Files by Request Type").
ARTIFACT_DIR=$(dirname "$MAIN_C")
README="$ARTIFACT_DIR/README.md"

# ── main.c checks ──────────────────────────────────────────────────────────

# 1. Avoid generated scratch link variables that become unused under -Werror.
grep -q '^[[:space:]]*gboolean[[:space:]]\+ret[[:space:]]*[=;]' "$MAIN_C" && RESULT=1 || RESULT=0
check "No local gboolean ret scratch variable" $RESULT

# 2. Every gst_element_factory_make assignment must have a corresponding null check.
awk '
function trim(s) {
  sub(/^[[:space:]]+/, "", s)
  sub(/[[:space:]]+$/, "", s)
  return s
}
function ere_escape(s) {
  gsub(/[][(){}.*+?^$|\\]/, "\\\\&", s)
  return s
}
{
  lines[NR] = $0
  if ($0 ~ /gst_element_factory_make[[:space:]]*\(/) {
    lhs = $0
    sub(/=.*/, "", lhs)
    lhs = trim(lhs)
    n = split(lhs, parts, /[[:space:]\*]+/)
    var = parts[n]
    gsub(/[;,]/, "", var)
    if (var != "")
      vars[++count] = var
  }
}
END {
  missing = 0
  for (i = 1; i <= count; i++) {
    var = vars[i]
    escaped = ere_escape(var)
    found = 0
    for (j = 1; j <= NR; j++) {
      line = lines[j]
      if (line ~ "if[[:space:]]*\\([^)]*!" escaped "([[:space:]]|\\)|\\|\\||&&)" ||
          line ~ "if[[:space:]]*\\([^)]*" escaped "[[:space:]]*==[[:space:]]*NULL" ||
          line ~ "if[[:space:]]*\\([^)]*NULL[[:space:]]*==[[:space:]]*" escaped) {
        for (k = j; k <= j + 5 && k <= NR; k++) {
          if (lines[k] ~ /goto[[:space:]]+cleanup(_pipeline)?[[:space:]]*;/) {
            found = 1
            break
          }
        }
        if (found)
          break
      }
    }
    if (!found) {
      printf("[DETAIL] no null check plus cleanup goto found for factory element: %s\n", var) > "/dev/stderr"
      missing = 1
    }
  }
  exit missing
}
' "$MAIN_C" && RESULT=0 || RESULT=1
check "Every gst_element_factory_make result has a null check plus cleanup goto" $RESULT

# 3. GstAppContext must NOT be redefined
grep -q "typedef struct.*GstAppContext\|typedef struct _GstAppContext" "$MAIN_C" && RESULT=1 || RESULT=0
check "GstAppContext not redefined in main.c" $RESULT

# 4. Correct include path (full sub-path)
grep -q '#include.*<gst/sampleapps/gst_sample_apps_utils\.h>' "$MAIN_C" && RESULT=0 || RESULT=1
check "Include uses full path <gst/sampleapps/gst_sample_apps_utils.h>" $RESULT

# 5. Wrong include forms must not appear
grep -q '#include.*"gstappsutils\.h"\|#include.*<gst_sample_apps_utils\.h>' "$MAIN_C" && RESULT=1 || RESULT=0
check "No wrong include form (gstappsutils.h or short form)" $RESULT

# 6. Correct GstAppContext field name for main loop
grep -q 'appctx\.\(loop\b\|main_loop\b\)' "$MAIN_C" && RESULT=1 || RESULT=0
check "appctx.mloop used (not .loop or .main_loop)" $RESULT

# 7. Deprecated request_pad API not used
grep -q 'gst_element_get_request_pad' "$MAIN_C" && RESULT=1 || RESULT=0
check "gst_element_get_request_pad not used (deprecated)" $RESULT

# 8. No invented plugin names in factory_make calls
# These are known invented names seen in generated code:
HALLUCINATED="qtivdec\|qtimlinference\|qtioverlay\|qtivenc\|qtmux\|qtijpegdec\|qticamdec\|qtimlpostprocessing"
grep -q "gst_element_factory_make.*\(\"\\($HALLUCINATED\\)\"\)" "$MAIN_C" && RESULT=1 || RESULT=0
check "No invented plugin names in gst_element_factory_make" $RESULT

# 9. qtimlvconverter mode property must use gst_element_set_enum_property, not g_object_set
# Check: if mode=roi-batch-cumulative is set, it must not be via g_object_set
if grep -q 'roi-batch-cumulative' "$MAIN_C"; then
  grep -q 'g_object_set.*roi-batch-cumulative' "$MAIN_C" && RESULT=1 || RESULT=0
  check "qtimlvconverter mode set via gst_element_set_enum_property (not g_object_set)" $RESULT
else
  echo "[SKIP] qtimlvconverter roi-batch-cumulative not used in this app"
fi

# 10. qtimlvconverter image-disposition must use gst_element_set_enum_property, not g_object_set
if grep -q 'image-disposition' "$MAIN_C"; then
  grep -q 'g_object_set.*image-disposition' "$MAIN_C" && RESULT=1 || RESULT=0
  check "qtimlvconverter image-disposition set via gst_element_set_enum_property (not g_object_set)" $RESULT
else
  echo "[SKIP] image-disposition not used in this app"
fi

# 10b. qtimetatransform module must use gst_element_set_enum_property, not g_object_set
if grep -q 'qtimetatransform' "$MAIN_C"; then
  grep -q 'g_object_set.*qtimetatransform.*module\|g_object_set (G_OBJECT (qtimetatransform)' "$MAIN_C" && RESULT=1 || RESULT=0
  check "qtimetatransform module set via gst_element_set_enum_property (not g_object_set)" $RESULT
else
  echo "[SKIP] qtimetatransform not used in this app"
fi

# 11. Bus callbacks not reimplemented (they come from the header)
grep -q '^static.*error_cb\|^static.*warning_cb\|^static.*eos_cb\|^static.*state_changed_cb' "$MAIN_C" && RESULT=1 || RESULT=0
check "Bus callbacks not reimplemented in main.c (use from header)" $RESULT

# 12. Nonexistent sample-app context helpers not used
grep -q 'init_app_context\|deinit_app_context' "$MAIN_C" && RESULT=1 || RESULT=0
check "init_app_context/deinit_app_context not used (not in sample-app utils)" $RESULT

# 13. Nonexistent sample-app bus helpers not used
grep -q 'gst_set_default_bus_callback\|register_bus_signals' "$MAIN_C" && RESULT=1 || RESULT=0
check "gst_set_default_bus_callback/register_bus_signals not used (not in sample-app utils)" $RESULT

# 14. Nonexistent bus/interrupt helpers not used
grep -q 'bus_callback\|setup_interrupt_handler' "$MAIN_C" && RESULT=1 || RESULT=0
check "bus_callback/setup_interrupt_handler not used (use sample-app callbacks directly)" $RESULT

# 15. handle_interrupt_signal not reimplemented
grep -q '^static.*handle_interrupt_signal' "$MAIN_C" && RESULT=1 || RESULT=0
check "handle_interrupt_signal not reimplemented in main.c (use from header)" $RESULT

# 16. get_enum_value must use sample-app utility signature: (element, prop_name, nick)
grep -q 'get_enum_value[[:space:]]*([[:space:]]*GST_TYPE_' "$MAIN_C" && RESULT=1 || RESULT=0
check "get_enum_value uses element/property/nick signature, not GST_TYPE_*" $RESULT

# 16b. get_enum_value returns the integer directly; it does not take an output pointer.
grep -q 'get_enum_value.*&[[:alnum:]_]*' "$MAIN_C" && RESULT=1 || RESULT=0
check "get_enum_value does not use output pointer argument" $RESULT

# 17. qtimlpostprocess module values must be resolved by get_enum_value, not hardcoded.
grep -Eq '^[[:space:]]*#define[[:space:]]+([A-Za-z0-9]+_)*MODULE(_[A-Za-z0-9]+)*[[:space:]]+[0-9]+' "$MAIN_C" && RESULT=1 || RESULT=0
check "qtimlpostprocess module IDs are not hardcoded numeric defines" $RESULT

# 18. TFLite delegate values must use GST_ML_TFLITE_DELEGATE_* constants.
grep -Eq '^[[:space:]]*#define[[:space:]]+[A-Za-z0-9_]*TFLITE[A-Za-z0-9_]*DELEGATE[A-Za-z0-9_]*[[:space:]]+[0-9]+' "$MAIN_C" && RESULT=1 || RESULT=0
check "TFLite delegate IDs are not hardcoded numeric defines" $RESULT

# 19. qtimlvconverter mode/image-disposition must not be set as raw integers through g_object_set.
awk '
{
  stmt = stmt $0 " "
  if ($0 ~ /;/) {
    if (stmt ~ /g_object_set[[:space:]]*\(/ &&
        (stmt ~ /"mode"[[:space:]]*,[[:space:]]*[0-3]([[:space:]],|\))/ ||
         stmt ~ /"image-disposition"[[:space:]]*,[[:space:]]*[0-9]+([[:space:]],|\))/)) {
      bad = 1
    }
    stmt = ""
  }
}
END { exit bad }
' "$MAIN_C" && RESULT=0 || RESULT=1
check "qtimlvconverter enum properties are not raw integers via g_object_set" $RESULT

# 20. qtimlpostprocess module must not be set as a raw integer through g_object_set.
awk '
{
  stmt = stmt $0 " "
  if ($0 ~ /;/) {
    if (stmt ~ /g_object_set[[:space:]]*\(/ &&
        stmt ~ /"module"[[:space:]]*,[[:space:]]*[0-9]+([[:space:]],|\))/) {
      bad = 1
    }
    stmt = ""
  }
}
END { exit bad }
' "$MAIN_C" && RESULT=0 || RESULT=1
check "qtimlpostprocess module is not raw integer via g_object_set" $RESULT

# 21. qtimetamux C code must not invent sink_0/sink_1 request pads.
if grep -q '"qtimetamux"' "$MAIN_C"; then
  if grep -Eq 'gst_element_request_pad_simple[[:space:]]*\([^;]*(qtimetamux|metamux)[^;]*"sink_[01]"|link_to_metamux[[:space:]]*\([^;]*"sink_[01]"' "$MAIN_C"; then
    RESULT=1
  else
    RESULT=0
  fi
  check "qtimetamux does not request invented sink_0/sink_1 pads" $RESULT
else
  echo "[SKIP] qtimetamux not used in this app"
fi

# 22. Topology A metadata path must make text/x-raw explicit before qtimetamux.
if grep -q '"qtimlpostprocess"' "$MAIN_C" && grep -q '"qtimetamux"' "$MAIN_C"; then
  grep -q 'text/x-raw' "$MAIN_C" && RESULT=0 || RESULT=1
  check "qtimlpostprocess metadata path declares text/x-raw when qtimetamux is used" $RESULT
else
  echo "[SKIP] qtimlpostprocess + qtimetamux metadata path not used in this app"
fi

# 23. Generated sample apps should start with PAUSED; sample callback transitions to PLAYING.
if grep -q 'GST_STATE_PLAYING' "$MAIN_C"; then
  grep -q 'GST_STATE_PAUSED' "$MAIN_C" && RESULT=0 || RESULT=1
  check "Pipeline uses PAUSED-first lifecycle before PLAYING" $RESULT
else
  echo "[SKIP] GST_STATE_PLAYING not referenced directly"
fi

# 24. qtivtransform placement review for encode chains
# We check for qtivtransform factory_make — if present, verify encode chains stay on the
# direct composer-to-encoder capsfilter sequence.
# Cannot fully automate placement check, but flag presence for review
if grep -q '"qtivtransform"' "$MAIN_C"; then
  echo "[REVIEW] qtivtransform found — verify encode chains use direct composer-to-encoder capsfilter sequence"
fi


has_multistream_topology() {
  grep -Eiq 'multistream|multi[-_ ]stream|ai[[:space:]_-]*wall|num[_-]?streams?|stream[_-]?count|source[_-]?count|streams?[[:space:]]*[=:][[:space:]]*[2-9]' "$MAIN_C" && return 0
  grep -Eiq '\b(if|else|elif|switch|case)\b' "$MAIN_C" && return 1
  local source_count
  source_count="$(grep -Eo '"(filesrc|rtspsrc|qticamsrc|qtiqmmfsrc|v4l2src)"' "$MAIN_C" | wc -l || true)"
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
  ' "$README"
}

# ── README runtime setup and run steps ────────────────────────────────────

if [ -f "$README" ]; then
  need_pulse=0; need_gpu=0; need_ulimit=0; need_wayland=0
  grep -Eq '"pulsesrc"|"pulsesink"' "$MAIN_C" && need_pulse=1
  grep -Eq '"delegate"[[:space:]]*,[[:space:]]*"gpu"|"inference-delegate"[[:space:]]*,[[:space:]]*"gpu"|delegate=gpu' "$MAIN_C" && need_gpu=1
  has_multistream_topology && need_ulimit=1
  grep -q '"waylandsink"' "$MAIN_C" && need_wayland=1
  if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
    readme_setup_block_valid "$need_pulse" "$need_gpu" "$need_ulimit" "$need_wayland" && RESULT=0 || RESULT=1
    check "README uses one ordered &&-chained runtime setup block" $RESULT
  fi

  # Steps to Run on QLI must document the explicit scp/ssh/chmod/run sequence
  # (see artifact-contract.md "Steps to Run on QLI").
  grep -q '^## Steps to Run on QLI[[:space:]]*$' "$README" && RESULT=0 || RESULT=1
  check "README includes '## Steps to Run on QLI' heading" $RESULT

  grep -Eq '\bscp\b' "$README" && grep -Eq '\bssh\b' "$README" && grep -Eq 'chmod[[:space:]]+\+x' "$README" && grep -Eq '(^|[[:space:]])\./[A-Za-z0-9_-]+' "$README" && RESULT=0 || RESULT=1
  check "README Steps to Run on QLI includes scp, ssh, chmod +x, and a ./<binary> run command" $RESULT

  grep -q 'present on the device' "$README" && RESULT=0 || RESULT=1
  check "README Steps to Run on QLI states models/labels/media files must be present on the device" $RESULT

  # Deployment step ordering: scp (host) -> ssh (enter device) -> env-setup
  # block (on-device, if applicable) -> chmod +x -> run.
  scp_line="$(grep -n "^scp " "$README" | head -1 | cut -d: -f1 || true)"
  ssh_line="$(grep -n "^ssh " "$README" | head -1 | cut -d: -f1 || true)"
  chmod_line="$(grep -n "chmod +x" "$README" | head -1 | cut -d: -f1 || true)"
  [[ -n "$scp_line" && -n "$ssh_line" && "$ssh_line" -gt "$scp_line" ]] && RESULT=0 || RESULT=1
  check "README Steps to Run on QLI runs ssh after scp, not before" $RESULT
  [[ -n "$ssh_line" && -n "$chmod_line" && "$chmod_line" -gt "$ssh_line" ]] && RESULT=0 || RESULT=1
  check "README Steps to Run on QLI runs chmod +x after ssh (on the device), not before" $RESULT
  if (( need_pulse || need_gpu || need_ulimit || need_wayland )); then
    env_marker_line="$(grep -nE "OCL_ICD_FILENAMES|ulimit[[:space:]]+-n[[:space:]]+10000|wpctl[[:space:]]+status|WS=\\\$\\(find" "$README" | head -1 | cut -d: -f1 || true)"
    [[ -n "$env_marker_line" && -n "$ssh_line" && "$env_marker_line" -gt "$ssh_line" ]] && RESULT=0 || RESULT=1
    check "README Steps to Run on QLI env-setup block runs on the device (after ssh), not on the host before scp" $RESULT
    [[ -n "$env_marker_line" && -n "$chmod_line" && "$env_marker_line" -lt "$chmod_line" ]] && RESULT=0 || RESULT=1
    check "README Steps to Run on QLI env-setup block runs before chmod +x/the binary run command" $RESULT
  fi
else
  echo "[FAIL] README.md not found next to main.c at $README"
  FAIL=$((FAIL + 1))
fi

echo "=== CMakeLists.txt checks: $CMAKE ==="
echo ""

# 25. cmake minimum version must be 3.16
grep -q 'cmake_minimum_required(VERSION 3\.16)' "$CMAKE" && RESULT=0 || RESULT=1
check "cmake_minimum_required(VERSION 3.16)" $RESULT

# 26. LANGUAGES C CXX declared
grep -q 'LANGUAGES C CXX' "$CMAKE" && RESULT=0 || RESULT=1
check "project() has LANGUAGES C CXX" $RESULT

# 27. gstappsutils linked
grep -q 'gstappsutils' "$CMAKE" && RESULT=0 || RESULT=1
check "gstappsutils in target_link_libraries" $RESULT

# 28. No non-standard include path for sampleapps
grep -q 'gstreamer-1.0/gst/sampleapps' "$CMAKE" && RESULT=1 || RESULT=0
check "No gstreamer-1.0/gst/sampleapps in target_include_directories" $RESULT

# 29. install() target present
grep -q '^install(' "$CMAKE" && RESULT=0 || RESULT=1
check "install() target present in CMakeLists.txt" $RESULT

# 30. No non-standard libraries linked (known bad ones)
grep -q 'gstqtisampleappsutils\|gstqtimlmeta\|gstqti-' "$CMAKE" && RESULT=1 || RESULT=0
check "No non-standard QTI libraries linked (gstqtisampleappsutils etc.)" $RESULT

echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
