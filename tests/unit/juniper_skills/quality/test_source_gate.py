"""Tests for Juniper source quality gates."""

import gc  # Release SQLite handles before Windows cleanup.
from pathlib import Path  # Build platform-safe fixture paths.

from src.juniper_skills.quality import SourceQualityDatabase, SourceQualityGate  # Test source gate contracts.


def test_space_stripping_failure_returns_specific_reason() -> None:
    text = "setclassoperator-and-bootallow-commandsrequestsystemreboot" * 12  # Simulate the issue 2925 defect.
    score = SourceQualityGate(threshold=0.08).score_text("bad-doc", text)  # Score the corrupted source text.
    assert score.status == "fail"  # Corrupted source must not produce a skill.
    assert "space-to-letter" in score.reason  # The report must name the measured defect.


def test_empty_text_failure_returns_specific_reason() -> None:
    score = SourceQualityGate(threshold=0.08).score_text("empty-doc", "Title\n")  # Score a near-empty source.
    assert score.status == "fail"  # Empty extraction must be quarantined.
    assert score.reason == "almost no extractable text"  # The reason must be actionable.


def test_repeated_header_failure_returns_specific_reason() -> None:
    repeated = "Page Header Footer Text\n" * 80  # Create a document dominated by repeated boilerplate.
    body = "Use this procedure to configure the system login class for operators.\n" * 5  # Add a little content.
    score = SourceQualityGate(threshold=0.01).score_text("repeat-doc", repeated + body)  # Score repeated text.
    assert score.status == "fail"  # Boilerplate-dominated text must be blocked.
    assert "headers" in score.reason  # The reason must identify repeated headers or footers.


def test_zero_document_gate_fails() -> None:
    report = SourceQualityGate().check_paths(())  # Run the guard on no documents.
    assert not report.passed  # A zero-document guard must fail.
    assert report.errors == ("source quality gate checked zero documents",)  # The report must state the cause.


def test_quarantine_writes_quality_and_queue_rows() -> None:
    database_path = Path("data") / "juniper_skills" / "test_source_quality.db"  # Store test state under project data.
    database_path.unlink(missing_ok=True)  # Remove an old test database so assertions read fresh rows.
    report = SourceQualityGate(threshold=0.08).check_paths((Path("data") / "missing.md",))  # Produce zero rows.
    bad_score = SourceQualityGate(threshold=0.08).score_text("bad-doc", "setclassoperatorandboot")  # Build failure.
    report = type(report)((bad_score,), report.distribution, ())  # Reuse the real report class with one failed score.
    SourceQualityDatabase(database_path).write_report(report)  # Persist the quality result and quarantine row.
    import sqlite3  # Import locally so the test dependency stays explicit.

    connection = sqlite3.connect(database_path)  # Open the database so the test can verify durable rows.
    row = connection.execute(
        "SELECT status, reason FROM source_quality WHERE document_key = ?", ("bad-doc",)
    ).fetchone()
    quarantine = connection.execute(
        "SELECT reason FROM source_quarantine WHERE document_key = ?", ("bad-doc",)
    ).fetchone()
    connection.close()  # Close the handle so Windows can remove the test database.
    assert row == ("fail", "almost no extractable text")  # The score table must hold the failure reason.
    assert quarantine == ("almost no extractable text",)  # The quarantine table must block the source.
    gc.collect()  # Force finalizers so Windows can release SQLite file handles.
    database_path.unlink(missing_ok=True)  # Clean the project data file created by this test.
