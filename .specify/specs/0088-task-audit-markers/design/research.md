# Research: Task Audit Marker Classification

## Decision 1: Use a heuristic, not a Markdown parser

**Decision**: Classify bullets with regular expressions.
**Rationale**: The module has no Markdown dependency, and the package keeps four runtime dependencies. The task files use a small set of bullet shapes.
**Alternative rejected**: A full CommonMark parser. It adds a dependency for one report line.

## Decision 2: Treat a legend label as prose

**Decision**: A capital label such as `P` or `US1` is a legend, unless a task ID directly follows it.
**Rationale**: The consumer scan found 27 legend bullets such as `- [P] tasks = different files`. The same shape with a task ID is a task with a bad box.

## Decision 3: Exclude `X` from labels

**Decision**: `LABEL_RE` starts with a letter other than `X`.
**Rationale**: `X` is a valid checkbox mark, and the box pattern handles it first.

## Decision 4: Reject links in the box pattern

**Decision**: A box followed by `(`, `[`, or `:` is not a task.
**Rationale**: CommonMark renders `- [x](url)` as a link, so it must not count as a checked task.

## Decision 5: Remove bracket spans before the task ID check

**Decision**: `[T001]` in the rest of a line is link text.
**Rationale**: `- [Docs] [T001]` with a `[T001]:` definition is prose.

## Decision 6: Keep the exit policy

**Decision**: Do not change `blocks_merge` or the citation rule.
**Rationale**: The issue asks to keep the completed-spec and citation decisions.

## Known Limits

The heuristic can miss a malformed marker that has a multi-character box and no task ID. It trades that miss for no false positives on legends.
