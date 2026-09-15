"""Shared coverage records for repository analyzers."""

from __future__ import annotations  # Keep annotations light for command-line tools.

import logging  # Record analyzer coverage actions for operators.
from dataclasses import dataclass, field  # Define small immutable and mutable records.
from pathlib import Path, PurePosixPath  # Normalize paths without hardcoded separators.

logger = logging.getLogger(__name__)  # Use a module logger for shared reporting.


@dataclass(frozen=True, slots=True)
class AnalyzerCoverageRecord:
    """One file or target that an analyzer read or skipped."""

    path: str  # Store paths in POSIX form so Windows and Linux reports match.
    reason: str = ""  # Give the skip reason, or leave empty for a read record.
    explicit: bool = False  # True means the caller named this target directly.


@dataclass(frozen=True, slots=True)
class AnalyzerCoverageSummary:
    """The immutable coverage summary for one analyzer run."""

    analyzer: str  # Name the analyzer that produced the summary.
    read_files: tuple[AnalyzerCoverageRecord, ...] = ()  # Files the analyzer read.
    skipped_files: tuple[AnalyzerCoverageRecord, ...] = ()  # Files or targets the analyzer skipped.
    complete: bool = False  # True when the analyzer measured its full supported scope.

    @property
    def scope_label(self) -> str:
        """Return a short label for the measured scope."""
        return "complete" if self.complete else "partial"  # Keep the report wording stable.

    @property
    def has_unintended_skip(self) -> bool:
        """Return True when an explicit target did not receive analysis."""
        return any(record.explicit for record in self.skipped_files)  # Explicit skips must fail the run.


@dataclass(slots=True)
class AnalyzerCoverageTracker:
    """Collect read and skipped paths while an analyzer runs."""

    analyzer: str  # Analyzer name used in reports.
    complete: bool = False  # The caller sets this when full scope is known.
    _read_files: dict[str, AnalyzerCoverageRecord] = field(default_factory=dict)  # De-duplicate read paths.
    _skipped_files: dict[str, AnalyzerCoverageRecord] = field(default_factory=dict)  # De-duplicate skips.

    def record_read(self, path: str | Path) -> None:
        """Record one file that the analyzer read."""
        normalized = self._normalize(path)  # Normalize once before storage and logging.
        logger.info("Recording analyzer read path %s", normalized)  # Log before mutating coverage.
        self._read_files[normalized] = AnalyzerCoverageRecord(normalized)  # Store one record per path.
        logger.debug("Analyzer read path count is %d", len(self._read_files))  # Log the new count.

    def record_skip(self, path: str | Path, reason: str, explicit: bool = False) -> None:
        """Record one file or target that the analyzer skipped."""
        normalized = self._normalize(path)  # Normalize once before storage and logging.
        logger.info("Recording analyzer skipped path %s with reason %s", normalized, reason)  # Log before write.
        self._skipped_files[normalized] = AnalyzerCoverageRecord(normalized, reason, explicit)  # Store the skip.
        logger.debug("Analyzer skipped path count is %d", len(self._skipped_files))  # Log the new count.

    def merge(self, summary: AnalyzerCoverageSummary) -> None:
        """Merge another coverage summary into this tracker."""
        logger.info("Merging analyzer coverage from %s", summary.analyzer)  # Log before the merge.
        for record in summary.read_files:  # Add each read record from the child summary.
            self._read_files[record.path] = record  # Preserve the child path and explicit flag.
        for record in summary.skipped_files:  # Add each skip record from the child summary.
            self._skipped_files[record.path] = record  # Preserve the child reason and explicit flag.
        logger.debug("Merged coverage has %d read and %d skipped", len(self._read_files), len(self._skipped_files))

    def summary(self) -> AnalyzerCoverageSummary:
        """Return a deterministic immutable coverage summary."""
        logger.info("Building analyzer coverage summary for %s", self.analyzer)  # Log before sorting records.
        read_files = tuple(self._sorted(self._read_files.values()))  # Sort reads for stable output.
        skipped_files = tuple(self._sorted(self._skipped_files.values()))  # Sort skips for stable output.
        logger.debug("Coverage summary has %d read and %d skipped", len(read_files), len(skipped_files))
        return AnalyzerCoverageSummary(self.analyzer, read_files, skipped_files, self.complete)  # Freeze the view.

    @staticmethod
    def _sorted(records: object) -> list[AnalyzerCoverageRecord]:
        """Return coverage records sorted by path and reason."""
        return sorted(records, key=lambda record: (record.path, record.reason))  # Stable report order.

    @staticmethod
    def _normalize(path: str | Path) -> str:
        """Return a portable POSIX string for a path-like value."""
        return PurePosixPath(Path(path).as_posix()).as_posix()  # Avoid OS-specific separators in reports.


class AnalyzerCoverageRenderer:
    """Render analyzer coverage summaries for text, Markdown, and JSON reports."""

    def to_text(self, summary: AnalyzerCoverageSummary) -> str:
        """Return a concise text block for the coverage summary."""
        logger.info("Rendering text coverage for %s", summary.analyzer)  # Log before rendering.
        lines = self._base_lines(summary)  # Start with the shared heading and counts.
        lines.extend(f"  Read: {record.path}" for record in summary.read_files)  # List measured files.
        lines.extend(self._skip_lines(summary, "  Skipped: "))  # List skipped paths with reasons.
        logger.debug("Rendered %d text coverage line(s)", len(lines))  # Log the rendered size.
        return "\n".join(lines)  # Return without a trailing newline for embedding.

    def to_markdown(self, summary: AnalyzerCoverageSummary) -> list[str]:
        """Return a Markdown section for the coverage summary."""
        logger.info("Rendering markdown coverage for %s", summary.analyzer)  # Log before rendering.
        lines = ["## Analyzer Coverage", ""]  # Add a stable section title.
        lines.extend(f"- {line.strip()}" for line in self._base_lines(summary))  # Convert counts to bullets.
        lines.extend(self._markdown_group("Read files", summary.read_files))  # Add read-file list.
        lines.extend(self._markdown_group("Skipped files", summary.skipped_files))  # Add skipped-file list.
        logger.debug("Rendered %d markdown coverage line(s)", len(lines))  # Log the rendered size.
        return lines  # Return lines for caller composition.

    def to_json(self, summary: AnalyzerCoverageSummary) -> dict[str, object]:
        """Return a JSON-safe coverage payload."""
        logger.info("Rendering JSON coverage for %s", summary.analyzer)  # Log before rendering.
        payload = {
            "analyzer": summary.analyzer,  # Preserve the producer name.
            "scope": summary.scope_label,  # State whether the measurement was partial.
            "read_files": [record.path for record in summary.read_files],  # Emit read paths only.
            "skipped_files": [self._record_payload(record) for record in summary.skipped_files],  # Emit reasons.
            "unintended_skip": summary.has_unintended_skip,  # Let automation fail partial green runs.
        }
        logger.debug("Rendered JSON coverage with %d key(s)", len(payload))  # Log the payload shape.
        return payload  # Return the JSON-safe object.

    @staticmethod
    def _base_lines(summary: AnalyzerCoverageSummary) -> list[str]:
        """Return the shared text lines with scope and counts."""
        return [
            f"Analyzer coverage: {summary.analyzer}",  # Name the analyzer.
            f"Scope: {summary.scope_label}",  # Mark complete or partial scope.
            f"Files read: {len(summary.read_files)}",  # Count measured files.
            f"Files skipped: {len(summary.skipped_files)}",  # Count skipped paths.
        ]

    @staticmethod
    def _record_payload(record: AnalyzerCoverageRecord) -> dict[str, object]:
        """Return one skipped record as a JSON-safe object."""
        return {"path": record.path, "reason": record.reason, "explicit": record.explicit}  # Keep all skip data.

    @staticmethod
    def _skip_lines(summary: AnalyzerCoverageSummary, prefix: str) -> list[str]:
        """Return rendered skip lines for text output."""
        return [f"{prefix}{record.path} ({record.reason})" for record in summary.skipped_files]

    @staticmethod
    def _markdown_group(title: str, records: tuple[AnalyzerCoverageRecord, ...]) -> list[str]:
        """Return a Markdown bullet group for read or skipped records."""
        if not records:  # Empty groups still need a clear statement.
            return ["", f"### {title}", "", "- None"]  # Tell the caller no records exist.
        lines = ["", f"### {title}", ""]  # Start the subgroup.
        lines.extend(f"- {record.path}{f' ({record.reason})' if record.reason else ''}" for record in records)
        return lines  # Return the subgroup lines.
