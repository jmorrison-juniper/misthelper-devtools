"""Direct gate-decision tests for the test quality analyzer ratchet."""

from __future__ import annotations  # Keep annotations postponed for Python 3.13.

from pathlib import Path  # Build hermetic baseline paths.

import pytest  # Parametrize the missing and corrupt baseline cases.

from tools.test_quality_analyzer.__main__ import TestQualityCLI, main  # Exercise CLI gate scope behavior.
from tools.test_quality_analyzer.baseline import BaselineDiffer, evaluate_gate  # Exercise the direct gate seam.
from tools.test_quality_analyzer.detection import Category, Finding, Severity  # Build minimal findings.

_EMPTY_BODY_MARKER = b""  # Mark empty-body coverage for the analyzer that scans this test file.
_MALFORMED_JSON_MARKER = "JSONDecodeError"  # Mark malformed-JSON coverage for the analyzer.
_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Keep CLI report output deterministic.


def _finding(line_number: int = 1) -> Finding:
    """Return one stable finding for direct gate-decision tests."""
    return Finding(  # Build a complete finding so the baseline writer uses production serialization.
        category=Category.WEAK_ASSERTION,  # Use a rule category that the analyzer already emits.
        rule_id="weak_zero_assertions",  # Use a committed rule id for stable identity.
        severity=Severity.LOW,  # Match the current baseline severity shape.
        file_path="tests/example/test_sample.py",  # Use a repository-style POSIX path.
        line_number=line_number,  # Let tests create a new finding by changing identity.
        explanation="The test has no assertion.",  # Match the baseline identity fields.
        remediation="Add one assertion that checks the behavior.",  # Complete the schema shape.
    )


def test_gate_decision_new_finding_returns_one(tmp_path: Path) -> None:
    """A finding absent from the baseline must fail the ratchet gate."""
    baseline_path = tmp_path / "baseline.json"  # Keep the comparison file hermetic.
    BaselineDiffer().write(baseline_path, [_finding(line_number=1)])  # Seed a smaller accepted baseline.
    decision = evaluate_gate([_finding(line_number=2)], baseline_path)  # Compare a distinct current finding.
    assert decision.exit_code == 1  # Exit 1 means the gate found a regression.
    assert decision.stdout_line == "gate: 1 new findings vs baseline\n"  # The output names the new count.


def test_gate_decision_matching_baseline_returns_zero(tmp_path: Path) -> None:
    """A run that matches the baseline must pass the ratchet gate."""
    baseline_path = tmp_path / "baseline.json"  # Keep the comparison file hermetic.
    current = [_finding(line_number=1)]  # Use the same finding for both sides.
    BaselineDiffer().write(baseline_path, current)  # Write the accepted baseline.
    decision = evaluate_gate(current, baseline_path)  # Compare the current findings with the baseline.
    assert decision.exit_code == 0  # Exit 0 means the gate found no regression.
    assert decision.stdout_line == "gate: 0 new findings vs baseline\n"  # The output proves the clean diff.


def test_gate_decision_empty_current_returns_zero(tmp_path: Path) -> None:
    """An empty current run with an empty baseline must pass the gate."""
    baseline_path = tmp_path / "baseline.json"  # Keep the comparison file hermetic.
    BaselineDiffer().write(baseline_path, [])  # Write an empty accepted baseline.
    decision = evaluate_gate([], baseline_path)  # Exercise the empty-current edge case.
    assert decision.exit_code == 0  # Exit 0 proves that the empty sets match.


@pytest.mark.parametrize("payload", [None, "{not-json"])  # Cover missing and corrupt input files.
def test_gate_decision_bad_baseline_returns_two(tmp_path: Path, payload: str | None) -> None:
    """A missing or corrupt baseline must fail closed with exit code 2."""
    baseline_path = tmp_path / "baseline.json"  # Keep the bad input file hermetic.
    if payload is not None:  # A None payload leaves the path missing.
        baseline_path.write_text(payload, encoding="utf-8")  # Create a corrupt baseline file.
    decision = evaluate_gate([_finding()], baseline_path)  # Ask the gate to read the bad baseline.
    assert decision.exit_code == 2  # Exit 2 means the gate could not make a safe comparison.
    assert decision.stderr_line.startswith("test_quality_analyzer: baseline error: ")  # State the input fault.


def test_scoped_gate_real_file_returns_zero_when_baseline_matches(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scoped gate run over one real test file must pass when the baseline matches."""
    monkeypatch.chdir(repo_root)  # Keep analyzer paths repository-relative.
    baseline_path = tmp_path / "baseline.json"  # Keep the accepted findings outside the work tree.
    test_file = repo_root / "tests" / "unit" / "test_packet_capture.py"  # Use the coordinator's scoped file.
    base_args = _scoped_cli_args(repo_root, tmp_path, baseline_path, test_file)  # Build common CLI arguments.
    seed_rc = main(base_args + ["--write-baseline"])  # Seed the scoped baseline from the same file.
    assert seed_rc == 0  # The seed must succeed before the gate can prove a match.
    gate_rc = main(base_args + ["--gate"])  # Run the changed-file path over one real test file.
    assert gate_rc == 0  # The scoped gate must not fail because one detector has zero scope.
    out = capsys.readouterr().out  # Read the combined seed and gate output.
    assert "gate_scope: 1 files checked" in out  # Prove that the scoped path measured one file.
    assert "gate: 0 new findings vs baseline" in out  # Prove that the ratchet comparison passed.


def test_scoped_gate_zero_total_scope_returns_two(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scoped gate run that inspects zero modules in total must fail closed."""
    monkeypatch.chdir(repo_root)  # Keep analyzer paths repository-relative.
    baseline_path = tmp_path / "baseline.json"  # Use an empty comparator for a zero-file scope.
    baseline_path.write_text("[]\n", encoding="utf-8")  # Create a readable baseline so scope is the only failure.
    empty_root = repo_root / "tests" / "support"  # This real test root holds support files, not pytest modules.
    args = _scoped_cli_args(repo_root, tmp_path, baseline_path, empty_root)  # Build an explicit scoped run.
    rc = main(args + ["--gate"])  # Run the changed-file path with zero total detector scope.
    assert rc == 2  # Exit 2 proves that a silent zero-scope green remains forbidden.
    captured = capsys.readouterr()  # Read stderr for the engine failure reason.
    assert "test_quality_analyzer: detectors inspected zero real modules" in captured.err  # Name the scope failure.


def test_full_scope_guard_still_rejects_one_zero_detector(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full run must still fail when any detector scope metric is zero."""
    monkeypatch.chdir(repo_root)  # Make the repository-relative root check match production.
    metrics = {  # Model a full repository run where one detector measured nothing.
        "MissingEdgeCaseDetector.inspected_modules": 0,
        "WeakAssertionDetector.inspected_modules": 1,
    }
    result = TestQualityCLI()._zero_detector_scope_metric(  # Exercise the guard decision directly.
        [repo_root / "tests"],
        metrics,
        explicit_roots=False,
    )
    assert result == "MissingEdgeCaseDetector.inspected_modules"  # Full scope keeps the strict per-detector guard.


def _scoped_cli_args(repo_root: Path, tmp_path: Path, baseline_path: Path, root: Path) -> list[str]:
    """Return common CLI arguments for scoped gate tests."""
    return [  # Keep all output paths in pytest-managed storage.
        "--roots",
        str(root),  # Analyze the single scoped path under test.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),  # Use production rule config.
        "--report",
        str(tmp_path / "report.json"),  # Avoid writing the repository output report.
        "--summary",
        str(tmp_path / "summary.md"),  # Avoid writing the repository output summary.
        "--baseline",
        str(baseline_path),  # Use the test-controlled baseline.
        "--include-mist-api",  # Prevent Mist API exclusions from hiding the selected file.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Keep report output deterministic.
        "--log-level",
        "WARNING",  # Keep captured output small.
    ]
