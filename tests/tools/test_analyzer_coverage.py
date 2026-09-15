"""Tests for the shared analyzer coverage report model."""

from __future__ import annotations  # Keep annotations light for pytest.

from pathlib import Path  # Build paths with platform-safe separators.

from tools.analyzer_coverage import AnalyzerCoverageRenderer, AnalyzerCoverageTracker  # Coverage under test.


def test_coverage_renderer_reports_reads_and_skips() -> None:
    """The shared renderer must list read files and skipped files."""
    tracker = AnalyzerCoverageTracker("unit_analyzer")  # Build a tracker for a synthetic analyzer.
    tracker.record_read(Path("src") / "sample.py")  # Record a measured source file.
    tracker.record_skip(Path("tests") / "fixtures" / "sample.py", "fixture", explicit=True)  # Record a skip.
    summary = tracker.summary()  # Freeze the coverage records for rendering.
    text = AnalyzerCoverageRenderer().to_text(summary)  # Render the operator-facing text.
    assert "Files read: 1" in text  # The read count must be visible.
    assert "Files skipped: 1" in text  # The skip count must be visible.
    assert "tests/fixtures/sample.py (fixture)" in text  # The skipped path and reason must be visible.
    assert summary.has_unintended_skip is True  # Explicit skipped targets must fail callers.
