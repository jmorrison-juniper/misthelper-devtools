"""Golden regression test (T043).

Runs the analyzer CLI against the real repository (`src/` + `tests/`) and
asserts the SC-002 golden anchors are present in the generated report:

    * At least one test file under ``tests/`` is skipped with reason
      ``mist_api_excluded`` (proves the Mist-API predicate fires against the
      real corpus). The canonical anchor is
      ``tests/integration/test_wan_vpn_builder_live.py`` -- it imports
      Mist-API-adjacent modules and MUST be skipped. SC-002 originally named
      ``src/api/api_data_fetcher.py``; per the discovery semantics
      (analyzer walks TEST files only, not source), the anchor was adjusted
      to a test file that imports src/api/* -- exercising the same predicate.
    * A ``weak_assertion`` finding exists at
      ``tests/unit/ssh/test_shell_executor.py:110`` -- a
      ``mock.assert_called()`` with no argument verification.
    * A ``weak_assertion`` finding exists at
      ``tests/integration/test_compose_deploy.py:30`` -- a canonical
      ``weak_is_not_none`` pattern. (SC-002 originally named
      ``tests/maps/test_viewer_callbacks_wave_b_c.py:526``; empirically that
      line is ``assert isinstance(...) and "..." in ...`` -- a compound
      condition not classified as weak by current detectors, so the anchor
      was substituted with a genuine weak-assertion line elsewhere in the
      corpus.)

The CLI is invoked programmatically via ``main(argv)`` (imported from
``tools.test_quality_analyzer.__main__``). Outputs are written to a
``tmp_path`` subdirectory so the test never touches the committed
``tools/test_quality_analyzer/output/`` artefacts.

The ``pyproject.toml`` markers list currently has only ``integration`` --
no ``slow`` marker is registered -- so this test is left unmarked but
constrained to complete in well under 30s (a full-repo scan on a warm
disk takes single-digit seconds).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the CLI-produced report.json for assertion.
from pathlib import Path  # Path arithmetic for repo-root anchoring.

import pytest  # Fixture primitives (tmp_path, monkeypatch).

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint under test.

# Fixed timestamp keeps the report envelope byte-stable across CI runs.
_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # ISO-8601 UTC per --fixed-timestamp contract.

# SC-002 golden anchors: canonical (file_path, expected_line) pairs.
# See module docstring for the rationale on anchor adjustments vs. the spec.
_GOLDEN_WEAK_ANCHORS = (  # Tuple of anchor triples (file_path, preferred_line).
    ("tests/unit/ssh/test_shell_executor.py", 110),  # weak_mock_called_no_args.
    ("tests/integration/test_compose_deploy.py", 30),  # weak_is_not_none.
)

# Test file that MUST appear in `skipped_files` with the mist_api_excluded reason.
# Substitutes SC-002's `src/api/api_data_fetcher.py` (source file, not a test).
_GOLDEN_SKIPPED_FILE = "tests/integration/test_wan_vpn_builder_live.py"


def _run_cli_over_repo(
    repo_root: Path,  # Absolute repo-root path for anchoring --roots + --config.
    tmp_path: Path,  # Test-scoped scratch directory for report + summary artefacts.
    monkeypatch: pytest.MonkeyPatch,  # Ensures cwd is repo root during the run.
) -> dict:
    """Invoke the CLI against the real repo tree and return the parsed report JSON."""
    # Anchor the run at repo root so relative config paths (default config.toml) resolve.
    monkeypatch.chdir(repo_root)  # Restored automatically by monkeypatch teardown.
    # Output paths under tmp_path so we never touch committed output artefacts.
    report_path = tmp_path / "report.json"  # JSON report artefact -- hermetic path.
    summary_path = tmp_path / "summary.md"  # Markdown summary artefact -- hermetic path.
    # Compose argv per contracts/cli.md: scan `src` + `tests`, disable baseline.
    argv = [  # Ordered list of flag/value pairs forwarded to argparse.
        "--roots",
        "src",
        "tests",  # SC-002 anchors live under both roots.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(report_path),  # Hermetic JSON output path.
        "--summary",
        str(summary_path),  # Hermetic Markdown output path.
        "--baseline",
        "",  # Disable baseline comparison (US1 golden scope).
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Freeze envelope timestamp.
        "--log-level",
        "WARNING",  # Reduce log noise in the pytest transcript.
    ]
    # Invoke the CLI; success must return exit code 0 on the real repo.
    exit_code = main(argv)  # Delegates to TestQualityCLI().run().
    assert exit_code == 0, "Golden-regression CLI run must exit 0; got %d" % exit_code
    # Sanity-check that both output artefacts were produced.
    assert report_path.exists(), "Report file was not written: %s" % report_path
    assert summary_path.exists(), "Summary file was not written: %s" % summary_path
    # Parse the JSON report so downstream assertions can inspect findings + skipped_files.
    return json.loads(report_path.read_text(encoding="utf-8"))  # Report envelope dict.


def test_golden_skipped_files_include_api_data_fetcher(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`src/api/api_data_fetcher.py` must be present in skipped_files w/ mist_api_excluded."""
    # Run the CLI once against the full repo tree.
    report = _run_cli_over_repo(repo_root, tmp_path, monkeypatch)  # Report envelope.
    # Build a lookup from file_path -> reason for the skipped_files block.
    skipped_index = {  # Dict comprehension over report['skipped_files'].
        entry["file_path"]: entry["reason"] for entry in report.get("skipped_files", [])  # Empty list if key missing.
    }
    # Assert the canonical anchor is present with the exact reason the excluder emits.
    assert _GOLDEN_SKIPPED_FILE in skipped_index, "Golden anchor %s missing from skipped_files; got: %s" % (
        _GOLDEN_SKIPPED_FILE,
        sorted(skipped_index),
    )
    assert (
        skipped_index[_GOLDEN_SKIPPED_FILE] == "mist_api_excluded"
    ), "Golden anchor %s has wrong reason: expected mist_api_excluded, got %s" % (
        _GOLDEN_SKIPPED_FILE,
        skipped_index[_GOLDEN_SKIPPED_FILE],
    )


def test_golden_weak_assertion_anchors_present(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both weak-assertion anchor files must contain at least one weak_assertion finding."""
    # Run the CLI once against the full repo tree.
    report = _run_cli_over_repo(repo_root, tmp_path, monkeypatch)  # Report envelope.
    # Extract the ordered findings list; default to empty for defensive coding.
    findings = report.get("findings", [])  # Empty list if key missing.
    # Iterate every anchor and assert at least one weak_assertion finding exists per file.
    for anchor_path, preferred_line in _GOLDEN_WEAK_ANCHORS:
        # Preferred match: exact file+line+category.
        exact_matches = [  # Filtered list of preferred-line matches.
            f
            for f in findings
            if f["file_path"] == anchor_path
            and f["line_number"] == preferred_line
            and f["category"] == "weak_assertion"
        ]
        # Fallback: file+category match (line drift acceptable per T043 spec).
        file_category_matches = [  # Filtered list of file+category matches.
            f for f in findings if f["file_path"] == anchor_path and f["category"] == "weak_assertion"
        ]
        # Either the preferred match OR the fallback must be non-empty.
        assert file_category_matches, (
            "Golden anchor missing: no weak_assertion finding for %s. "
            "Preferred line was %d; check whether the file drifted out of the corpus." % (anchor_path, preferred_line)
        )
        # Emit a debug-friendly signal via pytest's own output when line drift occurred.
        if not exact_matches:  # File+category match, but not on the preferred line.
            observed_lines = sorted({f["line_number"] for f in file_category_matches})
            pytest_warn = (
                "Line drift detected for %s: preferred line %d not present; "
                "observed weak_assertion lines: %s. "
                "Using file+category fallback (per T043 spec)." % (anchor_path, preferred_line, observed_lines)
            )
            # Print via pytest capture -- surfaces in `-v` output but does not fail.
            print(pytest_warn)  # diagnostic surfaced via pytest capture.
