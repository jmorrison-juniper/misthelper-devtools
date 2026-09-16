r"""MissingEdgeCaseDetector (T040): flag uncovered input edge cases.

The detector inspects a test module only when the module includes an explicit
`test-quality: edge-case-required=` marker. It avoids assertion helpers, mock
helpers, factories, and HTTP response builders, because their inputs do not
describe the source under test. A `numeric` marker requires zero and negative
coverage. A `collection` marker requires empty-input coverage. A `none` marker
requires `None` coverage.

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
from dataclasses import dataclass  # Compact value object for per-file edge coverage.
from pathlib import Path  # Path metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.

_SUPPORT_CALL_NAMES = frozenset(  # Names that belong to pytest, mocks, or Python helpers.
    {
        "ANY",  # Mock sentinel arguments are not source-under-test inputs.
        "MagicMock",  # Mock construction arguments are not source-under-test inputs.
        "Mock",  # Mock construction arguments are not source-under-test inputs.
        "PropertyMock",  # Mock construction arguments are not source-under-test inputs.
        "SimpleNamespace",  # Namespace fixtures are not production behavior.
        "append",  # List construction in tests is fixture setup, not production behavior.
        "call",  # Mock call objects describe expectations, not source-under-test inputs.
        "dict",  # Container constructors do not identify a source-under-test call.
        "enumerate",  # Loop helper input is not a source-under-test call.
        "float",  # Type conversion input is not a source-under-test call.
        "int",  # Type conversion input is not a source-under-test call.
        "len",  # Length checks are assertions, not source-under-test inputs.
        "list",  # Container constructors do not identify a source-under-test call.
        "patch",  # Mock patch targets are not source-under-test inputs.
        "parametrize",  # Pytest parameter lists are case generation, not source behavior.
        "print",  # Debug output is not a source-under-test call.
        "range",  # Loop bounds are not source-under-test inputs.
        "set",  # Container constructors do not identify a source-under-test call.
        "sorted",  # Sorting helper input is not a source-under-test call.
        "str",  # Type conversion input is not a source-under-test call.
        "tuple",  # Container constructors do not identify a source-under-test call.
        "zip",  # Loop helper input is not a source-under-test call.
    }
)

_SUPPORT_CALL_PREFIXES = (  # Helper prefixes usually build fixtures rather than exercise behavior.
    "_Fake",  # Fake classes create test doubles, not production behavior.
    "_make_",  # Private factory helpers commonly create fake responses or data.
    "assert",  # Assertion helpers check expectations, not source-under-test behavior.
    "build_",  # Builder helpers create fixtures, not production behavior.
    "create_",  # Creation helpers commonly create test data.
    "fake_",  # Fake helpers create doubles, not production behavior.
    "fixture_",  # Fixture helpers are test support by naming convention.
    "make_",  # Factory helpers create fixture data.
    "sample_",  # Sample helpers create fixture data.
)

_HTTP_STATUS_KEYWORDS = frozenset({"status", "status_code"})  # HTTP statuses are categorical codes.
_EDGE_CASE_MARKER = "test-quality: edge-case-required="  # Explicit opt-in marker for this heuristic.
_EDGE_CASE_DOMAINS = frozenset({"collection", "none", "numeric"})  # Supported edge-case domains.


@dataclass
class EdgeCaseCoverage:
    """Store source-under-test edge coverage that one test file demonstrates."""

    requires_empty_input: bool = False  # True when the source under test accepts a collection input.
    requires_numeric_edges: bool = False  # True when the source under test accepts a numeric input.
    has_empty_input: bool = False  # True when an empty collection reaches the source under test.
    has_zero_value: bool = False  # True when zero reaches the source under test.
    has_negative_value: bool = False  # True when a negative integer reaches the source under test.
    has_none_input: bool = False  # True when None reaches the source under test.


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
        domains = self._required_edge_case_domains(source)  # Read explicit domains for this heuristic.
        if not domains:
            _LOGGER.debug("File %s has no edge-case marker; skipping", test_path)
            return []  # Unmarked files are too ambiguous for safe edge-case findings.
        _LOGGER.info("Building edge-case coverage profile for %s", test_path)
        coverage = self._build_coverage_profile(tree)  # Gather only source-under-test edge evidence.
        self._apply_required_domains(coverage, domains)  # Limit obligations to declared input domains.
        _LOGGER.debug("Edge-case coverage profile for %s: %s", test_path, coverage)
        if not coverage.requires_empty_input and not coverage.requires_numeric_edges:
            _LOGGER.debug("File %s is not numeric-SUT testing; skipping", test_path)
            return []  # Non-numeric test files are out of scope for this detector.
        # Accumulate findings for each uncovered edge case.
        findings: list[Finding] = []  # Return accumulator.
        # --- empty_input ------------------------------------------------------
        if coverage.requires_empty_input and not coverage.has_empty_input:
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_empty_input",
                    "No empty-input edge case is exercised.",
                    'Add a test that passes an empty container (e.g. [], "", b"", {}) to the SUT.',
                )
            )
        # --- zero_value -------------------------------------------------------
        if coverage.requires_numeric_edges and not coverage.has_zero_value:
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_zero_value",
                    "No zero-value edge case is exercised.",
                    "Add a test that passes the integer literal 0 to the SUT.",
                )
            )
        # --- negative_value ---------------------------------------------------
        if coverage.requires_numeric_edges and not coverage.has_negative_value:
            findings.append(
                self._finding(
                    posix,
                    "missing_ec_negative_value",
                    "No negative-value edge case is exercised.",
                    "Add a test that passes a negative integer literal (e.g. -1, -5) to the SUT.",
                )
            )
        # --- none_input -------------------------------------------------------
        if "none" in domains and not coverage.has_none_input:
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

    def _required_edge_case_domains(self, source: str) -> set[str]:
        """Return explicitly marked edge-case domains from source text."""
        domains: set[str] = set()  # Accumulate supported domains from marker lines.
        for line in source.splitlines():
            if _EDGE_CASE_MARKER in line:
                marker_text = line.split(_EDGE_CASE_MARKER, 1)[1]  # Keep text after marker.
                domains.update(self._parse_marker_domains(marker_text))  # Merge domains from this line.
        return domains  # Empty set means the file is outside heuristic scope.

    def _parse_marker_domains(self, marker_text: str) -> set[str]:
        """Return supported domains from one marker value."""
        clean_text = marker_text.replace("`", "").strip(" .#")  # Remove comment and Markdown noise.
        raw_domains = {part.strip().lower() for part in clean_text.split(",")}  # Split comma list.
        return raw_domains & _EDGE_CASE_DOMAINS  # Ignore unknown domains so comments stay safe.

    def _apply_required_domains(self, coverage: EdgeCaseCoverage, domains: set[str]) -> None:
        """Disable inferred obligations that the marker did not request."""
        if "collection" not in domains:
            coverage.requires_empty_input = False  # Numeric-only markers must not require containers.
        if "numeric" not in domains:
            coverage.requires_numeric_edges = False  # Collection-only markers must not require numbers.

    def _build_coverage_profile(self, tree: ast.Module) -> EdgeCaseCoverage:
        """Return source-under-test edge coverage that the test file demonstrates."""
        coverage = EdgeCaseCoverage()  # Accumulate coverage across all test functions.
        for call in self._collect_source_under_test_calls(tree):
            self._record_call_coverage(call, coverage)  # Merge this call into the file profile.
        return coverage  # The caller converts missing coverage into findings.

    def _collect_source_under_test_calls(self, tree: ast.Module) -> list[ast.Call]:
        """Return calls that look like source-under-test calls inside `test_*` functions."""
        candidate_calls: list[ast.Call] = []  # Accumulator for non-support calls in tests.
        candidate_names: set[str] = set()  # Call targets with a positive or collection input.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and self._is_non_support_call(sub):
                        candidate_calls.append(sub)  # Save the call for a second target-aware pass.
                        self._record_candidate_name(sub, candidate_names)  # Mark targets that prove a domain.
        source_calls = [call for call in candidate_calls if self._call_leaf_name(call.func) in candidate_names]
        return source_calls  # Return all target-matched calls so edge calls for the same target count.

    def _is_non_support_call(self, call: ast.Call) -> bool:
        """Return True when a call target is not a known test helper."""
        call_name = self._call_leaf_name(call.func)  # Read the stable leaf name for filtering.
        if call_name is None:
            return False  # Dynamic call targets are too uncertain for this heuristic.
        call_path = self._call_path(call.func)  # Read dotted names such as `patch.object`.
        if call_path.startswith("patch."):
            return False  # Mock patch helpers configure tests instead of exercising behavior.
        if self._is_support_call_name(call_name):
            return False  # Known helper calls would create false edge-case findings.
        return True  # The caller decides whether the remaining call carries edge evidence.

    def _record_candidate_name(self, call: ast.Call, candidate_names: set[str]) -> None:
        """Add the call target when this call proves an edge-case domain."""
        call_name = self._call_leaf_name(call.func)  # Read the target name once for reuse.
        if call_name is None:
            return  # Dynamic targets cannot build stable per-target coverage.
        if self._has_edge_case_signal(call):
            candidate_names.add(call_name)  # Track the target so its other edge calls count.

    def _call_leaf_name(self, node: ast.expr) -> str | None:
        """Return the rightmost call target name for a simple call expression."""
        if isinstance(node, ast.Name):
            return node.id  # Plain function call.
        if isinstance(node, ast.Attribute):
            return node.attr  # Method call or assertion helper.
        return None  # Other call targets are too dynamic for this detector.

    def _call_path(self, node: ast.expr) -> str:
        """Return a dotted call path when the target is simple."""
        if isinstance(node, ast.Name):
            return node.id  # Plain function call has a one-part path.
        if isinstance(node, ast.Attribute):
            parent = self._call_path(node.value)  # Recursively build the dotted parent path.
            return f"{parent}.{node.attr}" if parent else node.attr  # Join only when parent exists.
        return ""  # Dynamic call targets have no stable dotted path.

    def _is_support_call_name(self, call_name: str) -> bool:
        """Return True when a call name belongs to test support code."""
        if call_name in _SUPPORT_CALL_NAMES:
            return True  # Exact helper names are not source-under-test calls.
        return call_name.startswith(_SUPPORT_CALL_PREFIXES)  # Naming conventions catch fixture helpers.

    def _has_edge_case_signal(self, call: ast.Call) -> bool:
        """Return True when a call carries numeric or collection edge-case evidence."""
        return self._call_has_numeric_signal(call) or self._call_has_collection_signal(call)

    def _record_call_coverage(self, call: ast.Call, coverage: EdgeCaseCoverage) -> None:
        """Merge one source-under-test call into the file coverage profile."""
        if self._call_has_numeric_signal(call):
            coverage.requires_numeric_edges = True  # A positive integer proves numeric input.
        if self._call_has_collection_signal(call):
            coverage.requires_empty_input = True  # A non-empty collection proves collection input.
        coverage.has_empty_input |= self._has_empty_container_arg(call)  # Track collection edge coverage.
        coverage.has_zero_value |= self._has_zero_arg(call)  # Track zero edge coverage.
        coverage.has_negative_value |= self._has_negative_int_arg(call)  # Track negative edge coverage.
        coverage.has_none_input |= self._has_none_arg(call)  # Track None edge coverage.

    def _call_args(self, call: ast.Call) -> list[ast.expr]:
        """Return all positional and keyword argument expressions from a call."""
        args = list(call.args)  # Copy positional arguments before keyword values are appended.
        args.extend(keyword.value for keyword in call.keywords)  # Include keyword values in coverage.
        return args  # Combined argument list for literal checks.

    def _call_has_numeric_signal(self, call: ast.Call) -> bool:
        """Return True when a call sends a positive integer to the source under test."""
        for arg in call.args:
            if self._is_positive_int(arg) and not self._is_http_status_literal(call, arg, None):
                return True  # A positive integer input creates zero and negative obligations.
        for keyword in call.keywords:
            if self._is_positive_int(keyword.value) and not self._is_http_status_literal(
                call, keyword.value, keyword.arg
            ):
                return True  # Keyword integers count when they are not status codes.
        return False  # No positive integer source-under-test input found.

    def _call_has_collection_signal(self, call: ast.Call) -> bool:
        """Return True when a call sends a non-empty container."""
        return any(self._is_non_empty_container_arg(arg) for arg in self._call_args(call))

    def _is_positive_int(self, node: ast.expr) -> bool:
        """Return True if `node` is a positive integer literal (>= 1)."""
        # Bare `ast.Constant` with int value gates positive-integer detection.
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            # Exclude booleans -- Python's bool is a subclass of int but we do not count it.
            if isinstance(node.value, bool):
                return False  # `True` / `False` are not numeric inputs for this detector.
            return node.value >= 1  # Positive-int threshold.
        return False  # Not a positive int literal.

    def _is_non_empty_container_arg(self, node: ast.expr) -> bool:
        """Return True if an argument proves that the source accepts a container."""
        if isinstance(node, ast.List):
            return bool(node.elts)  # Non-empty lists create an empty-list obligation.
        if isinstance(node, ast.Dict):
            return bool(node.keys)  # Non-empty dicts create an empty-dict obligation.
        if isinstance(node, ast.Tuple):
            return bool(node.elts)  # Non-empty tuples create an empty-tuple obligation.
        return False  # Other values do not prove a collection input.

    def _is_http_status_literal(
        self,
        call: ast.Call,  # Call whose target name provides response context.
        node: ast.expr,  # Candidate numeric argument.
        keyword_name: str | None,  # Keyword name when the argument is a keyword.
    ) -> bool:
        """Return True when an integer literal is an HTTP status code."""
        value = node.value if isinstance(node, ast.Constant) else None  # Extract literal safely.
        if not isinstance(value, int) or isinstance(value, bool):
            return False  # Only integer literals can be HTTP status codes here.
        if keyword_name in _HTTP_STATUS_KEYWORDS and 100 <= value <= 599:
            return True  # Explicit status keywords are categorical, not numeric edges.
        call_name = self._call_leaf_name(call.func) or ""  # Use the call name as weak context.
        status_context = ("response", "status")  # These names usually carry HTTP categorical values.
        has_status_name = any(name in call_name.lower() for name in status_context)  # Match status helpers.
        return has_status_name and 100 <= value <= 599  # Status-like names are not numeric domains.

    def _has_empty_container_arg(self, call: ast.Call) -> bool:
        """Return True if any arg is an empty container literal."""
        for node in self._call_args(call):
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

    def _has_zero_arg(self, call: ast.Call) -> bool:
        """Return True if any arg is the integer literal 0."""
        for node in self._call_args(call):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                # Exclude booleans -- `False == 0` but we treat it as non-edge-case.
                if isinstance(node.value, bool):
                    continue  # Skip booleans -- not a numeric-zero edge case.
                if node.value == 0:
                    return True  # Zero literal argument satisfies the edge case.
        return False  # No zero-int argument found.

    def _has_negative_int_arg(self, call: ast.Call) -> bool:
        """Return True if any arg is a negative int literal (e.g. `-5`)."""
        for node in self._call_args(call):
            # Negative literals parse as UnaryOp(USub, Constant(int)).
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                operand = node.operand  # Inner expression -- expected to be an int constant.
                if isinstance(operand, ast.Constant) and isinstance(operand.value, int):
                    if isinstance(operand.value, bool):
                        continue  # Booleans excluded from numeric detection.
                    if operand.value >= 1:
                        return True  # -1, -5, -42, ... all satisfy the edge case.
        return False  # No negative-int argument found.

    def _has_none_arg(self, call: ast.Call) -> bool:
        """Return True if any arg is the literal `None`."""
        for node in self._call_args(call):
            if isinstance(node, ast.Constant) and node.value is None:
                return True  # None literal argument satisfies the edge case.
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
