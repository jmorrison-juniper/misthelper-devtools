"""Contract tests for the shared advisory exclusion drift report."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from misthelper_devtools.exclusion_drift import Exclusion, ExclusionDriftReporter


@pytest.fixture()
def drift_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create a small exclusion manifest and scan tree."""
    manifest = tmp_path / "quality_gate_exclusions.fixture.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "recorded_at": "2026-01-01",
                "entries": [{"gate": "ruff", "path": "scripts", "scan_path": "scripts", "recorded_count": 1}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "example.py").write_text('print("x")\n', encoding="utf-8")
    return tmp_path, manifest


class TestExclusionDriftReporter:
    """Check count parsing and the non-blocking drift contract."""

    def test_ruff_json_count(self) -> None:
        """Ruff JSON findings must count as one result each."""
        output = '[{"code":"E501"},{"code":"F401"}]'
        assert ExclusionDriftReporter._count_findings("ruff", output) == 2

    def test_ruff_empty_body_count_is_zero(self) -> None:
        """An empty tool JSON body must count as zero findings."""
        output = b"".decode()  # Model a tool that returned no JSON bytes.
        assert ExclusionDriftReporter._count_findings("ruff", output) == 0  # The report must not crash.

    def test_ruff_malformed_json_count_is_zero(self) -> None:
        """A malformed tool JSON body must count as zero findings."""
        output = "{not valid JSONDecodeError"  # Model a damaged tool JSON body.
        assert ExclusionDriftReporter._count_findings("ruff", output) == 0  # The report must not crash.

    def test_mypy_error_count(self) -> None:
        """Mypy error lines must count while notes stay excluded."""
        output = "file.py:1: error: Bad type\nfile.py:1: note: Detail"
        assert ExclusionDriftReporter._count_findings("mypy", output) == 1

    def test_bandit_json_count(self) -> None:
        """Bandit results must count from JSON after log lines."""
        output = 'INFO scan\n{"results":[{"issue_text":"one"}]}'
        assert ExclusionDriftReporter._count_findings("bandit", output) == 1

    def test_drift_does_not_fail_the_report(self, drift_fixture: tuple[Path, Path]) -> None:
        """A changed count must remain an advisory result."""
        root, manifest = drift_fixture
        exclusion = Exclusion("ruff", "scripts", "scripts", 1)
        reporter = ExclusionDriftReporter(root=root, manifest_path=manifest)
        with patch.object(reporter, "_commands_for", return_value=[["tool"]]):
            with patch("misthelper_devtools.exclusion_drift.subprocess.run") as run:
                run.return_value.stdout = '[{"code":"E501"},{"code":"F401"}]'
                run.return_value.stderr = ""
                run.return_value.returncode = 1
                result = reporter.measure(exclusion)
        assert result["delta"] == 1
        assert result["status"] == "measured"

    def test_missing_path_reports_zero_without_running_a_tool(self, drift_fixture: tuple[Path, Path]) -> None:
        """A missing optional path must report zero without a tool error."""
        root, manifest = drift_fixture
        exclusion = Exclusion("bandit", "missing", "missing", 3)
        reporter = ExclusionDriftReporter(root=root, manifest_path=manifest)
        with patch("misthelper_devtools.exclusion_drift.subprocess.run") as run:
            result = reporter.measure(exclusion)
        run.assert_not_called()
        assert result["status"] == "missing"
        assert result["current_count"] == 0
        assert result["delta"] == -3

    def test_mypy_file_scan_reuses_the_manifest_revision_cache(self, drift_fixture: tuple[Path, Path]) -> None:
        """Repeated mypy command builds must reuse the same file scan."""
        root, manifest = drift_fixture
        calls = 0
        exclusion = Exclusion("mypy", "scripts", "scripts", 1)
        reporter = ExclusionDriftReporter(root=root, manifest_path=manifest)
        reporter.load_exclusions()

        def counted_rglob(path: Path, pattern: str):
            nonlocal calls
            calls += 1
            assert pattern == "*.py"
            return iter([path / "example.py"])

        with patch.object(type(root), "rglob", counted_rglob):
            first = reporter._commands_for(exclusion)
            second = reporter._commands_for(exclusion)

        assert calls == 1
        assert first == second

    def test_duplicate_scan_path_reuses_the_tool_result(self, drift_fixture: tuple[Path, Path]) -> None:
        """Matching gate and scan path must not launch the tool twice."""
        root, manifest = drift_fixture
        reporter = ExclusionDriftReporter(root=root, manifest_path=manifest)
        reporter.load_exclusions()
        first = Exclusion("bandit", "scripts", "scripts", 1)
        second = Exclusion("bandit", "scripts\\", "scripts", 1)
        with patch.object(reporter, "_commands_for", return_value=[["tool"]]):
            with patch("misthelper_devtools.exclusion_drift.subprocess.run") as run:
                run.return_value.stdout = '{"results":[{"issue_text":"one"}]}'
                run.return_value.stderr = ""
                run.return_value.returncode = 1
                first_result = reporter.measure(first)
                second_result = reporter.measure(second)

        assert run.call_count == 1
        assert first_result["current_count"] == 1
        assert second_result["current_count"] == 1
