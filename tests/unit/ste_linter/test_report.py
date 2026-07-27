"""Tests for the reporters."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Parses the JSON report in the test.

from tools.ste_linter.models import Score, SectionScore, Severity, Violation  # The types to render.
from tools.ste_linter.report import JsonReporter, TextReporter  # The reporters under test.


def _sample_score() -> Score:
    """Return a score with one violation for the reporter tests."""
    violation = Violation(
        rule_id="STE-S3-PASSIVE",  # A sample rule.
        section="3-verbs",  # The section.
        severity=Severity.WARNING,  # The severity.
        path="doc.md",  # The file path.
        line=3,  # The line.
        message="Passive voice.",  # The message.
        suggestion="Use the active voice.",  # The suggestion.
    )  # Build the violation.
    section = SectionScore(section="3-verbs", penalty=0.2, score=80, violation_count=1)  # A section.
    return Score("doc.md", 80, [section], [violation], False, 42)  # Build the score.


def test_text_report_contains_score() -> None:
    """The text report shows the score and the rule."""
    text = TextReporter().render([_sample_score()], min_score=None)  # Render the text report.
    assert "Score: 80/100" in text  # The score line is present.
    assert "STE-S3-PASSIVE" in text  # The rule identifier is present.


def test_text_report_quiet_hides_violations() -> None:
    """The quiet text report shows only the score line."""
    text = TextReporter().render([_sample_score()], min_score=None, quiet=True)  # Quiet render.
    assert "Violations" not in text  # The violation list is hidden.


def test_json_report_is_valid() -> None:
    """The JSON report parses and holds the score."""
    payload = json.loads(JsonReporter().render([_sample_score()], min_score=75))  # Parse the JSON.
    assert payload["results"][0]["score"] == 80  # The score is present.
    assert payload["summary"]["passed"] is True  # The file met the threshold.


def test_json_report_marks_failure() -> None:
    """The JSON summary marks a failure when the score is below the threshold."""
    payload = json.loads(JsonReporter().render([_sample_score()], min_score=90))  # Threshold above score.
    assert payload["summary"]["passed"] is False  # The file did not meet the threshold.
