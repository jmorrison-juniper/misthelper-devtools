"""Checks for the sample package."""

from sample_gates import add


def test_add_returns_the_sum() -> None:
    """The function adds two integers."""
    assert add(2, 3) == 5
