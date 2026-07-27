"""Report formatting.

Turns the scores into a human-readable text report or a machine-readable JSON
report. The JSON shape follows ``specs/1026-ste-linter/contracts/cli.md``.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Builds the JSON report.

from . import __version__  # The linter version for the JSON envelope.
from .models import Score  # The score type the reporters render.


class TextReporter:
    """Renders the scores as human-readable text."""

    def render(self, scores: list[Score], min_score: int | None, quiet: bool = False) -> str:
        """Return the text report for the scores."""
        blocks = [self._render_one(score, min_score, quiet) for score in scores]  # One block per file.
        return "\n".join(blocks)  # Join the file blocks with a blank line between.

    def _render_one(self, score: Score, min_score: int | None, quiet: bool) -> str:
        """Return the text block for one file."""
        lines = [score.path]  # The first line names the file.
        gate = self._gate_label(score.score, min_score)  # The pass or fail label, when a gate is set.
        dictionary = "used" if score.dictionary_used else "skipped"  # The dictionary state.
        lines.append(
            f"  Score: {score.score}/100  (words graded: {score.word_count}, " f"dictionary: {dictionary}){gate}"
        )  # The score line.
        if quiet:  # The quiet mode shows only the score line.
            return "\n".join(lines)  # Return the short block.
        lines.append("  Sections: " + self._sections(score))  # The per-section line.
        lines.append(f"  Violations ({len(score.violations)}):")  # The violation header.
        lines.extend(self._violation_lines(score))  # The violation lines.
        return "\n".join(lines)  # Return the full block.

    def _gate_label(self, value: int, min_score: int | None) -> str:
        """Return a pass or fail label when a threshold is set, else an empty string."""
        if min_score is None:  # No threshold is set.
            return ""  # Show no label.
        return "  PASS" if value >= min_score else "  FAIL"  # Compare to the threshold.

    def _sections(self, score: Score) -> str:
        """Return the per-section scores as one line."""
        if not score.sections:  # No section ran.
            return "none"  # Show that no section ran.
        return " | ".join(f"{section.section} {section.score}" for section in score.sections)  # Join them.

    def _violation_lines(self, score: Score) -> list[str]:
        """Return one indented line for each violation."""
        lines: list[str] = []  # Holds the violation lines.
        for violation in score.violations:  # Walk each violation in order.
            lines.append(
                f"    L{violation.line}  {violation.rule_id}  "
                f"{violation.severity.label}  {violation.message} {violation.suggestion}"
            )  # Build the violation line.
        if not lines:  # There were no violations.
            lines.append("    None. The text follows the STE rules.")  # Show a clean result.
        return lines  # Return the violation lines.


class JsonReporter:
    """Renders the scores as machine-readable JSON."""

    def render(self, scores: list[Score], min_score: int | None, quiet: bool = False) -> str:
        """Return the JSON report for the scores."""
        results = [self._result(score) for score in scores]  # One result per file.
        passed = all(min_score is None or score.score >= min_score for score in scores)  # Gate result.
        envelope = {
            "version": __version__,  # The linter version.
            "results": results,  # The per-file results.
            "summary": {
                "files": len(scores),  # The number of files graded.
                "min_score": min_score,  # The threshold, or null.
                "passed": passed,  # Whether every file met the threshold.
            },
        }  # The full JSON envelope.
        return json.dumps(envelope, indent=2)  # Return the formatted JSON.

    def _result(self, score: Score) -> dict[str, object]:
        """Return the JSON object for one file."""
        return {
            "path": score.path,  # The file path.
            "score": score.score,  # The overall score.
            "word_count": score.word_count,  # The graded word count.
            "dictionary_used": score.dictionary_used,  # Whether the dictionary checks ran.
            "sections": [
                {
                    "section": section.section,  # The section name.
                    "score": section.score,  # The section score.
                    "penalty": round(section.penalty, 4),  # The section penalty.
                    "violation_count": section.violation_count,  # The number of violations.
                }
                for section in score.sections  # One entry per section.
            ],
            "violations": [
                {
                    "rule_id": violation.rule_id,  # The rule identifier.
                    "section": violation.section,  # The writing-guide section.
                    "severity": violation.severity.label,  # The severity label.
                    "path": violation.path,  # The file path.
                    "line": violation.line,  # The source line.
                    "column": violation.column,  # The source column.
                    "message": violation.message,  # The problem description.
                    "suggestion": violation.suggestion,  # The suggested fix.
                }
                for violation in score.violations  # One entry per violation.
            ],
        }  # Return the file result.
