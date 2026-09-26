"""Repair converter defects before the segmenter builds topic units."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class DefectRepairResult:
    """The repaired text and the measured defect counts."""

    text: str  # Store Markdown after converter defect repair.
    orphan_words: int  # Count single-word lines joined into the preceding sentence.
    cover_headings: int  # Count fake cover-art headings removed before the real structure.
    non_knowledge_sections: int = 0  # Count front matter sections that do not teach agent knowledge.


class OrphanWordRepairer:
    """Join inline-styled words that the converter moved to their own line."""

    ORPHAN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,24}$")  # Match one useful inline code token.
    PREPOSITION_PATTERN = re.compile(r"\b(?:at|by|for|from|in|into|of|on|to|with)\.?$", re.IGNORECASE)  # Dangling end.

    def repair(self, text: str) -> DefectRepairResult:
        """Run the repair operation."""
        logger.info("Repairing orphan inline words")  # Log before scanning converter output.
        lines: list[str | None] = [line for line in text.splitlines()]  # Permit deletes after splitting source lines.
        repairs = 0  # Count orphan words that join into a sentence.
        index = 0  # Track the scan position across source lines.
        while index < len(lines):  # Visit every line once.
            if self._is_orphan(lines, index):  # Detect a single token separated by blank converter lines.
                repairs += self._join_orphan(lines, index)  # Join the token into the sentence above when safe.
            index += 1  # Continue after the current source line.
        repaired = "\n".join(line for line in lines if line is not None)  # Remove orphan lines that were joined.
        logger.debug("Repaired %s orphan inline words", repairs)  # Report the measured repair count.
        return DefectRepairResult(repaired, repairs, 0, 0)  # Cover-art repair runs in another class.

    def _is_orphan(self, lines: list[str | None], index: int) -> bool:
        line = lines[index] or ""  # Treat a removed line as empty during later checks.
        if not self.ORPHAN_PATTERN.match(line.strip()):  # A repairable orphan is one short token.
            return False
        previous_index = self._previous_text_line(lines, index)  # Find the sentence that may be dangling.
        next_index = self._next_text_line(lines, index)  # Ensure that prose follows the orphan token.
        return (
            previous_index is not None and next_index is not None and self._can_join(lines[previous_index] or "")
        )  # Safe.

    def _join_orphan(self, lines: list[str | None], index: int) -> int:
        previous_index = self._previous_text_line(lines, index)  # Find the line that lost its inline token.
        if previous_index is None:  # Guard against a race with earlier repairs.
            return 0
        word = (lines[index] or "").strip()  # Preserve the orphan token exactly.
        lines[previous_index] = self._insert_word(lines[previous_index] or "", word)  # Rebuild the sentence.
        lines[index] = None  # Delete the standalone orphan line after joining it.
        return 1  # Report one repaired orphan token.

    def _can_join(self, line: str) -> bool:
        stripped = line.strip()  # Inspect sentence shape without changing stored text.
        checks = [" in them." in stripped, stripped.endswith(" ."), stripped.endswith(" ,")]  # Known defects.
        return any(checks) or bool(self.PREPOSITION_PATTERN.search(stripped))  # Join only dangling sentence shapes.

    def _insert_word(self, line: str, word: str) -> str:
        if " in them." in line:  # Repair the measured Junos phrase without changing other words.
            return line.replace(" in them.", f" {word} in them.", 1)
        if line.endswith(" ."):  # Put a missing inline word before a separated period.
            return f"{line[:-2]} {word}."
        if line.endswith(" ,"):  # Put a missing inline word before a separated comma.
            return f"{line[:-2]} {word},"
        return f"{line} {word}"  # Append when the sentence ends with a dangling preposition.

    def _previous_text_line(self, lines: list[str | None], index: int) -> int | None:
        index -= 1  # Start with the line before the orphan token.
        while index >= 0:  # Walk backward across blank converter lines.
            line = lines[index]  # Read the optional line once so type narrowing is stable.
            if line is not None and line.strip():  # Return the first real text line.
                return index
            index -= 1  # Skip blank or removed lines.
        return None  # No join target exists.

    def _next_text_line(self, lines: list[str | None], index: int) -> int | None:
        index += 1  # Start with the line after the orphan token.
        while index < len(lines):  # Walk forward across blank converter lines.
            line = lines[index]  # Read the optional line once so type narrowing is stable.
            if line is not None and line.strip():  # Return the next real text line.
                return index
            index += 1  # Skip blank or removed lines.
        return None  # No following prose exists.


class CoverArtHeadingRepairer:
    """Remove fake cover-art headings before the first real document heading."""

    REAL_START_PATTERN = re.compile(r"^##\s+(?:Chapter\s+\d+|Contents|Introduction)\b", re.IGNORECASE)  # Real start.

    def repair(self, result: DefectRepairResult) -> DefectRepairResult:
        """Run the repair operation."""
        logger.info("Repairing cover-art heading defects")  # Log before removing fake heading structure.
        lines = result.text.splitlines()  # Preserve all non-heading source lines.
        real_start = self._real_start_index(lines)  # Find the first heading that starts the true structure.
        if real_start is None:  # Leave documents without a known real start unchanged.
            logger.debug("Removed %s cover-art headings", 0)  # Report that no cover block existed.
            return result
        heading_count = sum(1 for line in lines[:real_start] if line.startswith("## "))  # Measure fake headings.
        repaired = self._drop_prefix(lines, real_start) if heading_count >= 5 else lines  # Drop the cover block.
        dropped = 1 if heading_count >= 5 else 0  # Count the front matter region as one non-knowledge section.
        logger.debug("Removed %s cover-art headings", heading_count if heading_count >= 5 else 0)  # Report count.
        return DefectRepairResult(
            "\n".join(repaired), result.orphan_words, heading_count if heading_count >= 5 else 0, dropped
        )  # Return metrics.

    def _real_start_index(self, lines: list[str]) -> int | None:
        for index, line in enumerate(lines):  # Search in source order for the true first heading.
            if self.REAL_START_PATTERN.match(line.strip()):  # Stop at the first real structural heading.
                return index
        return None  # No cover art block can be proven.

    def _drop_prefix(self, lines: list[str], real_start: int) -> list[str]:
        return lines[real_start:]  # Drop cover, marketing, reviewers, and copyright before the real structure.


class DocumentDefectRepairer:
    """Apply all segmenter-side converter repairs in the correct order."""

    def __init__(self) -> None:
        """Initialize the DocumentDefectRepairer instance."""
        self.orphan_repairer = OrphanWordRepairer()  # Repair inline code movement before structure cleanup.
        self.cover_repairer = CoverArtHeadingRepairer()  # Repair false heading structure after text repair.

    def repair(self, text: str) -> DefectRepairResult:
        """Run the repair operation."""
        logger.info("Starting document defect repair")  # Log the combined repair pass before any change.
        orphan_result = self.orphan_repairer.repair(text)  # Rejoin orphan words first so headings stay stable.
        final_result = self.cover_repairer.repair(orphan_result)  # Remove cover-art headings after text repair.
        logger.debug(
            "Document repair joined %s orphans and removed %s headings",
            final_result.orphan_words,
            final_result.cover_headings,
        )  # Report both measured repair counts.
        return final_result
