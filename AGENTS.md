# QIMSDK Agentic Skills Contributor Rules

This repository contains portable skill bundles for coding agents.

## Current Layout

- `skills/`: skill payloads.
- `skills-metadata/`: skill metadata records.
- `sample-prompts/`: sample prompts for skills.

## Guidance File Rules

- Keep `AGENTS.md` only at area or ownership boundaries.
- Do not add `AGENTS.md` inside individual skill directories, skill reference
  directories, individual sample-prompt directories, or per-skill metadata
  files.
- Allowed guidance locations are the repo root, `skills/`, and
  `skills-metadata/`.

## Layout Rules

- Skill payloads live directly under `skills/<skill-name>/`.
- Keep sample prompts under `sample-prompts/<skill-name>/`.
- Keep skill metadata under `skills-metadata/<skill-name>.md`.

## Skill Rules

- Every complete skill directory under `skills/` must contain `SKILL.md`.
- Keep skill names aligned with their directory names.
- Put runtime references needed by a skill inside that skill directory.
- Do not perform broad IMSDK/QIMSDK wording rewrites inside actual skill payloads
  unless explicitly requested. Skill payloads include `skills/<skill>/SKILL.md`
  and any files under that skill's `references/` or `reference/` directories.
- Preserve API names, package names, repository names, paths, headers, library
  targets, and command examples inside skill payloads exactly unless the task is
  specifically to update those technical references.
- Skill authors adding or changing skills under `skills/` should create or
  update the matching metadata file under `skills-metadata/`.
