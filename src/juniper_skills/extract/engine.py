"""Exhaustive source extraction engine for Juniper skill topics."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

import logging  # Record each engine stage for operator evidence.
import re  # Normalize facts for deduplication and source keys.
from pathlib import Path  # Handle all paths with platform-safe objects.

from src.juniper_skills.rewrite import KnowledgeCard  # Use the shared topic card model.

from .extractors import (  # Use one extractor class for each required fact class.
    CommandFactExtractor,
    ConfigurationFactExtractor,
    ConstraintFactExtractor,
    DefinitionFactExtractor,
    FactExtractor,
    NumericFactExtractor,
    OutputFieldFactExtractor,
    PlatformReleaseFactExtractor,
    PrerequisiteFactExtractor,
    TableRowFactExtractor,
)
from .models import DepthExtractionResult, ExtractedFact, SourceLine, TopicSplit  # Share engine records.
from .parser import SourcePageParser  # Parse exact page citations before extraction.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class FactExtractionEngine:
    """Mine source Markdown into dense, cited, copyright-safe cards."""

    SOFT_LIMIT = 12_000  # Report a split risk above the contract soft limit.
    HARD_LIMIT = 20_000  # Split topic files before the contract hard limit.

    def __init__(self, extractors: tuple[FactExtractor, ...] | None = None) -> None:
        """Create the extraction engine and default fact extractors."""
        self.parser = SourcePageParser()  # Use one page parser for all documents.
        self.extractors = extractors or self._default_extractors()  # Allow tests to inject a small extractor set.

    def extract_text(self, text: str, source_key: str) -> DepthExtractionResult:
        """Return dense cards and split topic content for one source text."""
        logger.info("Starting depth extraction for %s", source_key)  # Log before the full extraction.
        lines = self.parser.parse(text)  # Attach page numbers to all source lines.
        facts = self._facts(lines, source_key)  # Run each class-specific extractor.
        kept_facts, merges = self._deduplicate(facts)  # Merge repeated facts from long documents.
        cards = tuple(fact.to_card() for fact in kept_facts)  # Convert kept facts to topic cards.
        type_counts = self._type_counts(kept_facts)  # Count deduplicated cards by extractor fact class.
        topics = self._topics(cards, source_key)  # Render and split topic Markdown.
        prose_chars = self.parser.prose_chars(text)  # Measure source prose for retention.
        pages = tuple(sorted({line.page for line in lines}))  # Count pages that had source content.
        logger.debug("Depth extraction kept %d cards after %d merges", len(cards), merges)  # Log final counts.
        return DepthExtractionResult(cards, len(facts), merges, pages, topics, prose_chars, type_counts)  # Return.

    def extract_path(self, path: Path, source_key: str | None = None) -> DepthExtractionResult:
        """Return dense cards for one Markdown source file."""
        logger.info("Reading source document %s", path)  # Log before file input.
        text = path.read_text(encoding="utf-8")  # Read the source Markdown document.
        logger.debug("Read %d characters from %s", len(text), path)  # Log file size.
        key = source_key or self._source_key(path)  # Build a stable citation key when none is supplied.
        return self.extract_text(text, key)  # Extract facts from the source text.

    def _default_extractors(self) -> tuple[FactExtractor, ...]:
        """Return the default extractor set in deterministic order."""
        return (  # Keep extractor order stable for repeatable topic output.
            CommandFactExtractor(),
            ConfigurationFactExtractor(),
            NumericFactExtractor(),
            TableRowFactExtractor(),
            OutputFieldFactExtractor(),
            ConstraintFactExtractor(),
            PrerequisiteFactExtractor(),
            DefinitionFactExtractor(),
            PlatformReleaseFactExtractor(),
        )

    def _facts(self, lines: tuple[SourceLine, ...], source_key: str) -> tuple[ExtractedFact, ...]:
        """Return facts from all configured extractors."""
        logger.info("Running %d fact extractors", len(self.extractors))  # Log before extractor fan-out.
        facts: list[ExtractedFact] = []  # Collect facts from all classes.
        for extractor in self.extractors:  # Run each class-specific extractor.
            facts.extend(extractor.extract(lines, source_key))  # Append facts in extractor order.
        logger.debug("Extractors emitted %d raw facts", len(facts))  # Log raw extraction count.
        return tuple(facts)  # Return immutable facts for deduplication.

    def _deduplicate(self, facts: tuple[ExtractedFact, ...]) -> tuple[tuple[ExtractedFact, ...], int]:
        """Return deduplicated cards and the merge count."""
        logger.info("Deduplicating extracted facts")  # Log before merge analysis.
        by_key: dict[str, ExtractedFact] = {}  # Store the fullest fact for each normalized meaning.
        for fact in facts:  # Check every extracted fact for repeated meaning.
            key = self._dedup_key(fact)  # Normalize away citation and minor wording differences.
            by_key[key] = self._fuller(by_key.get(key), fact)  # Keep the fact with the fullest source span.
        kept_facts = tuple(by_key.values())  # Keep fact type metadata after deduplication.
        merges = len(facts) - len(kept_facts)  # Count how many candidates merged away.
        logger.debug("Merged %d repeated facts", merges)  # Log deduplication effectiveness.
        return kept_facts, merges  # Return both facts and evidence count.

    def _type_counts(self, facts: tuple[ExtractedFact, ...]) -> dict[str, int]:
        """Return the deduplicated card count for each fact type."""
        logger.info("Counting deduplicated facts by extractor type")  # Log before result metric aggregation.
        counts: dict[str, int] = {}  # Store each fact type count for reports.
        for fact in facts:  # Count only facts that survived deduplication.
            counts[fact.fact_type] = counts.get(fact.fact_type, 0) + 1  # Add one fact to its extractor class.
        logger.debug("Counted %d deduplicated fact types", len(counts))  # Log the number of populated types.
        return counts  # Return counts for measurement reports.

    def _dedup_key(self, fact: ExtractedFact) -> str:
        """Return a stable key for equivalent facts."""
        without_citation = re.sub(r"\[[^\]]+\]", "", fact.fact)  # Remove citation-like text before comparison.
        normalized = re.sub(r"\s+", " ", without_citation.lower()).strip()  # Fold whitespace and case.
        return f"{fact.fact_type}:{normalized}"  # Keep fact classes separate during merge.

    def _fuller(self, current: ExtractedFact | None, new_fact: ExtractedFact) -> ExtractedFact:
        """Return the fact with the fuller source explanation."""
        if current is None:  # A new key has no prior fact.
            return new_fact  # Store the first observed fact.
        if len(new_fact.source_span) > len(current.source_span):  # More source span gives a better citation.
            return new_fact  # Replace the current fact with the fuller evidence.
        return current  # Keep the current fact when it is at least as full.

    def _topics(self, cards: tuple[KnowledgeCard, ...], source_key: str) -> tuple[TopicSplit, ...]:
        """Return topic file parts that satisfy the hard size limit."""
        logger.info("Rendering depth extraction topics for %s", source_key)  # Log before Markdown rendering.
        parts: list[TopicSplit] = []  # Collect split topic bodies.
        current: list[str] = [self._heading(source_key, 1)]  # Start the first topic part.
        for card in cards:  # Add cards without dropping content for size.
            current = self._append_card(parts, current, card, source_key)  # Split before a hard-limit overflow.
        self._store_part(parts, current, source_key)  # Store the final part after all cards are added.
        logger.debug("Rendered %d topic part files for %s", len(parts), source_key)  # Log split count.
        return tuple(parts)  # Return immutable topic parts.

    def _append_card(
        self, parts: list[TopicSplit], current: list[str], card: KnowledgeCard, source_key: str
    ) -> list[str]:
        """Return the active topic lines after one card is added."""
        candidate = [*current, card.to_markdown()]  # Test the next card before changing the active part.
        if self._size(candidate) <= self.HARD_LIMIT:  # The candidate still satisfies the hard limit.
            return candidate  # Keep adding cards to the active part.
        self._store_part(parts, current, source_key)  # Close the current part before it exceeds the limit.
        return [self._heading(source_key, len(parts) + 1), card.to_markdown()]  # Start the next part.

    def _store_part(self, parts: list[TopicSplit], lines: list[str], source_key: str) -> None:
        """Store one topic part when it contains cards."""
        if len(lines) <= 1:  # A heading-only part carries no cards.
            return  # Do not report empty topic parts.
        content = "\n".join(lines) + "\n"  # Render the Markdown body with a final newline.
        name = f"{source_key.lower()}-depth-{len(parts) + 1:03d}.md"  # Build a stable topic part name.
        parts.append(TopicSplit(name, content, len(content.encode("utf-8"))))  # Record content and exact size.

    def _heading(self, source_key: str, part_number: int) -> str:
        """Return the topic heading for one split part."""
        return f"# {source_key} depth facts part {part_number:03d}\n"  # Keep a heading in every part.

    def _size(self, lines: list[str]) -> int:
        """Return the encoded byte size for topic lines."""
        return len(("\n".join(lines) + "\n").encode("utf-8"))  # Match file-system byte size.

    def _source_key(self, path: Path) -> str:
        """Return a stable uppercase citation key for one source path."""
        stem = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-")  # Convert the file name to a citation slug.
        return stem.upper() or "SOURCE"  # Return a non-empty citation key.
