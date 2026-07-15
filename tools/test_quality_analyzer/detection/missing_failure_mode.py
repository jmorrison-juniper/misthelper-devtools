"""MissingFailureModeDetector (T036): flag uncovered HTTP failure modes.

The detector inspects a test module. If the module appears to exercise an
HTTP-style SUT (identified by import of `requests` or `httpx`, or by any
occurrence of an HTTP marker such as `status_code` in the source text),
then each of the six FR-006 failure modes must have at least one marker in
the file's source. Missing modes produce a `missing_fm_*` finding.

Sub-rules and markers:

- missing_fm_connection_timeout: `Timeout` / `ReadTimeout`.
- missing_fm_connection_error:   `ConnectionError` / `ConnectError`.
- missing_fm_http_4xx:           any 3-digit int literal 400-499 in source.
- missing_fm_http_5xx:           any 3-digit int literal 500-599 in source.
- missing_fm_malformed_json:     `JSONDecodeError`.
- missing_fm_empty_body:         empty bytes literal `b""` in source.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # AST inspection for import + literal scanning.
import logging  # Principle VII structured logging.
import re  # Regex for HTTP status-code marker matching.
from pathlib import Path  # Path metadata.

from tools.test_quality_analyzer.detection import (  # Registry + shared types.
    Category,
    DetectorRegistry,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.

# HTTP libraries whose presence marks the file as HTTP-testing.
_HTTP_MODULES = frozenset({"requests", "httpx", "aiohttp"})  # Recognized clients.

# Markers used to detect coverage of each failure mode.
# The detector accepts a match on ANY string in the tuple to consider the mode covered.
_TIMEOUT_MARKERS: tuple[str, ...] = ("Timeout", "ReadTimeout")  # Timeout exception names.
_CONNECTION_ERROR_MARKERS: tuple[str, ...] = ("ConnectionError", "ConnectError")  # Conn err names.
_MALFORMED_JSON_MARKERS: tuple[str, ...] = ("JSONDecodeError",)  # Malformed-JSON exception.
_EMPTY_BODY_MARKERS: tuple[str, ...] = ('b""', "b''")  # Empty bytes literals.

# Regex for HTTP status codes 400-599 (three-digit ints starting with 4 or 5).
# Word boundaries prevent 4000 or 5000 from matching.
_STATUS_4XX_RE = re.compile(r"\b4\d\d\b")  # Matches 400-499.
_STATUS_5XX_RE = re.compile(r"\b5\d\d\b")  # Matches 500-599.


class MissingFailureModeDetector:
    """Detects HTTP-style tests that omit standard failure-mode coverage."""

    def __init__(self) -> None:
        """No configuration required."""
        return  # Explicit noop -- inline-comment principle.

    # --- Detector protocol ---------------------------------------------------

    def detect(
        self,
        test_path: Path,  # File under analysis.
        tree: ast.Module,  # Parsed AST.
        source: str,  # Raw source text -- primary substrate for marker matching.
    ) -> list[Finding]:
        """Return one Finding per uncovered failure mode in an HTTP-style test file."""
        _LOGGER.info("Scanning %s for missing failure modes", test_path)
        # POSIX-normalized file path stored on each finding.
        posix = test_path.as_posix()  # Cross-platform stable path.
        # Skip files that are not HTTP-testing; nothing to check.
        if not self._is_http_test_module(tree, source):
            _LOGGER.debug("File %s is not HTTP-testing; skipping", test_path)
            return []  # No findings for non-HTTP modules.
        # Collect findings for each uncovered failure mode.
        findings: list[Finding] = []  # Accumulator returned to caller.
        # --- connection_timeout ---------------------------------------------
        if not self._matches_any(source, _TIMEOUT_MARKERS):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_timeout",
                    "No connection-timeout failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.Timeout` (or equivalent).",
                )
            )
        # --- connection_error -----------------------------------------------
        if not self._matches_any(source, _CONNECTION_ERROR_MARKERS):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_connection_error",
                    "No connection-error failure mode is exercised.",
                    "Add a test that raises `requests.exceptions.ConnectionError` (or equivalent).",
                )
            )
        # --- http_4xx --------------------------------------------------------
        if not _STATUS_4XX_RE.search(source):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_4xx",
                    "No HTTP 4xx failure mode is exercised.",
                    "Add a test whose fake response has a 4xx status_code (e.g. 400, 404).",
                )
            )
        # --- http_5xx --------------------------------------------------------
        if not _STATUS_5XX_RE.search(source):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_http_5xx",
                    "No HTTP 5xx failure mode is exercised.",
                    "Add a test whose fake response has a 5xx status_code (e.g. 500, 503).",
                )
            )
        # --- malformed_json --------------------------------------------------
        if not self._matches_any(source, _MALFORMED_JSON_MARKERS):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_malformed_json",
                    "No malformed-JSON failure mode is exercised.",
                    "Add a test where `.json()` raises `json.JSONDecodeError`.",
                )
            )
        # --- empty_body ------------------------------------------------------
        if not self._matches_any(source, _EMPTY_BODY_MARKERS):
            findings.append(
                self._finding(
                    posix,
                    "missing_fm_empty_body",
                    "No empty-body failure mode is exercised.",
                    'Add a test whose fake response body is empty (e.g. b"").',
                )
            )
        _LOGGER.debug("Missing-failure-mode finding count for %s: %s", test_path, len(findings))
        return findings

    # --- Helpers -------------------------------------------------------------

    def _is_http_test_module(self, tree: ast.Module, source: str) -> bool:
        """Return True if this file exercises an HTTP-style SUT."""
        # Scan module-scope imports for known HTTP client modules.
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _HTTP_MODULES:
                        return True  # `import requests` -> HTTP-testing module.
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in _HTTP_MODULES:
                    return True  # `from requests import ...` -> HTTP-testing.
        # Fallback: any status_code reference implies HTTP testing even without imports.
        return "status_code" in source  # Common HTTP marker not tied to any client.

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
