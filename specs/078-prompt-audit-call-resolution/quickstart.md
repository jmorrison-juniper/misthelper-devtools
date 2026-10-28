# Quickstart: Prompt Audit Call Resolution

## Prerequisites

- Use Python 3.13 or newer.
- Install the repository development dependencies.
- Run commands from the repository root.

## Validate the Regression Fixtures

Run the focused unit tests:

```sh
python -m pytest tests/unit/test_prompt_audit.py -q
```

Expected result: all inline and assigned construction, dotted class call, alias, `self`, `cls`, ambiguity, unresolved-target, order, repetition, recursion, and depth-boundary cases pass.

Run the full test suite:

```sh
python -m pytest
```

Expected result: all repository tests pass, including the new prompt audit tests.

## Fixture Validation Scenarios

The unit tests should build `FunctionIndex` from temporary source roots. They must not read a MistHelper checkout.

1. Define two classes with the same method name and distinct prompt helpers. Verify `self` and `cls` resolve within the correct class.
2. Call methods through inline and dotted constructors, and call `Class.method()` and `mh.Class.method()`. Verify unique class names resolve, and duplicate class names do not.
3. Construct a local receiver, call a method, alias the receiver, and call the method again. Verify both prompt occurrences and their order.
4. Reassign a known receiver from an unresolved expression. Verify that the previous class association is not used.
5. Nest recognized prompt calls in mixed positional, starred, and keyword arguments. Verify calls follow source evaluation order before the enclosing call.
6. Add direct and mutual recursive paths. Verify termination and preservation of later, separate call sites.
7. Create a call chain with prompts at depths four and five. Verify only the depth-four prompt appears.

For entity and resolution rules, see [data-model.md](data-model.md). For design choices, see [research.md](research.md).
