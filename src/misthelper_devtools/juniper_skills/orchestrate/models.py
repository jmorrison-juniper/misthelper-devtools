"""Shared models for the Juniper skill factory orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkItem:
    """One leased source document and its ordered Markdown parts."""

    document_key: str  # Identify the logical source document across restarts.
    title: str  # Store the title for package indexes and progress reports.
    category: str  # Store the catalog category for the source record.
    source_pdf: str  # Store the PDF path for the citation source table.
    pages: int  # Store the source page count for package metadata.
    priority: int  # Preserve inventory priority for fair queue order.
    domain: str  # Route the document into one generated domain skill.
    part_paths: tuple[Path, ...]  # Store ordered Markdown part paths for the join stage.
    citation_only: bool = False  # Mark superseded documents that must not generate topics.


@dataclass(frozen=True)
class StageOutcome:
    """One durable stage outcome for crash recovery."""

    document_key: str  # Link the event to the leased work item.
    stage: str  # Store the fine-grained pipeline stage name.
    status: str  # Store started, completed, failed, or skipped.
    detail: str  # Store a short operator-readable result.
    worker_id: str  # Identify the worker that wrote the checkpoint.
    data: dict[str, object] = field(default_factory=dict)  # Store measured counts for resume and reports.


@dataclass(frozen=True)
class BackendProbe:
    """State the result of one headless rewrite backend probe."""

    name: str  # Identify the tool or fallback path that was checked.
    available: bool  # Record whether the probe found a usable backend.
    evidence: str  # Store the exact useful finding for the final report.
    command: tuple[str, ...] = ()  # Store the command that the subprocess backend can run.
