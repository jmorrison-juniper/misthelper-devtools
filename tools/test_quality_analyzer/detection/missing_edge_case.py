r"""MissingEdgeCaseDetector (T040): flag uncovered numeric-input edge cases.

The detector inspects a test module. If the module appears to exercise a
numeric / collection SUT (identified by the presence of at least one
positive integer literal being passed as a call argument inside a
`test_*` function), then each of the four FR-007 edge cases must have at
least one marker in the file's source. Missing markers produce a
`missing_ec_*` finding with `heuristic=True`.

Sub-rules and markers:

- missing_ec_empty_input:   any empty container literal `[]`, `{}`, `""`, `b""`.
- missing_ec_zero_value:    the integer literal `0` used as a call argument.
- missing_ec_negative_value: any negative int literal `-\d+` used as a call argument.
- missing_ec_none_input:    the literal `None` used as a call argument.

All findings emitted by this detector are heuristic (heuristic=True) per
plan.md's classification of edge-case detection as best-effort.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for numeric-SUT gating + literal scanning.
import logging  # Principle VII structured logging.
from pathlib import Path  # Path metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


class MissingEdgeCaseDetector:
    """Detects numeric-style tests that omit standard edge-case coverage."""

    def __init__(self) -> None:
        """No configuration required."""
        return  # Explicit noop -- inline-comment principle.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text used for auxiliary marker scans.
    ) -> list[Finding]:
        """Return one heuristic Finding per uncovered edge case in a numeric test file."""
        _LOGGER.info("Scanning %s for missing edge cases", test_path)
        # POSIX-normalized file path stored on each finding.
        posix = test_path.as_posix()  # Cross-platform stable path.
        # Gather every literal used as a positional call argument inside `test_*` funcs.
        call_arg_literals = self._collect_test_call_arg_literals(tree)  # AST-derived set.
        # Gate: at least one positive integer must appear as a call arg -> numeric SUT.
        if not any(self._is_positive_int(node) for node in call_arg_literals):
            _LOGGER.debug("File %s is not numeric-SUT testing; skipping", test_path)
            return []  # Non-numeric test files are out of scope for this detector.
        # Accumulate findings for each uncovered edge case.
        findings: list[Finding] = []  # Return accumulator.
        # --- empty_input ------------------------------------------------------
        if not self._has_empty_container_arg(call_arg_literals):
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_empty_input",
                    "No empty-input edge case is exercised.",
                    'Add a test that passes an empty container (e.g. [], "", b"", {}) to the SUT.',
                )
            )
        # --- zero_value -------------------------------------------------------
        if not self._has_zero_arg(call_arg_literals):
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_zero_value",
                    "No zero-value edge case is exercised.",
                    "Add a test that passes the integer literal 0 to the SUT.",
                )
            )
        # --- negative_value ---------------------------------------------------
        if not self._has_negative_int_arg(call_arg_literals):
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_negative_value",
                    "No negative-value edge case is exercised.",
                    "Add a test that passes a negative integer literal (e.g. -1, -5) to the SUT.",
                )
            )
        # --- none_input -------------------------------------------------------
        if not self._has_none_arg(call_arg_literals):
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_none_input",
                    "No None-input edge case is exercised.",
                    "Add a test that passes the literal None to the SUT.",
                )
            )
        _LOGGER.debug("Missing-edge-case finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Helpers -------------------------------------------------------------

    def _collect_test_call_arg_literals(self, tree: ast.Module) -> list[ast.expr]:
        """Return every positional-argument expression from calls inside `test_*` funcs."""
        # Walk every top-level definition; only look inside functions whose name starts test_.
        args: list[ast.expr] = []  # Accumulator of argument expressions.
        for node in ast.walk(tree):
            # Only pytest test functions are examined -- avoids SUT-body false positives.
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # Walk the body of the test function collecting every Call arg expression.
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        # Positional args only -- kwargs are excluded by design.
                        args.extend(sub.args)  # Append each expr as-is (no normalization).
        return args  # Full list of argument expressions across every test_ function.

    def _is_positive_int(self, node: ast.expr) -> bool:
        """Return True if `node` is a positive integer literal (>= 1)."""
        # Bare `ast.Constant` with int value gates positive-integer detection.
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            # Exclude booleans -- Python's bool is a subclass of int but we do not count it.
            if isinstance(node.value, bool):
                return False  # `True` / `False` are not numeric inputs for this detector.
            return node.value >= 1  # Positive-int threshold.
        return False  # Not a positive int literal.

    def _has_empty_container_arg(self, nodes: list[ast.expr]) -> bool:
        """Return True if any arg is an empty container literal."""
        for node in nodes:
            # Empty list literal `[]`.
            if isinstance(node, ast.List) and not node.elts:
                return True  # Empty list argument satisfies the edge case.
            # Empty dict literal `{}`.
            if isinstance(node, ast.Dict) and not node.keys:
                return True  # Empty dict argument satisfies the edge case.
            # Empty tuple literal `()`.
            if isinstance(node, ast.Tuple) and not node.elts:
                return True  # Empty tuple argument satisfies the edge case.
            # Empty set literal via `set()` -- handled by regular Call, not here.
            # Empty string / empty bytes constants.
            if isinstance(node, ast.Constant):
                if isinstance(node.value, str) and node.value == "":
                    return True  # Empty string argument.
                if isinstance(node.value, bytes) and node.value == b"":
                    return True  # Empty bytes argument.
        return False  # No empty container literal argument found.

    def _has_zero_arg(self, nodes: list[ast.expr]) -> bool:
        """Return True if any arg is the integer literal 0."""
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                # Exclude booleans -- `False == 0` but we treat it as non-edge-case.
                if isinstance(node.value, bool):
                    continue  # Skip booleans -- not a numeric-zero edge case.
                if node.value == 0:
                    return True  # Zero literal argument satisfies the edge case.
        return False  # No zero-int argument found.

    def _has_negative_int_arg(self, nodes: list[ast.expr]) -> bool:
        """Return True if any arg is a negative int literal (e.g. `-5`)."""
        for node in nodes:
            # Negative literals parse as UnaryOp(USub, Constant(int)).
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                operand = node.operand  # Inner expression -- expected to be an int constant.
                if isinstance(operand, ast.Constant) and isinstance(operand.value, int):
                    if isinstance(operand.value, bool):
                        continue  # Booleans excluded from numeric detection.
                    if operand.value >= 1:
                        return True  # -1, -5, -42, ... all satisfy the edge case.
        return False  # No negative-int argument found.

    def _has_none_arg(self, nodes: list[ast.expr]) -> bool:
        """Return True if any arg is the literal `None`."""
        for node in nodes:
            # `None` parses as ast.Constant(value=None).
            if isinstance(node, ast.Constant) and node.value is None:
                return True  # None-literal argument satisfies the edge case.
        return False  # No None argument found.

    def _finding(
        self,
        posix: str,  # POSIX file path.
        rule_id: str,  # Sub-rule id (missing_ec_*).
        explanation: str,  # Human-facing message.
        remediation: str,  # Suggested fix.
    ) -> Finding:
        """Construct a heuristic MEDIUM-severity Finding with common metadata."""
        return Finding(
            category=Category.MISSING_EDGE_CASE,
            rule_id=rule_id,
            severity=Severity.MEDIUM,
            file_path=posix,
            line_number=1,  # File-level finding -- point at file header.
            explanation=explanation,
            remediation=remediation,
            heuristic=True,  # Edge-case detection is heuristic per plan.md.
            related_source=posix,
        )


# Register a default instance on import (T019 registry contract).
DetectorRegistry.append(MissingEdgeCaseDetector())  # Singleton registration.
