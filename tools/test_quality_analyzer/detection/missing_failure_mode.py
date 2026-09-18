"""MissingFailureModeDetector (T036): flag uncovered HTTP failure modes.

The detector inspects a test module only when the source under test has an
inferred network or JSON body risk. It reads repository source imports instead
of a hand-added opt-in marker. Missing modes produce a `missing_fm_*` finding.

Sub-rules and markers:

- missing_fm_connection_timeout: `Timeout` / `ReadTimeout`.
- missing_fm_connection_error:   `ConnectionError` / `ConnectError`.
- missing_fm_http_4xx:           any 3-digit int literal 400-499 in source.
- missing_fm_http_5xx:           any 3-digit int literal 500-599 in source.
- missing_fm_malformed_json:     `JSONDecodeError`.
- missing_fm_empty_body:         empty body literal such as `b""` or `data=""`.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for import + literal scanning.
import logging  # Principle VII structured logging.
import re  # Regex for HTTP status-code marker matching.
from dataclasses import dataclass  # Store inferred failure-mode obligations.
from pathlib import Path  # Path metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.

# Markers used to detect coverage of each failure mode.
# The detector accepts a match on ANY string in the tuple to consider the mode covered.
_TIMEOUT_MARKERS: tuple[str, ...] = ("Timeout", "ReadTimeout")  # Timeout exception names.
_CONNECTION_ERROR_MARKERS: tuple[str, ...] = ("ConnectionError", "ConnectError")  # Conn err names.
_MALFORMED_JSON_MARKERS: tuple[str, ...] = ("JSONDecodeError",)  # Malformed-JSON exception.
_EMPTY_BODY_MARKERS: tuple[str, ...] = ('b""', "b''", 'data=""', "data=''")  # Empty body literals.

# Regex for HTTP status codes 400-599 (three-digit ints starting with 4 or 5).
# Word boundaries prevent 4000 or 5000 from matching.
_STATUS_4XX_RE = re.compile(r"\b4\d\d\b")  # Matches 400-499.
_STATUS_5XX_RE = re.compile(r"\b5\d\d\b")  # Matches 500-599.

_SOURCE_HTTP_MODULES = frozenset({"aiohttp", "httpx", "mistapi", "requests"})  # Libraries that can fail by network.
_SOURCE_JSON_MODULES = frozenset({"json"})  # JSON parser modules that can fail on malformed bodies.


@dataclass
class FailureModeRisk:
    """Store failure modes that can occur in the source under test."""

    network: bool = False  # True when the source can raise connection and HTTP failures.
    json_parse: bool = False  # True when the source can raise body parsing failures.

    @property
    def applies(self) -> bool:
        """Return True when the rule can measure this test module."""
        return self.network or self.json_parse  # Any source risk puts the module in scope.


@dataclass
class FailureModeCoverage:
    """Store failure modes that the test module exercises."""

    connection_timeout: bool = False  # True when a timeout path reaches the caller.
    connection_error: bool = False  # True when a connection path reaches the caller.
    http_4xx: bool = False  # True when a client error path reaches the caller.
    http_5xx: bool = False  # True when a server error path reaches the caller.
    malformed_json: bool = False  # True when invalid JSON reaches the caller.
    empty_body: bool = False  # True when an empty body reaches the caller.


class FailureModeApplicabilityInferer:
    """Infer failure-mode scope from the source code that a test imports."""

    def __init__(self, test_path: Path, tree: ast.Module) -> None:
        """Build source lookup state for one test module."""
        self._test_path = test_path  # Keep the test path so source roots can be resolved.
        self._tree = tree  # Keep the parsed test module for import inspection.
        self._root = self._repository_root(test_path)  # Resolve imports against the nearest source root.
        self._source_cache: dict[Path, tuple[ast.Module, str] | None] = {}  # Avoid reparsing source modules.

    def infer(self) -> FailureModeRisk:
        """Return the failure-mode risk that imported source code exposes."""
        risk = FailureModeRisk()  # Accumulate risk across all source imports.
        self._merge(risk, self._local_source_risk())  # Analyzer fixtures can define their SUT locally.
        for node in self._tree.body:  # Only module imports define the test contract.
            if isinstance(node, ast.ImportFrom) and node.module:  # Direct imports can name source symbols.
                self._merge(risk, self._risk_from_import_from(node))  # Add risk from imported callables.
            if isinstance(node, ast.Import):  # Module imports can expose source functions by attribute.
                self._merge(risk, self._risk_from_import(node))  # Add risk from imported modules.
        return risk  # Return the measured source risk.

    def _risk_from_import_from(self, node: ast.ImportFrom) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for one import statement.
        module_path = self._module_path(node.module or "")  # Resolve only repository source modules.
        module_source = self._source_tree(module_path)  # Parse the module once when it exists.
        if module_source is None:  # Third-party imports do not describe the source under test.
            return risk  # Keep external test helpers outside scope.
        tree, source = module_source  # Split the parsed tree from its source text.
        for alias in node.names:  # Each imported symbol can be the source under test.
            self._merge(risk, self._risk_from_symbol(tree, source, alias.name))  # Inspect the real symbol body.
        return risk  # Return merged risk for the statement.

    def _risk_from_import(self, node: ast.Import) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for one module import statement.
        for alias in node.names:  # One import statement can import several modules.
            module_path = self._module_path(alias.name)  # Resolve only repository source modules.
            alias_name = alias.asname or alias.name.split(".")[-1]  # Tests call this module name.
            self._merge(risk, self._risk_from_module_alias(module_path, alias_name))  # Inspect called symbols.
        return risk  # Return merged risk for the statement.

    def _risk_from_symbol(self, tree: ast.Module, source: str, symbol: str) -> FailureModeRisk:
        for node in tree.body:  # Top-level symbols hold public functions and classes.
            if isinstance(node, ast.FunctionDef) and node.name == symbol:  # Direct function import.
                return self._risk_from_source(ast.get_source_segment(source, node) or "")  # Inspect function body.
            if isinstance(node, ast.ClassDef) and node.name == symbol:  # Direct class import.
                return self._risk_from_source(ast.get_source_segment(source, node) or "")  # Inspect class body.
        return FailureModeRisk()  # Constants and unknown names do not prove network or parse risk.

    def _risk_from_module_alias(self, module_path: Path | None, alias_name: str) -> FailureModeRisk:
        risk = FailureModeRisk()  # Accumulate risk for symbols called through a module alias.
        module_source = self._source_tree(module_path) if module_path is not None else None  # Parse if possible.
        if module_source is None:  # Unknown modules cannot prove source-under-test behavior.
            return risk  # Keep the module outside scope.
        tree, source = module_source  # Split the source module into parsed and raw forms.
        for call in (node for node in ast.walk(self._tree) if isinstance(node, ast.Call)):  # Find test calls.
            if isinstance(call.func, ast.Attribute) and self._root_name(call.func.value) == alias_name:  # Alias call.
                self._merge(risk, self._risk_from_symbol(tree, source, call.func.attr))  # Inspect called symbol.
        return risk  # Return only risk from symbols the test calls.

    def _local_source_risk(self) -> FailureModeRisk:
        if not self._allows_local_source():  # Real test helpers must not create scope by themselves.
            return FailureModeRisk()  # Only imported production source can put real tests in scope.
        source = "\n".join(ast.unparse(node) for node in self._tree.body if not self._is_test_node(node))  # Local SUT.
        return self._risk_from_source(source)  # Analyzer fixtures keep their historical coverage contract.

    def _allows_local_source(self) -> bool:
        resolved = (self._root / self._test_path).resolve() if not self._test_path.is_absolute() else self._test_path
        try:
            resolved.relative_to((self._root / "tests").resolve())  # Real tests usually define local helpers.
            return False  # Do not infer failure-mode scope from real test helper functions.
        except ValueError:
            return True  # Analyzer fixtures and synthetic tests can define a local source under test.

    def _risk_from_source(self, source: str) -> FailureModeRisk:
        try:
            tree = ast.parse(source)  # Parse the source slice so docstrings do not create false scope.
        except SyntaxError:
            return FailureModeRisk()  # Unparseable slices cannot establish applicability.
        network = any(self._node_has_network_risk(node) for node in ast.walk(tree))  # Find executable client use.
        json_parse = any(self._node_has_json_risk(node) for node in ast.walk(tree))  # Find executable JSON parsing.
        return FailureModeRisk(network=network, json_parse=json_parse)  # Require a real parser before JSON findings.

    def _node_has_network_risk(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Import):  # Direct imports identify source dependencies.
            return any(alias.name.split(".")[0] in _SOURCE_HTTP_MODULES for alias in node.names)  # HTTP client.
        if isinstance(node, ast.ImportFrom) and node.module:  # From-imports identify source dependencies.
            return node.module.split(".")[0] in _SOURCE_HTTP_MODULES  # HTTP client module.
        if isinstance(node, ast.Attribute):  # Dotted names catch annotations and direct SDK calls.
            return self._root_name(node) in _SOURCE_HTTP_MODULES  # A client root means network scope.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Dynamic imports often use strings.
            return "mistapi.api." in node.value  # Mist SDK import paths mean cloud API scope.
        return False  # Other syntax does not prove a network operation.

    def _node_has_json_risk(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):  # Require parser execution.
            return False  # Attribute names alone do not prove JSON parsing.
        if node.func.attr == "loads":  # json.loads parses response bodies.
            return self._root_name(node.func) in _SOURCE_JSON_MODULES  # Require the json module root.
        return node.func.attr == "json"  # response.json() can raise JSONDecodeError.

    def _root_name(self, node: ast.AST) -> str:
        current = node  # Walk left through an attribute chain.
        while isinstance(current, ast.Attribute):  # Attributes hold dotted module or object paths.
            current = current.value  # Move toward the root name.
        return current.id if isinstance(current, ast.Name) else ""  # Return a comparable root name.

    def _module_path(self, module_name: str) -> Path | None:
        candidate = self._root / Path(*module_name.split(".")).with_suffix(".py")  # Build a portable source path.
        return candidate if candidate.exists() else None  # Only repository modules are valid evidence.

    def _source_tree(self, module_path: Path | None) -> tuple[ast.Module, str] | None:
        if module_path is None:  # Unknown modules cannot establish source-under-test behavior.
            return None  # The detector must not guess from third-party imports.
        if module_path not in self._source_cache:  # Parse each module at most once.
            self._source_cache[module_path] = self._parse_source(module_path)  # Cache success or failure.
        return self._source_cache[module_path]  # Return the cached parse result.

    def _parse_source(self, module_path: Path) -> tuple[ast.Module, str] | None:
        try:
            source = module_path.read_text(encoding="utf-8")  # Read source so AST nodes can map to text.
            return ast.parse(source, filename=str(module_path)), source  # Return parsed and raw source.
        except (OSError, SyntaxError) as exc:
            _LOGGER.debug("Cannot inspect failure-mode source %s: %s", module_path, exc)  # Explain skipped source.
            return None  # Unreadable source cannot establish applicability.

    def _repository_root(self, test_path: Path) -> Path:
        resolved = (Path.cwd() / test_path).resolve() if not test_path.is_absolute() else test_path.resolve()
        for parent in (resolved.parent, *resolved.parents):  # Walk upward from the test module path.
            if (parent / "pyproject.toml").exists() and (parent / "src").exists():  # Find the nearest project root.
                return parent  # Nested projects must resolve their own source imports.
        return Path.cwd()  # Fall back to the current checkout used by the CLI.

    def _is_test_node(self, node: ast.AST) -> bool:
        return isinstance(node, ast.FunctionDef) and node.name.startswith("test_")  # Exclude pytest cases.

    def _merge(self, target: FailureModeRisk, source: FailureModeRisk) -> None:
        target.network = target.network or source.network  # Preserve any network risk already found.
        target.json_parse = target.json_parse or source.json_parse  # Preserve any parse risk already found.


class MissingFailureModeDetector:
    """Detects HTTP-style tests that omit standard failure-mode coverage."""

    def __init__(self) -> None:
        """Initialize per-run inspection state."""
        self._inspected_paths: set[str] = set()  # Track files where inferred applicability exists.

    def reset_inspection(self) -> None:
        """Clear per-run inspection state before a CLI scan."""
        _LOGGER.info("Resetting missing-failure-mode inspection state")  # Log before resetting singleton state.
        self._inspected_paths.clear()  # Registry instances persist across in-process test invocations.
        _LOGGER.debug("Missing-failure-mode inspection state reset")  # Confirm there is no stale count.

    def inspected_module_count(self) -> int:
        """Return the number of test modules with inferred failure-mode applicability."""
        return len(self._inspected_paths)  # Count unique modules, not findings.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text -- primary substrate for marker matching.
    ) -> list[Finding]:
        """Return one Finding per uncovered failure mode in an HTTP-style test file."""
        _LOGGER.info("Scanning %s for missing failure modes", test_path)
        posix = test_path.as_posix()  # Cross-platform stable path.
        risk = FailureModeApplicabilityInferer(test_path, tree).infer()  # Infer from the imported source under test.
        coverage = self._coverage_from_source(source)  # Read existing test evidence from the whole file.
        _LOGGER.debug("Failure-mode risk for %s: %s", test_path, risk)  # Log the inferred rule scope.
        if not risk.applies:  # Files that test DTOs or route tables do not own network failure tests.
            return []  # Do not emit findings when the source cannot fail this way.
        self._inspected_paths.add(posix)  # Count this module as measured by the detector.
        findings = self._findings_for_coverage(posix, risk, coverage)  # Convert missing evidence to findings.
        _LOGGER.debug("Missing-failure-mode finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Helpers -------------------------------------------------------------

    def _coverage_from_source(self, source: str) -> FailureModeCoverage:
        """Return the failure modes that one test source exercises."""
        return FailureModeCoverage(
            connection_timeout=self._matches_any(source, _TIMEOUT_MARKERS),  # Timeout marker proves timeout path.
            connection_error=self._matches_any(source, _CONNECTION_ERROR_MARKERS),  # Connection marker proof.
            http_4xx=bool(_STATUS_4XX_RE.search(source)),  # Any 4xx status proves client-error coverage.
            http_5xx=bool(_STATUS_5XX_RE.search(source)),  # Any 5xx status proves server-error coverage.
            malformed_json=self._matches_any(source, _MALFORMED_JSON_MARKERS),  # JSONDecodeError marker proof.
            empty_body=self._matches_any(source, _EMPTY_BODY_MARKERS),  # Empty body marker proof.
        )

    def _findings_for_coverage(
        self,
        posix: str,  # POSIX file path.
        risk: FailureModeRisk,  # Source-under-test failure modes.
        coverage: FailureModeCoverage,  # Test evidence for this module.
    ) -> list[Finding]:
        """Return findings for each required failure mode that lacks evidence."""
        findings: list[Finding] = []  # Accumulate file-level rule findings.
        if risk.network and not coverage.connection_timeout:  # Network calls can time out.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_timeout",
                    "No connection-timeout failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.Timeout` (or equivalent).",
                )
            )
        if risk.network and not coverage.connection_error:  # Network calls can fail before an HTTP response exists.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_error",
                    "No connection-error failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.ConnectionError` (or equivalent).",
                )
            )
        if risk.network and not coverage.http_4xx:  # Cloud APIs can refuse or reject the request.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_4xx",
                    "No HTTP 4xx failure mode is exercised.",
                    "Add a test whose fake response has a 4xx status_code (e.g. 400, 404).",
                )
            )
        if risk.network and not coverage.http_5xx:  # Cloud APIs can return a server error.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_5xx",
                    "No HTTP 5xx failure mode is exercised.",
                    "Add a test whose fake response has a 5xx status_code (e.g. 500, 503).",
                )
            )
        if risk.json_parse and not coverage.malformed_json:  # JSON parsers can reject malformed response bodies.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_malformed_json",
                    "No malformed-JSON failure mode is exercised.",
                    "Add a test where `.json()` raises `json.JSONDecodeError`.",
                )
            )
        if risk.json_parse and not coverage.empty_body:  # Empty response bodies can break JSON parsing.
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_empty_body",
                    "No empty-body failure mode is exercised.",
                    'Add a test whose fake response body is empty (e.g. b"").',
                )
            )
        return findings  # Return all missing failure-mode findings.

    def _matches_any(self, source: str, needles: tuple[str, ...]) -> bool:
        """Return True if any of `needles` appears as a substring of `source`."""
        # Substring test is sufficient: markers are distinctive exception / literal names.
        return any(needle in source for needle in needles)

    def _finding(
        self,
        posix: str,  # POSIX file path.
        rule_id: str,  # Sub-rule id (missing_fm_*).
        explanation: str,  # Human-facing message.
        remediation: str,  # Suggested fix.
    ) -> Finding:
        """Construct a MEDIUM-severity Finding with common metadata."""
        return Finding(
            category=Category.MISSING_FAILURE_MODE,
            rule_id=rule_id,
            severity=Severity.MEDIUM,
            file_path=posix,
            line_number=1,  # File-level finding -- point at file header.
            explanation=explanation,
            remediation=remediation,
            heuristic=False,
            related_source=posix,
        )


# Register a default instance on import (T019 registry contract).
DetectorRegistry.append(MissingFailureModeDetector())  # Singleton registration.
