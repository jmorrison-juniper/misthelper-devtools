"""Fixture-based meta-tests for detectors (T023, T027, T031, T035, T039, T042, T052, T053).

Each detector has a paired bad + good fixture under
`tools/test_quality_analyzer/fixtures/{bad,good}/`, and this file's tests
assert:

    - The detector emits the expected findings against the bad fixture.
    - The detector emits ZERO findings against the good fixture.

T052 (US3): iterate over the rule ids emitted by the whole-corpus meta
run and verify ``--disable-rule <id>`` isolates its findings without
perturbing the remaining rule counts.

T053 (US3): full-corpus run over ``fixtures/good/`` must emit zero
findings (SC-003 zero-false-positive contract).

Meta-tests must FAIL until the corresponding detector implementation lands,
enforcing TDD ordering per plan.md.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import ast  # Parse fixture Python sources into AST.
import json  # Parse CLI-produced report artefacts for whole-corpus tests.
from collections import Counter  # Tally rule id occurrences across the whole corpus.
from pathlib import Path  # File paths for fixture lookup.

import pytest  # Test framework primitives.

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint (whole-corpus meta).

# Resolve the fixture directories once at import time (POSIX-normalized paths).
_HERE = Path(__file__).resolve()  # Absolute path of this test module.
_REPO_ROOT = _HERE.parents[3]  # tests/tools/test_quality_analyzer -> repo root is parents[3].
_FIXTURE_BAD = _REPO_ROOT / "tools" / "test_quality_analyzer" / "fixtures" / "bad"  # Bad fixtures.
_FIXTURE_GOOD = _REPO_ROOT / "tools" / "test_quality_analyzer" / "fixtures" / "good"  # Good.
_CONFIG_PATH = _REPO_ROOT / "tools" / "test_quality_analyzer" / "config.toml"  # CLI config.
_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Deterministic envelope for meta runs.


def _parse(path: Path) -> tuple[ast.Module, str]:
    """Return `(ast.Module, source_text)` for the given fixture path."""
    # Read UTF-8 source text so unicode fixtures round-trip cleanly.
    source = path.read_text(encoding="utf-8")
    # Parse source into an AST; filename kept for diagnostic messages.
    return ast.parse(source, filename=str(path)), source


def test_untested_detector() -> None:
    """UntestedDetector: bad fixture yields 3 findings; good fixture yields zero."""
    # Import inside the test so a missing module surfaces as a clean test failure.
    from tools.test_quality_analyzer.detection.untested import UntestedDetector

    # Locate the bad-fixture SUT (three untested public functions expected).
    bad_sut = _FIXTURE_BAD / "test_untested_source_module_source.py"  # Bad-scenario SUT.
    # Locate the good-fixture SUT+test (all public functions are referenced).
    good_sut = _FIXTURE_GOOD / "test_untested_good.py"  # Good-scenario SUT+test pair.

    # Sanity-check that the fixture files exist before running the detector.
    assert bad_sut.exists(), "T021 bad fixture missing: %s" % bad_sut  # Setup precondition.
    assert good_sut.exists(), "T022 good fixture missing: %s" % good_sut  # Setup precondition.

    # BAD case: detector scans the SUT for public functions and receives NO test refs.
    detector_bad = UntestedDetector(source_paths=[bad_sut])  # SUT scan input.
    # analyze() takes a list of (path, tree) pairs representing the test corpus.
    findings_bad = detector_bad.analyze(test_files=[])  # Empty test corpus -> all untested.
    # Expect exactly one finding per public function in the bad fixture (3 fns).
    rule_ids_bad = [f.rule_id for f in findings_bad]  # Extract rule ids for assertion.
    assert rule_ids_bad.count("untested_public_function") == 3, (
        "Expected 3 untested_public_function findings, got: %s" % rule_ids_bad
    )

    # GOOD case: detector scans the SUT+test and finds every public function referenced.
    detector_good = UntestedDetector(source_paths=[good_sut])  # SUT scan input.
    # The good fixture is self-referential; pass it as both source and test file.
    good_tree, good_source = _parse(good_sut)  # Parse once for the test corpus.
    # analyze() takes a list of parsed test files; good fixture references its own public fns.
    findings_good = detector_good.analyze(
        test_files=[(good_sut, good_tree, good_source)],  # Self-referential test file.
    )
    # Expect zero findings because every public function is referenced by the test.
    assert findings_good == [], "Expected zero findings on good fixture, got: %s" % findings_good


def test_weak_assertion_detector() -> None:
    """WeakAssertionDetector: bad fixture yields 6 findings; good fixture yields zero."""
    # Import inside the test so a missing module surfaces as a clean test failure.
    from tools.test_quality_analyzer.detection.weak_assertion import WeakAssertionDetector

    # Locate the bad fixture with one function per weak sub-rule.
    bad_path = _FIXTURE_BAD / "test_weak_assertion_bad.py"  # Six weak-assertion cases.
    # Locate the good fixture with corresponding strong assertion cases.
    good_path = _FIXTURE_GOOD / "test_weak_assertion_good.py"  # Zero-findings baseline.

    # Sanity-check fixture presence before invoking the detector.
    assert bad_path.exists(), "T025 bad fixture missing: %s" % bad_path
    assert good_path.exists(), "T026 good fixture missing: %s" % good_path

    # BAD case: parse and run the detector; expect one finding per weak case.
    bad_tree, bad_source = _parse(bad_path)  # Parse the six-case fixture.
    detector = WeakAssertionDetector()  # Detector requires no ctor args.
    findings_bad = detector.detect(bad_path, bad_tree, bad_source)  # Per-file detection.
    # Assert exactly six findings and every expected sub-rule id present.
    expected_rule_ids = {
        "weak_bare_assert",  # `assert result` truthiness-only.
        "weak_is_not_none",  # `assert x is not None`.
        "weak_mock_called_no_args",  # `mock.assert_called()` no args.
        "weak_pytest_raises_exception",  # `pytest.raises(Exception)`.
        "weak_zero_assertions",  # test with zero asserts.
        "weak_self_mock_echo",  # `assert mock() == mock.return_value`.
    }
    got_rule_ids = {f.rule_id for f in findings_bad}  # Actual sub-rule ids.
    assert got_rule_ids == expected_rule_ids, "Weak sub-rule mismatch. Got: %s Expected: %s" % (
        got_rule_ids,
        expected_rule_ids,
    )
    # Verify one finding per case (no duplicates within a single test function).
    assert len(findings_bad) == 6, "Expected 6 weak-assertion findings, got %s: %s" % (
        len(findings_bad),
        [f.rule_id for f in findings_bad],
    )

    # GOOD case: parse and run the detector; expect zero findings.
    good_tree, good_source = _parse(good_path)  # Parse the strong-assertion fixture.
    detector_good = WeakAssertionDetector()  # Fresh detector for isolation.
    findings_good = detector_good.detect(good_path, good_tree, good_source)  # Per-file detection.
    # Assert zero findings for the healthy fixture.
    assert findings_good == [], "Expected zero findings on good fixture, got: %s" % findings_good


def test_tautological_detector() -> None:
    """TautologicalTestDetector: bad fixture yields 4 findings; good fixture yields zero."""
    # Import inside the test so a missing module surfaces as a clean failure.
    from tools.test_quality_analyzer.detection.tautological import TautologicalTestDetector

    # Locate the tautology fixtures (bad = 4 cases, good = zero findings).
    bad_path = _FIXTURE_BAD / "test_tautological_bad.py"  # Four tautology cases.
    good_path = _FIXTURE_GOOD / "test_tautological_good.py"  # Zero-findings baseline.

    # Sanity-check fixture presence.
    assert bad_path.exists(), "T029 bad fixture missing: %s" % bad_path
    assert good_path.exists(), "T030 good fixture missing: %s" % good_path

    # BAD case: parse and run detector; expect one finding per case.
    bad_tree, bad_source = _parse(bad_path)  # Parse the four-case fixture.
    detector = TautologicalTestDetector()  # Detector requires no ctor args.
    findings_bad = detector.detect(bad_path, bad_tree, bad_source)  # Per-file detection.
    # Assert exactly four findings and every expected sub-rule id present.
    expected_rule_ids = {
        "taut_literal_true",  # `assert True`.
        "taut_literal_equality",  # `assert 1 == 1`.
        "taut_variable_self_compare",  # `assert x == x`.
        "taut_isinstance_type_self",  # `assert isinstance(x, type(x))`.
    }
    got_rule_ids = {f.rule_id for f in findings_bad}  # Actual rule ids.
    assert got_rule_ids == expected_rule_ids, "Tautology sub-rule mismatch. Got: %s Expected: %s" % (
        got_rule_ids,
        expected_rule_ids,
    )
    assert len(findings_bad) == 4, "Expected 4 tautology findings, got %s: %s" % (
        len(findings_bad),
        [f.rule_id for f in findings_bad],
    )

    # GOOD case: parse and run; expect zero findings.
    good_tree, good_source = _parse(good_path)  # Parse the healthy fixture.
    detector_good = TautologicalTestDetector()  # Fresh detector for isolation.
    findings_good = detector_good.detect(good_path, good_tree, good_source)
    assert findings_good == [], "Expected zero findings on tautology good fixture, got: %s" % findings_good


def test_missing_failure_mode_detector() -> None:
    """MissingFailureModeDetector: bad fixture yields 6 findings; good fixture yields zero."""
    # Import inside the test so a missing module surfaces as a clean failure.
    from tools.test_quality_analyzer.detection.missing_failure_mode import (
        MissingFailureModeDetector,
    )

    # Locate the failure-mode fixtures.
    bad_path = _FIXTURE_BAD / "test_missing_failure_mode_bad.py"  # Happy-path only.
    good_path = _FIXTURE_GOOD / "test_missing_failure_mode_good.py"  # Full coverage.

    # Sanity-check fixture presence.
    assert bad_path.exists(), "T033 bad fixture missing: %s" % bad_path
    assert good_path.exists(), "T034 good fixture missing: %s" % good_path

    # BAD case: expect one finding per uncovered failure mode.
    bad_tree, bad_source = _parse(bad_path)  # Parse the happy-path fixture.
    detector = MissingFailureModeDetector()  # No ctor args required.
    findings_bad = detector.detect(bad_path, bad_tree, bad_source)  # Per-file detection.
    expected_rule_ids = {
        "missing_fm_connection_timeout",
        "missing_fm_connection_error",
        "missing_fm_http_4xx",
        "missing_fm_http_5xx",
        "missing_fm_malformed_json",
        "missing_fm_empty_body",
    }
    got_rule_ids = {f.rule_id for f in findings_bad}  # Actual rule ids.
    assert got_rule_ids == expected_rule_ids, "Missing-failure-mode sub-rule mismatch. Got: %s Expected: %s" % (
        got_rule_ids,
        expected_rule_ids,
    )
    assert len(findings_bad) == 6, "Expected 6 missing_fm_* findings, got %s: %s" % (
        len(findings_bad),
        [f.rule_id for f in findings_bad],
    )

    # GOOD case: expect zero findings for the fully-covered fixture.
    good_tree, good_source = _parse(good_path)  # Parse the healthy fixture.
    detector_good = MissingFailureModeDetector()  # Fresh detector.
    findings_good = detector_good.detect(good_path, good_tree, good_source)
    assert findings_good == [], "Expected zero findings on missing-failure-mode good fixture, got: %s" % findings_good


def test_missing_edge_case_detector() -> None:
    """MissingEdgeCaseDetector: bad fixture yields 4 heuristic findings; good yields zero."""
    # Import inside the test so a missing module surfaces as a clean failure.
    from tools.test_quality_analyzer.detection.missing_edge_case import MissingEdgeCaseDetector

    # Locate the edge-case fixtures.
    bad_path = _FIXTURE_BAD / "test_missing_edge_case_bad.py"  # Positive-int only.
    good_path = _FIXTURE_GOOD / "test_missing_edge_case_good.py"  # Full edge coverage.

    # Sanity-check fixture presence.
    assert bad_path.exists(), "T037 bad fixture missing: %s" % bad_path
    assert good_path.exists(), "T038 good fixture missing: %s" % good_path

    # BAD case: expect one finding per uncovered edge case, all heuristic.
    bad_tree, bad_source = _parse(bad_path)  # Parse the happy-path fixture.
    detector = MissingEdgeCaseDetector()  # No ctor args required.
    findings_bad = detector.detect(bad_path, bad_tree, bad_source)  # Per-file detection.
    expected_rule_ids = {
        "missing_ec_empty_input",
        "missing_ec_zero_value",
        "missing_ec_negative_value",
        "missing_ec_none_input",
    }
    got_rule_ids = {f.rule_id for f in findings_bad}  # Actual rule ids.
    assert got_rule_ids == expected_rule_ids, "Missing-edge-case sub-rule mismatch. Got: %s Expected: %s" % (
        got_rule_ids,
        expected_rule_ids,
    )
    assert len(findings_bad) == 4, "Expected 4 missing_ec_* findings, got %s: %s" % (
        len(findings_bad),
        [f.rule_id for f in findings_bad],
    )
    # All edge-case findings must be flagged as heuristic per the plan.
    assert all(f.heuristic for f in findings_bad), "All missing_ec_* findings must have heuristic=True; got: %s" % [
        (f.rule_id, f.heuristic) for f in findings_bad
    ]

    # GOOD case: expect zero findings for the fully-covered fixture.
    good_tree, good_source = _parse(good_path)  # Parse the healthy fixture.
    detector_good = MissingEdgeCaseDetector()  # Fresh detector.
    findings_good = detector_good.detect(good_path, good_tree, good_source)
    assert findings_good == [], "Expected zero findings on missing-edge-case good fixture, got: %s" % findings_good


def _run_cli_meta(
    tmp_path: Path,
    fixtures_root: Path,
    extra_args: list[str] | None = None,
) -> dict:
    """Run the CLI meta over `fixtures_root` and return the parsed report dict.

    Uses ``--baseline ""`` so baseline diff logic is disabled -- these meta-runs
    only exercise the detector pipeline plus the ``--disable-rule`` filter.
    """
    # Hermetic report + summary paths under the caller's temp directory.
    report_path = tmp_path / "report.json"  # JSON envelope emitted by CLI.
    summary_path = tmp_path / "summary.md"  # Markdown summary (unused here).
    # Base argv shared with the CLI test module; kept in sync with its _base_argv.
    argv = [
        "--roots",
        str(fixtures_root),  # Scan the requested fixture pool.
        "--config",
        str(_CONFIG_PATH),  # Canonical config.toml.
        "--report",
        str(report_path),  # Report artefact for assertion parsing.
        "--summary",
        str(summary_path),  # Summary artefact (written but ignored).
        "--baseline",
        "",  # Disable baseline load/diff for meta runs.
        "--include-mist-api",  # Uniform predicate handling across fixture runs.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Deterministic envelope timestamp.
        "--log-level",
        "WARNING",  # Suppress detector info logs.
    ]
    # Append caller-supplied extras (e.g. --disable-rule <id>) after the base flags.
    if extra_args:
        argv.extend(extra_args)
    # Non-gate run must exit 0 (there is no baseline to diff against).
    rc = main(argv)
    assert rc == 0, "CLI meta run must exit 0 (baseline disabled); got %d" % rc
    # Parse the JSON envelope back into a plain dict for downstream assertions.
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_disable_rule_isolates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T052 [US3]: --disable-rule <id> removes only that rule's findings.

    Baseline meta-run enumerates every rule id the detector pipeline emits
    against ``fixtures/bad/``. For each rule id we then re-run the CLI with
    ``--disable-rule <id>`` and assert both invariants required by US3:

        (a) the disabled rule contributes ZERO findings, and
        (b) every other rule's finding count is unchanged from the baseline.
    """
    # Config resolution + fixture roots resolve from repo root for stable paths.
    monkeypatch.chdir(_REPO_ROOT)
    # Baseline meta-run: enumerate every rule id emitted against fixtures/bad/.
    baseline_dir = tmp_path / "baseline"  # Isolated dir for the baseline report.
    baseline_dir.mkdir()
    baseline_report = _run_cli_meta(baseline_dir, _FIXTURE_BAD)  # No disables.
    baseline_counter = Counter(f["rule_id"] for f in baseline_report["findings"])
    # Precondition: baseline meta-run must produce at least one rule id to test.
    assert len(baseline_counter) > 0, "Baseline meta-run must emit at least one finding to exercise --disable-rule."
    # Sort for deterministic per-rule iteration and failure-message ordering.
    for rule_id in sorted(baseline_counter):
        # Fresh per-rule output directory keeps each report artefact isolated.
        run_dir = tmp_path / ("disable_" + rule_id)
        run_dir.mkdir()
        # Re-run the CLI with only `rule_id` disabled.
        report = _run_cli_meta(run_dir, _FIXTURE_BAD, extra_args=["--disable-rule", rule_id])
        counter = Counter(f["rule_id"] for f in report["findings"])
        # (a) The disabled rule must contribute zero findings.
        assert counter[rule_id] == 0, "--disable-rule %s must remove all %s findings; got %d remaining." % (
            rule_id,
            rule_id,
            counter[rule_id],
        )
        # (b) Every other rule id must be unchanged from the baseline count.
        for other_id, baseline_count in baseline_counter.items():
            if other_id == rule_id:
                continue  # Skip the disabled rule -- already asserted above.
            assert (
                counter[other_id] == baseline_count
            ), "Disabling %s perturbed unrelated rule %s: baseline=%d got=%d" % (
                rule_id,
                other_id,
                baseline_count,
                counter[other_id],
            )


def test_sc003_zero_false_positives_over_good_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T053 [US3]: full CLI meta-run over ``fixtures/good/`` yields zero findings.

    Encodes the SC-003 zero-false-positive contract: healthy fixtures must NOT
    trip any detector. If a false positive slips in, this test surfaces the
    exact finding list for triage.
    """
    # Config resolution + fixture roots resolve from repo root for stable paths.
    monkeypatch.chdir(_REPO_ROOT)
    # Single CLI meta-run against the good corpus with the default rule set.
    report = _run_cli_meta(tmp_path, _FIXTURE_GOOD)
    findings = report["findings"]  # List of finding dicts from the envelope.
    # SC-003 contract: zero findings across the entire good corpus.
    assert findings == [], "SC-003 violated: good corpus emitted %d finding(s): %s" % (len(findings), findings)
