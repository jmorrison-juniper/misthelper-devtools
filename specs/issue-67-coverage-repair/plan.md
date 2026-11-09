# Implementation Plan: Issue 67 Coverage Repair

**Branch**: `jmorrison-jnpr-analyzer-coverage-inference` | **Date**: 2026-11-09 | **Spec**: `specs/issue-67-coverage-repair/spec.md`

## Summary

Extend the existing AST slicer to select tests that call repository source through a same-module pytest fixture. Include only the referenced class-scoped static response helpers and their required definitions. Keep unrelated tests, helpers, fixtures, and status values outside coverage.

## Technical Context

**Language/Version**: Python 3.13+
**Primary Dependencies**: Python standard-library `ast` and the existing pytest dependency. Add no dependencies.
**Storage**: N/A
**Testing**: pytest and the existing analyzer meta-fixture tests.
**Target Platform**: Windows, macOS, and Linux.
**Project Type**: Python library and command-line analyzer.
**Performance Goals**: Preserve bounded, deterministic AST traversal. Deduplicate shared dependencies.
**Constraints**: Reuse `SourceDrivenCoverageSlicer` and current source resolution. Limit fixture discovery to the analyzed module. Preserve direct-source and module-helper results.
**Scale/Scope**: Issue 67 only. Change the analyzer selection and evidence closure. Do not broaden exclusions or infer arbitrary return types.

## Constitution Check

No `.specify/memory/constitution.md` exists, so no project constitution gates are defined. The repository instructions require focused changes, tests, and no new dependency. This plan adds no production, test, README, or Git/GitHub changes.

## Design Decisions

- Accept fixture evidence only when the decorator resolves to pytest's `fixture` name, including imported aliases. Ignore similarly named decorators and names shadowed by local or module bindings.
- Match a test parameter to a fixture by pytest's fixture name. Require an executable return path that constructs an instance of an imported repository-source class. Imports, annotations, docstrings, and unused references alone do not qualify.
- Select the test only when it calls a method through that fixture parameter. Keep existing direct-source selection unchanged.
- For a selected test method, resolve static helpers within its owning class. Follow only called helpers and their required response definitions. Emit those definitions, not the entire class or sibling tests.
- Keep status-argument signatures keyed by owning class and method. Do not match calls only by a shared method name across classes.

## Project Structure

```text
specs/issue-67-coverage-repair/
├── plan.md
├── research.md
├── data-model.md
└── quickstart.md

src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py
tests/tools/test_quality_analyzer/test_meta_fixtures.py
```

**Structure Decision**: Keep implementation within the existing failure-mode detector and validate it with its analyzer meta-fixture tests. No external interface or contract changes are planned.

## Complexity Tracking

No constitution violations or added project complexity require justification.
