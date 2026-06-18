"""Command-line interface for the compliance analyzer.

Run as a module from the repository root::

    python -m tools.compliance_analyzer <file-or-dir> [...] -o report.md
"""

from __future__ import annotations  # Enable modern annotation syntax.

import argparse  # Parse command-line arguments.
import logging  # Configure action logging for the CLI run.
from pathlib import Path  # Portable filesystem path handling.

from .engine import ComplianceAnalyzer  # Core analysis engine.
from .reporting import MarkdownReportGenerator  # Markdown report renderer.
from .scoring import ComplianceScorer  # Grade the overall score for gating.

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


class ComplianceCLI:
    """Parse arguments, run the analyzer, write the report, and gate the exit code."""

    def __init__(self) -> None:
        """Create the CLI with a reusable scorer for gating decisions."""
        self._scorer = ComplianceScorer()  # Used to grade the overall score.

    def run(self, argv: list[str] | None = None) -> int:
        """Execute the full CLI workflow and return a process exit code."""
        args = self._parse_args(argv)  # Parse the command-line arguments.
        self._configure_logging(args.quiet)  # Configure logging verbosity.
        logger.info("Starting compliance analysis of %d target(s)", len(args.targets))  # Log start.
        analyzer = ComplianceAnalyzer()  # Build the analysis engine.
        reports = analyzer.analyze_targets(args.targets, args.recursive, args.exclude)  # Analyze targets.
        if not reports:  # Nothing was analyzed (bad targets).
            logger.error("No Python files were analyzed; check the supplied targets")  # Log the problem.
            return 2  # Usage-error exit code.
        report_text = MarkdownReportGenerator().generate(reports)  # Render the Markdown report.
        self._write_report(args.output, report_text)  # Persist the report to disk.
        self._print_summary(reports, args.output)  # Print a concise console summary.
        return self._exit_code(reports, args)  # Gate the exit code on thresholds.

    @staticmethod
    def _parse_args(argv: list[str] | None) -> argparse.Namespace:
        """Build the argument parser and parse the provided arguments."""
        parser = argparse.ArgumentParser(
            prog="compliance-analyzer",  # Program name shown in help text.
            description="Grade Python files against the project coding guidelines.",  # Help summary.
        )
        parser.add_argument("targets", nargs="+", help="Python files or directories to analyze.")  # Inputs.
        parser.add_argument("-o", "--output", default="compliance_report.md", help="Report output path.")
        parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into subdirectories.")
        parser.add_argument(
            "--exclude",
            action="append",
            default=[],
            metavar="TEXT",
            help="Skip any path containing TEXT (may be repeated).",  # Exclusion filter.
        )
        parser.add_argument(
            "--fail-under",
            type=float,
            default=None,
            metavar="SCORE",
            help="Exit non-zero when the overall score is below SCORE.",  # Score gate.
        )
        parser.add_argument(
            "--min-grade",
            default=None,
            metavar="GRADE",
            help="Exit non-zero when the overall grade is below GRADE (e.g. C).",  # Grade gate.
        )
        parser.add_argument("-q", "--quiet", action="store_true", help="Log only warnings and errors.")
        return parser.parse_args(argv)  # Parse and return the namespace.

    @staticmethod
    def _configure_logging(quiet: bool) -> None:
        """Configure root logging based on the quiet flag."""
        level = logging.WARNING if quiet else logging.INFO  # Quiet mode hides info logs.
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")  # ASCII format.

    @staticmethod
    def _write_report(output: str, text: str) -> None:
        """Write the rendered report text to the output path, creating parents."""
        path = Path(output)  # Normalize the output path.
        if path.parent and not path.parent.exists():  # Ensure the parent directory exists.
            path.parent.mkdir(parents=True, exist_ok=True)  # Create missing parent directories.
        logger.info("Writing compliance report to %s", path)  # Log before writing.
        path.write_text(text, encoding="utf-8")  # Persist the report as UTF-8.
        logger.debug("Wrote %d characters to %s", len(text), path)  # Log the bytes written.

    def _print_summary(self, reports: list, output: str) -> None:
        """Print a concise per-file and overall summary to the console."""
        overall = MarkdownReportGenerator().overall_score(reports)  # Aggregate score.
        grade = self._scorer.grade(overall)  # Aggregate grade.
        print(f"Compliance report written to {output}")  # Tell the user where the report is.
        print(f"Overall score: {overall:.1f}/100  Grade: {grade}")  # Show the headline result.
        for report in reports:  # List each file's grade and score.
            print(f"  {report.grade:>2}  {report.score:5.1f}  {report.path}")  # Aligned per-file line.

    def _exit_code(self, reports: list, args: argparse.Namespace) -> int:
        """Return 0, or 1 when a configured score/grade gate is not met."""
        overall = MarkdownReportGenerator().overall_score(reports)  # Aggregate score.
        grade = self._scorer.grade(overall)  # Aggregate grade.
        if args.fail_under is not None and overall < args.fail_under:  # Score gate check.
            logger.warning("Overall score %.1f is below --fail-under %.1f", overall, args.fail_under)  # Warn.
            return 1  # Gate failure exit code.
        return self._grade_gate(grade, args.min_grade)  # Apply the grade gate.

    def _grade_gate(self, grade: str, minimum: str | None) -> int:
        """Return 0, 1 (below grade), or 2 (invalid grade) for the grade gate."""
        if minimum is None:  # No grade gate configured.
            return 0  # Success exit code.
        try:
            passes = self._scorer.meets_minimum(grade, minimum)  # Compare against the minimum grade.
        except ValueError:  # The supplied minimum grade was not a valid letter.
            logger.error("Invalid --min-grade value: %s", minimum)  # Log the bad input.
            return 2  # Usage-error exit code.
        if not passes:  # The overall grade is below the minimum.
            logger.warning("Overall grade %s is below --min-grade %s", grade, minimum)  # Warn the user.
            return 1  # Gate failure exit code.
        return 0  # Success exit code.


if __name__ == "__main__":  # Allow direct module execution.
    raise SystemExit(ComplianceCLI().run())  # Run the CLI and propagate its exit code.
