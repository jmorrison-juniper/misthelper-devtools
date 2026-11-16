# Feature Specification: Task Audit Marker Classification

**Feature Branch**: `jmorrison-jnpr-speckit-task-audit-markdown-links`

**Created**: 2026-11-15

**Status**: Draft

**Input**: Upstream issue jmorrison-juniper/misthelper-devtools#88, consumer issue jmorrison-juniper/MistHelper#4703, and the user rules about non-overlapping work and one pull request for each file set.

**Record order**: The source candidate (three local commits on base `0fdcf610`) was written and reviewed before these records. These records describe that candidate. They do not claim that the specification came first.

## User Scenarios & Testing

### User Story 1 - Count only real task markers (Priority: P1)

A maintainer runs `speckit-task-audit` on a repository. The malformed total must count only bullets that try to be task boxes. Markdown links and Spec Kit legend bullets must not count.

**Why this priority**: The consumer report counted 36 lines, and all 36 were normal Markdown (27 legend bullets and 9 links). The count hid real defects.

**Independent Test**: Scan one task file that holds links, reference links, label bullets, legend bullets, and real tasks. Compare each count with the expected value.

**Acceptance Scenarios**:

1. **Given** a bullet such as `- [x](https://example.invalid)`, **When** the scanner reads it, **Then** it counts as no task and no malformed marker.
2. **Given** `- [Docs] [T001]` with a `[T001]:` definition, **When** the scanner reads it, **Then** it is not malformed.
3. **Given** `- [P] tasks in Phase 6 (`T025`) can run in parallel`, **When** the scanner reads it, **Then** it is a legend and is not malformed.
4. **Given** `- [Label]: text` or `- [ref][id]`, **When** the scanner reads it, **Then** it is not malformed.
5. **Given** `- [x] T001`, `- [ ] T002`, **When** the scanner reads them, **Then** they count as one checked and one open task.
6. **Given** `- [a]` and a `[a]: https://example.invalid` definition, **When** the scanner reads the bullet, **Then** it is a shortcut reference link and is not malformed.
7. **Given** `- [Docs] [T001] relates to T002` and a `[T001]:` definition, **When** the scanner reads the bullet, **Then** it is a reference link in prose and is not malformed.
8. **Given** `- [z] T001 do this` with no `[z]:` definition, **When** the scanner reads it, **Then** it is malformed.

### User Story 2 - Report each real malformed marker (Priority: P1)

A maintainer needs the file and line of each real malformed marker, so the maintainer can repair it.

**Why this priority**: The report printed a total only.

**Independent Test**: Run the audit on a tree with one bad box, one legend, and one valid task. Read the printed report.

**Acceptance Scenarios**:

1. **Given** `- [z] Do this task` or `- [] T001 text` or `- [-] text`, **When** the audit runs, **Then** it prints `specs/<path>/tasks.md:<line>: malformed task marker`.
2. **Given** `- [todo] T002 text`, **When** the audit runs, **Then** it reports that line as malformed.
3. **Given** `- [P] T001 text` (a label that stands in for a box), **When** the audit runs, **Then** it reports that line as malformed.
4. **Given** a valid legend on another line, **When** the audit runs, **Then** it does not report that line.

### User Story 3 - Keep the exit policy (Priority: P2)

Malformed markers alone must not change the exit code. The completed-spec and citation decisions stay as they are.

**Independent Test**: Run the audit on a Draft spec with only a malformed marker and read the exit code.

**Acceptance Scenarios**:

1. **Given** a Draft spec with a malformed marker and no open tasks, **When** the audit runs, **Then** the exit code is 0.
2. **Given** a complete spec with an open task, **When** the audit runs, **Then** the exit code is 1, as before.

### Edge Cases

- A fenced code block holds task-like text; the scanner skips it, as before.
- A label in capital letters (`P`, `US1`) is a legend. The letter `X` is a checkbox mark, so it is not a label.
- A one-character box other than a space or `x` is malformed.
- A box with more than one character is malformed only when a task ID follows it.
- A task ID that sits inside a bracket span, such as `[T001]`, is link text and does not mark a task.

## Requirements

### Functional Requirements

- **FR-001**: A task box (`- [ ]`, `- [x]`, `- [X]`) followed by `(`, `[`, or `:` MUST NOT count as a task.
- **FR-002**: A bracketed bullet that is a Markdown link, reference link, or label definition MUST NOT count as malformed.
- **FR-003**: An uppercase legend label MUST be malformed only when a task ID directly follows it.
- **FR-004**: An empty or one-character box MUST be malformed.
- **FR-005**: A longer box MUST be malformed only when a task ID follows it outside bracket spans.
- **FR-006**: The audit MUST print the relative file path and line of each malformed marker.
- **FR-007**: The audit MUST keep the existing exit policy, completed-spec rule, and citation rule.
- **FR-009**: A bullet whose first bracket label, or whose second bracket label after the first, has a reference definition in the same file outside code fences MUST NOT count as malformed. An undefined label keeps the earlier rules.
- **FR-008**: The scanner MUST keep the line numbers of malformed markers in `TaskCounts`.

### Key Entities

- **TaskCounts**: The checked, unchecked, and malformed counts of one task file, plus the malformed line numbers.
- **TaskFileScanner**: Reads one task file and classifies each bullet.
- **SpecTaskAudit**: Runs the scan for each feature record and prints the report.

## Success Criteria

- **SC-001**: A task file with links and legends reports 0 malformed markers.
- **SC-002**: Each real malformed marker appears in the report with its path and line.
- **SC-003**: The exit code stays the same for each case that the earlier tests covered.

## Assumptions

- A legend label is one or more capital letters or digits that start with a letter other than `X`.
- The classification is a heuristic. It does not parse Markdown in full.
- No constitution exists in this repository, so the specification does not cite one.
