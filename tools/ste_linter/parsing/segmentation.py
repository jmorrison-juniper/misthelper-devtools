"""Sentence and paragraph segmentation.

Splits prose into paragraphs on blank lines and splits a paragraph into sentences
on sentence-ending punctuation. A guard list stops common abbreviations from
ending a sentence too early. Each split keeps the character offset of its start so
the document builder can map it back to a source line.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the sentence-boundary search.

# Abbreviations that end with a period but do not end a sentence. The segmenter
# does not break after these.
_ABBREVIATIONS = frozenset(
    {
        "e.g",
        "i.e",
        "etc",
        "vs",
        "no",
        "fig",
        "eq",
        "al",
        "cf",
        "approx",  # General abbreviations.
        "mr",
        "mrs",
        "ms",
        "dr",
        "st",
        "mt",
        "inc",
        "ltd",
        "co",  # Titles and names.
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",  # Months.
    }
)

# Matches a sentence-ending mark (., !, or ?) that is followed by whitespace and a
# capital letter, a digit, an opening quote or bracket, an inline code mark, or
# the end of the text. A technical sentence often starts with a code span, and
# such a sentence needs a boundary as much as one that starts with a capital.
_BOUNDARY = re.compile(r"([.!?])(\s+)(?=[A-Z0-9`\"'(\[]|$)")

# Matches the end of a docstring field entry. A Google-style block writes one
# entry for each name, and a name starts in lower case. The sentence rule above
# needs a capital letter, so it joined every entry of one block into a single
# long "sentence" and the length rule then reported a violation that no writer
# could repair. Issue #1993 records 119 such reports across one package.
_FIELD_BOUNDARY = re.compile(r"([.!?])(\s*\n\s*)(?=[a-z_][a-z0-9_]*(\s*\([^)]*\))?:\s)")


class Segmenter:
    """Splits prose into paragraphs and sentences with offsets."""

    def split_paragraphs(self, text: str) -> list[tuple[str, int]]:
        """Return each paragraph with its character offset in ``text``.

        A blank line separates paragraphs. Empty paragraphs are dropped.
        """
        paragraphs: list[tuple[str, int]] = []  # Holds the found paragraphs.
        offset = 0  # Tracks the character offset of the current block.
        for block in re.split(r"\n\s*\n", text):  # Split on one or more blank lines.
            if block.strip():  # Keep only blocks that hold text.
                start = text.find(block, offset)  # Find where the block starts in the text.
                start = start if start >= 0 else offset  # Fall back to the running offset when not found.
                paragraphs.append((block, start))  # Record the paragraph and its offset.
                offset = start + len(block)  # Advance the offset past this block.
        return paragraphs  # Return the paragraph list.

    def split_sentences(self, text: str, base_offset: int = 0) -> list[tuple[str, int]]:
        """Return each sentence with its character offset from ``base_offset``.

        Splits on sentence-ending punctuation but not after a known abbreviation.
        """
        sentences: list[tuple[str, int]] = []  # Holds the found sentences.
        start = 0  # The start index of the current sentence within the text.
        for match in self._boundaries(text):  # Walk each candidate boundary.
            end = match.end(1)  # The index just after the punctuation mark.
            candidate = text[start:end].strip()  # The sentence text without outer spaces.
            if candidate and not self._ends_with_abbreviation(candidate):  # A real boundary.
                leading = len(text[start:end]) - len(text[start:end].lstrip())  # Count trimmed spaces.
                sentences.append((candidate, base_offset + start + leading))  # Record the sentence.
                start = match.end()  # The next sentence starts after the whitespace.
        tail = text[start:].strip()  # Any text after the last boundary is a sentence.
        if tail:  # Only add a non-empty tail.
            leading = len(text[start:]) - len(text[start:].lstrip())  # Count trimmed spaces.
            sentences.append((tail, base_offset + start + leading))  # Record the final sentence.
        return sentences  # Return the sentence list.

    def _boundaries(self, text: str) -> list[re.Match[str]]:
        """Return every sentence boundary of one paragraph, in reading order.

        Why:
            Two patterns end a sentence. The first is an end mark before a
            capital letter. The second is an end mark before the next entry of
            a docstring field block, which starts in lower case.

            A Google-style block writes one entry for each name. Without the
            second pattern the segmenter joins every entry into one long
            sentence, and the length rule reports a violation that no writer
            can repair.

        Args:
            text: The paragraph to scan.

        Returns:
            The matches of both patterns, sorted by their position. No position
            appears twice, because a duplicate would split one sentence twice.
        """
        found: dict[int, re.Match[str]] = {}  # One match for each end position.
        for match in _BOUNDARY.finditer(text):  # The ordinary sentence boundary.
            found[match.end(1)] = match  # Key on the position of the end mark.
        for match in _FIELD_BOUNDARY.finditer(text):  # The docstring field boundary.
            found.setdefault(match.end(1), match)  # The ordinary rule wins a tie.
        return [found[position] for position in sorted(found)]  # Reading order keeps the offsets right.

    def _ends_with_abbreviation(self, sentence: str) -> bool:
        """Return True when the last token is a known abbreviation."""
        tokens = sentence.rstrip(".!?").split()  # Drop the end mark and split into tokens.
        if not tokens:  # Guard an empty sentence.
            return False  # An empty sentence ends nothing.
        last = tokens[-1].lower().rstrip(".")  # The last token in lower case without a period.
        return last in _ABBREVIATIONS  # True when the token is on the guard list.
