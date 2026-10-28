# Implementation Plan: Prompt Audit Call Resolution

**Branch**: `jmorrison-jnpr-fix-78-prompt-audit-call-resolution` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)

**Input**: `specs/078-prompt-audit-call-resolution/spec.md`

## Summary

Resolve calls through inline or assigned constructed receivers, ordinary dotted class calls, and `self` or `cls`. Walk each resolved call at its source call site. Preserve repeated prompt occurrences and stop recursive paths with an active-path identity guard. Keep `FunctionIndex` public dictionaries and `lookup` unchanged. Limit source changes to `src/misthelper_devtools/prompt_audit.py` and regression tests in `tests/unit/test_prompt_audit.py`.

## Technical Context

**Language/Version**: Python 3.13 or newer.

**Primary Dependencies**: Python standard-library `ast`; pytest for tests. Add no dependency.

**Storage**: None. The tool reads source files and builds in-memory indexes.

**Testing**: `python -m pytest tests/unit/test_prompt_audit.py -q`, then `python -m pytest`.

**Target Platform**: Windows, macOS, and Linux. The repository instructions state that the package runs on these platforms.

**Project Type**: Internal Python command-line development tool.

**Performance Goals**: No numeric target exists. Limit call traversal to `MAX_DEPTH` and parse each indexed source file once.

**Constraints**:

- Keep all implementation changes within `prompt_audit.py` and `tests/unit/test_prompt_audit.py`.
- Preserve the public `FunctionIndex.by_qualname`, `FunctionIndex.by_name`, and `FunctionIndex.lookup` interfaces.
- Do not execute inspected source or infer runtime branch outcomes.
- Do not read or change the MistHelper checkout.
- Keep the solution specific to direct construction, simple local aliases, and class-context method resolution. Do not add a general dataflow engine.
- Write design artifacts only under this feature directory. Do not create `tasks.md` in this phase.

**Scale/Scope**: Index the repository `src` tree as the current tool does. Resolve only statically supported targets. The requested handler starts at depth zero, and `MAX_DEPTH` remains four.

## Constitution Check

**Constitution status**: `.specify/memory/constitution.md` is absent. Constitution-specific rules cannot be evaluated.

Use the applicable repository instructions in `AGENTS.md` and `.github/copilot-instructions.md` as design constraints. This design retains the existing module boundary, adds no dependency, keeps source parsing static, and plans focused unit tests. The repository instructions require typed interfaces and tests for edge cases. No design conflict is known.

## Gate Evaluation

| Gate | Result | Evidence |
|---|---|---|
| Scope | Pass | Planned source and test paths match the feature scope. |
| Public API | Pass | Keep the three named `FunctionIndex` interfaces unchanged. |
| Static analysis | Pass | The design reads AST nodes and does not execute source. |
| Depth and recursion | Pass | Keep `MAX_DEPTH = 4` and use a path-local node identity set. |
| Constitution | Not evaluable | No constitution file exists. Use repository instructions instead. |
| External contract | Not needed | No externally exposed interface change is designed. |

## Design Decisions

1. Keep the existing public name indexes and `lookup` implementation. Add typed private metadata: `dict[int, ast.ClassDef]` for method ownership and `dict[str, list[ast.ClassDef]]` for class-name candidates.
2. Resolve `self.method()` and `cls.method()` against the exact containing `ClassDef`. Resolve inline or local receivers only when a unique indexed class identifies their class. Preserve `Class.method()` and dotted class expressions such as `mh.Class.method()` when the trailing class name is unique.
3. Track simple `Name` assignments in source order with a per-callable `dict[str, ast.ClassDef]`. A direct, uniquely identified constructor or a known local alias assigns a class identity. An unsupported or unknown reassignment clears the binding.
4. Do not merge branch-specific assignments into the outer local map. Analyze calls in each branch with a copy of the incoming map, while preserving sequential assignments within a suite. Invalidate names written in compound control-flow statements after that statement. This avoids stale receiver types without adding a general dataflow engine.
5. Visit each call's function expression first. Visit positional and starred argument expressions in order, then keyword expressions in order, matching Python call evaluation. Visit all arguments before handling the enclosing call. Visit statements in source order. Recurse into a resolved callable at its call site before visiting later calls.
6. Track active callable node identities only during the current descent. Remove an identity when the descent returns. Do not use a global visited set.
7. Treat depth zero as the requested handler. Analyze callable targets through depth four, and do not enter targets at depth five.
8. Leave attribute calls unresolved unless the receiver has supported class context. Keep bare-name lookup conservative through the existing unique-match behavior.

## Regression Design

Add self-contained source fixtures to `tests/unit/test_prompt_audit.py`. Build indexes from temporary source roots. Do not use a consumer checkout.

| Case | Expected result |
|---|---|
| Inline and assigned construction | Follow the method and report its prompt. |
| Dotted class construction and class method calls | Resolve the method only when the trailing class name is unique. |
| Simple alias | Follow both the original receiver and its alias when each call site is reached. |
| Unknown reassignment | Do not keep the earlier class association. |
| `self` with duplicate method names | Follow the method in the current class only. |
| `cls` with duplicate method names | Follow the method in the current class only. |
| Unknown receiver or ambiguous class name | Report no prompt from an unproven target. |
| Ordered nested call arguments | Include positional, starred, and keyword argument expressions in source order before handling the enclosing call. |
| Repeated calls and repeated helper call sites | Preserve each call-site occurrence. |
| Direct and mutual recursion | Terminate without suppressing later, distinct call sites. |
| Depth boundary | Report a prompt at depth four and none from a target at depth five. |

## Project Structure

### Documentation

```text
specs/078-prompt-audit-call-resolution/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
└── quickstart.md
```

No `contracts/` directory is needed because no external interface change is designed. The implementation tasks are recorded in `tasks.md`.

### Source Code

```text
src/misthelper_devtools/prompt_audit.py
tests/unit/test_prompt_audit.py
```

**Structure Decision**: Keep this behavior in the existing single-file tool. Add one focused unit-test module in the repository's existing `tests/unit/` layout.

## Complexity Tracking

No constitution violation or added architectural layer requires an exception.
