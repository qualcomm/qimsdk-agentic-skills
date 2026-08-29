#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Run a pipeline from YAML. Use shipped configs as quick smoke tests."""

import argparse
from pathlib import Path

from qimsdk import Pipeline

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "test_mlbin_yolov8.yaml"

def create_and_execute_pipeline() -> None:

    parser = argparse.ArgumentParser(description="Run an IMSDK pipeline from YAML config.")
    parser.add_argument("config", nargs="?", default=str(DEFAULT_CONFIG), help="Path to YAML configuration file")
    args = parser.parse_args()

    config_content = Path(args.config).read_text(encoding="utf-8")

    pipe = Pipeline.from_yaml("demo-pipeline", config_content)
    pipe.execute()


def main() -> None:

    from qimsdk import ImsdkGstLogMode, ImsdkLogLevel, SetImsdkGstLogMode, SetImsdkLogLevel
    SetImsdkGstLogMode(ImsdkGstLogMode.ImsdkLog)
    SetImsdkLogLevel(ImsdkLogLevel.Debug)

    create_and_execute_pipeline()


if __name__ == "__main__":
    main()
