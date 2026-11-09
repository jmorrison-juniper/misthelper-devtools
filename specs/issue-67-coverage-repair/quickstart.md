# Quickstart: Issue 67 Coverage Repair

## Prerequisites

- Use Python 3.13 or newer.
- Install the project and development dependencies in the repository virtual environment.
- Run commands from the repository root.

## Validate the repair

1. Add an analyzer fixture with a pytest fixture that returns an imported source-class instance.
2. Add a test that calls source through the fixture parameter.
3. Add negative cases for unused fixtures, unrelated objects, and shadowed pytest names.
4. Add a fixture with no executable return path.
5. Add a case where a selected test calls a static response helper.
6. Add another class with the same helper name and a different status.
7. Run the analyzer meta-fixture tests:

   ```sh
   python -m pytest tests/tools/test_quality_analyzer/test_meta_fixtures.py
   ```

8. Run the analyzer test package:

   ```sh
   python -m pytest tests/tools/test_quality_analyzer
   ```

Expected results: Fixture-driven tests provide the same failure coverage as equivalent direct-source tests. Unused or unrelated data adds no coverage. Existing direct-source and module-level helper results stay unchanged.

See [the specification](spec.md), [the plan](plan.md), and [the data model](data-model.md) for scope and evidence rules.
