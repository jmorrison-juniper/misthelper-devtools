"""WeakAssertionDetector (T028): flag weak assertion patterns.

Implements FR-004 sub-rules:

- weak_bare_assert: `assert result` with a truthiness-only single Name/Attribute.
- weak_is_not_none: `assert x is not None` comparison.
- weak_mock_called_no_args: `<mock>.assert_called()` invoked with zero args.
- weak_pytest_raises_exception: `pytest.raises(Exception)` (bare Exception class).
- weak_zero_assertions: a `test_*` function containing zero `Assert` statements
  AND zero `assert_*` method calls on mocks / `pytest.raises` blocks.
- weak_self_mock_echo: `assert <name>(...) == <name>.return_value` where both
  operands derive from the same mock identifier.

The detector conforms to the `Detector` structural protocol.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for assertion patterns.
import logging  # Principle VII structured logging.
from pathlib import Path  # Path arithmetic for finding metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.

# Mock-assertion method names that verify call arguments (strong).
_STRONG_MOCK_ASSERT_METHODS = frozenset(
    {
        "assert_called_with",  # Positional / keyword args verified.
        "assert_called_once_with",  # Called exactly once with args.
        "assert_any_call",  # At least one call matched args.
        "assert_has_calls",  # Call list verified.
    },
)


class WeakAssertionDetector:
    """Detects weak assertion patterns within a test file."""

    def __init__(self) -> None:
        """No configuration required for the detector."""
        # Nothing to store; state is confined to per-detect() locals.
        return  # Explicit noop to satisfy inline-comment rule.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST of the test file.
        source: str,  # Raw source text (unused).
    ) -> list[Finding]:
        """Return one Finding per weak assertion detected in `tree`."""
        # info-before per Principle VII.
        _LOGGER.info("Scanning %s for weak assertions", test_path)
        # Aggregate findings across every top-level test_* function.
        findings: list[Finding] = []  # Accumulator returned to caller.
        # POSIX-normalized path for cross-platform stable output.
        posix = test_path.as_posix()  # File path stored on each finding.
        # Walk to collect every test_* function at any nesting (module or class).
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # Non-function node.
            if not node.name.startswith("test_"):
                continue  # Only public test functions matter here.
            findings.extend(self._analyze_function(node, posix))
        # debug-after with finding count.
        _LOGGER.debug("Weak-assertion finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Internal helpers ----------------------------------------------------

    def _analyze_function(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,  # Test function to scan.
        posix: str,  # POSIX path for finding metadata.
    ) -> list[Finding]:
        """Return findings for a single test function's body."""
        # Local accumulator per-function so zero-assert detection is scoped.
        findings: list[Finding] = []  # Per-function finding list.
        # Track whether this test contains ANY meaningful assertion.
        has_assert_stmt = False  # `assert ...` statement seen.
        has_mock_assert = False  # `mock.assert_*` method call seen.
        has_pytest_raises = False  # `with pytest.raises(...)` seen.
        # Walk the body once for structural checks.
        for sub in ast.walk(func):
            # --- Bare / non-none / self-echo -- all show up as ast.Assert ----
            if isinstance(sub, ast.Assert):
                has_assert_stmt = True  # Count this as a "real" assertion.
                finding = self._classify_assert(sub, posix)  # Weak sub-rule?
                if finding is not None:
                    findings.append(finding)  # Record classification.
            # --- Mock method calls -----------------------------------------
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                attr = sub.func.attr  # Method name being invoked.
                if attr in _STRONG_MOCK_ASSERT_METHODS:
                    has_mock_assert = True  # Strong assertion counted.
                elif attr == "assert_called" and not sub.args and not sub.keywords:
                    has_mock_assert = True  # Still counts as an assertion attempt.
                    findings.append(
                        Finding(
                            category=Category.WEAK_ASSERTION,
                            rule_id="weak_mock_called_no_args",
                            severity=Severity.MEDIUM,
                            file_path=posix,
                            line_number=sub.lineno,
                            explanation=("mock.assert_called() has no argument verification."),
                            remediation=(
                                "Use assert_called_with(<expected_args>) or "
                                "assert_called_once_with(...) to verify call arguments."
                            ),
                            heuristic=False,
                            related_source=posix,
                        ),
                    )
            # --- pytest.raises(Exception) via `with` --------------------------
            elif isinstance(sub, ast.With):
                for item in sub.items:  # Iterate context managers on the With.
                    call = item.context_expr  # Expression that produces the CM.
                    if not isinstance(call, ast.Call):
                        continue  # Skip non-call CMs.
                    if not self._is_pytest_raises(call):
                        continue  # Not a pytest.raises(...) call.
                    has_pytest_raises = True  # Counts as an assertion mechanism.
                    if call.args and self._is_bare_exception(call.args[0]):
                        findings.append(
                            Finding(
                                category=Category.WEAK_ASSERTION,
                                rule_id="weak_pytest_raises_exception",
                                severity=Severity.MEDIUM,
                                file_path=posix,
                                line_number=call.lineno,
                                explanation=("pytest.raises(Exception) is too broad; hides the real error."),
                                remediation=("Narrow to the specific exception class you expect."),
                                heuristic=False,
                                related_source=posix,
                            ),
                        )
        # --- Zero-assertion sweep after the walk --------------------------
        if not (has_assert_stmt or has_mock_assert or has_pytest_raises):
            findings.append(
                Finding(
                    category=Category.WEAK_ASSERTION,
                    rule_id="weak_zero_assertions",
                    severity=Severity.MEDIUM,
                    file_path=posix,
                    line_number=func.lineno,
                    explanation=("Test function '%s' contains no assertions." % func.name),
                    remediation=("Add at least one assertion verifying observable behavior."),
                    heuristic=False,
                    related_source=posix,
                ),
            )
        return findings

    def _classify_assert(self, node: ast.Assert, posix: str) -> Finding | None:
        """Return a Finding if the `assert` statement matches a weak sub-rule."""
        # Extract the operand expression from the assert node.
        test = node.test  # `assert <test>` -- test is the operand.
        # --- weak_is_not_none ------------------------------------------------
        if isinstance(test, ast.Compare):
            # Handle `x is not None` (single-op compare with IsNot + None).
            if (
                len(test.ops) == 1
                and isinstance(test.ops[0], ast.IsNot)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None
            ):
                return Finding(
                    category=Category.WEAK_ASSERTION,
                    rule_id="weak_is_not_none",
                    severity=Severity.MEDIUM,
                    file_path=posix,
                    line_number=node.lineno,
                    explanation="assert x is not None only verifies non-None state.",
                    remediation=("Assert on the actual value / type / structure you expect."),
                    heuristic=False,
                    related_source=posix,
                )
            # --- weak_self_mock_echo ---------------------------------------
            if len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1:
                left_id = self._mock_root(test.left)  # Root name of left operand.
                right_id = self._mock_root(test.comparators[0])  # Right root name.
                # Both operands must derive from the same mock AND right side must
                # be a return_value attribute for the "self-echo" tautology.
                if (
                    left_id is not None
                    and left_id == right_id
                    and self._is_mock_return_value_ref(test.comparators[0])
                    and isinstance(test.left, ast.Call)
                ):
                    return Finding(
                        category=Category.WEAK_ASSERTION,
                        rule_id="weak_self_mock_echo",
                        severity=Severity.MEDIUM,
                        file_path=posix,
                        line_number=node.lineno,
                        explanation=("Assertion compares a mock's call result to its own return_value."),
                        remediation=("Assert against the SUT output rather than the mock's echo."),
                        heuristic=False,
                        related_source=posix,
                    )
            # Other compare shapes are considered strong assertions.
            return None
        # --- weak_bare_assert ------------------------------------------------
        # A bare `assert <name>` or `assert <name>.attr` is truthiness-only.
        if isinstance(test, (ast.Name, ast.Attribute)):
            return Finding(
                category=Category.WEAK_ASSERTION,
                rule_id="weak_bare_assert",
                severity=Severity.MEDIUM,
                file_path=posix,
                line_number=node.lineno,
                explanation="Bare assert -- truthiness only, no expected value comparison.",
                remediation=("Compare the result to the expected value with `assert result == <value>`."),
                heuristic=False,
                related_source=posix,
            )
        # Not a recognized weak pattern.
        return None

    def _is_pytest_raises(self, call: ast.Call) -> bool:
        """Return True if `call` is `pytest.raises(...)`."""
        # `pytest.raises(...)` -> Call(func=Attribute(value=Name('pytest'), attr='raises')).
        func = call.func  # Callable being invoked.
        if not isinstance(func, ast.Attribute):
            return False  # Bare name -- not the pytest.raises pattern.
        if func.attr != "raises":
            return False  # Attribute must be `raises`.
        return isinstance(func.value, ast.Name) and func.value.id == "pytest"

    def _is_bare_exception(self, node: ast.expr) -> bool:
        """Return True if `node` names the bare builtin `Exception`."""
        # Only bare `Exception` counts; namespaced references are considered narrower.
        return isinstance(node, ast.Name) and node.id == "Exception"

    def _mock_root(self, node: ast.expr) -> str | None:
        """Return the outermost `ast.Name` id feeding into `node`, else None."""
        # For `mock()`: Call(func=Name('mock')) -> 'mock'.
        # For `mock.return_value`: Attribute(value=Name('mock'), attr='return_value') -> 'mock'.
        current: ast.expr = node  # Traversal cursor.
        # Peel Call -> Attribute -> Name until we hit a Name node.
        while True:
            if isinstance(current, ast.Name):
                return current.id  # Root identifier found.
            if isinstance(current, ast.Attribute):
                current = current.value  # Descend into the object side.
                continue
            if isinstance(current, ast.Call):
                current = current.func  # Descend into the callable expression.
                continue
            return None  # Unrecognized shape.

    def _is_mock_return_value_ref(self, node: ast.expr) -> bool:
        """Return True if `node` is `<name>.return_value` on a mock-like object."""
        # Recognize Attribute(attr='return_value', value=Name(...)).
        return isinstance(node, ast.Attribute) and node.attr == "return_value" and isinstance(node.value, ast.Name)


# Register a default instance on import (T019 registry contract).
DetectorRegistry.append(WeakAssertionDetector())  # Singleton registration.
