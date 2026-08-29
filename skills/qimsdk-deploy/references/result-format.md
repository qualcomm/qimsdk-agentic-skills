# Deploy Result Format

Every deploy run writes `result.json` to `<DEPLOY_OUTPUT_DIR>/<artifact-name>/result.json`.
The AI **always** reads this file after a deploy script exits and renders the result card below.
The card is the canonical response — show it before any other commentary.

## result.json schema

| Field | Type | Values |
|-------|------|--------|
| `mode` | string | `"A"`, `"B"`, `"C"`, `"P"`, `"D"` |
| `artifact` | string | artifact folder name |
| `build_passed` | bool or `"N/A"` | `true` / `false` / `"N/A"` (Modes A, P — no build step) |
| `playing_reached` | bool | `true` / `false` |
| `error_lines` | list of strings | GStreamer `ERROR:` lines (real errors only, benign filtered) |
| `output_file_size` | string | `"8.5 MB"` / `"0 bytes"` / `"missing"` / `"N/A (display-only)"` |
| `output_local_path` | string or null | absolute local path to pulled output file |
| `log_local_path` | string or null | absolute local path to device.log |
| `build_log_path` | string or null | absolute local path to build.log (Modes B/C/D only) |
| `failure_reason` | string or null | exact failure message, or null on success |
| `steps` | list of step objects | per-step progress (see below) |

### Step object schema

| Field | Values | Meaning |
|-------|--------|---------|
| `step` | string (step name) | which step this is |
| `status` | `"ok"` / `"fail"` / `"skip"` | `ok` = completed, `fail` = failed here, `skip` = not reached |
| `detail` | string or null | brief detail (version, path, error message, elapsed time) |

### Steps per mode

Mode A: `parse_artifact`, `verify_inputs`, `push_pipeline`, `setup_device`, `run_pipeline`, `pull_output`

Mode B: `parse_artifact`, `clean_app_dir`, `push_source`, `cmake`, `make`, `install`, `run`, `pull_output`

Mode C: `parse_artifact`, `sdk_verify`, `imsdk_check`, `push_source`, `cross_compile`, `pull_binary`, `push_to_device`, `run`, `pull_output`

Mode P: `parse_artifact`, `push_app`, `run`, `pull_output`

Mode D: `parse_artifact`, `sdk_verify`, `qimsdk_check`, `push_source`, `cross_compile`, `pull_binary`, `push_to_device`, `run`, `pull_output`

## AI Result Card

After every deploy script exits (success or failure), read `result.json` and render this card.
Show the card first, before any explanation.

```
┌─ Deploy Result ────────────────────────────────────────────┐
│ Mode      : <A/B/C/P/D>    Artifact : <artifact name>       │
│ Status    : PASS / FAIL                                    │
├─ Steps ────────────────────────────────────────────────────┤
│ ✓ <step_name>   <detail>                                   │
│ ✗ <step_name>   <failure detail>                           │
│ — <step_name>   (skipped)                                  │
├─ Files ────────────────────────────────────────────────────┤
│ result.json : <log_local_path/../result.json>              │
│ device.log  : <log_local_path or "—">                      │
│ build.log   : <build_log_path or "—">                      │
│ output file : <output_local_path or "—">                   │
└────────────────────────────────────────────────────────────┘
```

### Status = PASS when:
- `build_passed` is `true` or `"N/A"` (Modes A, P)
- `playing_reached` is `true`
- `error_lines` is empty
- `failure_reason` is null

### Status = FAIL otherwise. Show `failure_reason` prominently if set.

### After the card:
- If `failure_reason` is set: state it in one sentence. Do not suggest fixes.
- If `error_lines` is non-empty: list them (max 5, then "... N more in device.log").
- If `output_file_size` is "0 bytes" or "missing": note it.
- Nothing else unless the user asks.
