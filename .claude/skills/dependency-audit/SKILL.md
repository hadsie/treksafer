---
name: dependency-audit
description: Audit Python dependencies for vulnerabilities, outdated
  packages, and unused imports. Use when asked to check, update, or
  secure dependencies.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Python Dependency Audit

## Step 1 — Install audit tooling
```bash
pip install pip-audit pipdeptree
```

## Step 2 — Scan for known vulnerabilities
```bash
pip-audit -r requirements.txt
```

Flag any findings by severity. Critical and high findings
require a fix PR. Moderate and low go in the report only.

## Step 3 — Check for outdated packages
```bash
pip list --outdated --format=json
```

Cross-reference against `requirements.txt` to report only
direct dependencies, not transitive ones.

## Step 4 — Check dependency tree for conflicts
```bash
pipdeptree --warn fail
```

## Step 5 — Flag sensitive packages

`shapely`, `pyproj`, and `fiona` have C library bindings.
Major version bumps on these must be flagged for manual
review — do not auto-update.

## Step 6 — Apply safe fixes

Patch-level updates for security vulnerabilities can be
applied directly. Pin to exact versions in `requirements.txt`
(e.g. `requests==2.32.3`).

Run `pytest` after any change to verify nothing breaks.

## Step 7 — Produce report

Summarize:
- Critical/High vulnerabilities (with fix status)
- Available updates (major/minor/patch)
- Any dependency conflicts
- PRs created
