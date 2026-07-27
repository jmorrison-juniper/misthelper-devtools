"""Markdown prose extraction.

Reads Markdown text and returns the gradable prose as spans. The parser skips
fenced code, headings, tables, and raw HTML, and it strips inline code, links,
images, and emphasis markers. Each span holds a block of consecutive source lines
so the document builder can map a sentence back to its line.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the line tests and the inline cleaning.

from ..models import ProseSpan  # The output type for each prose block.

# Matches the start or end of a fenced code block, with backticks or tildes.
_FENCE = re.compile(r"^\s*(```|~~~)")

# Matches an ATX heading line, for example "## Title".
_HEADING = re.compile(r"^\s*#{1,6}\s")

# Matches a Markdown table row or separator, which starts with a pipe.
_TABLE = re.compile(r"^\s*\|")

# Matches a line that is only a table separator, for example "---|:--:".
_TABLE_RULE = re.compile(r"^\s*[:\-\| ]+$")

# Matches an inline image, which the parser removes whole.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# Matches an inline link and keeps the visible text, dropping the URL.
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# Matches a reference-style link and keeps the visible text.
_REF_LINK = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")

# Matches an inline code span, which the parser removes.
_INLINE_CODE = re.compile(r"`[^`]*`")

# Matches an HTML tag, which the parser removes.
_HTML_TAG = re.compile(r"<[^>]+>")

# Matches emphasis and strike markers, which the parser removes.
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|~~)")

# Matches a leading list marker, for example "- ", "* ", or "1. ".
_LIST_MARKER = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")

# Matches a leading blockquote marker.
_BLOCKQUOTE = re.compile(r"^\s*>+\s?")


class MarkdownParser:
    """Extracts prose spans from Markdown text."""

    def parse(self, text: str) -> list[ProseSpan]:
        """Return the gradable prose spans found in ``text``."""
        spans: list[ProseSpan] = []  # Holds the finished prose spans.
        buffer: list[str] = []  # Holds the cleaned lines of the current block.
        block_start = 0  # The source line where the current block starts.
        in_fence = False  # True while the parser is inside a fenced code block.
        for number, raw in enumerate(text.splitlines(), start=1):  # Walk each source line.
            if _FENCE.match(raw):  # A fence line toggles code mode and never grades.
                in_fence = not in_fence  # Flip the fence state.
                block_start = self._flush(spans, buffer, block_start)  # End the current block.
                continue  # Do not grade the fence line.
            if in_fence or self._is_skippable(raw):  # Code, headings, tables, or blank markup.
                block_start = self._flush(spans, buffer, block_start)  # End the current block.
                continue  # Skip the line.
            cleaned = self._clean_inline(raw)  # Remove inline code, links, and markup.
            if not buffer:  # This line starts a new block.
                block_start = number  # Record the block start line.
            buffer.append(cleaned)  # Add the cleaned line to the block.
        self._flush(spans, buffer, block_start)  # Flush any block left at the end.
        return spans  # Return every prose span.

    def _flush(self, spans: list[ProseSpan], buffer: list[str], start_line: int) -> int:
        """Turn the buffer into a span, clear the buffer, and return a reset start."""
        if buffer and any(line.strip() for line in buffer):  # Only keep a block with text.
            spans.append(ProseSpan(text="\n".join(buffer), start_line=start_line, kind="markdown"))  # Save it.
        buffer.clear()  # Empty the buffer for the next block.
        return 0  # Reset the start line marker.

    def _is_skippable(self, raw: str) -> bool:
        """Return True when the line is a heading, a table row, or a table rule."""
        if _HEADING.match(raw):  # A heading is a label, not gradable prose.
            return True  # Skip the heading.
        if _TABLE.match(raw):  # A table row holds cells, not prose sentences.
            return True  # Skip the table row.
        if raw.strip() and _TABLE_RULE.match(raw):  # A table separator line.
            return True  # Skip the separator.
        return False  # The line is not skippable.

    def _clean_inline(self, raw: str) -> str:
        """Remove inline code, links, images, HTML, and markers from a line."""
        line = _BLOCKQUOTE.sub("", raw)  # Drop a leading blockquote marker.
        line = _LIST_MARKER.sub(r"\1", line)  # Drop a leading list marker, keep indent.
        line = _IMAGE.sub(" ", line)  # Remove inline images whole.
        line = _INLINE_LINK.sub(r"\1", line)  # Replace a link with its visible text.
        line = _REF_LINK.sub(r"\1", line)  # Replace a reference link with its text.
        line = _INLINE_CODE.sub(" ", line)  # Remove inline code spans.
        line = _HTML_TAG.sub(" ", line)  # Remove HTML tags.
        line = _EMPHASIS.sub("", line)  # Remove emphasis and strike markers.
        return line  # Return the cleaned line.
