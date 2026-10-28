# Tasks: Prompt Audit Call Resolution

**Input**: Design documents in `specs/078-prompt-audit-call-resolution/`

**Prerequisites**: `plan.md`, `spec.md`; supporting rules in `data-model.md`, `research.md`, and `quickstart.md`.

**Scope**: Implementation tasks may edit only `src/misthelper_devtools/prompt_audit.py` and `tests/unit/test_prompt_audit.py`. Use self-contained temporary-source fixtures; do not access a consumer checkout. Preserve the public `FunctionIndex.by_qualname`, `FunctionIndex.by_name`, and `FunctionIndex.lookup` interfaces.

**Tests**: Required by FR-010 and FR-011. For each story, first add its regression fixture(s), then run those new tests against the then-current source and confirm they fail at the intended behavior assertion (not during setup/import), before implementing that story.

## Phase 1: Setup

**Purpose**: Establish the repository's existing test baseline; no project or dependency initialization is needed.

- [X] T001 Run `python -m pytest` from the repository root before implementation and record the baseline result; confirm the target source path `src/misthelper_devtools/prompt_audit.py` and regression-test path `tests/unit/test_prompt_audit.py`.

---

## Phase 2: Foundational

**Purpose**: No shared infrastructure changes are required. The existing AST-based module, pytest setup, and temporary-source test pattern are sufficient; keep the public `FunctionIndex` interfaces unchanged.

**Checkpoint**: Proceed to user-story regression fixtures. Do not edit implementation code before the relevant regression test has demonstrated the current behavior gap.

---

## Phase 3: User Story 1 - Find prompts through constructed objects (Priority: P1) 🎯 MVP

**Goal**: Resolve methods on statically identified constructed receivers and simple local aliases, while leaving unknown receivers unresolved and clearing stale receiver knowledge after unknown reassignment.

**Independent Test**: Build an index from a temporary source fixture containing a handler, a constructed receiver, a method with a recognized prompt helper, a simple alias, and an unknown receiver/reassignment. Check the complete expected prompt occurrence list and verify no prompt is guessed for unresolved receivers.

### Tests for User Story 1

- [X] T002 [US1] Add self-contained inline and assigned construction, dotted constructor, local-variable, simple-alias, unknown-reassignment, and unresolved-receiver cases to `tests/unit/test_prompt_audit.py`; assert exact prompt results.
- [X] T003 [US1] Run the new User Story 1 cases against the current source; confirm that the expected prompt assertions fail before implementation, not fixture setup or imports.

### Implementation for User Story 1

- [X] T004 [US1] Implement conservative receiver-class resolution in `src/misthelper_devtools/prompt_audit.py` for uniquely identified direct constructors, local receiver names, and simple same-scope name aliases; clear a binding on unsupported or unknown reassignment, leave unproven receivers unresolved, and do not add a general dataflow engine.

**Checkpoint**: Re-run the User Story 1 cases in `tests/unit/test_prompt_audit.py`; verify the expected prompts and unresolved cases pass independently.

---

## Phase 4: User Story 2 - Resolve methods in their class context (Priority: P1)

**Goal**: Resolve `self.method()` and `cls.method()` against the exact containing class, even when method names repeat, and avoid arbitrary selection when context is unavailable.

**Independent Test**: Index two classes with same-named methods and distinct prompt helpers. Verify `self` and `cls` calls report only the prompt from the owning class; an unproven duplicate-name call reports no guessed prompt.

### Tests for User Story 2

- [X] T005 [US2] Add temporary-source regression cases for `self`, `cls`, `Class.method()`, and dotted class calls across duplicate method names, plus ambiguous and unknown receivers; assert exact prompt results.
- [X] T006 [US2] Run the new User Story 2 cases in `tests/unit/test_prompt_audit.py` against the then-current `src/misthelper_devtools/prompt_audit.py`; confirm the missing class-context behavior fails at the expected assertions before implementing it.

### Implementation for User Story 2

- [X] T007 [US2] Add private method-owner/class-candidate metadata and resolve `self` and `cls` calls within the exact owning class in `src/misthelper_devtools/prompt_audit.py`; preserve `FunctionIndex.by_qualname`, `FunctionIndex.by_name`, and `FunctionIndex.lookup`, and leave ambiguous or unsupported targets unresolved.

**Checkpoint**: Re-run the User Story 2 cases in `tests/unit/test_prompt_audit.py`; verify duplicate method names resolve only in their class context.

---

## Phase 5: User Story 3 - Preserve prompt call order and occurrences (Priority: P1)

**Goal**: Report every statically reached prompt occurrence in source call order, descending into each resolved call at its call site.

**Independent Test**: Use a temporary-source handler with ordered prompt calls, mixed positional/starred/keyword nested calls, repeated prompt calls, and separate call sites reaching one helper. Compare the complete result list, including duplicates, with the expected sequence.

### Tests for User Story 3

- [X] T008 [US3] Add self-contained ordering and multiplicity cases to `tests/unit/test_prompt_audit.py` for different prompt kinds, repeated calls, repeated helper call sites, and nested positional/starred/keyword argument expressions; assert the complete source-evaluation sequence.
- [X] T009 [US3] Run the new User Story 3 cases in `tests/unit/test_prompt_audit.py` against the then-current `src/misthelper_devtools/prompt_audit.py`; confirm ordering or occurrence assertions fail before changing traversal.

### Implementation for User Story 3

- [X] T010 [US3] Replace unordered callable/call discovery in `src/misthelper_devtools/prompt_audit.py` with call-site traversal in Python evaluation order: visit a call's function expression, then positional and starred arguments, then keyword expressions, and descend into each resolved target at that call site; retain duplicate prompt occurrences and static branch behavior.

**Checkpoint**: Re-run the User Story 3 cases in `tests/unit/test_prompt_audit.py`; verify exact ordering and repeated occurrences without runtime branch inference.

---

## Phase 6: User Story 4 - Bound recursive call analysis (Priority: P2)

**Goal**: Terminate direct and mutual recursive paths while reporting prompts at call depths zero through four and preserving distinct call-site occurrences.

**Independent Test**: Build direct- and mutual-recursion temporary-source fixtures with prompts at depth four and only beyond depth four. Verify termination, exact prompt sequence, and repeated downstream call-site occurrences.

### Tests for User Story 4

- [X] T011 [US4] Add direct- and mutual-recursion fixtures to `tests/unit/test_prompt_audit.py` with prompt calls at the depth-four boundary, beyond that boundary, and at separate reachable call sites; assert termination and exact results.
- [X] T012 [US4] Run the new User Story 4 cases in `tests/unit/test_prompt_audit.py` against the then-current `src/misthelper_devtools/prompt_audit.py`; confirm recursion, depth-boundary, or occurrence assertions fail before implementing the guard.

### Implementation for User Story 4

- [X] T013 [US4] Enforce `MAX_DEPTH == 4` and termination in `src/misthelper_devtools/prompt_audit.py` using an active-path set of callable node identities that is removed on return; analyze targets through depth four, do not enter depth five, and do not globally suppress later call sites.

**Checkpoint**: Re-run the User Story 4 cases in `tests/unit/test_prompt_audit.py`; verify all recursion fixtures terminate with the required boundary results.

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Run the specified focused and repository-wide gates and confirm implementation/spec alignment without expanding file scope.

- [X] T014 Run `python -m pytest tests/unit/test_prompt_audit.py -q` from the repository root and verify all regression cases pass.
- [X] T015 Run `python -m pytest` from the repository root and verify the full suite passes; investigate only failures relevant to this feature or report pre-existing failures against the T001 baseline.
- [X] T016 Review `src/misthelper_devtools/prompt_audit.py` and `tests/unit/test_prompt_audit.py` against `specs/078-prompt-audit-call-resolution/spec.md`, `plan.md`, and `data-model.md`; verify FR-001–FR-011 and SC-001–SC-005, dotted class resolution, compound-suite traversal, the depth and public-interface constraints, and the file scope.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; captures the pre-change baseline.
- **Foundational (Phase 2)**: No implementation work; confirms the existing project infrastructure is adequate.
- **User Stories (Phases 3–6)**: Start after the baseline. Each story's regression tests and red proof precede its implementation. Execute these phases serially because implementation and tests share one source file and one test file.
- **Polish (Final Phase)**: Depends on all four stories; run the focused gate before the full suite, then perform the consistency/scope review.

### User Story Dependencies

- **US1 (P1)**: No functional dependency on another story; establish constructed receiver bindings first.
- **US2 (P1)**: No functional dependency on another story; uses class identity context and retains the public index interface.
- **US3 (P1)**: No functional dependency on another story; introduces the source-ordered call-site traversal used by the report.
- **US4 (P2)**: Builds on the call-site traversal from US3 so recursion termination does not suppress distinct occurrences.

### Within Each User Story

- Add self-contained regression fixtures first, then prove the intended behavior fails against the then-current source before implementation.
- Keep each story's implementation minimal and limited to `src/misthelper_devtools/prompt_audit.py`; keep all regression cases in `tests/unit/test_prompt_audit.py`.
- Re-run that story's regression cases after implementation. Do not use global visited-name state or infer dynamic behavior.

## Parallel Opportunities

No tasks are marked `[P]`: all story changes share `src/misthelper_devtools/prompt_audit.py` and `tests/unit/test_prompt_audit.py`, and later traversal/recursion behavior builds on earlier code. The regression fixtures are conceptually separable but concurrent edits to the same test file would conflict. Run the focused and full pytest gates sequentially.

## Parallel Example: User Story 1

There are no safe parallel task pairs for User Story 1 because both the regression fixture and implementation touch their respective shared files, and the red proof must occur between them:

1. Complete T002, then T003 against the unmodified resolver.
2. Complete T004 only after the red proof; re-run the story's cases at its checkpoint.

## Validation Results

- T001 first found that the system Python had no pytest module. The permitted development install created `.venv`.
- The baseline then passed: 928 tests passed, 11 skipped, and 3 warnings in 39.06 seconds.
- Each story's new tests failed at a prompt-result assertion before its implementation. The failures did not occur during fixture setup or import.
- A branch-local receiver test exposed a binding gap during implementation. The fix made that test pass.
- The focused gate passed: `.venv/bin/python -m pytest tests/unit/test_prompt_audit.py -q` reported six passing tests.
- The full gate passed: `.venv/bin/python -m pytest` reported 934 passed, 11 skipped, and 3 warnings in 21.73 seconds.
- Ruff passed: `.venv/bin/python -m ruff check .` reported `All checks passed!`.
- Black passed: `.venv/bin/python -m black --check .` reported 249 files would be left unchanged.
- Mypy passed: `.venv/bin/python -m mypy -p misthelper_devtools --config-file pyproject.toml` reported no issues in 158 source files.
- The STE gate passed for all seven issue records. Scores were 95, 97, 90, 96, 95, 96, and 95 out of 100.
- The `.specify` directory exists and contains templates. The prerequisite script `.specify/scripts/python/check_prerequisites.py` is absent.
- The Python call-order plan now follows runtime evaluation. Python evaluates positional and starred arguments before keyword expressions, even when source text places a keyword first.
- The new dotted-class regression first failed with four tests passing and one prompt-list assertion failing. The compound-suite regression then failed with four tests passing and two prompt-list assertions failing. These failures proved missing direct/dotted call resolution, lost sequential bindings, and nested-function traversal before correction.
- After correction, the focused prompt-audit test file passes six cases. The new cases cover inline and module-qualified constructors, ordinary dotted class calls, duplicate class names, and sequential `try` and `with` suites.

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete T001 to capture the baseline.
2. Complete T002–T004 in order and validate the constructed-receiver story independently.
3. Stop at the US1 checkpoint to confirm the smallest useful increment before proceeding.

### Incremental Delivery

1. Add and prove US1 regressions, implement constructed receiver and alias resolution, then validate.
2. Add and prove US2 regressions, implement class-context resolution, then validate.
3. Add and prove US3 regressions, implement ordered call-site traversal, then validate.
4. Add and prove US4 regressions, implement active-path recursion and the four-edge boundary, then validate.
5. Run the focused test module, full pytest suite, and final spec/scope consistency review.
