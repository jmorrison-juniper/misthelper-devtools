"""Synthetic source module (SUT) with untested public functions (T021 fixture).

README (bad fixture scenario for UntestedDetector):
    This module represents a synthetic "source under test" containing three
    public callables. Under the bad scenario, no companion test file exists
    that references these callables. UntestedDetector must flag each
    public function with rule id `untested_public_function`.

    The file name intentionally begins with `test_` so pytest's default
    discovery could pick it up if it were placed under `tests/`, but it lives
    under `tools/test_quality_analyzer/fixtures/bad/` which is excluded from
    collection. The `_source` suffix indicates its role as SUT, not as a test.

Expected finding count: 3 (one per public function below).
Related rule id:        untested_public_function
Severity:               HIGH
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.


def orphan_calculation(value: int) -> int:
    """Public function that has no test coverage."""
    # Return a doubled integer; used only to give the detector a callable to flag.
    return value * 2


def orphan_transformer(items: list[str]) -> list[str]:
    """Second public function that has no test coverage."""
    # Uppercase every item to give the detector a second callable to flag.
    return [item.upper() for item in items]


def orphan_reducer(numbers: list[int]) -> int:
    """Third public function that has no test coverage."""
    # Sum the sequence for a trivial return value that is easy to reason about.
    return sum(numbers)


def _private_helper(value: int) -> int:
    """Private helper (leading underscore) -- MUST NOT trigger a finding."""
    # Private names are excluded from the untested-public-function rule.
    return value + 1
