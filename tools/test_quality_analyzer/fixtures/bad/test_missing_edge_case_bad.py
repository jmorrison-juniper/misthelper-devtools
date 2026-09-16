"""Bad fixture for MissingEdgeCaseDetector (T037).

README (bad fixture scenario for MissingEdgeCaseDetector):
    This module exercises a numeric-input SUT but only tests a single
    positive value. MissingEdgeCaseDetector (heuristic=True) must emit
    exactly one finding for each uncovered numeric edge case:
    test-quality: edge-case-required=numeric

        - missing_ec_zero_value
        - missing_ec_negative_value

Expected finding count: 2 (all heuristic=True).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def process(value):  # SUT accepting any input; happy-path test only exercises positive int.
    """Return `value * 2` when truthy, else 0."""
    return value * 2 if value else 0  # Simple truthy-doubling logic.


def test_positive_int_case() -> None:
    """Only the happy positive-integer case is exercised."""
    # SUT called with a positive integer only -- no edge coverage.
    assert process(5) == 10  # Happy-path assertion.
