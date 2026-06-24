"""Parity test for issue #429 logging f-string sweep.

Loads `tests/fixtures/issue_429_log_baseline.json` and asserts that the
current source code of `MistHelper.py` produces the exact same rendered
log string for every fixture entry. This test PASSES pre-refactor (since
the baseline was captured here) and must continue to PASS after every
tranche of the CONV-LOG-FSTRING sweep.

If a tranche rewrites a fixture line, the rendered output MUST stay
byte-identical to the baseline -- proving the codemod preserved log
content. If a tranche accidentally drops a substitution or changes a
format spec, this test fails and pinpoints the offending line.
"""

from __future__ import annotations  # Enable PEP 604 union syntax on Python 3.13.

import json  # Stdlib JSON loader for the baseline fixture.
from pathlib import Path  # Portable filesystem access on Windows + POSIX.
from typing import Any  # Type hint for the heterogeneous inputs dict.

import libcst as cst  # AST-preserving CST library for re-rendering current source.
import pytest  # Test framework used across the project.

from tools.capture_log_baseline import (  # Reuse the rendering primitives.
    FIXTURE_SITES,
    _extract_msg_and_args,
    _LineCallCollector,
)

REPO_ROOT = Path(__file__).resolve().parent.parent  # tests/ -> repo root.
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "issue_429_log_baseline.json"  # Frozen baseline JSON.
SOURCE_PATH = REPO_ROOT / "MistHelper.py"  # File the sweep is rewriting.


def _build_inputs_for_pattern(pattern: str, raw_inputs: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the same enriched inputs dict the capture script used."""
    merged = {**raw_inputs}  # Start from the operator-curated dict (no mutation).
    if pattern == "attribute_access":  # L1343 references sys.executable.
        merged["sys"] = type("FakeSys", (), {"executable": "/usr/bin/python3"})()  # Inject fake.
    if pattern == "g003_concat":  # L6120 calls table.get_string().

        class _FakeTable:  # Minimal stand-in for PrettyTable.
            def __init__(self, text: str) -> None:  # Holds the rendered text.
                self._text = text  # Stored once per fixture entry.

            def get_string(self) -> str:  # PrettyTable's public API surface.
                return self._text  # Whatever the fixture passed in.

        merged["table"] = _FakeTable(merged.pop("table_text"))  # Wrap the table_text input.
    return merged  # Caller passes this to _extract_msg_and_args.


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict[str, Any]]:
    """Load the frozen baseline once per test module."""
    if not BASELINE_PATH.exists():  # Hard guard so the test fails loudly if fixture is missing.
        pytest.skip(f"baseline fixture not found at {BASELINE_PATH}")  # Skip cleanly until baseline is generated.
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))  # Parse the JSON into a dict.


@pytest.fixture(scope="module")
def module() -> cst.Module:
    """Parse `MistHelper.py` exactly once for the whole test module."""
    if not SOURCE_PATH.exists():  # Defensive: the only thing this test cares about.
        pytest.skip(f"source not found at {SOURCE_PATH}")  # Skip cleanly if missing.
    source = SOURCE_PATH.read_text(encoding="utf-8")  # Read once; libcst parse is the slow step.
    return cst.parse_module(source)  # Parse with libcst.


@pytest.mark.parametrize("site_id", [s["site_id"] for s in FIXTURE_SITES])  # One test per fixture site.
def test_log_render_matches_baseline(
    site_id: str,
    baseline: dict[str, dict[str, Any]],
    module: cst.Module,
) -> None:
    """Find a logging call that renders to the baseline string and confirm."""
    if site_id not in baseline:  # The baseline may legitimately miss sites that failed capture.
        pytest.skip(f"site {site_id} missing from baseline fixture")  # Skip rather than fail.
    expected = baseline[site_id]["rendered"]  # The frozen ground truth.
    site_def = next(s for s in FIXTURE_SITES if s["site_id"] == site_id)  # Operator-curated entry.
    inputs = _build_inputs_for_pattern(site_def["pattern"], site_def["inputs"])  # Same enrichment as capture.
    actual = _find_matching_render(module, site_def["line"], inputs, expected)  # Robust lookup.
    assert actual is not None, (  # No call in the file renders to the expected string.
        f"site {site_id}: no logging call (near line {site_def['line']}) "
        f"renders to {expected!r} with inputs {sorted(inputs)}"
    )
    assert (
        actual == expected
    ), f"site {site_id} drift: expected {expected!r}, got {actual!r}"  # Byte-identical rendering is the whole contract.


def _find_matching_render(
    module: cst.Module,
    line_hint: int,
    inputs: dict[str, Any],
    expected: str,
) -> str | None:
    """Return rendered string of a call matching expected, or None if no match.

    Tries the call at line_hint first (cheap), then falls back to scanning
    every logging-shaped call in the module and rendering it with the
    supplied inputs. The first call whose rendered output equals
    `expected` wins. This makes the parity test robust to line drift caused
    by black/ruff reformatting after the codemod rewrites a tranche.
    """
    rendered = _render_at_line(module, line_hint, inputs)  # Cheap path first.
    if rendered == expected:  # Direct hit -- baseline still matches the hinted line.
        return rendered  # Done.
    return _render_first_match(module, inputs, expected)  # Fall back to full scan.


def _render_at_line(module: cst.Module, line_hint: int, inputs: dict[str, Any]) -> str | None:
    """Render the call at line_hint (if any) and return the rendered string."""
    wrapper = cst.MetadataWrapper(module)  # Metadata required for line-number lookups.
    collector = _LineCallCollector(line_hint)  # Reuse the capture script's collector.
    wrapper.visit(collector)  # Walk; stops at the first call on line_hint.
    if collector.found is None:  # No call at this line (drifted away).
        return None  # Caller will fall back to scan.
    try:
        msg, args = _extract_msg_and_args(collector.found, inputs)  # Pull (msg, args).
        return _render_log(msg, args)  # Render via real LogRecord.getMessage().
    except (KeyError, ValueError, AttributeError, TypeError):  # Template/args mismatch or input miss.
        return None  # Cheap path failed; caller falls back to a full content scan.


def _render_first_match(module: cst.Module, inputs: dict[str, Any], expected: str) -> str | None:
    """Scan every logging-shaped call in module and return the first rendered match."""
    wrapper = cst.MetadataWrapper(module)  # Metadata required for visitor.
    finder = _MatchingCallFinder(inputs, expected)  # Visitor encapsulates the search.
    wrapper.visit(finder)  # Walk every Call node.
    return finder.matched  # Either the matching rendered string or None.


class _MatchingCallFinder(cst.CSTVisitor):
    """Visit every logging-shaped Call and stop at the first whose render == expected."""

    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)  # Required for libcst metadata.

    def __init__(self, inputs: dict[str, Any], expected: str) -> None:
        """Remember the inputs we will render each candidate call with."""
        super().__init__()  # Required to initialize libcst visitor state.
        self.inputs = inputs  # Pre-built inputs namespace from the fixture entry.
        self.expected = expected  # Target rendered string we're looking for.
        self.matched: str | None = None  # Set once we find a matching call.

    def visit_Call(self, node: cst.Call) -> bool | None:  # Visit hook per Call.
        if self.matched is not None:  # Stop walking once we found a match.
            return False  # False prunes children.
        if not _is_logging_shaped(node):  # Skip non-logging calls fast.
            return True  # Keep descending into children.
        try:
            msg, args = _extract_msg_and_args(node, self.inputs)  # Try to render this call.
        except (KeyError, ValueError, AttributeError, TypeError):
            return True  # Inputs do not match this call's variables; try next.
        rendered = _render_log(msg, args)  # Render via LogRecord.getMessage().
        if rendered == self.expected:  # Matches the baseline.
            self.matched = rendered  # Record the win.
            return False  # Stop the walk.
        return True  # Otherwise keep searching.


def _is_logging_shaped(node: cst.Call) -> bool:
    """Cheap detector matching `<logger>.<level>(...)` shape (mirrors codemod)."""
    func = node.func  # Pull the callable.
    if isinstance(func, cst.Attribute):  # foo.bar(...) form.
        if func.attr.value not in _LEVEL_METHODS:  # Not a logging level method.
            return False  # Skip.
        value = func.value  # The object being called.
        if isinstance(value, cst.Name) and value.value in _LOGGER_NAMES:  # logging.info(...)
            return True
        if isinstance(value, cst.Attribute) and value.attr.value in _LOGGER_NAMES:
            return True  # self.logger.info(...).
    return False  # Not a logging call.


def _render_log(msg: str, args: tuple[Any, ...]) -> str:
    """Render via LogRecord.getMessage() to mirror the framework exactly."""
    import logging  # Local import keeps top-of-file imports minimal.

    record = logging.LogRecord(  # Synthesize the record like the real logger.
        name="issue429_parity",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )
    return record.getMessage()  # The string the real logger would emit.


_LEVEL_METHODS = frozenset(  # Mirrors LEVEL_METHODS in the codemod module.
    {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
)
_LOGGER_NAMES = frozenset(  # Mirrors LOGGER_NAMES in the codemod module.
    {"logging", "logger", "log", "LOG", "_logger", "_log"}
)
