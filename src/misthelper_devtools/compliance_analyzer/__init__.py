"""Compliance analyzer package.

Grade Python files against the project coding guidelines (the 5-Item Rule,
no wrappers/delegators/aliases/shims, complexity limits, inline comments,
safe input handling, portable paths) and export an AI-agent-ready Markdown
remediation report.
"""

from __future__ import annotations  # Enable modern annotation syntax.

from .engine import ComplianceAnalyzer  # Public analysis engine.
from .models import FileReport, Severity, Violation  # Public record/enum types.
from .reporting import MarkdownReportGenerator  # Public report renderer.
from .scoring import ComplianceScorer  # Public scorer/grader.

__all__ = [
    "ComplianceAnalyzer",  # Engine entry point.
    "ComplianceScorer",  # Score and grade calculator.
    "FileReport",  # Per-file result record.
    "MarkdownReportGenerator",  # Markdown report renderer.
    "Severity",  # Severity enumeration.
    "Violation",  # Single-violation record.
]

__version__ = "1.0.0"  # Package version.
