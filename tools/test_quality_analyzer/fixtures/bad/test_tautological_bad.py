"""Bad fixture for TautologicalTestDetector (T029).

README (bad fixture scenario for TautologicalTestDetector):
    Each test below is a tautology: its assertion is logically true regardless
    of any SUT behavior. TautologicalTestDetector must emit exactly one
    finding per case, matching the sub-rule labeled in the inline comment.

Sub-rules covered:
    - taut_literal_true: `assert True` / `assert 1`.
    - taut_literal_equality: both operands are literal constants.
    - taut_variable_self_compare: `assert x == x` (same name each side).
    - taut_isinstance_type_self: `assert isinstance(x, type(x))`.

Expected finding count: 4.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def compute() -> int:  # Helper SUT invoked so each test has something to reference.
    """Trivial SUT returning a fixed integer."""
    return 5  # Constant return value.


def test_literal_true() -> None:
    """Sub-rule: `assert True` -- assertion is a literal truthy constant."""
    compute()  # Call the SUT so the test is not empty (avoids weak_zero_assertions overlap).
    assert True  # taut_literal_true -- always passes.


def test_literal_equality() -> None:
    """Sub-rule: both operands are literal constants."""
    compute()  # Call the SUT to distinguish this from zero-assertion cases.
    assert 1 == 1  # taut_literal_equality -- constant-versus-constant compare.


def test_variable_self_compare() -> None:
    """Sub-rule: `assert x == x` -- variable compared to itself."""
    result = compute()  # SUT call whose result is bound to a variable.
    assert result == result  # taut_variable_self_compare -- always true.


def test_isinstance_type_self() -> None:
    """Sub-rule: `assert isinstance(x, type(x))` -- reflective type check."""
    result = compute()  # SUT result bound for the reflective isinstance check.
    assert isinstance(result, type(result))  # taut_isinstance_type_self -- reflexive check.
