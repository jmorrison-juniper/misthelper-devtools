# Implementation Plan: Task Audit Marker Classification

**Branch**: `jmorrison-jnpr-speckit-task-audit-markdown-links` | **Date**: 2026-11-15
**Spec**: [.specify/specs/0088-task-audit-markers/spec.md](spec.md)

## Summary

Classify each bracketed bullet in a task file as a task box, a link or legend, or a malformed marker. Keep each malformed line number in `TaskCounts`. Print each one with its path and line. Keep the exit policy.

## Technical Context

**Language/Version**: Python 3.13 or newer.
**Primary Dependencies**: Standard library only (`re`, `dataclasses`).
**Testing**: pytest, with `tests/unit/tools/test_speckit_task_audit.py`.
**Project Type**: Command-line tool in `src/misthelper_devtools/`.
**Constraints**: Ruff, Black, and strict mypy over the full package. No new dependency.

## Constitution Check

The optional `.specify/memory/constitution.md` file is absent. This plan does not invent one. The repository `AGENTS.md` rules apply.

## Project Structure

```text
.specify/specs/0088-task-audit-markers/
  spec.md
  plan.md
  tasks.md
  design/
src/misthelper_devtools/speckit_task_audit.py
tests/unit/tools/test_speckit_task_audit.py
```

## Design Decisions

1. **Box pattern**: `CHECKBOX_RE` rejects a box followed by `(`, `[`, or `:`. The citation validator uses the same pattern, so it also skips link bullets.
2. **Malformed pattern**: `MALFORMED_RE` matches a bracketed bullet that is not a link, reference link, or label definition.
3. **Legend labels**: `LABEL_RE` matches capital labels such as `P` or `US1`. `X` is excluded. A label is malformed only when `LEADING_TASK_ID_RE` finds a task ID right after it.
4. **Other boxes**: An empty or one-character box is malformed. A longer box is malformed when a task ID follows it. `BRACKET_SPAN_RE` removes bracket spans first, so link text does not count.
5. **Report**: `TaskCounts.malformed_lines` holds the line numbers. `SpecTaskAudit` prints one line for each.
6. **Reference definitions**: `_reference_labels` collects `[label]:` definitions outside code fences, in lower case. A bullet whose first label, or whose second label after it, is defined is a link and is not malformed. Undefined labels keep rules 3 and 4.
7. **Exit policy**: `blocks_merge` and the citation logic do not change.

Details are in `design/`.

## Complexity Tracking

The patch changes `TaskCounts`, `TaskFileScanner`, and `SpecTaskAudit` in one source file. No second source module changes.

## Unavailable Automation

The Spec Kit helper scripts and the constitution are absent from this repository. The author wrote these records by hand from `.specify/templates/` and the 0086 pattern. No `speckit` command ran. The analyze stage did not run.
