"""Zero-network test (T044).

Monkeypatches ``socket.socket`` and ``socket.create_connection`` so that any
attempt to instantiate a socket raises ``RuntimeError``. Runs the CLI against
the fixture corpus and asserts a successful exit and non-empty report --
proving the analyzer performs no network I/O.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Parse the produced report to check non-emptiness.
import socket  # Target of the monkeypatch: reject any socket construction.
import sys  # Control process argv for the None-input CLI path.
from pathlib import Path  # Filesystem primitives for output paths.

import pytest  # Fixture primitives.

from tools.test_quality_analyzer.__main__ import main  # CLI entrypoint under test.

_FROZEN_TIMESTAMP = "2026-07-14T00:00:00+00:00"  # Freeze envelope for determinism.


def _forbid(*_args: object, **_kwargs: object) -> object:
    """Raise on any socket construction to prove the analyzer stays offline."""
    raise RuntimeError("network access forbidden by test_no_network")


def test_zero_network_during_full_run(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI must run to completion with no socket construction anywhere."""
    monkeypatch.setattr(socket, "socket", _forbid)  # Block raw socket creation.
    monkeypatch.setattr(socket, "create_connection", _forbid)  # Block helper too.
    monkeypatch.chdir(repo_root)  # Config default path resolves relative to repo root.
    report_path = tmp_path / "report.json"  # Hermetic report output.
    summary_path = tmp_path / "summary.md"  # Hermetic summary output.
    fixtures_root = repo_root / "tools" / "test_quality_analyzer" / "fixtures"
    argv = [
        "--roots",
        str(fixtures_root),  # Scan the analyzer's own fixture corpus.
        "--config",
        str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
        "--report",
        str(report_path),  # Hermetic path.
        "--summary",
        str(summary_path),  # Hermetic path.
        "--baseline",
        "",  # Disable baseline logic (US1 scope).
        "--include-mist-api",  # Bypass exclusion so fixture predicates all run.
        "--fixed-timestamp",
        _FROZEN_TIMESTAMP,  # Deterministic envelope.
        "--log-level",
        "WARNING",  # Reduce noise.
    ]
    exit_code = main(argv)  # Must not touch the network to complete.
    assert exit_code == 0, "CLI must exit 0 under the zero-network constraint; got %d" % exit_code
    assert report_path.exists(), "Report must be produced without socket access."
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("findings"), "Fixture corpus must yield findings even offline."
    process_report_path = tmp_path / "process-report.json"  # Keep the None-input report separate.
    process_summary_path = tmp_path / "process-summary.md"  # Keep the None-input summary separate.
    monkeypatch.setattr(  # Replace argv so main(None) stays hermetic and offline.
        sys,
        "argv",
        [
            "test_quality_analyzer",
            "--roots",
            str(fixtures_root),
            "--config",
            str(repo_root / "tools" / "test_quality_analyzer" / "config.toml"),
            "--report",
            str(process_report_path),
            "--summary",
            str(process_summary_path),
            "--baseline",
            "",
            "--include-mist-api",
            "--fixed-timestamp",
            _FROZEN_TIMESTAMP,
            "--log-level",
            "WARNING",
        ],
    )
    process_exit_code = main(None)  # Exercise the None edge case without socket access.
    process_report = json.loads(process_report_path.read_text(encoding="utf-8"))  # Parse the second report.
    assert process_exit_code == 0, "None argv must exit 0 under the zero-network constraint."
    assert process_report.get("findings"), "None argv must still yield findings without network access."
    monkeypatch.chdir(tmp_path)  # Move away from repository roots before the empty-input path.
    empty_exit_code = main([])  # Exercise the empty sequence edge case without socket access.
    assert empty_exit_code == 2, "Empty argv without a test root must exit 2 under the zero-network constraint."
