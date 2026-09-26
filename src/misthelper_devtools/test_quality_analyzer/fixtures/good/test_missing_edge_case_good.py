"""Good fixture for MissingEdgeCaseDetector (T038).

README (good fixture scenario for MissingEdgeCaseDetector):
    This module exercises the same numeric SUT but adds tests for the
    numeric edge cases tracked by MissingEdgeCaseDetector. The detector
    must NOT emit any finding when scanning this file.

Edge cases covered here (via distinctive markers):
    - zero_value:  `0` argument literal.
    - negative_value: negative-int argument literal `-5`.
    - empty_input: present as extra coverage but not required by numeric.
    - none_input: present as extra coverage but not required by numeric.

Expected finding count: 0.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def process(value: int):  # SUT accepts an integer, so numeric edge tests apply.
    """Return `value * 2` when truthy, else 0."""
    return value * 2 if value else 0  # Simple truthy-doubling logic.


def test_positive_int_case() -> None:
    """Happy-path positive integer."""
    assert process(5) == 10  # Baseline happy-path assertion.


def test_empty_input_case() -> None:
    """Extra edge: empty container as input."""
    assert process([]) == 0  # Empty list is accepted even though numeric marker does not require it.


def test_zero_value_case() -> None:
    """Edge: literal 0 as input."""
    assert process(0) == 0  # Zero is falsy -> SUT returns 0.


def test_negative_value_case() -> None:
    """Edge: negative int as input."""
    assert process(-5) == -10  # Negative int doubled.


def test_none_input_case() -> None:
    """Extra edge: None as input."""
    assert process(None) == 0  # None is accepted even though numeric marker does not require it.
