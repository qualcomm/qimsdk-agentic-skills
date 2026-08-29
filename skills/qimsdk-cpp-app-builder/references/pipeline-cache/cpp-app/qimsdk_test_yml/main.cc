/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 * SPDX-License-Identifier: BSD-3-Clause-Clear
 */

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include <qti/qimsdk.h>

using namespace qti;

namespace {

// Reads YAML configuration content from file.
std::string read_file(const std::string& path) {
  std::ifstream input(path);
  if (!input.is_open()) {
    throw std::runtime_error("Failed to open YAML config: " + path);
  }

  std::ostringstream buffer;
  buffer << input.rdbuf();

  if (buffer.fail()) {
    throw std::runtime_error("Failed to read YAML config: " + path);
  }

  return buffer.str();
}

// Creates and executes a pipeline from YAML configuration file.
void create_and_execute_pipeline(const std::string& config_path) {
  const std::string config = read_file(config_path);

  Pipeline pipeline("demo-pipeline", config);
  pipeline.execute();
}

} // namespace

int main(int argc, char** argv) {
  // Route GStreamer logs through IMSDK logger and enable debug output.
  qti::SetImsdkGstLogMode(qti::ImsdkGstLogMode::ImsdkLog);
  qti::SetImsdkLogLevel(qti::ImsdkLogLevel::Debug);

  // Require YAML config path from command line.
  if (argc < 2) {
    std::cerr << "Error: Missing YAML config path." << std::endl;
    std::cerr << "Usage: " << argv[0] << " <config.yaml>" << std::endl;
    return 1;
  }

  // Load config, run pipeline, and wait for completion.
  try {
    create_and_execute_pipeline(argv[1]);
  } catch (const std::exception& ex) {
    std::cerr << "Exception: " << ex.what() << std::endl;
    return 1;
  }

  return 0;
}
