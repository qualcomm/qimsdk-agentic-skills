# Contributing to qimsdk-agentic-skills

Thank you for contributing to QIMSDK agentic skills. This repository contains
portable skill bundles, sample prompts, and skill metadata for coding agents.

## Branching Strategy

Contributors should develop changes on topic branches based on `main` and submit
pull requests back to `main`.

## Repository Layout

- `skills/`: skill payloads. Each complete skill directory must contain
  `SKILL.md`.
- `skills-metadata/`: metadata records for skills.
- `sample-prompts/`: sample prompts grouped by skill.

Keep the top-level layout focused on the documented public release directories unless repository policy changes.

## Submitting a Pull Request

1. Read the [code of conduct](CODE-OF-CONDUCT.md) and [license](LICENSE.txt).
2. Fork and clone the repository:

    ```bash
    git clone https://github.com/<username>/qimsdk-agentic-skills.git
    ```

3. Add the upstream remote if needed:

    ```bash
    git remote add upstream https://github.com/qualcomm/qimsdk-agentic-skills.git
    ```

4. Create a branch from latest `main`:

    ```bash
    git fetch upstream main
    git switch -c <branch-name> upstream/main
    ```

5. Make focused changes. If you add or update a skill under `skills/`, update
   the matching metadata file under `skills-metadata/` and any relevant sample
   prompts under `sample-prompts/`.
6. Commit with DCO signoff:

    ```bash
    git commit -s -m "type(scope): short summary"
    ```

7. Rebase before pushing:

    ```bash
    git fetch upstream main
    git rebase upstream/main
    ```

8. Push to your fork:

    ```bash
    git push -u origin <branch-name>
    ```

9. Submit a pull request to `qualcomm/qimsdk-agentic-skills:main`.

## Pull Request Expectations

- Keep changes scoped to one logical update.
- Preserve existing skill style and directory naming.
- Do not rewrite API names, package names, paths, headers, library targets, or
  command examples inside skill payloads unless the task explicitly requires it.
- Update documentation when layout, behavior, or contributor rules change.
- Include validation details in the pull request when applicable.

## Security Analysis of Pull Requests

Pull requests may be scanned using automated security and repository checks. If
issues are flagged, contributors are expected to resolve them before merge.
