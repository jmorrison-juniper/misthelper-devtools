"""Shared records for Juniper source quality checks."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

from dataclasses import dataclass, field  # Define immutable records with defaults.
from pathlib import Path  # Store paths with platform-safe objects.


@dataclass(frozen=True)
class SourceQualityScore:
    """The quality score for one source document."""

    document_key: str  # Identify the source document in reports and the database.
    path: Path  # Store the measured Markdown path.
    score: int  # Store a 0 through 100 score for operator sorting.
    status: str  # Store pass, review, or fail for quarantine routing.
    reason: str  # Store the specific cause, never a bare Boolean.
    space_ratio: float  # Store spaces divided by letters for corruption detection.
    text_chars: int  # Store extractable text size for empty-source detection.
    repeated_line_ratio: float  # Store repeated line share for header and footer detection.
    structured_ratio: float  # Store CLI and table line share for false-positive control.
    source_pdf: Path | None = None  # Store a matching source PDF when one is known.

    @property
    def passed(self) -> bool:
        """Return whether this score permits skill output."""
        return self.status == "pass"  # Only an explicit pass can produce a skill.


@dataclass(frozen=True)
class SpaceRatioDistribution:
    """Summary statistics for a source corpus scan."""

    documents: int  # Count measured documents so zero scans fail visibly.
    minimum: float  # Store the smallest observed space ratio.
    p05: float  # Store the fifth percentile for threshold calibration.
    p25: float  # Store the first quartile for threshold calibration.
    median: float  # Store the median for healthy-corpus comparison.
    p75: float  # Store the third quartile for threshold calibration.
    p95: float  # Store the high percentile for report context.
    maximum: float  # Store the largest observed space ratio.
    threshold: float  # Store the chosen corruption threshold.


@dataclass(frozen=True)
class SourceQualityReport:
    """The full source quality gate result."""

    scores: tuple[SourceQualityScore, ...]  # Store one score for each measured document.
    distribution: SpaceRatioDistribution  # Store measured distribution and threshold evidence.
    errors: tuple[str, ...] = field(default_factory=tuple)  # Store guard-level failures.

    @property
    def files_checked(self) -> int:
        """Return how many source documents the gate measured."""
        return len(self.scores)  # Expose the contract count for zero-file failure checks.

    @property
    def passed(self) -> bool:
        """Return whether the gate permits the build to continue."""
        file_status = all(score.passed for score in self.scores)  # Require each document to pass.
        return self.files_checked > 0 and file_status and not self.errors  # Make a zero-document gate fail.

    @property
    def failed(self) -> tuple[SourceQualityScore, ...]:
        """Return the documents that must be quarantined."""
        return tuple(score for score in self.scores if not score.passed)  # Keep all non-pass rows for quarantine.


@dataclass(frozen=True)
class CardDeduplicationReport:
    """The result of card text deduplication."""

    cards: tuple[object, ...]  # Store cards without requiring one source model class.
    input_count: int  # Count cards before deduplication.
    merge_count: int  # Count removed duplicate card records.


@dataclass(frozen=True)
class CommandFenceCleanReport:
    """The result of command fence prose cleanup."""

    text: str  # Store the cleaned Markdown text.
    files_checked: int  # Count files measured for guard-proof output.
    lines_removed: int  # Count rejected command-fence lines.
    removed_examples: tuple[str, ...] = ()  # Store bounded examples for reports.


@dataclass(frozen=True)
class RepairAccuracyReport:
    """The held-out repair measurement for stripped source text."""

    lines_checked: int  # Count held-out lines used in the measurement.
    line_exact_accuracy: float  # Store exact line match share.
    tokens_checked: int  # Count original tokens used in the measurement.
    token_accuracy: float  # Store token match share.
    command_lines_checked: int  # Count held-out command lines.
    command_line_accuracy: float  # Store exact command line match share.
    auto_repair_commands: bool  # State whether command repair is trusted.
    decision: str  # Store the operator-facing decision with evidence.
