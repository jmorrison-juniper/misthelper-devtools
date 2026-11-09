# Data Model: Issue 67 Coverage Repair

This feature adds no persisted data or public schema. The following in-memory AST concepts define the evidence boundary.

| Concept | Relevant data | Relationship and validation |
| --- | --- | --- |
| Imported source class | Module path, imported symbol, local alias | The class must resolve to repository source in the analyzed module. |
| Pytest fixture | Fixture name, resolved pytest decorator, executable return evidence | The return must construct an instance of the imported source class. A shadowed or unrelated decorator does not qualify. |
| Selected test method | Owning class, method name, parameters, calls | A parameter must match the fixture name, and a call must use that parameter to invoke source. |
| Static response helper | Owning class, method name, static-method marker, calls | Include only helpers reached from a selected method, then follow their required definitions. |
| Status signature | Owning class, helper name, status parameter positions | Match calls within that class-qualified identity only. |
| Failure evidence | Status values and existing failure markers in selected source text | Collect from selected tests and their dependency closure only. |

The dependency closure is cycle-safe and deduplicated. It does not include unrelated fixtures, classes, tests, helpers, or statuses.
