"""STE word counting.

Counts the words in a sentence by the rules in the writing guide, Section 8. A
number, a number joined to a unit, an acronym, an alphanumeric identifier, a
quoted span, and a hyphenated group each count as one word. The sentence-length
rule depends on this count, so the method must match the guide.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the span protection and token tests.

# A placeholder that stands for one protected span. It holds no whitespace, so it
# survives the whitespace split as a single token.
_PROTECTED_TOKEN = "\x00P\x00"  # nosec B105 - The value is a null-byte delimiter that survives a split.

# Matches a double-quoted, single-quoted, or backtick-quoted span. Each match
# becomes one word under the STE counting rules.
_QUOTED_SPAN = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`")

# Common measurement units. A number that is followed by one of these merges with
# it into a single word, for example "10 mA".
_UNITS = frozenset(
    {
        "mm",
        "cm",
        "m",
        "km",
        "in",
        "ft",
        "yd",
        "mi",  # Length units.
        "mg",
        "g",
        "kg",
        "lb",
        "oz",  # Mass units.
        "ms",
        "s",
        "sec",
        "min",
        "h",
        "hr",  # Time units.
        "hz",
        "khz",
        "mhz",
        "ghz",  # Frequency units.
        "v",
        "mv",
        "kv",
        "a",
        "ma",
        "w",
        "kw",
        "mw",  # Electrical units.
        "b",
        "kb",
        "mb",
        "gb",
        "tb",
        "bps",
        "kbps",
        "mbps",
        "gbps",  # Data units.
        "c",
        "f",
        "k",  # Temperature units.
        "pa",
        "kpa",
        "psi",
        "bar",  # Pressure units.
        "percent",  # Written-out percent.
    }
)

# Matches a token that is a plain number, with an optional decimal part, an
# optional sign, and an optional trailing percent sign.
_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?%?$")


class WordCounter:
    """Counts words in prose by the STE rules."""

    def count(self, text: str) -> int:
        """Return the STE word count for ``text``.

        Protects quoted spans first, then merges a number with a following unit,
        then counts the tokens that hold at least one letter, digit, or protected
        span.
        """
        protected = _QUOTED_SPAN.sub(_PROTECTED_TOKEN, text)  # Turn each quoted span into one token.
        tokens = protected.split()  # Split on any run of whitespace.
        merged = self._merge_number_units(tokens)  # Join a number to a following unit.
        countable = [token for token in merged if self._is_word(token)]  # Drop pure punctuation.
        return len(countable)  # The number of countable tokens is the word count.

    def _merge_number_units(self, tokens: list[str]) -> list[str]:
        """Join a number token to a following unit token into one word."""
        result: list[str] = []  # Holds the tokens after merging.
        skip_next = False  # True when the previous step consumed the next token.
        for index, token in enumerate(tokens):  # Walk the tokens in order.
            if skip_next:  # The unit was already merged into the number.
                skip_next = False  # Reset the flag for the next token.
                continue  # Do not add the unit a second time.
            following = tokens[index + 1] if index + 1 < len(tokens) else ""  # Peek at the next token.
            if _NUMBER.match(token) and following.strip(".,").lower() in _UNITS:  # A number then a unit.
                result.append(token + " " + following)  # Merge them into one word.
                skip_next = True  # Skip the unit on the next turn.
            else:  # No merge applies here.
                result.append(token)  # Keep the token as is.
        return result  # Return the merged token list.

    def _is_word(self, token: str) -> bool:
        """Return True when the token counts as a word."""
        if _PROTECTED_TOKEN in token:  # A protected span always counts as one word.
            return True  # Count the quoted span.
        return any(character.isalnum() for character in token)  # Count tokens with a letter or digit.
