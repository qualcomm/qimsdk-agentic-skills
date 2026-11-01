# Deploy Result Format

Every deploy run writes `result.json` to `<DEPLOY_OUTPUT_DIR>/<artifact-name>/result.json`.
The AI **always** reads this file after a deploy script exits and renders the result card below.
The card is the canonical response — show it before any other commentary.

## result.json schema

| Field | Type | Values |
|-------|------|--------|
| `mode` | string | `"A"`, `"B"`, `"C"`, `"P"`, `"D"`, `"M"` |
| `artifact` | string | artifact folder name |
| `build_passed` | bool or `"N/A"` | `true` / `false` / `"N/A"` (Modes A, P — no build step; Mode M with `--so`/`--skip-build`) |
| `playing_reached` | bool | `true` / `false` — Mode M: the string `"N/A (no run step)"` |
| `error_lines` | list of strings | GStreamer `ERROR:` lines (real errors only, benign filtered) |
| `output_file_size` | string | `"8.5 MB"` / `"0 bytes"` / `"missing"` / `"N/A (display-only)"` / `"N/A (module install)"` (Mode M) |
| `output_local_path` | string or null | absolute local path to pulled output file (Mode M: the built `.so`) |
| `log_local_path` | string or null | absolute local path to device.log |
| `build_log_path` | string or null | absolute local path to build.log (Modes B/C/D/M only) |
| `failure_reason` | string or null | exact failure message, or null on success |
| `steps` | list of step objects | per-step progress (see below) |

### Step object schema

| Field | Values | Meaning |
|-------|--------|---------|
| `step` | string (step name) | which step this is |
| `status` | `"ok"` / `"fail"` / `"skip"` / `"warn"` | `ok` = completed, `fail` = failed here, `skip` = not reached, `warn` = completed with a caveat |
| `detail` | string or null | brief detail (version, path, error message, elapsed time) |

### Steps per mode

Mode A: `parse_artifact`, `verify_inputs`, `push_pipeline`, `setup_device`, `run_pipeline`, `pull_output`

Mode B: `parse_artifact`, `clean_app_dir`, `push_source`, `cmake`, `make`, `install`, `run`, `pull_output`

Mode C: `parse_artifact`, `sdk_verify`, `imsdk_check`, `push_source`, `cross_compile`, `pull_binary`, `push_to_device`, `run`, `pull_output`

Mode P: `parse_artifact`, `push_app`, `run`, `pull_output`

Mode D: `parse_artifact`, `sdk_verify`, `qimsdk_check`, `push_source`, `cross_compile`, `pull_binary`, `push_to_device`, `run`, `pull_output`

Mode M: `parse_artifact`, `preflight`, `emit_toolchain`, `cmake_configure`, `cross_compile`, `gate_arch`, `gate_filename`, `gate_symbol`, `gate_abi`, `gate_needed`, `connect`, `interrogate_device`, `abi_recheck`, `install_module`, `push_model`, `push_labels`, `ldd`, `verify_registration`, `wiring`

### Mode M — additional fields

Mode M installs a library instead of running a pipeline, so it adds these to the schema
above. `module_registered` is the one that decides PASS.

| Field | Type | Values |
|---|---|---|
| `module_name` | string or null | the `module=` value, e.g. `yolov9-det` |
| `lib_filename` | string or null | `libml-postprocess-<module>.so` |
| `toolchain` | string or null | cross-compiler used, with version |
| `include_dir` / `include_dir_source` | string or null | resolved header dir, and which rule matched |
| `so_path` / `so_size` / `so_sha256` | string or null | the gated library, its size, its digest |
| `file_output` | string or null | raw `file` output for the library |
| `required_glibc` / `required_glibcxx` | string or null | highest `GLIBC_` / `GLIBCXX_` referenced |
| `abi_ceiling` | object | `{"glibc": "2.38", "glibcxx": "3.4.32"}` in effect for the build gate |
| `needed_libs` | list of strings | `DT_NEEDED` entries |
| `gates_skipped` | list of strings | any gate bypassed by flag — **must be surfaced** |
| `hermetic_dropped` | list of strings | sourced-SDK toolchain vars neutralised before `cmake` |
| `device` / `device_os` / `auth` | string or null | `user@ip`, `PRETTY_NAME`, `password` or `key (<name>)` |
| `host_key_fp` | string or null | fingerprint seen on connect (not stored) |
| `modules_dir` | string or null | where the module was installed |
| `modules_dir_source` | string or null | **how it was determined — check this** |
| `device_glibc` / `device_glibcxx` | string or null | measured on the device, e.g. `2.38` / `3.4.32` |
| `so_required_glibc` / `so_required_glibcxx` | string or null | what the library actually needs |
| `abi_ok` | bool or null | null when device versions were unreadable |
| `suggested_build_flags` | string or null | `--max-glibc X --max-glibcxx Y` for future builds |
| `module_device_path` | string or null | installed path |
| `unchanged` | bool | the device already had byte-identical bytes; no new backup was made |
| `backup_path` | string or null | set when an existing module was replaced |
| `model_device_path` / `labels_device_path` | string or null | where model and labels landed |
| `settings_device_path` | string or null | reference copy only |
| `module_registered` | bool | **this is the definition of done** |
| `registry_cleared` | bool | whether `~/.cache/gstreamer-1.0` was cleared and re-inspected |
| `unresolved_deps` | list of strings | `ldd` lines containing `not found` |
| `warnings` | list of strings | non-fatal findings |
| `wiring` | string or null | ready-to-paste `qtimlpostprocess module=…` |
| `dry_run` | bool | `--dry-run`: read-only device checks only, nothing written |
| `next_step` | string or null | what remains |


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

---

## Mode M AI Result Card

Mode M has no run step, so the generic card's `playing_reached` / output-file rows say
nothing. Use this card instead — same `steps[]` and `failure_reason` shape, different
verdict line and different rows.

```
┌─ Post-Process Module Deploy ───────────────────────────────┐
│ Module   : <module_name>    Artifact : <artifact>          │
│ Device   : <device>  <device_os>                           │
│ Status   : PASS / FAIL                                     │
├─ Steps ────────────────────────────────────────────────────┤
│ ✓ <step_name>   <detail>                                   │
│ ✗ <step_name>   <failure detail>                           │
│ ! <step_name>   <warning detail>                           │
│ — <step_name>   (skipped)                                  │
├─ Build ────────────────────────────────────────────────────┤
│ library  : <so_path> (<so_size>)                           │
│ arch     : <from file_output>                              │
│ needs    : GLIBC_<x> / GLIBCXX_<y>  (ceiling <a>/<b>)      │
├─ On device ────────────────────────────────────────────────┤
│ module   : <module_device_path>                            │
│ model    : <model_device_path or "—">                      │
│ labels   : <labels_device_path or "—">                     │
│ backup   : <backup_path or "—">                            │
│ ABI      : needs <so_required_*> / device has <device_*>   │
├─ Files ────────────────────────────────────────────────────┤
│ result   : <log_local_path/../result.json>                 │
│ device.log : <log_local_path or "—">                       │
│ build.log  : <build_log_path or "—">                       │
└────────────────────────────────────────────────────────────┘

  <wiring>
```

### Status = PASS only when `module_registered` is true.

Nothing else qualifies — a run where the build passed all four gates, the copy succeeded,
and the model and labels pushed cleanly but the module is absent from the `module=` enum is
a **FAIL**. `dry_run: true` is also not a pass in the meaningful sense: nothing was
installed, so say so explicitly rather than letting a zero exit code read as success.
`build_passed: true` on its own is real progress and is **not** done.

### After the Mode M card:
- If `module_registered` is false, state `failure_reason` in one sentence. Do not suggest fixes.
- If `modules_dir_source` contains `assumed`, say so: the target directory was **not**
  confirmed from the device's own plugin, so a wrong-directory install is still possible.
- If `registry_cleared` is true, mention it — the install was correct but the device was
  serving a stale plugin registry.
- If `backup_path` is set, name it, and if the module was one that ships in the OS image say
  plainly that a vendor file was replaced.
- If `gates_skipped` is non-empty, or `build_passed` is `"N/A"`, say which safety check did
  not run and why (`--skip-abi-gate`, `--so`, `--skip-build`).
- If `unresolved_deps` is non-empty, list them even on success; they will bite at runtime.
- Report `suggested_build_flags` once. These are **measured** from this device and are better
  than the build's inferred defaults — use them for future builds against it.
- Never print the password. It is redacted from `result.json` and from `device.log`; do not
  reintroduce it by quoting the config.
- Nothing else unless asked.
