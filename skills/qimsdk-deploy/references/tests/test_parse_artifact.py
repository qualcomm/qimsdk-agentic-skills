# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""Regression tests for _parse_artifact source/output classification.

Guards the fix for the false-positive "camera" match: a videotestsrc+filesink app
whose README says "no camera required" must classify as file-source with the
filesink location as its output_path — NOT as a camera app.

Run:
  python skills/qimsdk-deploy/references/tests/test_parse_artifact.py
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

_REF = pathlib.Path(__file__).resolve().parents[1]


def _load(mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, _REF / f"{mod_name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


README_NO_CAMERA = (
    "# test\nGenerates an MP4 using videotestsrc. No display output, no camera required.\n"
)


class TestModeD(unittest.TestCase):
    def setUp(self):
        self.m = _load("deploy_mode_d")

    def test_videotestsrc_filesink_is_file_source(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "CMakeLists.txt").write_text('set(TEST_TARGET "foo")\n', encoding="utf-8")
            (art / "main.cc").write_text(
                'qti::Element source("videotestsrc","source");\n'
                'qti::Element sink("filesink","sink");\n'
                'sink.set("location", "/root/Downloads/qimsdk_samples/output/out.mp4");\n',
                encoding="utf-8")
            (art / "README.md").write_text(README_NO_CAMERA, encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["source_type"], "file-source")
            self.assertEqual(meta["output_path"],
                             "/root/Downloads/qimsdk_samples/output/out.mp4")

    def test_camera_source_detected(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "CMakeLists.txt").write_text('set(TEST_TARGET "foo")\n', encoding="utf-8")
            (art / "main.cc").write_text('qti::Element s("qtiqmmfsrc","s");\n', encoding="utf-8")
            (art / "README.md").write_text("# cam\n", encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["source_type"], "camera")

    def test_multi_filesink_and_rtsp(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "CMakeLists.txt").write_text('set(TEST_TARGET "foo")\n', encoding="utf-8")
            (art / "main.cc").write_text(
                'qti::Element a("filesink","a"); a.set("location", "/root/o1.mp4");\n'
                'qti::Element b("filesink","b"); b.set("location", "/root/o2.mp4");\n'
                'qti::Element r("qtirtspbin","r");\n',
                encoding="utf-8")
            (art / "README.md").write_text("# t\n", encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["output_paths"], ["/root/o1.mp4", "/root/o2.mp4"])
            self.assertEqual(meta["output_path"], "/root/o1.mp4")
            self.assertTrue(meta["rtsp_out"])


class TestModeC(unittest.TestCase):
    def setUp(self):
        self.m = _load("deploy_mode_c")

    def test_videotestsrc_filesink_is_file_source(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "CMakeLists.txt").write_text('set(GST_EXAMPLE_BIN foo)\n', encoding="utf-8")
            (art / "main.c").write_text(
                'gst_element_factory_make("videotestsrc","source");\n'
                'gst_element_factory_make("filesink","sink");\n'
                'g_object_set(sink, "location", "/root/Downloads/out.mp4", NULL);\n',
                encoding="utf-8")
            (art / "README.md").write_text(README_NO_CAMERA, encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["source_type"], "file-source")
            self.assertEqual(meta["output_path"], "/root/Downloads/out.mp4")

    def test_multi_filesink_and_rtsp(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "CMakeLists.txt").write_text('set(GST_EXAMPLE_BIN foo)\n', encoding="utf-8")
            (art / "main.c").write_text(
                'g_object_set(a, "location", "/root/o1.mp4", NULL);\n'
                'g_object_set(b, "location", "/root/o2.mp4", NULL);\n'
                'gst_element_factory_make("qtirtspbin","r");\n',
                encoding="utf-8")
            (art / "README.md").write_text("# t\n", encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["output_paths"], ["/root/o1.mp4", "/root/o2.mp4"])
            self.assertTrue(meta["rtsp_out"])


class TestModeP(unittest.TestCase):
    def setUp(self):
        self.m = _load("deploy_mode_p")

    def test_videotestsrc_filesink_is_file_source(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "main.py").write_text(
                'Element("videotestsrc","source")\n'
                'Element("filesink","sink").set("location", "/root/Downloads/out.mp4")\n',
                encoding="utf-8")
            (art / "README.md").write_text(README_NO_CAMERA, encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["source_type"], "file-source")
            self.assertEqual(meta["output_path"], "/root/Downloads/out.mp4")

    def test_multi_filesink_and_rtsp(self):
        with tempfile.TemporaryDirectory() as d:
            art = pathlib.Path(d)
            (art / "main.py").write_text(
                'Element("filesink","a").set("location", "/root/o1.mp4")\n'
                'Element("filesink","b").set("location", "/root/o2.mp4")\n'
                'Element("qtirtspbin","r")\n',
                encoding="utf-8")
            (art / "README.md").write_text("# t\n", encoding="utf-8")
            meta = self.m._parse_artifact(str(art))
            self.assertEqual(meta["output_paths"], ["/root/o1.mp4", "/root/o2.mp4"])
            self.assertTrue(meta["rtsp_out"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
