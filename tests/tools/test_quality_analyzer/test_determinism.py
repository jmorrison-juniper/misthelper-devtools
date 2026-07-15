"""Determinism test (T045).

Runs the CLI twice with identical arguments (including ``--fixed-timestamp``)
against the fixture corpus and asserts that both ``report.json`` payloads are
byte-identical. Guards against nondeterminism from dict ordering, floating
timestamps, or set iteration order.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

from pathlib import Path  # Filesystem primitives.

import pytest  # Fixture primitives.

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint under test.

_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Anchor the envelope timestamp.


def _run_once(repo_root: Path, out_dir: Path) -> bytes:
    """Invoke the CLI once and return the raw JSON report bytes."""
    out_dir.mkdir(parents=True, exist_ok=True)  # Ensure hermetic dir exists.
    report_path = out_dir / "report.json"  # Report artefact for this run.
    summary_path = out_dir / "summary.md"  # Summary artefact for this run.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures"
    argv = [
        "--roots",
        str(fixtures_root),  # Fixed corpus for repeatable comparison.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(report_path),  # Hermetic path.
        "--summary",
        str(summary_path),  # Hermetic path.
        "--baseline",
        "",  # Disable baseline logic.
        "--include-mist-api",  # Uniform predicate handling across runs.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Deterministic envelope.
        "--log-level",
        "WARNING",  # Reduce noise.
    ]
    exit_code = main(argv)  # Report only checked; exit code must be 0.
    assert exit_code == 0, "Determinism run must exit 0; got %d" % exit_code
    return report_path.read_bytes()  # Raw bytes for byte-exact comparison.


def test_two_identical_runs_produce_byte_identical_reports(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs with identical inputs must produce byte-identical JSON output."""
    monkeypatch.chdir(repo_root)  # Config resolves relative to repo root.
    first_bytes = _run_once(repo_root, tmp_path / "run1")  # First invocation.
    second_bytes = _run_once(repo_root, tmp_path / "run2")  # Second invocation.
    assert first_bytes == second_bytes, (
        "Two identical CLI runs produced diverging JSON output "
        "(len run1=%d, len run2=%d) -- nondeterminism detected." % (len(first_bytes), len(second_bytes))
    )
