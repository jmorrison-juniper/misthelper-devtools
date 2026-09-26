"""Page-aware source parser for Juniper fact extraction."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

import logging  # Record parser actions for operator evidence.
import re  # Detect page markers and prose tokens.

from .models import SourceLine  # Share source-line records across extractors.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

_PAGE_PATTERN = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)  # Match converter page markers.


class SourcePageParser:
    """Convert Markdown source text into page-tagged lines."""

    def parse(self, text: str) -> tuple[SourceLine, ...]:
        """Return content lines with exact page numbers."""
        logger.info("Parsing source text into page-tagged lines")  # Log before page parsing.
        current_page = 0  # Reject facts until the first page marker appears.
        lines: list[SourceLine] = []  # Collect content that carries a valid citation.
        for number, raw_line in enumerate(text.splitlines(), start=1):  # Preserve source order for reports.
            current_page = self._page(raw_line, current_page)  # Update the active citation page.
            if self._keeps_line(raw_line, current_page):  # Emit only lines that can cite a page.
                lines.append(SourceLine(number, current_page, raw_line.rstrip()))  # Keep original source spelling.
        logger.debug("Parsed %d page-tagged source lines", len(lines))  # Log the parser output count.
        return tuple(lines)  # Return immutable lines for extractors.

    def prose_chars(self, text: str) -> int:
        """Return source characters after page markers and blanks are removed."""
        logger.info("Measuring source prose characters")  # Log before source measurement.
        lines = [line for line in text.splitlines() if self._measurable(line)]  # Drop markers and blank lines.
        count = len("\n".join(lines))  # Measure the source prose used by the retention target.
        logger.debug("Measured %d source prose characters", count)  # Log the source size.
        return count  # Return the measured source size.

    def _page(self, line: str, current_page: int) -> int:
        """Return the active page after one line."""
        match = _PAGE_PATTERN.search(line)  # Find a page marker when the converter emitted one.
        if not match:  # Most source lines are not page markers.
            return current_page  # Keep the active page for following facts.
        return int(match.group(1))  # Store the exact page number for citations.

    def _keeps_line(self, line: str, current_page: int) -> bool:
        """Return whether a source line can produce a cited fact."""
        stripped = line.strip()  # Normalize edge whitespace for structure tests.
        if current_page <= 0 or not stripped:  # A fact without a page breaks the contract.
            return False  # Skip uncited or empty source lines.
        return not bool(_PAGE_PATTERN.search(stripped))  # Do not let page markers become facts.

    def _measurable(self, line: str) -> bool:
        """Return whether one source line contributes to retention size."""
        stripped = line.strip()  # Normalize edge whitespace for measurement filters.
        return bool(stripped and not _PAGE_PATTERN.search(stripped))  # Count source prose and structures only.
