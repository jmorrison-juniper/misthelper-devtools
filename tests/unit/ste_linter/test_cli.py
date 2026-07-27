"""Tests for the command-line interface."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Parses the JSON output.
import pathlib  # Builds paths to the fixtures.

import pytest  # Catches the SystemExit from the version flag.

from tools.ste_linter.cli import main  # The entry function under test.

# The folder that holds the shared fixture files.
_FIXTURES = pathlib.Path(__file__).parents[2] / "fixtures" / "ste_linter"


def test_grade_compliant_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """A compliant file with no threshold returns exit code zero."""
    code = main([str(_FIXTURES / "compliant.md")])  # Grade the compliant fixture.
    assert code == 0  # The run passed.
    assert "Score:" in capsys.readouterr().out  # The report was printed.


def test_min_score_failure_returns_one() -> None:
    """A file below the threshold returns exit code one."""
    code = main(["--min-score", "99", str(_FIXTURES / "noncompliant.md")])  # Grade with a high bar.
    assert code == 1  # The file failed the gate.


def test_missing_file_returns_two(capsys: pytest.CaptureFixture[str]) -> None:
    """A path that does not exist returns exit code two."""
    code = main([str(_FIXTURES / "no-such-file.md")])  # Grade a missing file.
    assert code == 2  # The run reported a usage error.
    assert "not found" in capsys.readouterr().out  # The error was printed.


def test_json_format_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The JSON format prints valid JSON."""
    main(["--format", "json", str(_FIXTURES / "compliant.md")])  # Grade with JSON output.
    payload = json.loads(capsys.readouterr().out)  # Parse the printed JSON.
    assert payload["results"][0]["score"] >= 0  # The result holds a score.


def test_unsupported_file_is_skipped(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    """An unsupported file type is skipped."""
    path = tmp_path / "notes.txt"  # A text file the linter does not grade.
    path.write_text("Some notes.", encoding="utf-8")  # Write the file.
    code = main([str(path)])  # Grade the unsupported file.
    assert code == 2  # No file produced a score.
    assert "Skipped" in capsys.readouterr().out  # The skip was reported.


def test_version_flag_exits() -> None:
    """The version flag prints the version and exits."""
    with pytest.raises(SystemExit) as caught:  # The version action raises SystemExit.
        main(["--version"])  # Ask for the version.
    assert caught.value.code == 0  # The version flag exits cleanly.
