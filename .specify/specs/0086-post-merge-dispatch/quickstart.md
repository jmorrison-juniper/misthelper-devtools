# Validation Guide: Immediate Post-Merge Gate Dispatch

## Prerequisites

Use Python 3.13 or newer, pytest, PyYAML, Bash, jq, and actionlint. Install the
repository development dependencies in a local environment. Do not use a
production token in tests. The optional constitution file is absent, so do not
claim that its gate passed.

## Focused tests

After implementation, run from the repository root:

```sh
python -m pytest tests/test_auto_merge_enable.py tests/test_auto_merge_dispatch.py tests/test_shared_workflow_guards.py
actionlint .github/workflows/reusable-auto-merge.yml
```

Each test must pass. Report a missing tool or a skipped test as unverified.
Run the shell tests with Bash 3 and Bash 5 when both are available. Run the
repository gates named in `.github/copilot-instructions.md` when available.

## Local validation record

**Checked at:** 2026-11-16T01:17:28Z

- Tests: `rtk .venv/bin/python -m pytest tests/test_auto_merge_enable.py tests/test_auto_merge_dispatch.py tests/test_shared_workflow_guards.py` passed. Result: 54 passed.
- Ruff: `rtk .venv/bin/python -m ruff check tests/test_auto_merge_enable.py tests/test_auto_merge_dispatch.py tests/test_shared_workflow_guards.py` passed.
- Black: `rtk .venv/bin/python -m black --check tests/test_auto_merge_enable.py tests/test_auto_merge_dispatch.py tests/test_shared_workflow_guards.py` passed. Three files would be left unchanged.
- Workflow lint: the SHA-256-verified actionlint 1.7.12 binary passed on `.github/workflows/reusable-auto-merge.yml`.
- Prose: `rtk .venv/bin/ste-linter --config .ste-linter.toml --min-score 80 docs/tooling-guide.md` passed with a score of 99/100.
- Shell versions: Bash 3.2.57 ran the tests. Bash 5 was not available.
- Spec Kit setup: the setup script was absent. The feature records were read and used directly.
- Consumer validation: T009 was not run. Wait for an authorized serial publication slot.

## Required scenarios

Use fake GitHub responses and inspect the real workflow step and job contract.
See [workflow.md](contracts/workflow.md) for the interface.

1. Accepted command plus merged state emits `merged=true`. An enabled request
   succeeds without that output. Pending state retries within the bound.
2. Merge-in-progress followed by merged, enabled, pending, closed, malformed,
   or unavailable state has the documented result. Other merge errors fail.
3. Schedule, manual, and merged-closed events remain eligible when the enable
   job is skipped. A confirmed merge on the default branch is eligible.
   Pending, blocked, failed, unlabeled, and non-default cases are not.
4. Exact-tip queued or completed coverage prevents dispatch. The existing
   bounded coverage retry and failed or malformed count behavior remain intact.
   The dispatch ref is the default branch.
5. A failed dispatch is visible, and other missing gates still receive an
   attempt. Repeated and serialized runs with visible coverage do not dispatch
   again.
6. Preserve the merge methods, label and workflow-file guards, permissions,
   retry settings, and concurrency configuration.

For an authorized end-to-end validation, observe an immediate merge and its
gate dispatches in a suitable consumer. Separately observe delayed auto-merge
and manual or scheduled recovery. Record timestamps, SHAs, and job results.
Do not claim that a simulated test proves GitHub queue timing or atomicity.
