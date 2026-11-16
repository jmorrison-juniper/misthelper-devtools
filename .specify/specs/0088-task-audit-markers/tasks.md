# Tasks: Task Audit Marker Classification

**Input**: Design documents in `.specify/specs/0088-task-audit-markers/`

**Prerequisites**: `plan.md`, `spec.md`, and `design/`

**Scope**: Change only `src/misthelper_devtools/speckit_task_audit.py`, `tests/unit/tools/test_speckit_task_audit.py`, and the records in this feature folder.

**Record order**: The tasks below describe source commits that were written and reviewed before these records. Each checked task cites the evidence that exists.

## Phase 1: Setup

No project initialization or dependency change is needed.

## Phase 2: User Story 1 - Count only real task markers

- [X] T001 [US1] Reject links, reference links, label bullets, and legend bullets in `src/misthelper_devtools/speckit_task_audit.py`.
- [X] T002 [P] [US1] Add tests for links, legends, inline links, and reference links in `tests/unit/tools/test_speckit_task_audit.py`.

## Phase 3: User Story 2 - Report each real malformed marker

- [X] T003 [US2] Keep malformed line numbers in `TaskCounts` and print each in `SpecTaskAudit` in `src/misthelper_devtools/speckit_task_audit.py`.
- [X] T004 [P] [US2] Add a test that runs the real report and asserts the printed path and line in `tests/unit/tools/test_speckit_task_audit.py`.

## Phase 4: User Story 3 - Keep the exit policy

- [X] T005 [US3] Assert in `tests/unit/tools/test_speckit_task_audit.py` that a malformed marker alone keeps exit code 0 for a Draft spec.

## Phase 5: Verification

- [X] T006 Record the counterfactual review evidence in `design/quickstart.md`.
- [ ] T007 Run the documented gates on the rebased head and record the results in the handoff.
- [ ] T008 Publish the branch after the coordinator authorizes it.

## Dependencies

T001 before T002. T003 before T004. T007 before T008.
