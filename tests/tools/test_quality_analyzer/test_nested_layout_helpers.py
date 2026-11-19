"""Regression tests for test helpers in a nested src-layout package.

A real test under ``<root>/tests`` treats only imported symbols as source under test.
"""

from __future__ import annotations  # Defer annotation evaluation for cleaner typing.

import ast  # Parse each synthetic test module.
from pathlib import Path  # Build the nested repository layout.

import pytest  # Fixtures for the scratch repository and the working directory.

from misthelper_devtools.test_quality_analyzer.detection import missing_edge_case, missing_failure_mode

_TEST_PATH = "framework/tests/test_build.py"  # Relative path, as the gate passes it from the repository root.

_LOCAL_HELPER_TEST = """
def build(ports=None):
    return ports or []


def test_build_keeps_ports():
    assert build(ports=[1]) == [1]
"""

_IMPORTED_SOURCE_TEST = """
from vmm_topology.sut import build


def test_build_keeps_ports():
    assert build(ports=[1]) == [1]
"""

_LOCAL_JSON_HELPER_TEST = """
def read_payload(response):
    return response.json()


def test_read_payload_returns_mapping(response):
    assert read_payload(response) == {}
"""


@pytest.fixture
def nested_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a nested package with src, pyproject.toml, tests, and one source module, then enter it."""
    package = tmp_path / "framework"  # Nested package root, as the gate sees it.
    (package / "src").mkdir(parents=True)  # The src directory marks the package root.
    (package / "pyproject.toml").write_text("[project]\nname = 'framework'\n", encoding="utf-8")  # Root marker.
    (package / "tests").mkdir()  # Real tests live under this directory.
    (package / "vmm_topology").mkdir()  # Imported source package for the true-positive case.
    source = "def build(ports=None):\n    return ports or []\n"  # Source with an optional parameter.
    (package / "vmm_topology" / "sut.py").write_text(source, encoding="utf-8")  # Resolvable source module.
    monkeypatch.chdir(tmp_path)  # Resolve the relative test path from the repository root.
    return tmp_path


def _edge_rules(source: str) -> list[str]:
    """Return the edge-case rule identifiers that the detector reports for one test module."""
    tree = ast.parse(source)  # Parse the synthetic test module once.
    findings = missing_edge_case.MissingEdgeCaseDetector().detect(Path(_TEST_PATH), tree, source)  # Run the rule.
    return [finding.rule_id for finding in findings]  # Keep the rule identifiers for comparison.


def _failure_mode_rules(source: str) -> list[str]:
    """Return the failure-mode rule identifiers that the detector reports for one test module."""
    tree = ast.parse(source)  # Parse the synthetic test module once.
    detector = missing_failure_mode.MissingFailureModeDetector()  # Fresh detector for this module.
    findings = detector.detect(Path(_TEST_PATH), tree, source)  # Run the failure-mode rule.
    return [finding.rule_id for finding in findings]  # Keep the rule identifiers for comparison.


@pytest.mark.usefixtures("nested_repository")
def test_local_helper_is_not_source_under_test() -> None:
    """A helper that a real test defines is not source under test in a nested layout."""
    assert _edge_rules(_LOCAL_HELPER_TEST) == []  # The helper's optional parameter is outside the contract.


@pytest.mark.usefixtures("nested_repository")
def test_imported_source_is_still_source_under_test() -> None:
    """An imported source function keeps its None-input obligation in a nested layout."""
    assert "missing_ec_none_input" in _edge_rules(_IMPORTED_SOURCE_TEST)  # The import defines the contract.


@pytest.mark.usefixtures("nested_repository")
def test_local_json_helper_is_not_failure_mode_source() -> None:
    """A helper that a real test defines does not create a malformed-JSON obligation in a nested layout."""
    assert "missing_fm_malformed_json" not in _failure_mode_rules(_LOCAL_JSON_HELPER_TEST)  # Test-only helper.
