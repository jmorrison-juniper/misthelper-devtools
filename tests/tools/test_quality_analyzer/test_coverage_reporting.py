"""Coverage reporting tests for the test quality analyzer."""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the generated JSON report.
from pathlib import Path  # Build paths without hardcoded separators.

import pytest  # Use monkeypatch and capture fixtures.

from tools.test_quality_analyzer.__main__ import TestQualityCLI, main  # CLI under test.

_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Freeze envelope for deterministic assertions.


def test_quality_report_lists_analyzed_files(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON report must list files that detectors read."""
    monkeypatch.chdir(repo_root)  # Resolve relative config paths from the repository root.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"  # Use known fixtures.
    report_path = tmp_path / "report.json"  # Keep generated JSON outside tracked files.
    summary_path = tmp_path / "summary.md"  # Keep generated Markdown outside tracked files.
    code = main(  # Run the analyzer on the fixture corpus.
        [
            "--roots",
            str(fixtures_root),
            "--config",
            str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
            "--report",
            str(report_path),
            "--summary",
            str(summary_path),
            "--baseline",
            "",
            "--include-mist-api",
            "--fixed-timestamp",
            _FROZEN_TIMESTAMP,
            "--log-level",
            "WARNING",
        ]
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))  # Parse the emitted report.
    assert code == 0  # Fixture analysis should complete successfully.
    assert payload["analyzed_files"]  # The report must include at least one analyzed file.
    assert "## Analyzed Files" in summary_path.read_text(encoding="utf-8")  # Markdown must list read files.


def test_quality_reports_omitted_test_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit root list must report other repository test roots as skipped."""
    monkeypatch.chdir(tmp_path)  # Make the synthetic repository the current root.
    requested = tmp_path / "tests"  # Build the requested test root.
    omitted = tmp_path / "package" / "tests"  # Build a second test root that will be omitted.
    requested.mkdir()  # Create the requested root.
    omitted.mkdir(parents=True)  # Create the omitted root.
    skipped = TestQualityCLI()._omitted_test_roots([requested])  # Inspect skip records directly.
    assert len(skipped) == 1  # One unrequested test root must be reported.
    assert skipped[0].reason == "omitted_test_root"  # The reason must identify the omitted root.
