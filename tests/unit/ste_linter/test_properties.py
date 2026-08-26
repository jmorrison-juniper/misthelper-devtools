"""Property tests for the word counter."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import pytest  # Skips the module when Hypothesis is not installed.

pytest.importorskip("hypothesis")  # Skip these tests when Hypothesis is absent.

from hypothesis import given, settings  # The decorator that drives the property tests.
from hypothesis import strategies as st  # Builds the input strategies.

from tools.ste_linter.parsing.wordcount import WordCounter  # The counter under test.

# A strategy that builds a simple lower-case word with no special characters.
_WORD = st.from_regex(r"[a-z]{1,10}", fullmatch=True)

# Issue #1803: these properties failed once in a full-suite run and never in
# isolation. Hypothesis reports a per-example deadline breach as a test
# failure, and a full suite runs under memory and CPU pressure that an
# isolated run does not have. Disabling the deadline removes the only
# mechanism consistent with that symptom. The assertions themselves are
# unchanged, so a real counting defect still fails the build.
_NO_DEADLINE = settings(deadline=None)


@_NO_DEADLINE
@given(words=st.lists(_WORD, min_size=1, max_size=20))
def test_count_matches_plain_word_count(words: list[str]) -> None:
    """A run of plain words counts to the number of words."""
    text = " ".join(words)  # Join the words with single spaces.
    assert WordCounter().count(text) == len(words)  # Each plain word counts once.


@_NO_DEADLINE
@given(text=st.text(alphabet="abcdefghij ", min_size=1, max_size=50))
def test_count_is_never_negative(text: str) -> None:
    """The word count is never negative."""
    assert WordCounter().count(text) >= 0  # The count is a non-negative number.
