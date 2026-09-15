"""Coverage reporting tests for the compliance analyzer."""

from __future__ import annotations  # Keep annotations light for pytest.

from pathlib import Path  # Build output paths safely.

import pytest  # Capture stdout and provide tmp_path.

from tools.compliance_analyzer.__main__ import ComplianceCLI  # CLI under test.


def test_compliance_reports_read_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid compliance run must print the files it read."""
    monkeypatch.chdir(Path(__file__).parents[2])  # Anchor relative CLI paths at the repository root.
    output = tmp_path / "compliance.md"  # Keep the generated report outside tracked files.
    code = ComplianceCLI().run(["tools/analyzer_coverage.py", "--output", str(output)])  # Run one file.
    stdout = capsys.readouterr().out  # Capture the CLI report summary.
    assert code == 0  # A valid file should not fail the analyzer.
    assert "Analyzer coverage: compliance_analyzer" in stdout  # The coverage block must be visible.
    assert "Read: tools/analyzer_coverage.py" in stdout  # The read file must be listed.
    assert "## Analyzer Coverage" in output.read_text(encoding="utf-8")  # Markdown must include coverage.


def test_compliance_fails_explicit_excluded_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit target skipped by an exclusion must fail instead of passing."""
    monkeypatch.chdir(Path(__file__).parents[2])  # Anchor relative CLI paths at the repository root.
    output = tmp_path / "compliance.md"  # Keep the generated report outside tracked files.
    code = ComplianceCLI().run(  # Run with a target that the exclusion removes.
        ["tools/analyzer_coverage.py", "--exclude", "analyzer_coverage.py", "--output", str(output)]
    )
    stdout = capsys.readouterr().out  # Capture the visible skip reason.
    assert code == 2  # Explicit skipped targets make the run invalid.
    assert "Skipped: tools/analyzer_coverage.py (excluded_target)" in stdout  # The reason must be visible.
