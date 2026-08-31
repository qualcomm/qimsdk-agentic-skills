# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Regression tests for the additive --phase flag on deploy_mode_{c,d,p}.py.

These are host-only (no device, no workstation) — they exercise the argparse
surface and the --dry-run path, which parses the artifact and returns before any
SSH. The key invariant: --phase all is still accepted and behaves as before, and
the new build/run values parse and stamp result['phase'].

Run:
  python skills/qimsdk-deploy/references/tests/test_phase_flag.py
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_REF = _HERE.parent                       # qimsdk-deploy/references
_REPO = _REF.parents[3]                    # repo root

SCRIPTS = {
    "P": _REF / "deploy_mode_p.py",
    "D": _REF / "deploy_mode_d.py",
    "C": _REF / "deploy_mode_c.py",
}


def _make_py_artifact(tmp):
    """Minimal Mode-P artifact: main.py (display-only) + README.md."""
    d = pathlib.Path(tmp) / "art_py"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(
        "# SPDX-License-Identifier: BSD-3-Clause-Clear\n"
        "from qimsdk import Element, Pipeline\n"
        "print('hello')\n", encoding="utf-8")
    (d / "README.md").write_text("# Test\nOutput: Wayland display\n", encoding="utf-8")
    return d


def _run(script, artifact, out_dir, phase, extra=None):
    cmd = [sys.executable, str(script),
           "--artifact-path", str(artifact),
           "--output-dir", str(out_dir),
           "--phase", phase, "--dry-run"]
    if extra:
        cmd += extra
    env = dict(os.environ)
    # dry-run short-circuits before SSH, but D/C validate workstation env in main();
    # provide harmless placeholders so argparse validation doesn't exit early.
    env.setdefault("DEVICE_IP", "1.2.3.4")
    env.setdefault("DEVICE_USER", "root")
    env.setdefault("DEVICE_PASSWORD", "x")
    env.setdefault("DEPLOY_OUTPUT_DIR", str(out_dir))
    env.setdefault("LINUX_WORKSTATION_HOST", "ws")
    env.setdefault("LINUX_WORKSTATION_USER", "u")
    env.setdefault("LINUX_WORKSTATION_BUILD_DIR", "/tmp/x")
    return subprocess.run(cmd, cwd=str(_REPO), env=env,
                          capture_output=True, text=True)


class TestPhaseFlagAccepted(unittest.TestCase):
    def test_all_three_phases_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = _make_py_artifact(tmp)
            out = pathlib.Path(tmp) / "out"
            for phase in ("all", "build", "run"):
                r = _run(SCRIPTS["P"], art, out, phase)
                # Should not crash on argparse; dry-run returns quickly.
                self.assertNotIn("unrecognized arguments", r.stderr, msg=r.stderr)
                self.assertNotEqual(r.returncode, 2,
                                    msg=f"argparse error for --phase {phase}: {r.stderr}")

    def test_run_timeout_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = _make_py_artifact(tmp)
            out = pathlib.Path(tmp) / "out"
            r = _run(SCRIPTS["P"], art, out, "run", extra=["--run-timeout", "12"])
            self.assertNotIn("unrecognized arguments", r.stderr, msg=r.stderr)

    def test_help_lists_phase_for_all_scripts(self):
        for mode, script in SCRIPTS.items():
            r = subprocess.run([sys.executable, str(script), "--help"],
                               cwd=str(_REPO), capture_output=True, text=True)
            self.assertIn("--phase", r.stdout, msg=f"Mode {mode} missing --phase in --help")
            self.assertIn("--run-timeout", r.stdout, msg=f"Mode {mode} missing --run-timeout")


class TestPhaseKeyStamped(unittest.TestCase):
    def test_result_json_has_phase(self):
        """dry-run writes result.json; it must carry the phase it ran under."""
        with tempfile.TemporaryDirectory() as tmp:
            art = _make_py_artifact(tmp)
            out = pathlib.Path(tmp) / "out"
            _run(SCRIPTS["P"], art, out, "build")
            rj = out / "art_py" / "result.json"
            self.assertTrue(rj.is_file(), "result.json not written on dry-run build")
            data = json.loads(rj.read_text(encoding="utf-8"))
            self.assertEqual(data.get("phase"), "build")


if __name__ == "__main__":
    unittest.main(verbosity=2)
