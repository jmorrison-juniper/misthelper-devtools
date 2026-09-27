"""Coverage reporting tests for the STE linter."""

from __future__ import annotations  # Keep annotations light for pytest.

from pathlib import Path  # Anchor CLI paths at the repository root.

import pytest  # Capture stdout from the CLI.

from misthelper_devtools.ste_linter.cli import LinterCLI  # CLI under test.


def test_ste_linter_reports_graded_files(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid linter run must print the file it read."""
    monkeypatch.chdir(Path(__file__).parents[2])  # Anchor relative CLI paths at the repository root.
    code = LinterCLI().run(["README.md", "--min-score", "0"])  # Grade one Markdown file.
    stdout = capsys.readouterr().out  # Capture the text report.
    assert code == 0  # The low threshold prevents prose findings from failing this test.
    assert "Analyzer coverage: ste_linter" in stdout  # The coverage block must be visible.
    assert "Read: README.md" in stdout  # The graded file must be visible.


def test_ste_linter_reports_unsupported_file(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported file must appear as a skipped path and fail usage."""
    monkeypatch.chdir(Path(__file__).parents[2])  # Anchor relative CLI paths at the repository root.
    code = LinterCLI().run(["pyproject.toml", "--min-score", "0"])  # TOML is outside the linter scope.
    stdout = capsys.readouterr().out  # Capture the skip report.
    assert code == 2  # Unsupported explicit paths fail as usage errors.
    assert "Skipped: pyproject.toml (unsupported_file_type)" in stdout  # The skip reason must be visible.
