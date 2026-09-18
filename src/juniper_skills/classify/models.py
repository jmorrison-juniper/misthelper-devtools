"""Data contracts for Juniper domain classification."""

from __future__ import annotations  # Keep annotations available without import cycles.

from dataclasses import dataclass  # Use dataclasses for explicit classifier records.
from pathlib import Path  # Store file locations with the cross-platform path type.


@dataclass(frozen=True)
class DomainRule:
    """One ordered taxonomy rule from the contract."""

    priority: int  # Preserve the contract order for first-match assignment.
    domain: str  # Store the skill package name that owns the matched subject.
    keywords: tuple[str, ...]  # Keep all routing terms for a deterministic scan.
    category_keywords: tuple[str, ...] = ()  # Match category names without content noise.
    regex_keywords: tuple[str, ...] = ()  # Match model and command patterns safely.


@dataclass(frozen=True)
class DomainDocument:
    """All available signals for one source document."""

    document_key: str  # Preserve the database primary key for persistence.
    title: str  # Use the human title because it is the strongest signal.
    category: str  # Use the harvester category as a lower-priority signal.
    source_pdf: str  # Use the original path because it often holds the subject.
    pages: int = 0  # Include pages so reports can reconcile size.
    headings: tuple[str, ...] = ()  # Use headings when title and path do not decide.
    content: str = ""  # Use sampled body text as the last technical signal.
    part_paths: tuple[Path, ...] = ()  # Keep paths for traceable file reads.


@dataclass(frozen=True)
class DomainAssignment:
    """One domain decision that can be stored in SourceDocument."""

    document_key: str  # Tie the decision to the exact source document row.
    domain: str  # Store the single source-of-truth domain.
    confidence: float  # Show whether the rule matched a strong signal.
    signal: str  # Explain which signal and keyword selected the domain.
    matched_rule: str  # Preserve the contract rule name for audit reports.
    low_confidence: bool  # Flag fallback and weak body-only decisions.


@dataclass(frozen=True)
class DomainReport:
    """Summary for one database classification run."""

    total_documents: int  # Reconcile classification against the source table.
    total_pages: int  # Reconcile page ownership across all domains.
    domain_counts: dict[str, int]  # Show how many documents each domain owns.
    domain_pages: dict[str, int]  # Show how many pages each domain owns.
    signal_counts: dict[str, int]  # Show which signal type made each decision.
    low_confidence: tuple[DomainAssignment, ...]  # List decisions that need taxonomy work.
