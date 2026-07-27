"""Data models for the STE linter.

Defines the small, immutable value types the linter passes between its stages:
parsing, analysis, scoring, and reporting. See
``specs/1026-ste-linter/data-model.md`` for the field-level contract.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import enum  # Provides the Severity enumeration.
from dataclasses import dataclass, field  # Declares the value types with less boilerplate.


class Severity(enum.Enum):
    """The weight class of a violation.

    The value is the default penalty weight the scoring model uses. A project can
    override the weight per rule in the configuration.
    """

    ERROR = 3  # A clear rule break. Carries the most weight.
    WARNING = 2  # A likely rule break. Carries a medium weight.
    INFO = 1  # A heuristic guess the writer should confirm. Carries the least weight.

    @property
    def label(self) -> str:
        """Return the lower-case name used in reports."""
        return self.name.lower()  # Report prose uses "error", "warning", or "info".


@dataclass(frozen=True)
class ProseSpan:
    """One run of gradable prose taken from a file."""

    text: str  # The prose text with code, links, and markup removed.
    start_line: int  # The 1-based source line where the span starts.
    kind: str  # The source kind: "markdown", "docstring", or "comment".


@dataclass(frozen=True)
class Sentence:
    """One sentence inside a prose span."""

    text: str  # The sentence text.
    line: int  # The 1-based source line where the sentence starts.
    word_count: int  # The STE word count for the sentence.
    mode: str  # The writing mode: "procedural" or "descriptive".


@dataclass
class Document:
    """The parsed file, ready to grade."""

    path: str  # The file path shown in the report.
    spans: list[ProseSpan] = field(default_factory=list)  # The gradable prose spans.
    sentences: list[Sentence] = field(default_factory=list)  # Every sentence in reading order.
    paragraphs: list[list[Sentence]] = field(default_factory=list)  # Sentences grouped by paragraph.
    word_count: int = 0  # The total STE word count across all spans.
    parse_note: str = ""  # A note set when parsing had to fall back, else empty.


@dataclass(frozen=True)
class Violation:
    """One rule failure at one place in a file."""

    rule_id: str  # The rule that failed, for example "STE-S1-LEN".
    section: str  # The writing-guide section, for example "1-words".
    severity: Severity  # The severity of this finding.
    path: str  # The file path.
    line: int  # The 1-based source line.
    message: str  # What is wrong, in plain STE prose.
    suggestion: str  # How to fix it.
    column: int = 0  # The 1-based column, or 0 when the column is not known.


@dataclass(frozen=True)
class SectionScore:
    """The score for one writing-guide section."""

    section: str  # The section name, for example "3-verbs".
    penalty: float  # The section penalty between 0.0 and 1.0.
    score: int  # The section score between 0 and 100.
    violation_count: int  # The number of violations in the section.


@dataclass(frozen=True)
class Score:
    """The final grade for one file."""

    path: str  # The file path.
    score: int  # The overall score between 0 and 100.
    sections: list[SectionScore]  # The per-section breakdown.
    violations: list[Violation]  # Every violation, sorted by line.
    dictionary_used: bool  # True when the dictionary checks ran.
    word_count: int  # The graded word count.
