/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#pragma once

#include <cstdint>
#include <string>
#include <vector>

struct LabelEntry {
  std::string name;
  uint32_t color = 0x00FF00FF;
};

std::vector<LabelEntry> load_labels(const std::string& path);
