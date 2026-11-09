# Research: Issue 67 Coverage Repair

This design uses repository inspection and the feature specification. No research agents or external dependencies were used.

## Existing AST Selection

**Decision**: Extend `SourceDrivenCoverageSlicer` in `src/misthelper_devtools/test_quality_analyzer/detection/missing_failure_mode.py`.

**Rationale**: The slicer already resolves repository imports, selects source-driving test functions, and emits their local dependency closure. The detector passes that sliced text to the existing failure-evidence inferer.

**Alternatives considered**: Add a second slicer or scan full test modules. Both duplicate logic or admit unrelated evidence.

## Fixture Proof

**Decision**: Recognize same-module pytest fixtures through pytest-resolved decorator names and actual executable returns of imported source-class instances. Require the selected test to call through the matching fixture parameter.

**Rationale**: This ties fixture selection to pytest naming and a concrete source instance. Shadowed names, annotations, comments, and unused fixture parameters cannot establish the call.

**Alternatives considered**: Select from import presence, fixture naming alone, type annotations, or any source-class reference. Each can select tests without evidence that they execute source.

## Class Helper Closure

**Decision**: Resolve class-scoped static response helpers from selected test methods by their owning class. Traverse only called helpers and their required definitions.

**Rationale**: This recovers failure evidence that lives in a referenced helper while excluding unrelated class members and tests.

**Alternatives considered**: Include the full class or match helpers by method name alone. The former admits unrelated evidence. The latter can bind to a different class.

## Status Signature Matching

**Decision**: Keep status-helper signatures and call matching class-qualified.

**Rationale**: Class identity prevents a same-named method in another class from contributing status evidence.

**Alternatives considered**: Preserve bare method-name matching. It cannot distinguish same-named methods in separate classes.

## Dependencies and Unresolved Questions

Use the existing Python AST and pytest dependencies. The spec bounds discovery to one module and excludes general return-type inference. No unresolved clarification remains.
