"""Data models for exhaustive Juniper fact extraction."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

from dataclasses import dataclass  # Define small immutable records for extraction.

from src.juniper_skills.rewrite import CardClassMark, KnowledgeCard  # Reuse the locked card contract.


@dataclass(frozen=True)
class SourceLine:
    """One source line with the exact page that produced it."""

    number: int  # Store the physical line number for deterministic sort order.
    page: int  # Store the source page from the nearest page marker.
    text: str  # Store the source line text for extractor scans.


@dataclass(frozen=True)
class CoverageEntry:
    """One checklist item that a writer must cover."""

    category: str  # Store the manifest section that owns the item.
    value: str  # Store the command, parameter, row, caveat, or qualifier.
    citation_key: str  # Store the exact source page citation.

    def to_markdown(self) -> str:
        """Return one manifest line."""
        return f"- `{self.value}` {self.citation_key}"  # Keep the item exact and cited.


@dataclass(frozen=True)
class CoverageManifest:
    """One source-region checklist for a writing agent."""

    topic: str  # Store the inferred topic name for the source region.
    region: str  # Store the exact source page range for the region.
    entries: tuple[CoverageEntry, ...]  # Store all deduplicated checklist items.
    merge_count: int  # Store how many repeated checklist items merged away.
    numeric_rejection_count: int = 0  # Store numeric candidates rejected because they lacked a full anchor.

    def count(self, category: str) -> int:
        """Return the count of manifest entries in one category."""
        return sum(1 for entry in self.entries if entry.category == category)  # Count only the requested category.

    def values(self, category: str) -> tuple[str, ...]:
        """Return manifest values for one category."""
        return tuple(entry.value for entry in self.entries if entry.category == category)  # Keep source order.

    def to_markdown(self) -> str:
        """Return a publishable coverage manifest."""
        lines = [f"# Coverage manifest: {self.topic}", "", f"region: {self.region}", ""]  # Start report header.
        for category in self.categories():  # Emit each populated section.
            lines.extend(self._section(category))  # Add the section heading and entries.
        return "\n".join(lines).rstrip() + "\n"  # Return Markdown with one final newline.

    def categories(self) -> tuple[str, ...]:
        """Return populated categories in first-seen order."""
        seen: dict[str, None] = {}  # Use dictionary order to keep first occurrence order.
        for entry in self.entries:  # Walk entries in manifest order.
            seen.setdefault(entry.category, None)  # Keep each category once.
        return tuple(seen)  # Return category names for output sections.

    def _section(self, category: str) -> list[str]:
        """Return one manifest section."""
        section = [f"## {category}", ""]  # Start a visible section for the writer.
        section.extend(entry.to_markdown() for entry in self.entries if entry.category == category)  # Add items.
        section.append("")  # Separate sections with a blank line.
        return section  # Return section lines for the report.


@dataclass(frozen=True)
class CoverageVerificationReport:
    """Coverage comparison between a manifest and a finished topic."""

    total_entries: int  # Store the count of manifest facts checked.
    covered_entries: int  # Store the count found in the finished topic.
    missing_entries: tuple[CoverageEntry, ...]  # Store manifest facts not found in the topic.

    @property
    def coverage_percent(self) -> float:
        """Return the share of manifest facts covered by the topic."""
        if self.total_entries <= 0:  # A zero-entry manifest cannot prove coverage.
            return 0.0  # Return zero so callers do not treat it as passed.
        return self.covered_entries * 100.0 / self.total_entries  # Convert covered facts to a percentage.


@dataclass(frozen=True)
class GroundTruthRegion:
    """One independent region with facts found outside the manifest pass."""

    name: str  # Store the human-readable region name.
    source_text: str  # Store the source region text under evaluation.
    facts: tuple[CoverageEntry, ...]  # Store independent facts expected from the region.


@dataclass(frozen=True)
class GroundTruthMeasurement:
    """Recall and precision for one region against independent ground truth."""

    name: str  # Store the region name.
    ground_truth_count: int  # Store the count of independent facts.
    manifest_count: int  # Store the count of manifest entries.
    matched_truth_count: int  # Store how many independent facts the manifest found.
    precise_manifest_count: int  # Store how many manifest entries are real facts.
    missing_facts: tuple[CoverageEntry, ...]  # Store independent facts absent from the manifest.
    noisy_entries: tuple[CoverageEntry, ...]  # Store manifest entries absent from independent truth.

    @property
    def recall_percent(self) -> float:
        """Return the share of independent facts found by the manifest."""
        if self.ground_truth_count <= 0:  # Empty ground truth cannot prove recall.
            return 0.0  # Return zero so empty cases do not pass by accident.
        return self.matched_truth_count * 100.0 / self.ground_truth_count  # Return recall as a percentage.

    @property
    def precision_percent(self) -> float:
        """Return the share of manifest entries that match independent facts."""
        if self.manifest_count <= 0:  # Empty manifests cannot prove precision.
            return 0.0  # Return zero so empty cases do not pass by accident.
        return self.precise_manifest_count * 100.0 / self.manifest_count  # Return precision as a percentage.


@dataclass(frozen=True)
class GroundTruthReport:
    """Aggregated ground-truth measurement for multiple regions."""

    regions: tuple[GroundTruthMeasurement, ...]  # Store per-region recall and precision.

    @property
    def recall_percent(self) -> float:
        """Return aggregate recall across all ground-truth regions."""
        total = sum(region.ground_truth_count for region in self.regions)  # Count all independent facts.
        matched = sum(region.matched_truth_count for region in self.regions)  # Count all matched facts.
        return 0.0 if total <= 0 else matched * 100.0 / total  # Return the aggregate recall.

    @property
    def precision_percent(self) -> float:
        """Return aggregate precision across all ground-truth regions."""
        total = sum(region.manifest_count for region in self.regions)  # Count all manifest entries.
        precise = sum(region.precise_manifest_count for region in self.regions)  # Count real manifest entries.
        return 0.0 if total <= 0 else precise * 100.0 / total  # Return the aggregate precision.


@dataclass(frozen=True)
class ExtractedFact:
    """One candidate fact before cross-extractor deduplication."""

    fact_type: str  # Name the extractor class that produced the fact.
    mark: CardClassMark  # Store the MUST, SHOULD, or INFO card class.
    fact: str  # Store the publishable fact text.
    citation_key: str  # Store the exact source page citation.
    source_span: str  # Store a short internal span for dedup priority.

    def to_card(self) -> KnowledgeCard:
        """Return the existing rewrite card model for this fact."""
        return KnowledgeCard(self.mark, self.fact, self.citation_key)  # Preserve the shared card shape.


@dataclass(frozen=True)
class TopicSplit:
    """One rendered topic file part after size enforcement."""

    name: str  # Store the generated topic part name.
    content: str  # Store the Markdown body for this part.
    size_bytes: int  # Store the encoded size for the hard-limit report.


@dataclass(frozen=True)
class DepthExtractionResult:
    """The full report from one source document extraction."""

    cards: tuple[KnowledgeCard, ...]  # Store the deduplicated cards.
    raw_count: int  # Store the count before deduplication.
    merge_count: int  # Store how many candidate facts merged away.
    pages: tuple[int, ...]  # Store every page that produced at least one line.
    topics: tuple[TopicSplit, ...]  # Store rendered topic parts after size splitting.
    source_prose_chars: int  # Store the measurable source size.
    fact_type_counts: dict[str, int]  # Store deduplicated card counts by extractor fact class.

    @property
    def table_card_count(self) -> int:
        """Return the count of deduplicated table-derived cards."""
        return self.fact_type_counts.get("table-row", 0)  # Report table rows as the table-derived metric.

    @property
    def cards_per_page(self) -> float:
        """Return the average count of cards for each measured page."""
        if not self.pages:  # A damaged source with no pages cannot produce a ratio.
            return 0.0  # Return a safe zero for the report.
        return len(self.cards) / len(self.pages)  # Divide cards by precise page count.

    @property
    def retention_percent(self) -> float:
        """Return generated topic size as a percentage of source prose size."""
        if self.source_prose_chars <= 0:  # Avoid a division error for empty documents.
            return 0.0  # Return a safe zero for the report.
        retained = sum(topic.size_bytes for topic in self.topics)  # Measure generated Markdown bytes.
        return retained * 100.0 / self.source_prose_chars  # Report the retained content percentage.
