# Tasks: Issue 67 Coverage Repair

**Input**: `specs/issue-67-coverage-repair/spec.md` and `plan.md`

**Scope**: Repair source-driven coverage selection and evidence closure. Add no dependencies.

## Phase 1: Setup

No project setup changes are required.

## Phase 2: Foundational

No shared prerequisite changes are required. Reuse the existing AST slicer and source resolution.

## Phase 3: User Story 1 - Count Tests Through Fixtures (Priority: P1)

**Goal**: Select tests that call repository source through a same-module pytest fixture.

**Independent Test**: Compare fixture-driven and direct-source tests for equal failure coverage. Confirm unrelated and unused fixtures add no coverage.

- [X] T001 [US1] Add fixture-driven regression cases and verify they fail before implementation in `tests/tools/test_quality_analyzer/test_source_coverage_slicer.py`. Cover imported source-class construction, missing executable returns, calls through matching parameters, unused or unrelated fixtures, shadowed pytest names, and import-only or marker-only scope.
- [X] T002 [US1] Extend fixture selection and source-call proof in `src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py`. Preserve direct-source selection and require a pytest fixture that constructs the imported source class.

## Phase 4: User Story 2 - Count Referenced Static Response Helpers (Priority: P1)

**Goal**: Include only referenced class-scoped static response helpers and their required definitions.

**Independent Test**: Compare a selected test using a static helper with an equivalent inline response. Confirm unused helpers, sibling tests, and same-named helpers in other classes add no coverage.

- [X] T003 [US2] Add class-helper regression cases and verify they fail before helper-closure implementation in `tests/tools/test_quality_analyzer/test_source_coverage_slicer.py`. Cover required response definitions, unused helpers, sibling tests, and same-name helpers with different statuses in separate classes.
- [X] T004 [US2] Resolve referenced static response helpers by owning class and include only their required definitions in `src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py`. Preserve module-helper results and prevent cross-class status matches.

## Phase 5: Polish and Local Gates

- [X] T005 [P] Document fixture selection and class-scoped helper coverage in `src/misthelper_devtools/test_quality_analyzer/README.md`.
- [X] T006 Run `python -m pytest tests/tools/test_quality_analyzer/test_source_coverage_slicer.py -q` for `tests/tools/test_quality_analyzer/test_source_coverage_slicer.py`.
- [X] T007 Run the full local Python gates from the repository root for `src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py`, `tests/tools/test_quality_analyzer/test_source_coverage_slicer.py`, and `src/misthelper_devtools/test_quality_analyzer/README.md`: `python -m ruff check .`, `python -m black --check .`, `python -m mypy -p misthelper_devtools --config-file pyproject.toml`, and `python -m pytest`.

## Dependencies and Parallel Work

- Complete T001 before T002, and complete T003 before T004, to verify red-before-green behavior.
- User Stories 1 and 2 are behaviorally independent, but their tests share one file and their implementation shares one file. Complete them sequentially.
- T005 can run in parallel with T001–T004 because it edits a separate file and documents specified behavior.
- Run T006 and T007 after implementation and documentation are complete.
- No other task can run in parallel without editing a shared file.

## Parallel Example: User Story 1

No parallel test or implementation tasks exist for this story because its test and production changes use shared files.

## Parallel Example: User Story 2

No parallel test or implementation tasks exist for this story because its test and production changes use shared files.

## Implementation Strategy

Deliver User Story 1 as the MVP. Verify its tests fail before implementation, then pass independently. Add User Story 2 next, then update documentation and run the targeted and full local Python gates.
