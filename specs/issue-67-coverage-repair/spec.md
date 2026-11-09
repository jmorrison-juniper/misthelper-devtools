# Feature Specification: Issue 67 Coverage Repair

**Feature Branch**: Existing branch unchanged. No branch operation is authorized.

**Created**: 2026-11-09

**Status**: Ready for planning

**Input**: Repair issue #67 only. Count tests through source-class fixtures and referenced class-scoped static response helpers. Preserve unrelated exclusions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Count Tests Through Fixtures (Priority: P1)

An engineer analyzes tests that use a pytest fixture returning an instance of an imported repository-source class.
The analyzer counts a test when it calls a source method through that fixture parameter.

**Why this priority**: The current omission can report missing failure coverage that the test already supplies.

**Independent Test**: Compare a fixture-driven test with a direct-source test that checks the same failure behavior.
Both must contribute the same failure coverage.

**Acceptance Scenarios**:

1. **Given** a fixture returns an imported source-class instance, **When** a test calls its method through a fixture parameter, **Then** the analyzer selects that test.
2. **Given** the selected test checks a failure response, **When** the analyzer measures coverage, **Then** that check contributes coverage.
3. **Given** an unrelated fixture or an unused source fixture, **When** a test does not call source, **Then** the analyzer excludes that test.

### User Story 2 - Count Referenced Static Response Helpers (Priority: P1)

An engineer uses a class-scoped static response helper in a selected test method.
The analyzer includes the helper's relevant failure evidence without counting unrelated members of that class.

**Why this priority**: Test selection alone does not recover failure evidence in an omitted helper.

**Independent Test**: Compare a selected test using a static response helper with an equivalent test containing the response directly.
Both must contribute the same failure coverage.

**Acceptance Scenarios**:

1. **Given** a selected test calls its class-scoped static response helper, **When** the analyzer measures coverage, **Then** the referenced helper contributes failure evidence.
2. **Given** a referenced helper uses another response definition, **When** the analyzer measures coverage, **Then** it includes that required definition.
3. **Given** an unused helper, sibling test, or unrelated class contains other failure statuses, **When** analysis runs, **Then** those statuses contribute no coverage.

### Edge Cases

- A fixture parameter exists but the test does not call a method through it.
- A fixture references a source class but returns an unrelated object.
- Two test classes use the same helper name with different response statuses.
- A referenced response helper and an unused response helper have different failure statuses.
- A source import or a marker exists in a module containing unrelated tests.
- Multiple selected tests share a helper. Analysis must terminate and preserve the same coverage result.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The analyzer MUST select a test that calls source through a fixture returning an imported repository-source class instance.
- **FR-002**: Fixture use MUST establish a connection to the returned source instance. Fixture presence or an incidental source reference MUST NOT suffice.
- **FR-003**: A selected test MUST contribute its existing failure evidence as an equivalent direct-source test does.
- **FR-004**: The analyzer MUST include class-scoped static response helpers referenced by selected tests, plus their required response definitions.
- **FR-005**: Helper selection MUST respect the owning class. An unrelated helper with the same name MUST NOT contribute evidence.
- **FR-006**: Unrelated fixtures, classes, tests, helpers, and status values MUST NOT count toward failure coverage.
- **FR-007**: A source import or a marker MUST NOT select every test or definition in a module or class.
- **FR-008**: Existing direct-source selection and referenced module-level helper coverage MUST retain their current results.

### Key Entities

- **Selected test**: A test with an established call connection to repository source.
- **Source fixture**: A pytest fixture that returns an instance of an imported repository-source class.
- **Response helper**: A static helper owned by a test class and referenced by a selected test.
- **Failure evidence**: A relevant failure check or response status from a selected test or its required definitions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Both positive acceptance comparisons produce identical coverage for fixture-driven and equivalent direct-source tests.
- **SC-002**: Every negative acceptance case contributes zero additional coverage from unrelated definitions or status values.
- **SC-003**: Existing direct-source and module-helper regression cases retain 100% of their prior coverage results.
- **SC-004**: An engineer can interpret both repaired reports without a false missing-coverage finding for the represented failure checks.

## Assumptions

- This repair concerns `SourceDrivenCoverageSlicer` in `src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py`.
- Existing source resolution and failure classification remain authoritative. This repair changes selection and required evidence inclusion only.
- Fixtures and helpers are available in the analyzed test module. Cross-file fixture discovery and general return-type inference are outside this repair.
- Flask application factories and arbitrary indirect client creation are outside this repair.
- The user owns implementation and tests. This specification does not authorize source edits, test edits, or GitHub write actions.
