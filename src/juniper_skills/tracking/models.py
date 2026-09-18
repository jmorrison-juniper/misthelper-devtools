"""Data shapes for the Juniper skill factory GitHub journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class StageName(StrEnum):
    """Known stages that a document moves through."""

    QUEUED = "queued"
    PARTS_JOINED = "parts joined"
    SEGMENTED = "segmented"
    EXTRACTED = "extracted"
    REWRITTEN = "rewritten"
    GUARDED = "guarded"
    STE_VALIDATED = "STE validated"
    SPECKIT_EMITTED = "SpecKit artifacts emitted"
    PACKAGE_ASSEMBLED = "package assembled"
    INSTALLED = "installed"
    VERIFIED = "verified"
    RELEASED = "released"
    FAILED = "failed"


STAGE_ORDER: tuple[StageName, ...] = (  # Keep the resume order stable after a crash.
    StageName.QUEUED,
    StageName.PARTS_JOINED,
    StageName.SEGMENTED,
    StageName.EXTRACTED,
    StageName.REWRITTEN,
    StageName.GUARDED,
    StageName.STE_VALIDATED,
    StageName.SPECKIT_EMITTED,
    StageName.PACKAGE_ASSEMBLED,
    StageName.INSTALLED,
    StageName.VERIFIED,
    StageName.RELEASED,
)


@dataclass(frozen=True)
class DocumentRecord:
    """Source document metadata that identifies one factory work item."""

    source_path: Path
    title: str
    category: str
    page_count: int
    domain: str
    citation_key: str = ""
    part_count: int = 0
    skill_name: str = ""
    priority: str = "normal"
    version_status: str = "current"

    @property
    def document_key(self) -> str:
        """Return a stable key that does not expose local path details in logs."""
        normalized_path = self.source_path.as_posix().lower()  # Normalize the path for repeatable keys on Windows.
        safe_chars = [char if char.isalnum() else "-" for char in normalized_path]  # Keep the key URL-safe.
        return "".join(safe_chars).strip("-")[:160]  # Bound the key so SQLite and GitHub titles stay small.

    @property
    def audit_citation_key(self) -> str:
        """Return the citation key that the GitHub issue must show."""
        if self.citation_key:  # Prefer the source citation when the corpus provides one.
            return self.citation_key  # Keep the citation stable across file moves.
        return self.document_key  # Fall back to the path key so the field is never empty.

    @property
    def audit_skill_name(self) -> str:
        """Return the skill name that owns this document."""
        if self.skill_name:  # Prefer the assigned skill when the scheduler provides one.
            return self.skill_name  # Keep the issue body useful for a domain owner.
        return f"juniper-{self.domain}"  # Fall back to the domain skill naming convention.


@dataclass(frozen=True)
class StageEvent:
    """One journal event for a document stage."""

    document_key: str
    stage: StageName
    next_action: str
    issue_number: int | None
    created_at: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumePoint:
    """The exact point where the factory can continue after a crash."""

    document_key: str
    completed_stage: StageName | None
    next_action: str
    issue_number: int | None
    source: str
    failed: bool = False
