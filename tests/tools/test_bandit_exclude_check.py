"""Tests for the Bandit exclude separator guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from misthelper_devtools import bandit_exclude_check


def _write_pyproject(path: Path, excludes: list[str]) -> Path:
    """Write a small Bandit config fixture."""
    entries = (entry.replace("\\", "\\\\") for entry in excludes)
    text = "[tool.bandit]\nexclude_dirs = [\n" + "".join(f'  "{entry}",\n' for entry in entries) + "]\n"
    pyproject = path / "pyproject.toml"
    pyproject.write_text(text, encoding="utf-8")
    return pyproject


def test_separator_pair_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Paired spellings must pass with the legacy success line."""
    pyproject = _write_pyproject(tmp_path, ["src/fixtures", "src\\fixtures"])

    assert bandit_exclude_check.main(["--pyproject", str(pyproject)]) == 0

    assert bandit_exclude_check.SUCCESS_MESSAGE in capsys.readouterr().out


def test_missing_separator_pair_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A one-sided spelling must fail and name the missing spelling."""
    pyproject = _write_pyproject(tmp_path, ["src/fixtures"])

    assert bandit_exclude_check.main(["--pyproject", str(pyproject)]) == 1

    assert "exclude_dirs misses the spelling" in capsys.readouterr().err


def test_sample_uses_real_bandit_matcher(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A product source sample must stay inside the Bandit scan scope."""
    pyproject = _write_pyproject(tmp_path, ["tests/fixtures", "tests\\fixtures"])

    result = bandit_exclude_check.main(
        ["--pyproject", str(pyproject), "--include-sample", "./src/utils/zen_city_metadata.py"]
    )

    assert result == 0
    assert bandit_exclude_check.SUCCESS_MESSAGE in capsys.readouterr().out


def test_excluded_sample_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A sample that Bandit excludes must fail the guard."""
    pyproject = _write_pyproject(tmp_path, ["src/utils", "src\\utils"])

    assert (
        bandit_exclude_check.main(
            ["--pyproject", str(pyproject), "--include-sample", "./src/utils/zen_city_metadata.py"]
        )
        == 1
    )

    assert "bandit unexpectedly excludes" in capsys.readouterr().err


def test_no_sample_is_checked_by_default() -> None:
    """The caller names its own product files, so the parser holds no default sample."""
    parser = bandit_exclude_check.build_parser()

    args = parser.parse_args([])

    assert args.include_sample == []


def test_each_sample_spelling_is_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A Windows-only exclude entry must fail for the Windows spelling of a sample."""
    pyproject = _write_pyproject(tmp_path, ["src\\utils"])
    samples = ["./src/utils/zen_city_metadata.py", ".\\src\\utils\\zen_city_metadata.py"]

    result = bandit_exclude_check.main(
        ["--pyproject", str(pyproject), *(word for sample in samples for word in ("--include-sample", sample))]
    )

    assert result == 1
    err = capsys.readouterr().err
    assert err.startswith(bandit_exclude_check.ERROR_PREFIX)
    assert "bandit unexpectedly excludes .\\src\\utils\\zen_city_metadata.py" in err
    assert "exclude_dirs misses the spelling 'src/utils'" in err
