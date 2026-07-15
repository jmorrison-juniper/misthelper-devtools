"""TautologicalTestDetector (T032): flag assertions that are always true.

Implements four sub-rules:

- taut_literal_true: `assert True` / `assert 1` / `assert "x"` -- constant
  operand that is unconditionally truthy.
- taut_literal_equality: `assert C == C` where BOTH operands are literal
  constants.
- taut_variable_self_compare: `assert x == x` where both operands are the
  same Name node.
- taut_isinstance_type_self: `assert isinstance(x, type(x))` -- reflexive
  type check that is always true.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for tautology patterns.
import logging  # Principle VII structured logging.
from pathlib import Path  # Path arithmetic for finding metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


class TautologicalTestDetector:
    """Detects logically-always-true assertions within a test file."""

    def __init__(self) -> None:
        """No configuration required."""
        return  # Explicit noop -- inline-comment principle.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text (unused).
    ) -> list[Finding]:
        """Return one Finding per tautological assertion detected in `tree`."""
        _LOGGER.info("Scanning %s for tautological assertions", test_path)
        # POSIX-normalized file path stored on each finding.
        posix = test_path.as_posix()  # Cross-platform stable representation.
        # Accumulator returned to caller.
        findings: list[Finding] = []  # Per-file findings list.
        # Walk every Assert node in the module; we do NOT restrict to test_* here
        # because tautologies at module scope are equally suspect.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue  # Only Assert statements matter for tautology checks.
            classified = self._classify(node, posix)  # Optional Finding.
            if classified is not None:
                findings.append(classified)
        _LOGGER.debug("Tautology finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Classification ------------------------------------------------------

    def _classify(self, node: ast.Assert, posix: str) -> Finding | None:
        """Return a Finding if `node.test` matches a tautology sub-rule."""
        test = node.test  # `assert <test>` operand.
        # --- taut_literal_true ----------------------------------------------
        if isinstance(test, ast.Constant) and bool(test.value):
            return Finding(
                category=Category.TAUTOLOGICAL,
                rule_id="taut_literal_true",
                severity=Severity.MEDIUM,
                file_path=posix,
                line_number=node.lineno,
                explanation="assert on a truthy literal is always true.",
                remediation=("Assert on a real SUT result rather than a hard-coded truthy value."),
                heuristic=False,
                related_source=posix,
            )
        # --- taut_isinstance_type_self --------------------------------------
        if isinstance(test, ast.Call) and self._is_isinstance_type_self(test):
            return Finding(
                category=Category.TAUTOLOGICAL,
                rule_id="taut_isinstance_type_self",
                severity=Severity.MEDIUM,
                file_path=posix,
                line_number=node.lineno,
                explanation=("isinstance(x, type(x)) is always true regardless of SUT behavior."),
                remediation=("Compare against a specific expected type such as `int` or `str`."),
                heuristic=False,
                related_source=posix,
            )
        # --- Compare-based tautologies --------------------------------------
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
            op = test.ops[0]  # Single-op compare.
            left = test.left  # Left operand.
            right = test.comparators[0]  # Right operand.
            # taut_literal_equality: both operands are ast.Constant with equal value.
            if isinstance(op, ast.Eq) and isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
                return Finding(
                    category=Category.TAUTOLOGICAL,
                    rule_id="taut_literal_equality",
                    severity=Severity.MEDIUM,
                    file_path=posix,
                    line_number=node.lineno,
                    explanation=("Both operands are literals -- comparison is decided at write time."),
                    remediation=("Compare a SUT result to an expected literal instead of two literals."),
                    heuristic=False,
                    related_source=posix,
                )
            # taut_variable_self_compare: `x == x` (identical Name nodes).
            if (
                isinstance(op, ast.Eq)
                and isinstance(left, ast.Name)
                and isinstance(right, ast.Name)
                and left.id == right.id
            ):
                return Finding(
                    category=Category.TAUTOLOGICAL,
                    rule_id="taut_variable_self_compare",
                    severity=Severity.MEDIUM,
                    file_path=posix,
                    line_number=node.lineno,
                    explanation="Comparing a variable to itself is always true.",
                    remediation=("Compare the SUT result to an independent expected value."),
                    heuristic=False,
                    related_source=posix,
                )
        # Not a recognized tautology pattern.
        return None

    def _is_isinstance_type_self(self, call: ast.Call) -> bool:
        """Return True if `call` is `isinstance(x, type(x))` with matching x."""
        # Verify the outer callable is the builtin `isinstance` name.
        if not (isinstance(call.func, ast.Name) and call.func.id == "isinstance"):
            return False  # Not the isinstance builtin.
        # Must have exactly two positional args and no keywords.
        if len(call.args) != 2 or call.keywords:
            return False  # Wrong shape.
        subject = call.args[0]  # `x` in `isinstance(x, type(x))`.
        second = call.args[1]  # Second arg -- must be `type(x)`.
        # The second arg must be a Call to the `type` builtin with a single arg.
        if not isinstance(second, ast.Call):
            return False  # Not a call.
        if not (isinstance(second.func, ast.Name) and second.func.id == "type"):
            return False  # Not the type() builtin.
        if len(second.args) != 1 or second.keywords:
            return False  # Wrong shape for type(x).
        inner = second.args[0]  # `x` inside type().
        # Both x references must be the same Name id for the tautology to hold.
        return isinstance(subject, ast.Name) and isinstance(inner, ast.Name) and subject.id == inner.id


# Register a default instance on import (T019 registry contract).
DetectorRegistry.append(TautologicalTestDetector())  # Singleton registration.
