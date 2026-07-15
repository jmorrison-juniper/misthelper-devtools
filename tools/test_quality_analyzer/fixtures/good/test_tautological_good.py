"""Good fixture for TautologicalTestDetector (T030).

README (good fixture scenario for TautologicalTestDetector):
    Each test below uses a healthy, non-tautological assertion. The detector
    must NOT emit any finding when scanning this file.

Expected finding count: 0.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def compute(value: int) -> int:  # Helper SUT parameterized on input.
    """Trivial SUT returning double the input."""
    return value * 2  # Double the argument.


def test_result_equals_expected_literal() -> None:
    """Strong: compares SUT output to an unrelated expected literal."""
    result = compute(3)  # SUT call bound for comparison.
    assert result == 6  # Result compared to derived expected value.


def test_result_type() -> None:
    """Strong: verifies the type against an unrelated concrete type."""
    result = compute(3)  # SUT call bound for isinstance check.
    assert isinstance(result, int)  # Type asserted against int, not type(result).


def test_result_matches_derived_expected() -> None:
    """Strong: compares two independently computed values."""
    left = compute(3)  # First SUT call.
    right = compute(3) + 0  # Independently derived expected value (not `left`).
    assert left == right  # Two independent computations compared.


def test_result_within_range() -> None:
    """Strong: range assertion checking a real property of the result."""
    result = compute(3)  # SUT call bound for range check.
    assert 5 <= result <= 7  # Actual range check based on expected value.


def test_edge_case_markers_present() -> None:
    """Cross-detector: satisfy MissingEdgeCaseDetector markers (empty/zero/neg/None)."""
    assert compute(0) == 0  # Zero literal argument -- zero_value marker.
    assert compute(-5) == -10  # Negative literal argument -- negative_value marker.
    assert len([]) == 0  # Empty container literal argument -- empty_input marker.
    assert bool(None) is False  # None literal argument -- none_input marker.
