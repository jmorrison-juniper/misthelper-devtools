"""Unit tests for the compliance analyzer tool."""

from __future__ import annotations  # Enable modern annotation syntax.

import sys  # Adjust the import path so the tools package is importable.
from pathlib import Path  # Build throwaway sample files and resolve the repo root.

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # Add repo root to sys.path.

from tools.compliance_analyzer.engine import ComplianceAnalyzer  # System under test: engine.
from tools.compliance_analyzer.reporting import MarkdownReportGenerator  # Report renderer under test.
from tools.compliance_analyzer.scoring import ComplianceScorer  # Scorer under test.

# A deliberately non-compliant sample: a pass-through wrapper plus an alias.
WRAPPER_SOURCE = """
def real_add(first, second):
    return first + second


def add_wrapper(first, second):
    return real_add(first, second)


legacy_add = add_wrapper
"""

# A compliant sample: class-based, fully inline-commented, no indirection layers.
CLEAN_SOURCE = '''\
"""Sample compliant module used to validate the analyzer."""

from __future__ import annotations  # Enable modern annotations.


class Greeter:  # Encapsulate greeting behavior in a class.
    """Produce greetings for callers."""

    def greet(self, target_name: str) -> str:  # Build a greeting for the given name.
        greeting = "Hello, " + target_name  # Compose the greeting text.
        return greeting  # Return the finished greeting.
'''


def test_detects_wrapper_and_alias(tmp_path: Path) -> None:
    """The analyzer flags pass-through wrappers and module-level aliases."""
    target = tmp_path / "sample.py"  # Path for the throwaway bad sample.
    target.write_text(WRAPPER_SOURCE, encoding="utf-8")  # Write the non-compliant sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample file.
    rule_ids = {violation.rule_id for violation in report.violations}  # Collect reported rule ids.
    assert "ARCH-DELEGATE" in rule_ids  # The pass-through wrapper must be flagged.
    assert "ARCH-ALIAS" in rule_ids  # The module-level alias must be flagged.
    assert report.score < 100.0  # Violations must lower the score.


def test_clean_file_scores_well(tmp_path: Path) -> None:
    """A compliant file earns a high score and grade."""
    target = tmp_path / "clean.py"  # Path for the throwaway clean sample.
    target.write_text(CLEAN_SOURCE, encoding="utf-8")  # Write the compliant sample.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the clean sample.
    assert report.score >= 90.0  # A compliant file should score highly.
    assert report.grade in {"A+", "A", "A-"}  # And earn a top-tier grade.


def test_report_contains_speckit_plan(tmp_path: Path) -> None:
    """The Markdown report includes an agent-ready SpecKit remediation plan."""
    target = tmp_path / "sample.py"  # Reuse the non-compliant sample.
    target.write_text(WRAPPER_SOURCE, encoding="utf-8")  # Write the sample to disk.
    report = ComplianceAnalyzer().analyze_file(target)  # Analyze the sample.
    markdown = MarkdownReportGenerator().generate([report])  # Render the Markdown report.
    assert "SpecKit Remediation Plan" in markdown  # The agent-ready plan must be present.
    assert "CMP-001" in markdown  # At least one numbered remediation task must appear.
    assert "Machine-Readable Summary" in markdown  # The JSON summary block must be present.


def test_scorer_grades_and_minimums() -> None:
    """The scorer maps scores to grades and compares minimum grades."""
    scorer = ComplianceScorer()  # Build a scorer instance.
    assert scorer.grade(95.0) == "A"  # A 95 score is an A grade.
    assert scorer.grade(59.0) == "F"  # A failing score is an F grade.
    assert scorer.meets_minimum("B", "C")  # A B grade satisfies a C minimum.
    assert not scorer.meets_minimum("D", "C")  # A D grade fails a C minimum.
