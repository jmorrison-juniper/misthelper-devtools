"""Good fixture for UntestedDetector -- SUT + companion test (T022 fixture).

README (good fixture scenario for UntestedDetector):
    This module doubles as the synthetic source module AND the companion
    test. It defines two public callables and immediately references each
    of them in test-like functions. When UntestedDetector runs on this
    fixture pair, every public function has at least one reference in the
    test corpus, so ZERO findings should be emitted.

Expected finding count: 0
Related rule id:        untested_public_function (must NOT fire)
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def covered_calculation(value: int) -> int:
    """Public function that IS referenced by the tests below."""
    # Double the input for a trivial round-trip.
    return value * 2


def covered_transformer(items: list[str]) -> list[str]:
    """Second public function that IS referenced by the tests below."""
    # Uppercase every string for another trivial round-trip.
    return [item.upper() for item in items]


def test_covered_calculation_returns_double() -> None:
    """Reference `covered_calculation` explicitly so the detector sees coverage."""
    # Compare against the known-correct expected value.
    assert covered_calculation(3) == 6


def test_covered_transformer_uppercases_items() -> None:
    """Reference `covered_transformer` explicitly so the detector sees coverage."""
    # Compare against the known-correct expected value.
    assert covered_transformer(["a", "b"]) == ["A", "B"]


def test_edge_case_markers_present() -> None:
    """Cross-detector: satisfy MissingEdgeCaseDetector markers (empty/zero/neg/None)."""
    assert covered_calculation(0) == 0  # Zero literal argument -- zero_value marker.
    assert covered_calculation(-5) == -10  # Negative literal argument -- negative_value marker.
    assert covered_transformer([]) == []  # Empty container literal argument -- empty_input marker.
    assert bool(None) is False  # None literal argument -- none_input marker.
