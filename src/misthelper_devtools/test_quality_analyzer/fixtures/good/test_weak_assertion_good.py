"""Good fixture for WeakAssertionDetector (T026).

README (good fixture scenario for WeakAssertionDetector):
    Each function below uses a strong assertion pattern that the detector
    must NOT flag. Every weak sub-rule from FR-004 has a corresponding
    positive example demonstrating the healthy alternative.

Expected finding count: 0
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

from unittest.mock import MagicMock  # For the mock assertions with argument checks.

import pytest  # For pytest.raises with a narrow exception class.


def compute(value: int) -> int:  # Helper SUT used by strong-assertion cases below.
    """Trivial SUT that returns the doubled input."""
    return value * 2  # Doubles the argument -- shared across cases.


def test_asserts_expected_value() -> None:
    """Strong: compares result to a specific expected value."""
    result = compute(3)  # SUT call under test.
    assert result == 6  # Compares to expected literal -- strong assertion.


def test_asserts_specific_type_and_value() -> None:
    """Strong: verifies both type and value, not just non-None."""
    result = compute(3)  # SUT call under test.
    assert isinstance(result, int)  # Verify type explicitly.
    assert result == 6  # Verify value explicitly.


def test_mock_assert_called_with_specific_args() -> None:
    """Strong: `mock.assert_called_with(...)` verifies the exact argument tuple."""
    mock = MagicMock()  # Fresh mock invoked below with a known argument.
    mock("payload")  # Exercise the mock so the assertion below has data.
    mock.assert_called_with("payload")  # Strong -- verifies argument value.


def test_pytest_raises_narrow_exception() -> None:
    """Strong: `pytest.raises(ValueError)` with a specific exception class."""
    with pytest.raises(ValueError):  # Narrow exception class -- strong assertion.
        raise ValueError("boom")  # Only ValueError satisfies the assertion.


def test_covers_return_value_semantically() -> None:
    """Strong: contains multiple assertions covering distinct return semantics."""
    result = compute(4)  # SUT call under test.
    assert result == 8  # Value assertion.
    assert result % 2 == 0  # Semantic assertion (must be even).


def test_compares_two_distinct_sut_calls() -> None:
    """Strong: compares two independent SUT invocations, not the same mock echo."""
    left = compute(3)  # First independent SUT invocation.
    right = compute(3)  # Second independent SUT invocation with identical input.
    assert left == right  # Compares two real SUT calls -- deterministic invariant.


def test_edge_case_markers_present() -> None:
    """Cross-detector: satisfy MissingEdgeCaseDetector markers (empty/zero/neg/None)."""
    assert compute(0) == 0  # Zero literal argument -- zero_value marker.
    assert compute(-5) == -10  # Negative literal argument -- negative_value marker.
    assert len([]) == 0  # Empty container literal argument -- empty_input marker.
    assert bool(None) is False  # None literal argument -- none_input marker.
