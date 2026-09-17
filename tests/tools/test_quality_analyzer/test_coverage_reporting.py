"""Coverage reporting tests for the test quality analyzer."""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the generated JSON report.
import sys  # Control argv so the None-input CLI path is deterministic.
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


def test_quality_report_empty_argv_reports_missing_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty argument list must report a missing default root."""
    monkeypatch.chdir(tmp_path)  # Run in a synthetic repository that has no tests folder.
    code = main([])  # Exercise the empty sequence edge case for the CLI.
    stderr = capsys.readouterr().err  # Capture the CLI error text for the observable result.
    assert code == 2, "Empty argv without a test root must exit 2; got %d" % code
    assert "test_quality_analyzer: missing root skipped" in stderr  # The error must name the root problem.


def test_quality_report_none_argv_reads_process_arguments(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None argument list must read process arguments and produce coverage output."""
    monkeypatch.chdir(repo_root)  # Resolve repository-relative config paths.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"  # Use stable fixtures.
    report_path = tmp_path / "process-report.json"  # Keep generated JSON outside tracked files.
    summary_path = tmp_path / "process-summary.md"  # Keep generated Markdown outside tracked files.
    monkeypatch.setattr(  # Replace process argv so the None path uses hermetic arguments.
        sys,
        "argv",
        [
            "test_quality_analyzer",
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
        ],
    )
    code = main(None)  # Exercise the None edge case that module execution uses.
    payload = json.loads(report_path.read_text(encoding="utf-8"))  # Parse output to prove the run completed.
    assert code == 0, "None argv with process arguments must exit 0; got %d" % code
    assert payload["analyzed_files"], "The report must list analyzed files through the None argv path."
