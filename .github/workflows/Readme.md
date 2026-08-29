# Workflows

This folder contains GitHub Actions workflows for repository maintenance.

Current workflows:

1. `qcom-preflight-checks.yml`: Runs Qualcomm preflight checks, including
   copyright, email, repolinter, and security checks. See
   [qualcomm/qcom-actions](https://github.com/qualcomm/qcom-actions).
2. `stale-issues.yaml`: Runs periodically to identify stale issues and pull
   requests and add reminder comments.

Review workflow changes when repository layout, license policy, or contribution
requirements change.
