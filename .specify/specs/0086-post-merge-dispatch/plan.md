# Implementation Plan: Immediate Post-Merge Gate Dispatch

**Branch**: `jmorrison-jnpr-fix/86-post-merge-dispatch` | **Date**: 2026-11-15
**Spec**: [.specify/specs/0086-post-merge-dispatch/spec.md](spec.md)

## Summary

Expose a confirmed merge result from the existing enable job. Use that result
to start the existing dispatch job for a pull request on the configured default
branch. Do not start that job for an accepted, pending auto-merge request.
Keep manual, scheduled, and merged-closed recovery.

The dispatch step also needs a focused failure repair. It currently treats a
failed coverage lookup as missing coverage. Preserve that existing bounded
retry and fallback behavior. Keep the existing visible failure for a failed
workflow dispatch.

## Technical Context

**Language/Version**: GitHub Actions YAML, Bash 3 and 5, Python 3.13 or newer.
**Primary Dependencies**: GitHub Actions, GitHub CLI, jq, PyYAML, pytest.
**Storage**: GitHub pull-request state and workflow-run records.
**Testing**: Focused pytest shell harnesses and workflow contract guards.
**Target Platform**: GitHub-hosted Ubuntu runners; shell tests also cover Bash 3.
**Project Type**: Reusable GitHub Actions workflow.
**Performance Goals**: Attempt uncovered dispatches within two minutes after
merge confirmation under normal scheduling and API latency.
**Constraints**: Five coverage checks, 20 seconds apart; no new permissions,
credentials, triggers, workflow files, or consumer changes.
**Scale/Scope**: One reusable workflow and its configured default-branch gates.

GitHub controls job scheduling, API latency, and the branch tip. This design
cannot guarantee the two-minute target or an atomic branch-tip dispatch. It
must not claim those guarantees.

## Constitution Check

The optional `.specify/memory/constitution.md` file is absent. Do not invent or
restore a constitution for this change. Follow the repository instructions and
record the absent file as an unverified optional gate.

The design retains the workflow guard, permissions, merge methods, branch
guard, bounded retries, and dispatch serialization. Tests must prove each
changed contract and preserve the existing recovery paths.

## Project Structure

### Documentation

```text
.specify/specs/0086-post-merge-dispatch/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── workflow.md
└── quickstart.md
```

### Allowed implementation files

```text
.github/workflows/reusable-auto-merge.yml
tests/test_auto_merge_dispatch.py
tests/test_auto_merge_enable.py
tests/test_shared_workflow_guards.py
docs/tooling-guide.md
```

Change a listed test or guide only when it directly supports this feature.
Do not add helper files outside the feature folder. Do not change consumers.

## Implementation Design

1. Give the enable step an ID and expose its `merged` output as a job output.
   Emit `merged=true` only after a validated live `MERGED` state with a valid
   `mergedAt`. Emit no true result for any other state.
2. After an accepted merge command, use the existing bounded state verification.
   An open state with an enabled request is successful but not merged.
   An open state without the request is retried and fails after the bound.
   Preserve the same recovery for the specific merge-in-progress error.
   Other merge-command failures still fail without recovery.
3. Make the dispatch job depend on the enable job. Its job condition must use
   `always()` so skipped enable jobs do not suppress manual, scheduled, or
   merged-closed recovery. The new pull-request path requires a successful
   enable job, `merged=true`, and a base matching `inputs.default-branch`.
   Preserve the nonempty workflow input guard.
4. Preserve the serialized dispatch job and its existing exact-SHA five-check
   coverage loop, including its current handling of failed or malformed run
   counts. Keep dispatch attempts on the configured default-branch ref and
   keep per-workflow dispatch failures visible while attempting other gates.
5. Update focused tests and directly related guide text. Keep the existing
   caller interface and all job permissions unchanged.

## Validation and Gates

Use the scenario matrix in [quickstart.md](quickstart.md) and the interface in
[contracts/workflow.md](contracts/workflow.md). Run focused tests, workflow
lint, and the repository gates that are available. Record unavailable tools
and skipped tests. Validate real Actions scheduling only in an authorized
consumer. Do not start a live merge or dispatch as a test.

The five checks have four 20-second waits. API calls, runner queue time, and
serialization consume the remaining time. No test with fake responses proves
the two-minute success criterion under GitHub delays.

## Complexity Tracking

No new workflow, service, dependency, or persistent state is proposed. The
optional constitution is absent, so that gate remains unverified.
