"""Scoring and grading logic that turns violations into a score and grade."""

from __future__ import annotations  # Enable modern annotation syntax.

from .models import (  # Import scoring constants and types from the models module.
    CATEGORY_PENALTY_CAP,
    GRADE_THRESHOLDS,
    SEVERITY_WEIGHTS,
    Violation,
)


class ComplianceScorer:
    """Convert a list of violations into a 0-100 score and a letter grade."""

    # Grade letters ordered worst to best so a minimum grade can be compared.
    _GRADE_ORDER: tuple[str, ...] = (
        "F",
        "D-",
        "D",
        "D+",
        "C-",
        "C",
        "C+",
        "B-",
        "B",
        "B+",
        "A-",
        "A",
        "A+",
    )

    def score(self, violations: list[Violation]) -> float:
        """Return a 0-100 compliance score for the given violations."""
        penalties: dict[str, int] = {}  # Accumulate weighted penalty per category.
        for violation in violations:  # Inspect every violation exactly once.
            weight = SEVERITY_WEIGHTS[violation.severity]  # Look up the severity weight.
            current = penalties.get(violation.category, 0)  # Read the running category total.
            penalties[violation.category] = current + weight  # Add this violation's weight.
        capped = (min(points, CATEGORY_PENALTY_CAP) for points in penalties.values())  # Cap per category.
        total_penalty = float(sum(capped))  # Sum the capped per-category penalties.
        return max(0.0, 100.0 - total_penalty)  # Clamp the score to a 0-100 floor.

    def grade(self, score: float) -> str:
        """Return the letter grade for a numeric score."""
        for threshold, letter in GRADE_THRESHOLDS:  # Thresholds are ordered high to low.
            if score >= threshold:  # The first satisfied threshold wins.
                return letter  # Return its matching letter grade.
        return "F"  # Defensive fallback if no threshold matched.

    def meets_minimum(self, actual_grade: str, minimum_grade: str) -> bool:
        """Return True when actual_grade is at least the minimum_grade."""
        actual_rank = self._GRADE_ORDER.index(actual_grade)  # Rank of the achieved grade.
        minimum_rank = self._GRADE_ORDER.index(minimum_grade)  # Rank of the required grade.
        return actual_rank >= minimum_rank  # Higher or equal rank passes the gate.
