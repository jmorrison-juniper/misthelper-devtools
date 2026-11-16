# Tasks: Immediate Post-Merge Gate Dispatch

**Input**: Design documents in `.specify/specs/0086-post-merge-dispatch/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/workflow.md`, and `quickstart.md`

**Scope**: Change only the reusable workflow, the three named tests, the tooling guide, and Spec Kit records in this feature folder. Do not edit implementation files outside the approved list.

**Tests**: The specification and quickstart require focused tests. Add contract tests before changing workflow behavior.

## Phase 1: Setup

**Purpose**: Confirm the existing project structure and test tools.

No project initialization or dependency changes are needed. The workflow, test harness, and focused validation commands already exist.

## Phase 2: Foundational

**Purpose**: Lock shared workflow invariants before changing dispatch eligibility.

- [X] T001 Extend `tests/test_shared_workflow_guards.py` to assert the existing caller inputs, job permissions, label and workflow-file guards, supported merge methods, concurrency settings, and retry settings remain unchanged.

**Checkpoint**: Shared workflow invariants have regression coverage before story changes begin.

## Phase 3: User Story 1 - Start gates after an immediate merge (Priority: P1)

**Goal**: Start default-branch gates after the workflow confirms an immediate merge.

**Independent Test**: Simulate a successful merge command with a live `MERGED` response and valid `mergedAt`. Confirm that the enable job exposes a true merge result and the dispatch job accepts only a successful result for the configured default branch.

- [X] T002 [P] [US1] Add enable-job tests in `tests/test_auto_merge_enable.py` for a successful merge command followed by a valid `MERGED` state and valid `mergedAt`.
- [X] T003 [P] [US1] Assert the exact dispatch condition in `tests/test_auto_merge_dispatch.py`, including `!cancelled()`, skipped-enable recovery events, and the verified-merge/default-branch gate. Reject any additional permissive `OR` branch.
- [X] T004 [US1] Update `.github/workflows/reusable-auto-merge.yml` to verify live pull-request state after a successful `gh pr merge --auto` request and after the existing merge-in-progress error. Expose `merged=true` only for validated `MERGED` state with valid `mergedAt`. Make the dispatch job depend on the enable job and use `!cancelled()` while requiring a successful enable job, `merged=true`, and the configured default branch for immediate-merge dispatch.

**Checkpoint**: A confirmed immediate merge can start only uncovered default-branch gates.

## Phase 4: User Story 2 - Distinguish pending auto-merge from completed merge (Priority: P1)

**Goal**: Keep accepted but open auto-merge requests pending and avoid dispatch before merge confirmation.

**Independent Test**: Simulate open pull requests with an enabled request, no request, and invalid state. Confirm only a validated merged state produces `merged=true`. Confirm the existing bounded retries and failure outcomes remain distinct.

- [X] T005 [US2] Extend `tests/test_auto_merge_enable.py` to cover an open pull request with an enabled auto-merge request, pending state, closed-without-merge state, malformed state, the specific merge-in-progress error, and other merge-command failures. Assert that pending or enabled states never emit `merged=true`.

**Checkpoint**: Pending requests do not start immediate dispatch. The existing merge-in-progress recovery still distinguishes each live state.

## Phase 5: User Story 3 - Recover safely and idempotently (Priority: P2)

**Goal**: Preserve manual, scheduled, and merged-closed recovery, exact-SHA coverage, default-branch safety, and visible dispatch failures.

**Independent Test**: Simulate recovery events, a covered and uncovered exact tip SHA, failed or malformed coverage counts, and a failed gate dispatch. Confirm only the existing default-branch ref is targeted and failures remain visible.

- [X] T006 [P] [US3] Extend `tests/test_auto_merge_dispatch.py` to cover manual, scheduled, and merged-closed recovery when the enable job is skipped. Assert default-branch-only dispatch, the serialized exact-SHA five-check loop with 20-second waits, failed and malformed initial tip responses, current failed or malformed per-gate count behavior, visible dispatch failures, and attempts for other gates already found uncovered. Do not add a per-dispatch tip recheck or change coverage-count semantics.
- [X] T007 [P] [US3] Update `docs/tooling-guide.md` to describe confirmed immediate-merge dispatch, pending auto-merge behavior, and preserved manual, scheduled, and merged-closed recovery.

**Checkpoint**: Recovery paths remain available and safe. Coverage and dispatch behavior remain unchanged beyond merge-trigger eligibility.

## Final Phase: Polish and Validation

**Purpose**: Run focused local gates and record any validation that needs an authorized consumer.

- [X] T008 Run the focused tests in `tests/test_auto_merge_enable.py`, `tests/test_auto_merge_dispatch.py`, and `tests/test_shared_workflow_guards.py`, then run `actionlint` on `.github/workflows/reusable-auto-merge.yml`. Run the shell tests with Bash 3 and Bash 5 when available. Record unavailable tools or skipped tests in `.specify/specs/0086-post-merge-dispatch/quickstart.md`.
- [ ] T009 In an authorized consumer, validate immediate merge dispatch and delayed-merge recovery. Record timestamps, SHAs, and job results in `.specify/specs/0086-post-merge-dispatch/quickstart.md`. Keep this task unchecked until the user authorizes live validation. Do not start a live merge or dispatch as a test without that authorization.

## Dependencies and Execution Order

### Phase dependencies

- **Setup**: No changes or dependencies are required.
- **Foundational**: T001 must finish before the user-story work.
- **User Story 1**: T002 and T003 can run in parallel after T001. T004 depends on T001, T002, and T003.
- **User Story 2**: T005 depends on T004 because it verifies the merged and pending output behavior.
- **User Story 3**: T006 and T007 can run in parallel after T004. T006 can also run alongside T005 because it changes a different test file.
- **Polish**: T008 depends on T001 through T007. T009 depends on T008 and user authorization for a consumer test.

### User story completion order

1. **US1 (P1)**: T002, T003, then T004.
2. **US2 (P1)**: T005 after T004.
3. **US3 (P2)**: T006 and T007 after T004.

### Dependency graph

```text
T001 -> T002 and T003 -> T004 -> T005, T006, and T007 -> T008 -> T009
```

T008 depends on T001 through T007. T009 remains blocked until an authorized consumer is available.

## Parallel Execution Examples

### User Story 1

```text
Run T002 in tests/test_auto_merge_enable.py.
Run T003 in tests/test_auto_merge_dispatch.py.
Start T004 after both test tasks and T001 finish.
```

### User Story 2

T005 changes `tests/test_auto_merge_enable.py`. It has no same-story parallel task.

### User Story 3

```text
Run T006 in tests/test_auto_merge_dispatch.py.
Run T007 in docs/tooling-guide.md.
```

These tasks use separate files and can run in parallel after T004.

## Implementation Strategy

### MVP

Complete T001 through T004. Validate User Story 1 independently with the focused enable and dispatch tests. Do not expand coverage lookup behavior or add a tip recheck.

### Incremental delivery

1. Complete T001, then test and implement US1.
2. Complete T005 to verify pending and merge-in-progress behavior.
3. Complete T006 and T007 to verify recovery and update the guide.
4. Run T008. Run T009 only in an authorized consumer.

## Validation Notes

- Keep the exact-SHA five-check loop, 20-second waits, and serialization.
- Preserve the current failed or malformed coverage-count behavior.
- Keep dispatch failures visible and continue attempts for other uncovered gates.
- Preserve default-branch safety, guards, permissions, supported merge methods, and retry behavior.
- The optional `.specify/memory/constitution.md` file is absent. Its governance gate remains unverified.
- Simulated tests cannot prove hosted scheduling or atomic dispatch.
