"""Data models shared across the compliance analyzer package."""

from __future__ import annotations  # Enable modern union/builtin generic annotations.

import ast  # AST module is referenced by the parsed-file context type.
import enum  # Enum base class for the severity levels.
from dataclasses import dataclass, field  # Dataclasses keep the record types concise.


class Severity(enum.Enum):
    """Severity levels that drive scoring weight and report ordering."""

    CRITICAL = "critical"  # Most serious issues; heaviest score penalty.
    HIGH = "high"  # Serious architectural or correctness smells.
    MEDIUM = "medium"  # Notable maintainability concerns.
    LOW = "low"  # Minor or informational style signals.


# Penalty points subtracted from a perfect score for each violation by severity.
SEVERITY_WEIGHTS: dict[Severity, int] = {
    Severity.CRITICAL: 10,  # Critical issues weigh the most.
    Severity.HIGH: 6,  # High issues are significant but recoverable.
    Severity.MEDIUM: 3,  # Medium issues are moderate concerns.
    Severity.LOW: 1,  # Low issues only nudge the score.
}

# Maximum penalty any single category may contribute, so one bad area cannot
# zero the whole score on its own and grades stay comparable between files.
CATEGORY_PENALTY_CAP: int = 20

# Ordered (high to low) score thresholds mapped to letter grades.
GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (97.0, "A+"),  # Near-perfect compliance.
    (93.0, "A"),  # Excellent compliance.
    (90.0, "A-"),  # Strong compliance.
    (87.0, "B+"),  # Good with minor gaps.
    (83.0, "B"),  # Good overall.
    (80.0, "B-"),  # Acceptable with some gaps.
    (77.0, "C+"),  # Fair, needs attention.
    (73.0, "C"),  # Mediocre compliance.
    (70.0, "C-"),  # Marginal compliance.
    (67.0, "D+"),  # Poor, many issues.
    (63.0, "D"),  # Very poor compliance.
    (60.0, "D-"),  # Barely passing.
    (0.0, "F"),  # Failing; major refactor required.
)


@dataclass
class Violation:
    """A single guideline violation discovered in a source file."""

    rule_id: str  # Stable identifier such as "ARCH-DELEGATE" for grouping/closure.
    category: str  # Human-readable grouping such as "Architecture".
    severity: Severity  # Severity level used by the scorer.
    line: int  # 1-based line number where the issue begins.
    symbol: str  # Enclosing function/class/symbol name for context.
    message: str  # Description of what is wrong and why.
    remediation: str  # Concrete fix used to seed SpecKit remediation tasks.


@dataclass
class AnalysisContext:
    """A fully parsed, reusable view of one source file passed to analyzers."""

    path: str  # File path being analyzed.
    source: str  # Raw file text.
    lines: list[str]  # Source split into physical lines (line N is index N-1).
    tree: ast.Module  # Parsed AST root for structural inspection.
    code_lines: set[int]  # Line numbers that contain executable code.
    inline_comment_lines: set[int]  # Code lines that also carry an inline comment.


@dataclass
class FileReport:
    """Aggregated analysis result for a single file."""

    path: str  # File that was analyzed.
    violations: list[Violation] = field(default_factory=list)  # All findings.
    metrics: dict[str, float] = field(default_factory=dict)  # Numeric metrics only.
    hotspots: list[tuple[str, int]] = field(default_factory=list)  # (symbol, complexity) worst-first.
    score: float = 100.0  # Computed compliance score in the 0-100 range.
    grade: str = "A+"  # Letter grade derived from the score.
    parse_error: str | None = None  # Set when the file failed to parse.
