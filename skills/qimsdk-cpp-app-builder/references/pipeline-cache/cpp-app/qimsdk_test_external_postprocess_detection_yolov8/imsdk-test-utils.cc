// Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
// SPDX-License-Identifier: BSD-3-Clause-Clear

#include "imsdk-test-utils.h"

#include <fstream>
#include <regex>
#include <sstream>

std::vector<LabelEntry> load_labels(const std::string& path) {
  std::vector<LabelEntry> labels;

  std::ifstream file(path);
  if (!file.is_open()) {
    return labels;
  }

  std::stringstream buffer;
  buffer << file.rdbuf();
  const std::string content = buffer.str();

  static const std::regex kJsonObject(R"json(\{[^\}]*\})json");
  static const std::regex kIdField(R"json("id"\s*:\s*(\d+))json");
  static const std::regex kLabelField(R"json("label"\s*:\s*"([^"]+)")json");
  static const std::regex kColorField(R"json("color"\s*:\s*"([0-9a-fA-FxX]+)")json");

  std::sregex_iterator begin(content.begin(), content.end(), kJsonObject);
  std::sregex_iterator end;

  for (auto it = begin; it != end; ++it) {
    const std::string obj = (*it).str();

    std::smatch id_match;
    std::smatch label_match;
    std::smatch color_match;

    if (!std::regex_search(obj, id_match, kIdField) ||
        !std::regex_search(obj, label_match, kLabelField) ||
        !std::regex_search(obj, color_match, kColorField)) {
      continue;
    }

    const int id = std::stoi(id_match[1].str());
    if (id < 0) {
      continue;
    }

    const std::string name = label_match[1].str();
    const std::string color_str = color_match[1].str();
    const uint32_t color = static_cast<uint32_t>(
        std::stoul(color_str, nullptr, 16));

    if (labels.size() <= static_cast<size_t>(id)) {
      labels.resize(static_cast<size_t>(id + 1));
    }

    labels[static_cast<size_t>(id)] = {name, color};
  }

  return labels;
}
