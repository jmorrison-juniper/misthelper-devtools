"""Data models for the Juniper documentation rewrite stage."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

from dataclasses import dataclass, field  # Define small immutable factory records.
from enum import StrEnum  # Limit card marks to the locked contract values.
from pathlib import Path  # Keep path handling portable on Windows and Linux.


class CardClassMark(StrEnum):
    """The allowed knowledge card class marks from the locked contract."""

    MUST = "MUST"  # Mark a hard limit or a required practice.
    SHOULD = "SHOULD"  # Mark a recommended practice with a judged exception.
    INFO = "INFO"  # Mark a fact that sets no hard limit.


@dataclass(frozen=True)
class KnowledgeCard:
    """One restated fact that the generated topic can publish."""

    mark: CardClassMark  # Store the contract mark for the fact type.
    fact: str  # Store the restated fact without copied source prose.
    citation_key: str  # Store the exact source page citation.

    def to_markdown(self) -> str:
        """Return the contract list-item form for one card."""
        return f"- **{self.mark.value}** {self.fact} {self.citation_key}"  # Emit the locked card shape.


@dataclass(frozen=True)
class RewriteWorkPacket:
    """Input data that an agent or deterministic backend rewrites."""

    source_segment: str  # Store the source segment that needs a restated output.
    page_range: str  # Store the exact page range for the citation.
    detected_commands: tuple[str, ...]  # Preserve commands that must stay verbatim.
    target_domain: str  # Route the cards to the correct domain skill.
    lifecycle_tags: tuple[str, ...]  # Mark the life cycle stages the cards answer.
    citation_key: str  # Store the citation key used by every extracted card.


@dataclass(frozen=True)
class RewriteResult:
    """Output data from a rewrite backend."""

    cards: tuple[KnowledgeCard, ...]  # Store publishable knowledge cards.
    command_blocks: tuple[str, ...]  # Store command blocks that pass through unchanged.
    limitations: tuple[str, ...]  # State what the backend did not restate.


@dataclass(frozen=True)
class SimilarityCheckInput:
    """One generated file and its source text segments."""

    generated_path: Path  # Identify the generated topic file to check.
    source_segments: tuple[str, ...]  # Store the source segments that fed the topic.


@dataclass(frozen=True)
class SimilarityFileResult:
    """The similarity measurement for one generated file."""

    file_path: Path  # Identify the measured file in the guard report.
    longest_run: int  # Store the longest shared prose run in words.
    longest_phrase: str  # Store the measured phrase for a reviewer.
    status: str  # Store the band status: cleared, warned, or failed.
    passed: bool  # Record whether this file stayed within the threshold.


@dataclass(frozen=True)
class SimilarityGuardReport:
    """The full similarity guard result."""

    files_checked: int  # Report the count because zero checked files must fail.
    threshold: int  # Report the active contract threshold.
    warn_threshold: int  # Report the review band threshold.
    results: tuple[SimilarityFileResult, ...]  # Report every measured file.
    elapsed_seconds: float  # Report timing so large corpus runs can be tracked.
    errors: tuple[str, ...] = field(default_factory=tuple)  # Store guard-level failures.

    @property
    def passed(self) -> bool:
        """Return whether every file and guard condition passed."""
        file_status = all(result.passed for result in self.results)  # Require each file to pass.
        return self.files_checked > 0 and file_status and not self.errors  # Enforce the zero-file failure.

    @property
    def files_cleared(self) -> int:
        """Return the count of files below the warning band."""
        return sum(1 for result in self.results if result.status == "cleared")  # Count files below the warn line.

    @property
    def files_warned(self) -> int:
        """Return the count of files in the review band."""
        return sum(1 for result in self.results if result.status == "warned")  # Count files that need review.

    @property
    def files_failed(self) -> int:
        """Return the count of files above the hard-fail threshold."""
        return sum(1 for result in self.results if result.status == "failed")  # Count files that stop the build.


@dataclass(frozen=True)
class SteFileReport:
    """The STE score for one generated file."""

    path: Path  # Identify the measured file.
    score: int  # Store the existing STE linter score.
    word_count: int  # Store the number of graded words.
    violation_count: int  # Store the count of measurable rule breaks.


@dataclass(frozen=True)
class SteValidationReport:
    """The STE validation result for one or more files."""

    files_checked: int  # Report how many files the validator graded.
    reports: tuple[SteFileReport, ...]  # Store one score per file.
    minimum_score: int  # Store the pass threshold used by the validator.
    errors: tuple[str, ...] = field(default_factory=tuple)  # Store unreadable or unsupported file errors.

    @property
    def passed(self) -> bool:
        """Return whether every file met the configured STE score."""
        score_status = all(report.score >= self.minimum_score for report in self.reports)  # Check every score.
        return self.files_checked > 0 and score_status and not self.errors  # A zero-file validation must fail.
