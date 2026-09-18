"""Knowledge card extraction for segmented Juniper documentation."""

from __future__ import annotations  # Keep annotations lightweight at import time.

import logging  # Record extraction actions for factory operators.
import re  # Find page markers, sentences, and card fields.

from .models import CardClassMark, KnowledgeCard  # Use the shared card model.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

_LOG = logging.getLogger(__name__)  # Give the card extractor a stable logger name.


class CardExtractor:
    """Extract candidate knowledge cards from a segmented topic."""

    def extract(self, text: str, citation_prefix: str) -> tuple[KnowledgeCard, ...]:
        """Return candidate cards with exact page citations."""
        logger.info("Extracting candidate knowledge cards")  # Log before text analysis.
        cards: list[KnowledgeCard] = []  # Collect cards in document order.
        current_page = "p.unknown"  # Keep a citation even if a caller passes damaged text.
        for line in text.splitlines():  # Walk each line so page markers apply to following facts.
            current_page = self._updated_page(line, current_page)  # Update the exact page citation.
            cards.extend(self._cards_from_line(line, citation_prefix, current_page))  # Add cards from this line.
        logger.debug("Extracted %d candidate knowledge cards", len(cards))  # Log the card count.
        return tuple(cards)  # Return immutable cards for downstream stages.

    def from_markdown(self, line: str) -> KnowledgeCard:
        """Return a card parsed from the contract Markdown shape."""
        logger.info("Parsing one knowledge card from Markdown")  # Log before parsing the card line.
        match = re.match(r"^-\s+\*\*(MUST|SHOULD|INFO)\*\*\s+(.+?)\s+(\[[^\]]+\])$", line.strip())  # Match card.
        if not match:  # Invalid card lines cannot round-trip safely.
            raise ValueError("The line does not match the knowledge card format.")  # Stop with a clear error.
        card = KnowledgeCard(CardClassMark(match.group(1)), match.group(2), match.group(3))  # Build the card.
        logger.debug("Parsed a %s card with citation %s", card.mark.value, card.citation_key)  # Log safe metadata.
        return card  # Return the parsed card.

    def _updated_page(self, line: str, current_page: str) -> str:
        """Return the active page marker after reading one line."""
        match = re.search(r"<!--\s*page\s+(\d+)\s*-->", line, re.IGNORECASE)  # Find converter page markers.
        if not match:  # Most lines do not set a new page.
            return current_page  # Keep the prior citation page.
        return f"p.{match.group(1)}"  # Format the page as the contract citation suffix.

    def _cards_from_line(self, line: str, citation_prefix: str, page: str) -> list[KnowledgeCard]:
        """Return cards from one content line."""
        if self._skip_line(line):  # Headings, blanks, and source markers are not facts.
            return []  # Return no cards for structural lines.
        sentences = self._sentences(line)  # Split the line into sentence-sized facts.
        citation = f"[{citation_prefix} {page}]"  # Build the exact page citation.
        return [KnowledgeCard(self._mark_for(sentence), sentence, citation) for sentence in sentences]  # Create cards.

    def _skip_line(self, line: str) -> bool:
        """Return whether a line is structure instead of a candidate fact."""
        stripped = line.strip()  # Normalize edge whitespace for simple checks.
        return not stripped or stripped.startswith(("#", "---", "|", "<!--"))  # Skip non-prose structures.

    def _sentences(self, line: str) -> tuple[str, ...]:
        """Return readable candidate facts from one line."""
        stripped = re.sub(r"^\s*[-*]\s*", "", line.strip())  # Remove a Markdown bullet marker if present.
        parts = re.split(r"(?<=[.!?])\s+", stripped)  # Split on sentence-ending punctuation.
        return tuple(part.strip() for part in parts if self._valid_sentence(part))  # Keep useful candidate facts.

    def _valid_sentence(self, sentence: str) -> bool:
        """Return whether a sentence has enough content for a card."""
        words = re.findall(r"[A-Za-z0-9]+", sentence)  # Count plain words for a useful fact threshold.
        return len(words) >= 4  # Ignore fragments that are unlikely to make a good card.

    def _mark_for(self, sentence: str) -> CardClassMark:
        """Return the best contract class mark for one sentence."""
        lowered = sentence.lower()  # Compare modal words without case noise.
        if re.search(r"\b(must|required|require|maximum|minimum|only|never|do not)\b", lowered):  # Hard rule signal.
            return CardClassMark.MUST  # Mark the card as a required practice.
        if re.search(r"\b(should|recommend|recommended|may|can)\b", lowered):  # Recommendation signal.
            return CardClassMark.SHOULD  # Mark the card as a recommended practice.
        return CardClassMark.INFO  # Default to an informational fact.
