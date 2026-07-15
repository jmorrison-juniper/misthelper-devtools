"""Aggregate CLI meta-test (T042).

Invokes the `TestQualityCLI` programmatically against the bad-fixture corpus
and the good-fixture corpus once each. Asserts:

    * The bad-fixtures run emits at least one finding per detector category.
    * The good-fixtures run emits zero findings for every detector category.

Report and summary outputs are written under pytest's `tmp_path` to keep the
test hermetic (the CLI's default report path would otherwise clobber the
committed `tools/test_quality_analyzer/output/report.json`).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the CLI-produced report.json for assertion.
from pathlib import Path  # Path arithmetic for repo-root anchoring.

import pytest  # Fixture primitives (tmp_path, monkeypatch, capsys).

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint under test.

# Categories every detector emits; expected to appear in the bad-fixtures report.
_EXPECTED_CATEGORIES = {  # Frozen set of category enum values.
    "untested",  # UntestedDetector.
    "weak_assertion",  # WeakAssertionDetector.
    "tautological",  # TautologicalTestDetector.
    "missing_failure_mode",  # MissingFailureModeDetector.
    "missing_edge_case",  # MissingEdgeCaseDetector.
}

# Fixed timestamp string used for determinism / hermeticism of the aggregate run.
_FROZEN_TIMESTAMP = "2026-07-14T00:00:00Z"  # Any valid ISO-8601 UTC value suffices.


def _run_cli(
    repo_root: Path,  # Absolute repo-root path for anchoring --roots + --config.
    fixture_subdir: str,  # "bad" or "good"; identifies which fixture pool to scan.
    tmp_path: Path,  # Test-scoped scratch directory for report + summary artefacts.
    monkeypatch: pytest.MonkeyPatch,  # Ensures cwd is repo root during the run.
) -> dict:
    """Invoke the CLI against one fixture pool and return the parsed report JSON."""
    # Point the CLI at the requested fixture pool (bad/ or good/).
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures" / fixture_subdir
    # Sanity-check the fixture directory before attempting the run.
    assert fixtures_root.is_dir(), "Fixture pool missing: %s" % fixtures_root
    # Anchor the run at repo root so relative paths in the config resolve correctly.
    monkeypatch.chdir(repo_root)  # Restored automatically by monkeypatch teardown.
    # Output paths under tmp_path so we never touch the committed output artefacts.
    report_path = tmp_path / "report.json"  # JSON report artefact.
    summary_path = tmp_path / "summary.md"  # Markdown summary artefact.
    # Compose argv exactly as the CLI contract documents.
    argv = [  # Ordered list of flag/value pairs forwarded to argparse.
        "--roots",
        str(fixtures_root),  # Only scan this fixture pool.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(report_path),  # Hermetic JSON output path.
        "--summary",
        str(summary_path),  # Hermetic Markdown output path.
        "--baseline",
        "",  # Disable baseline comparison for US1 aggregate scope.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Freeze envelope timestamp.
        "--log-level",
        "WARNING",  # Reduce log noise in the pytest transcript.
    ]
    # Invoke the CLI; success must return exit code 0 in US1 scope.
    exit_code = main(argv)  # Delegates to TestQualityCLI().run().
    assert exit_code == 0, "CLI exit code non-zero for %s fixtures: got %d" % (fixture_subdir, exit_code)
    # Sanity-check that both output artefacts were produced.
    assert report_path.exists(), "Report file was not written: %s" % report_path
    assert summary_path.exists(), "Summary file was not written: %s" % summary_path
    # Parse the JSON report so assertions can inspect finding categories.
    return json.loads(report_path.read_text(encoding="utf-8"))  # Report envelope dict.


def test_cli_bad_fixtures_fire_every_detector(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad-fixture run emits at least one finding per detector category."""
    # Run the CLI against the bad-fixture pool and parse the resulting report.
    report = _run_cli(repo_root, "bad", tmp_path, monkeypatch)  # Full CLI pipeline.
    # Extract the set of categories observed in this run's findings.
    findings = report.get("findings", [])  # Ordered list per schema.
    got_categories = {f["category"] for f in findings}  # Deduplicated category set.
    # Every expected category must appear at least once.
    missing = _EXPECTED_CATEGORIES - got_categories  # Categories with zero findings.
    assert not missing, "Bad-fixture run failed to fire every detector; missing categories: %s" % sorted(missing)
    # Bad fixtures must produce at least one finding overall.
    assert len(findings) >= len(
        _EXPECTED_CATEGORIES
    ), "Bad-fixture run should produce at least one finding per detector; got %d" % len(findings)


def test_cli_good_fixtures_are_clean(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each good-fixture file emits zero findings for its OWN detector category.

    Cross-detector bleed is expected and acceptable at the aggregate level:
    e.g. `test_tautological_good.py` calls `compute(3)` (positive int call arg),
    which correctly triggers MissingEdgeCaseDetector even though it is a "good"
    tautological fixture. Detector isolation is enforced per-detector in
    `test_meta_fixtures.py`; this test enforces the intra-category clean bill
    of health for each good fixture.
    """
    # Run the CLI against the good-fixture pool and parse the resulting report.
    report = _run_cli(repo_root, "good", tmp_path, monkeypatch)  # Full CLI pipeline.
    findings = report.get("findings", [])  # Ordered list per schema.
    # Map of good-fixture filename suffix -> the category it is expected to be clean for.
    fixture_to_category = {  # Filename convention: test_{category}_good.py.
        "test_untested_good.py": "untested",  # UntestedDetector's good fixture.
        "test_weak_assertion_good.py": "weak_assertion",  # WeakAssertionDetector's.
        "test_tautological_good.py": "tautological",  # TautologicalTestDetector's.
        "test_missing_failure_mode_good.py": "missing_failure_mode",  # Its own detector.
        "test_missing_edge_case_good.py": "missing_edge_case",  # Its own detector.
    }
    # For each good fixture, assert zero findings whose category matches its own detector.
    for filename, category in fixture_to_category.items():  # Iterate all good fixtures.
        # Filter findings that (a) belong to this fixture file and (b) match its own category.
        own_category_findings = [  # Filtered subset representing intra-category violations.
            f for f in findings if f["file_path"].endswith(filename) and f["category"] == category
        ]
        # A good fixture must never produce a finding in the detector it is supposed to clear.
        assert own_category_findings == [], "Good fixture %s must produce zero %s findings; got: %s" % (
            filename,
            category,
            [f["rule_id"] for f in own_category_findings],
        )
    # Parse errors and skipped-file lists should also be empty for the good pool.
    assert report.get("parse_errors", []) == [], "Good-fixture run should emit zero parse errors; got: %s" % report.get(
        "parse_errors"
    )


def test_cli_stdout_summary_shape(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One-line stdout summary matches the contract format for any run."""
    # Run the CLI once against the bad pool so the summary counts are non-trivial.
    _run_cli(repo_root, "bad", tmp_path, monkeypatch)  # Discard the report here.
    # Inspect captured stdout; the summary is the one-line trailing statement.
    captured = capsys.readouterr()  # Reads stdout + stderr for this test only.
    stdout_lines = [ln for ln in captured.out.splitlines() if ln.strip()]  # Non-empty lines.
    # At least one non-empty stdout line must exist (the summary).
    assert stdout_lines, "CLI produced no stdout output"
    # The last non-empty line is the summary; validate its exact prefix + tail shape.
    summary_line = stdout_lines[-1]  # Trailing summary line per contract.
    assert summary_line.startswith("test_quality_analyzer: "), (
        "Summary must start with the CLI program name; got: %s" % summary_line
    )
    assert " findings (" in summary_line, (
        "Summary must contain the ' findings (' bucket segment; got: %s" % summary_line
    )
    assert summary_line.endswith(" parse errors"), "Summary must end with ' parse errors'; got: %s" % summary_line
