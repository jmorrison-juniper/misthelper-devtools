"""Tests for the scoring model."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from tools.ste_linter.config import LinterConfig  # The configuration for scoring.
from tools.ste_linter.parsing import DocumentBuilder  # Builds documents to score.
from tools.ste_linter.rules import RuleContext, load_rules  # The rules and context.
from tools.ste_linter.scoring import ScoringModel  # The scoring model under test.


def _score(text, backend, grammar, config):
    """Grade a piece of text and return the score object."""
    document = DocumentBuilder().build("test.md", text)  # Parse the text.
    rules = load_rules(config)  # Build the active rules.
    context = RuleContext(backend=backend, grammar=grammar, config=config, dictionary=None)  # Context.
    violations = [item for rule in rules for item in rule.check(document, context)]  # Run the rules.
    return ScoringModel().score(document, violations, rules, False, config)  # Score the document.


def test_empty_document_scores_100(backend, grammar, config) -> None:
    """A file with no prose scores 100."""
    result = _score("", backend, grammar, config)  # Grade empty text.
    assert result.score == 100  # An empty file is fully compliant.


def test_clean_text_scores_high(backend, grammar, config) -> None:
    """Clean STE text scores high."""
    result = _score("Set the switch to ON. The light is green.", backend, grammar, config)  # Clean text.
    assert result.score >= 90  # The clean text scores high.


def test_violation_lowers_score(backend, grammar, config) -> None:
    """A file with violations scores below 100."""
    text = "The file is created by the parser; it isn't ready."  # Passive plus semicolon plus contraction.
    result = _score(text, backend, grammar, config)  # Grade the bad text.
    assert result.score < 100  # The violations lowered the score.


def test_scoring_is_deterministic(backend, grammar, config) -> None:
    """The same input gives the same score twice."""
    text = "The data was corrupted; the file couldn't be read."  # A fixed input.
    first = _score(text, backend, grammar, config)  # Grade once.
    second = _score(text, backend, grammar, config)  # Grade again.
    assert first.score == second.score  # The scores match.


def test_section_breakdown_present(backend, grammar, config) -> None:
    """The score holds a per-section breakdown."""
    result = _score("The file is created by the parser.", backend, grammar, config)  # Grade text.
    assert result.sections  # The breakdown has at least one section.
    assert all(0 <= section.score <= 100 for section in result.sections)  # Each score is in range.


def test_config_weight_zero_removes_penalty(backend, grammar) -> None:
    """Setting a rule weight to zero removes its penalty."""
    config = LinterConfig(weights={"STE-S3-PASSIVE": 0})  # Turn off the passive rule.
    result = _score("The file is created by the parser.", backend, grammar, config)  # Grade text.
    assert all(item.rule_id != "STE-S3-PASSIVE" for item in result.violations)  # The rule did not run.
