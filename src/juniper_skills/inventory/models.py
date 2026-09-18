"""Data models for the Juniper corpus inventory."""

from __future__ import annotations  # Permit forward references without runtime imports.

from dataclasses import dataclass, field  # Use dataclasses for explicit inventory records.
from pathlib import Path  # Store paths with the cross-platform path type.


@dataclass(frozen=True)
class SourceRoot:
    """A configured source root from the locked contract."""

    name: str
    path: Path
    rank: int


@dataclass
class MarkdownPart:
    """One physical Markdown file from a source root."""

    part_key: str
    root: SourceRoot
    path: Path
    relative_path: str
    content_hash: str
    size_bytes: int
    text_chars: int
    front_matter: dict[str, str]
    catalog: dict[str, str] = field(default_factory=dict)  # Keep optional catalog metadata per part.
    manifest_status: str = "unknown"

    @property
    def has_front_matter(self) -> bool:
        """Return whether this part carries useful front matter."""

        return bool(self.front_matter)


@dataclass
class DocumentGroup:
    """One document candidate before or after duplicate resolution."""

    group_key: str
    title: str
    category: str
    source_pdf: str
    root: SourceRoot
    parts: list[MarkdownPart]
    pages: int
    status: str
    group_method: str
    duplicate_of: str = ""
    is_winner: bool = True
    priority: int = 0
    version_family_key: str = ""
    version_value: str = ""
    version_status: str = "unversioned"
    build_topics: bool = True

    @property
    def text_chars(self) -> int:
        """Return the total text yield for this document."""

        return sum(part.text_chars for part in self.parts)

    @property
    def part_count(self) -> int:
        """Return the number of physical files in this document."""

        return len(self.parts)

    @property
    def front_matter_parts(self) -> int:
        """Return the count of parts that have front matter."""

        return sum(1 for part in self.parts if part.has_front_matter)


@dataclass(frozen=True)
class DuplicateDecision:
    """The duplicate decision for one canonical document."""

    canonical_key: str
    winner: DocumentGroup
    losers: list[DocumentGroup]


@dataclass(frozen=True)
class EditionFamily:
    """A set of similar source names that can be editions, not split parts."""

    family_key: str
    document_count: int
    title_count: int
    page_values: list[int]
    source_files: list[str]


@dataclass(frozen=True)
class VersionedFamily:
    """A product guide family that has current and superseded versions."""

    family_key: str
    current_title: str
    current_version: str
    document_count: int
    superseded_count: int
    superseded_pages: int


@dataclass(frozen=True)
class VersionFamilyValidationResult:
    """The result from a version family invariant validation pass."""

    documents_checked: int
    families_checked: int
    multi_current_families: int
    zero_current_families: int


@dataclass(frozen=True)
class InventoryResult:
    """The measured inventory result."""

    roots_scanned: int
    physical_parts: int
    logical_documents: int
    part_sets: int
    duplicate_losers: int
    category_totals: dict[str, int]
    top_documents: list[DocumentGroup]
    part_set_details: list[DocumentGroup]
    duplicate_details: list[DuplicateDecision]
    edition_families: list[EditionFamily]
    versioned_families: list[VersionedFamily]
    superseded_documents: int
    superseded_pages: int
