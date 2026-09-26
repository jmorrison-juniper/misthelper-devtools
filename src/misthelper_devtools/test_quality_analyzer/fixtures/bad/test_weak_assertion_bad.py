"""Bad fixture for WeakAssertionDetector (T025).

README (bad fixture scenario for WeakAssertionDetector):
    Each function below intentionally exhibits one weak-assertion sub-rule
    from FR-004. WeakAssertionDetector must emit exactly one finding per
    function with the corresponding rule id. Inline comments identify the
    sub-rule id so the meta-test can pinpoint expected line numbers.

Expected finding count: 6 (one per case).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

from unittest.mock import MagicMock  # For the mock.assert_called() case.

import pytest  # For the pytest.raises(Exception) case.


def compute(value: int) -> int:  # Helper SUT used by weak-assertion cases below.
    """Trivial SUT that returns the doubled input."""
    return value * 2  # Doubles the argument -- used across cases.


def test_bare_assert_result() -> None:
    """Sub-rule: bare `assert result` with no comparison to expected value."""
    result = compute(3)  # SUT call under test.
    assert result  # weak_bare_assert -- truthiness-only check with no expected value.


def test_assert_is_not_none() -> None:
    """Sub-rule: `assert x is not None` with no further semantic check."""
    result = compute(3)  # SUT call under test.
    assert result is not None  # weak_is_not_none -- non-None check without comparison.


def test_mock_assert_called_no_args() -> None:
    """Sub-rule: `mock.assert_called()` with no argument verification."""
    mock = MagicMock()  # Fresh mock to invoke below.
    mock("payload")  # Exercise the mock so assert_called does not raise.
    mock.assert_called()  # weak_mock_called_no_args -- no args verified.


def test_pytest_raises_bare_exception() -> None:
    """Sub-rule: `pytest.raises(Exception)` -- too broad; hides real error type."""
    with pytest.raises(Exception):  # weak_pytest_raises_exception -- overly broad exc class.
        raise ValueError("boom")  # Any exception passes -- assertion is useless.


def test_zero_assertions() -> None:
    """Sub-rule: test function contains no assertions at all."""
    # weak_zero_assertions -- executes SUT but never verifies output.
    compute(3)  # Called but nothing about the result is verified.


def test_self_mock_echo() -> None:
    """Sub-rule: assertion compares a mock's return value to the same mock's return value."""
    mock = MagicMock()  # Fresh mock whose return value echoes back the input.
    mock.return_value = 42  # Configure mock so both sides of the comparison are the same.
    # weak_self_mock_echo -- both operands derive from the same mock; tautology.
    assert mock() == mock.return_value  # Trivially true; tests only mocks.
