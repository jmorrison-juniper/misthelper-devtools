"""Golden tests that check the score of real files."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import pathlib  # Builds paths to the fixtures and the guide.

# The folder that holds the shared fixture files.
_FIXTURES = pathlib.Path(__file__).parents[2] / "fixtures" / "ste_linter"

# The repository root, four levels above this test file.
_ROOT = pathlib.Path(__file__).parents[3]


def test_compliant_fixture_scores_high(grade) -> None:
    """The compliant fixture scores at or above 90."""
    result = grade(str(_FIXTURES / "compliant.md"))  # Grade the compliant fixture.
    assert result.score >= 90  # The clean file scores high.


def test_noncompliant_fixture_scores_low(grade) -> None:
    """The noncompliant fixture scores below 60."""
    result = grade(str(_FIXTURES / "noncompliant.md"))  # Grade the noncompliant fixture.
    assert result.score < 60  # The bad file scores low.


def test_writing_guide_scores_high(grade) -> None:
    """The STE writing guide scores at or above 90."""
    guide = _ROOT / "documentation" / "ASD-STE100_writing-guide.md"  # The guide path.
    result = grade(str(guide))  # Grade the guide.
    assert result.score >= 90  # The guide follows its own rules.
