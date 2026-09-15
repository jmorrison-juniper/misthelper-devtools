"""Coverage reporting tests for the refactor analyzer."""

from __future__ import annotations  # Keep annotations light for pytest.

from pathlib import Path  # Build fixture paths safely.

from tools.refactor_analyzer.analysis import RefactorAnalyzer  # Analyzer under test.
from tools.refactor_analyzer.reporting import MarkdownReportGenerator  # Report renderer under test.


def test_refactor_reports_graph_reads_and_unresolved_imports(tmp_path: Path) -> None:
    """The refactor analyzer must report read files and skipped imports."""
    src_root = tmp_path / "src"  # Build a first-party source root.
    src_root.mkdir()  # Create the source root for import classification.
    entrypoint = tmp_path / "entrypoint.py"  # Build a small entrypoint file.
    entrypoint.write_text("import src.missing\n\n\ndef keep():\n    value = 1\n    return value\n", encoding="utf-8")
    analyzer = RefactorAnalyzer(src_root, (), 1, True)  # Configure the analyzer for the fixture tree.
    result = analyzer.analyze(entrypoint)  # Run the analysis.
    assert result.coverage is not None  # New runs must attach coverage to the result.
    skipped = {record.reason for record in result.coverage.skipped_files}  # Inspect skip reasons.
    assert "unresolved_first_party_import" in skipped  # The missing first-party import must be visible.
    report = MarkdownReportGenerator().generate(result)  # Render the Markdown report.
    assert "## Analyzer Coverage" in report  # The Markdown report must include coverage.
    assert "entrypoint.py" in report  # The read entrypoint must be visible in the report.
