"""CLI exit-code + flag behavior tests (T050).

Covers the US2 gate/write-baseline surface documented in contracts/cli.md:

    * ``--gate`` against a matching baseline -> exit 0 + no new findings.
    * ``--gate`` when current findings contain a delta -> exit 1.
    * ``--gate`` when parse errors exist -> exit 2 (FR-018).
    * ``--write-baseline`` writes canonical JSON array + exits 0.
    * ``--baseline ""`` disables baseline logic without erroring.
    * ``--gate`` + ``--write-baseline`` together -> exit 2 (invalid usage).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the produced baseline + report artefacts.
from pathlib import Path  # Filesystem primitives for hermetic paths.

import pytest  # Fixture primitives.

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint under test.

_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Freeze envelope for deterministic assertions.


def _base_argv(
    repo_root: Path,
    fixtures_root: Path,
    tmp_path: Path,
    baseline: str,
) -> list[str]:
    """Build a base argv list; individual tests append gate/write-baseline flags."""
    return [
        "--roots",
        str(fixtures_root),  # Scan a specific fixture pool.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(tmp_path / "report.json"),  # Hermetic report path.
        "--summary",
        str(tmp_path / "summary.md"),  # Hermetic summary path.
        "--baseline",
        baseline,  # Baseline path or empty string.
        "--include-mist-api",  # Uniform predicate handling across fixture runs.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Deterministic envelope.
        "--log-level",
        "WARNING",  # Reduce noise in captured output.
    ]


def test_gate_clean_exits_zero(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gate mode against a baseline matching current findings must exit 0."""
    monkeypatch.chdir(repo_root)  # Config paths resolve relative to repo root.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"
    baseline_path = tmp_path / "baseline.json"
    # First: seed the baseline with the current findings via --write-baseline.
    seed_rc = main(_base_argv(repo_root, fixtures_root, tmp_path, str(baseline_path)) + ["--write-baseline"])
    assert seed_rc == 0, "--write-baseline seed must exit 0; got %d" % seed_rc
    assert baseline_path.exists(), "Baseline file must be written by --write-baseline."
    # Second: run --gate against that same baseline; should be no delta -> exit 0.
    gate_rc = main(_base_argv(repo_root, fixtures_root, tmp_path, str(baseline_path)) + ["--gate"])
    assert gate_rc == 0, "Gate mode with matching baseline must exit 0; got %d" % gate_rc
    out = capsys.readouterr().out  # Captured stdout for both invocations combined.
    assert "gate: 0 new findings vs baseline" in out


def test_gate_new_finding_exits_one(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A current-run finding absent from the baseline must trigger exit 1."""
    monkeypatch.chdir(repo_root)  # Config paths anchored at repo root.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"
    baseline_path = tmp_path / "empty_baseline.json"
    # Seed an EMPTY baseline (JSON array). Every current finding is now new.
    baseline_path.write_text("[]\n", encoding="utf-8")
    rc = main(_base_argv(repo_root, fixtures_root, tmp_path, str(baseline_path)) + ["--gate"])
    assert rc == 1, "Gate mode with new findings must exit 1; got %d" % rc
    out = capsys.readouterr().out  # Should contain the "gate: N new findings" line.
    assert "gate: " in out and "new findings vs baseline" in out
    # The N value is the number of current-run findings; must be > 0 against empty baseline.
    gate_line = next(line for line in out.splitlines() if line.startswith("gate:"))
    n = int(gate_line.split()[1])  # Second token is the count.
    assert n > 0, "Empty baseline vs bad-fixture corpus must report at least one new finding."


def test_gate_parse_error_exits_two(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file with a SyntaxError under --gate must trigger exit 2 (FR-018)."""
    monkeypatch.chdir(repo_root)  # Config paths anchored at repo root.
    # Build a tiny hermetic test root with a single unparseable file.
    bad_root = tmp_path / "corpus"
    bad_root.mkdir()
    unparseable = bad_root / "test_broken.py"
    unparseable.write_text("def broken(:\n    pass\n", encoding="utf-8")  # SyntaxError source.
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text("[]\n", encoding="utf-8")  # Empty baseline suffices.
    argv = [
        "--roots",
        str(bad_root),
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(tmp_path / "report.json"),
        "--summary",
        str(tmp_path / "summary.md"),
        "--baseline",
        str(baseline_path),
        "--include-mist-api",
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,
        "--log-level",
        "WARNING",
        "--gate",
    ]
    rc = main(argv)
    assert rc == 2, "Gate mode with parse errors must exit 2; got %d" % rc


def test_write_baseline_produces_canonical_json_array(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--write-baseline writes a JSON array (no envelope) and exits 0."""
    monkeypatch.chdir(repo_root)
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"
    baseline_path = tmp_path / "baseline.json"
    rc = main(_base_argv(repo_root, fixtures_root, tmp_path, str(baseline_path)) + ["--write-baseline"])
    assert rc == 0, "--write-baseline must exit 0; got %d" % rc
    text = baseline_path.read_text(encoding="utf-8")
    payload = json.loads(text)  # Must parse as JSON.
    assert isinstance(payload, list), "Baseline must be a JSON array, not an envelope."
    assert text.endswith("\n"), "Baseline file must end with a trailing newline."
    # Every entry must have the schema-conformant finding keys.
    if payload:
        required = {"category", "rule_id", "severity", "file_path", "line_number", "explanation", "remediation"}
        assert required.issubset(payload[0].keys()), "Baseline finding missing required keys: %s" % (
            required - set(payload[0].keys())
        )


def test_empty_baseline_flag_disables_baseline_logic(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--baseline ""` must skip baseline load/diff and complete without error."""
    monkeypatch.chdir(repo_root)
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"
    rc = main(_base_argv(repo_root, fixtures_root, tmp_path, ""))  # No gate/write-baseline flag.
    assert rc == 0, "Non-gate run with disabled baseline must exit 0; got %d" % rc
    report_path = tmp_path / "report.json"
    assert report_path.exists(), "Report must still be produced when baseline disabled."
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Stale entries must be empty when baseline logic is disabled (no baseline to audit).
    assert report.get("stale_baseline_entries", []) == []


def test_gate_and_write_baseline_are_mutually_exclusive(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Passing both --gate and --write-baseline must exit 2 (invalid usage)."""
    monkeypatch.chdir(repo_root)
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / "bad"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text("[]\n", encoding="utf-8")
    argv = _base_argv(repo_root, fixtures_root, tmp_path, str(baseline_path)) + ["--gate", "--write-baseline"]
    rc = main(argv)
    assert rc == 2, "Mutually-exclusive flags must exit 2; got %d" % rc
    err = capsys.readouterr().err  # Error message written to stderr.
    assert "mutually exclusive" in err
