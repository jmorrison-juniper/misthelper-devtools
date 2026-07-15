"""Unit tests for BaselineDiffer (T047, T051).

Covers:
    * Round-trip: write findings, load them back, assert canonical-key equality.
    * new-finding detection: introduce one new finding not in baseline.
    * removed-finding detection: baseline contains a finding no longer present.
    * unchanged-count: intersection cardinality.
    * canonical-key insensitivity to severity + remediation changes (FR-012).
    * Stale-baseline advisory (T051 / FR-019).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Read the on-disk baseline back for byte-level assertions.
from pathlib import Path  # Filesystem primitives for tmp_path assertions.

from tools.test_quality_analyzer.baseline import BaselineDiffer  # SUT.
from tools.test_quality_analyzer.detection import (  # Shared type layer.
    Baseline,
    Category,
    Finding,
    Severity,
)


def _make_finding(
    file_path: str = "tests/example.py",
    line_number: int = 42,
    rule_id: str = "weak_bare_assert",
    severity: Severity = Severity.MEDIUM,
    explanation: str = "Bare assert -- truthiness only.",
    remediation: str = "Compare to expected value.",
) -> Finding:
    """Return a Finding with sensible defaults for tests."""
    # Factory keeps individual tests focused on the field(s) that matter.
    return Finding(
        category=Category.WEAK_ASSERTION,
        rule_id=rule_id,
        severity=severity,
        file_path=file_path,
        line_number=line_number,
        explanation=explanation,
        remediation=remediation,
        heuristic=False,
        related_source=file_path,
    )


def test_roundtrip_write_then_load_preserves_findings(tmp_path: Path) -> None:
    """A findings tuple written then reloaded must compare equal by canonical key."""
    differ = BaselineDiffer()  # SUT instance.
    findings = (
        _make_finding(file_path="tests/a.py", line_number=1),
        _make_finding(file_path="tests/b.py", line_number=2, rule_id="weak_is_not_none"),
    )
    baseline_path = tmp_path / "baseline.json"  # Hermetic path.
    differ.write(baseline_path, findings)  # Write to disk.
    loaded = differ.load(baseline_path)  # Load back.
    assert isinstance(loaded, Baseline)  # Correct return type.
    assert len(loaded.findings) == len(findings)  # Count preserved.
    # Compare on canonical key so severity/remediation deltas would not fail.
    orig_keys = {(f.category, f.rule_id, f.file_path, f.line_number) for f in findings}
    load_keys = {(f.category, f.rule_id, f.file_path, f.line_number) for f in loaded.findings}
    assert orig_keys == load_keys  # Round-trip identity by canonical key.


def test_write_produces_canonical_json_array(tmp_path: Path) -> None:
    """Baseline output must be a JSON array (no envelope) with stable formatting."""
    differ = BaselineDiffer()
    baseline_path = tmp_path / "baseline.json"
    differ.write(baseline_path, (_make_finding(),))
    text = baseline_path.read_text(encoding="utf-8")
    # Must be JSON parseable and produce a list -- not a dict envelope.
    payload = json.loads(text)
    assert isinstance(payload, list)  # Array-of-findings shape (FR-012).
    assert text.endswith("\n")  # Trailing newline for POSIX tools.


def test_diff_detects_new_finding() -> None:
    """A finding present in current but not in baseline lands in new_findings."""
    differ = BaselineDiffer()
    existing = _make_finding(file_path="tests/existing.py")
    added = _make_finding(file_path="tests/added.py", line_number=99)
    baseline = Baseline(findings=(existing,))  # Existing only in baseline.
    diff = differ.diff((existing, added), baseline)  # Add one new.
    assert diff.unchanged_count == 1  # `existing` matches on both sides.
    assert len(diff.new_findings) == 1  # `added` is the delta.
    assert diff.new_findings[0].file_path == "tests/added.py"
    assert not diff.removed_findings  # Nothing removed.


def test_diff_detects_removed_finding() -> None:
    """A baseline finding absent from the current run lands in removed_findings."""
    differ = BaselineDiffer()
    kept = _make_finding(file_path="tests/kept.py")
    fixed = _make_finding(file_path="tests/fixed.py", line_number=7)
    baseline = Baseline(findings=(kept, fixed))  # Two in baseline.
    diff = differ.diff((kept,), baseline)  # Only one remains.
    assert diff.unchanged_count == 1
    assert not diff.new_findings
    assert len(diff.removed_findings) == 1
    assert diff.removed_findings[0].file_path == "tests/fixed.py"


def test_diff_unchanged_count_matches_intersection() -> None:
    """unchanged_count must equal the size of the canonical-key intersection."""
    differ = BaselineDiffer()
    a = _make_finding(file_path="tests/a.py", line_number=1)
    b = _make_finding(file_path="tests/b.py", line_number=2)
    c = _make_finding(file_path="tests/c.py", line_number=3)
    baseline = Baseline(findings=(a, b, c))
    diff = differ.diff((b, c), baseline)  # Two overlap, one removed.
    assert diff.unchanged_count == 2  # b + c match on both sides.
    assert len(diff.removed_findings) == 1  # `a` removed.
    assert not diff.new_findings


def test_diff_insensitive_to_severity_and_remediation() -> None:
    """Severity + remediation deltas must NOT count as new/removed findings."""
    differ = BaselineDiffer()
    original = _make_finding(severity=Severity.LOW, remediation="Old text.")
    retuned = _make_finding(severity=Severity.HIGH, remediation="New guidance.")
    baseline = Baseline(findings=(original,))  # Baseline has the LOW/old-text form.
    diff = differ.diff((retuned,), baseline)  # Current has the HIGH/new form.
    assert diff.unchanged_count == 1  # Canonical key ignores severity + remediation.
    assert not diff.new_findings  # No delta reported.
    assert not diff.removed_findings  # No delta reported.


def test_stale_advisory_flags_missing_files() -> None:
    """Baseline file_paths absent from the scanned set must appear in stale entries."""
    differ = BaselineDiffer()
    present = _make_finding(file_path="tests/still_here.py")
    absent = _make_finding(file_path="tests/deleted.py")
    baseline = Baseline(findings=(present, absent))
    stale = differ.stale_entries(baseline, scanned_files={"tests/still_here.py"})
    assert stale == ("tests/deleted.py",)  # Deterministically sorted single entry.


def test_load_missing_file_returns_empty_baseline(tmp_path: Path) -> None:
    """load() on a nonexistent path must return an empty Baseline, not raise."""
    differ = BaselineDiffer()
    baseline = differ.load(tmp_path / "no_such_file.json")
    assert isinstance(baseline, Baseline)
    assert baseline.findings == ()  # Empty tuple, not None.
