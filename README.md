# qimsdk-agentic-skills

Agentic skills for QIMSDK development workflows.

This repository packages QIMSDK-focused skills for coding agents. The repo keeps
runtime skill payloads, sample prompts, and skill metadata in separate top-level
areas.

## Purpose

Use this repo to maintain skills for:

- QIMSDK GStreamer application generation.
- QIMSDK C++ application generation.
- QIMSDK Python application generation.
- QIMSDK deployment workflows.

The skill payloads live directly under `skills/`.

## Directory Structure

```text
.
├── AGENTS.md
├── README.md
├── skills/
│   ├── qimsdk-gstreamer-app-builder/
│   ├── qimsdk-python-app-builder/
│   ├── qimsdk-cpp-app-builder/
│   └── qimsdk-deploy/
├── skills-metadata/
└── sample-prompts/
    ├── qimsdk-gstreamer-app-builder/
    ├── qimsdk-cpp-app-builder/
    └── qimsdk-python-app-builder/
```

## Skill Layout

Each implemented skill should have:

```text
skills/<skill-name>/
├── SKILL.md
└── references/        # optional runtime references
```

Skill names should match their directory names. For example:

```yaml
---
name: qimsdk-gstreamer-app-builder
description: ...
---
```

Current skills:

- `qimsdk-gstreamer-app-builder`
- `qimsdk-cpp-app-builder`
- `qimsdk-python-app-builder`
- `qimsdk-deploy`

## Skills Metadata

Skill metadata lives under `skills-metadata/`.

This directory contains metadata files for describing skill identity,
classification, required inputs, known gaps, and source references. It is
separate from runtime skill payloads under `skills/`.

Skill authors adding or changing skills should create or update the matching
metadata file in `skills-metadata/`.

## Sample Prompts

Sample prompts live under `sample-prompts/`.

The currently populated prompt sets are:

- `qimsdk-gstreamer-app-builder/`: `gst-launch` and C app prompts.
- `qimsdk-cpp-app-builder/`: C++ app-builder prompts.
- `qimsdk-python-app-builder/`: Python app-builder prompts.

## Contributing

Read `AGENTS.md` before making structural changes. It defines the repository
layout and rules for adding skills, prompts, and metadata.

## License

qimsdk-agentic-skills is licensed under the BSD-3-clause License. See
`LICENSE.txt` for the full license text.
