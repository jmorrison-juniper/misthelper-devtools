"""Card deduplication for generated Juniper topic files."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

import logging  # Record merge counts for the proof report.
import re  # Normalize card text and citation keys.
from pathlib import Path  # Use platform-safe paths for built-store cleanup.
from typing import Protocol  # Accept card-like records without one concrete class.

from .models import CardDeduplicationReport  # Return a measured merge result.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CardLike(Protocol):
    """Define the fields needed to deduplicate a knowledge card."""

    fact: str  # Read the card text without depending on one model module.
    citation_key: str  # Read citation specificity for tie breaking.


class CardDeduplicator:
    """Merge duplicate knowledge cards before a topic file is written."""

    def deduplicate(self, cards: tuple[CardLike, ...]) -> CardDeduplicationReport:
        """Run the deduplicate operation."""
        logger.info("Deduplicating %s generated knowledge cards", len(cards))  # Log before card merge.
        selected: dict[str, CardLike] = {}  # Store the best card for each normalized fact text.
        for card in cards:  # Inspect every card emitted by the rewrite stage.
            key = self._normalized_text(card.fact)  # Compare only the fact text, not the citation.
            selected[key] = self._specific_card(selected.get(key), card)  # Keep the most specific citation.
        output = tuple(selected.values())  # Preserve first-seen order from the dictionary insertion order.
        report = CardDeduplicationReport(output, len(cards), len(cards) - len(output))  # Build proof metrics.
        logger.debug("Deduplicated cards with %s merges", report.merge_count)  # Report merge count.
        return report  # Return deduplicated cards and the count.

    def deduplicate_markdown_text(self, text: str) -> tuple[str, int]:
        """Run the deduplicate markdown text operation."""
        logger.info("Deduplicating Markdown knowledge-card lines")  # Log before generated text cleanup.
        lines = text.splitlines()  # Preserve the topic file line order.
        selected = self._selected_card_lines(lines)  # Choose the best line for each normalized card.
        emitted: set[str] = set()  # Track selected keys already written to avoid identical duplicates.
        output = [self._replacement_line(line, selected, emitted) for line in lines]  # Remove duplicate card lines.
        cleaned = "\n".join(line for line in output if line is not None)  # Rebuild the Markdown without duplicates.
        merges = len([line for line in output if line is None])  # Count duplicate lines removed from the topic.
        logger.debug("Deduplicated Markdown knowledge cards with %s merges", merges)  # Report line merges.
        return cleaned, merges  # Return cleaned text and merge count.

    def deduplicate_markdown_paths(self, paths: tuple[Path, ...], write: bool = False) -> CardDeduplicationReport:
        """Run the deduplicate markdown paths operation."""
        logger.info("Deduplicating Markdown cards in %s files", len(paths))  # Log before built-store cleanup.
        output: list[object] = []  # Store paths that still exist after scanning.
        merges = 0  # Count duplicate card lines removed across all files.
        for path in paths:  # Process each Markdown path independently.
            text = path.read_text(encoding="utf-8")  # Read a generated topic file.
            cleaned, count = self.deduplicate_markdown_text(text)  # Remove repeated cards in this file.
            self._write_markdown(path, cleaned, count, write)  # Persist only when the caller requests cleanup.
            merges += count  # Add this file's merge count to the report.
            output.append(path)  # Preserve the checked path for count reporting.
        logger.debug("Deduplicated Markdown cards with %s merges across %s files", merges, len(output))  # Report.
        return CardDeduplicationReport(tuple(output), len(output), merges)  # Return aggregate proof counts.

    def _normalized_text(self, text: str) -> str:
        without_citation = re.sub(r"\[[^\]]+\]", "", text)  # Remove any embedded citation before comparison.
        without_citation = re.sub(r"\b[A-Z0-9-]{3,16}\s+p\.\d+(?:-\d+)?\b", "", without_citation)  # Remove keys.
        normalized = re.sub(r"[^a-z0-9]+", " ", without_citation.lower()).strip()  # Fold punctuation and case.
        return normalized  # Return a stable text key.

    def _selected_card_lines(self, lines: list[str]) -> dict[str, str]:
        selected: dict[str, str] = {}  # Store the best Markdown line for each normalized fact.
        for line in lines:  # Inspect each line in the topic file.
            if not self._is_card_line(line):  # Only knowledge card lines belong to this cleanup.
                continue  # Leave non-card content unchanged.
            key = self._normalized_text(line)  # Match duplicate cards by normalized card text.
            selected[key] = self._specific_line(selected.get(key), line)  # Keep the most specific citation line.
        return selected  # Return chosen card lines by normalized text.

    def _replacement_line(self, line: str, selected: dict[str, str], emitted: set[str]) -> str | None:
        if not self._is_card_line(line):  # Non-card lines must remain unchanged.
            return line  # Preserve headings, front matter, prose, and fences.
        key = self._normalized_text(line)  # Recompute the same normalized card key.
        if selected.get(key) != line or key in emitted:  # Drop losers and repeated winners after the first copy.
            return None  # Remove the duplicate card line.
        emitted.add(key)  # Record that the selected card line is now present.
        return line  # Keep the single best card line.

    def _is_card_line(self, line: str) -> bool:
        return bool(re.match(r"^-\s+(?:\*\*)?(?:MUST|SHOULD|INFO)(?:\*\*)?:", line))  # Match contract card lines.

    def _specific_line(self, current: str | None, candidate: str) -> str:
        if current is None:  # A new normalized card has no selected line.
            return candidate  # Keep the first line until a better citation appears.
        if self._citation_score(candidate) > self._citation_score(current):  # Compare citation specificity.
            return candidate  # Prefer the line that points to the better source range.
        return current  # Keep the current line when it is at least as specific.

    def _write_markdown(self, path: Path, cleaned: str, merges: int, write: bool) -> None:
        if write and merges:  # Avoid touching generated files that have no duplicate card lines.
            path.write_text(cleaned + "\n", encoding="utf-8")  # Persist the cleaned topic file.

    def _specific_card(self, current: CardLike | None, candidate: CardLike) -> CardLike:
        if current is None:  # A new fact has no competing citation.
            return candidate  # Store the first card for this text.
        if self._citation_score(candidate.citation_key) > self._citation_score(current.citation_key):  # Compare detail.
            return candidate  # Prefer page ranges, sections, tables, and figures over broad citations.
        return current  # Keep the current card when it is at least as specific.

    def _citation_score(self, citation: str) -> int:
        page_range = 3 if re.search(r"p\.\d+-\d+", citation) else 0  # Page ranges are more specific than defaults.
        named_anchor = 2 if re.search(r"(?:sec|table|fig)\.[a-z0-9-]+", citation) else 0  # Named anchors help review.
        default_penalty = -2 if "p.0-0" in citation else 0  # Penalize placeholder citations.
        return len(citation) + page_range + named_anchor + default_penalty  # Use length as a final detail signal.
