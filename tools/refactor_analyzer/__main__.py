"""Command-line interface for the refactor analyzer.

Run as a module from the repository root::

    python -m tools.refactor_analyzer MistHelper.py -o refactor_candidates.md
"""

from __future__ import annotations  # Enable modern annotation syntax.

import argparse  # Parse command-line arguments.
import logging  # Configure action logging for the CLI run.
from pathlib import Path  # Portable filesystem path handling.

from tools.refactor_analyzer.analysis import RefactorAnalyzer  # Core analysis orchestrator.
from tools.refactor_analyzer.models import (  # Category constants for summary line.
    CATEGORY_HOT,
    CATEGORY_LOW_USE,
    CATEGORY_SINGLE_USE,
    CATEGORY_SKIPPED,
    CATEGORY_UNUSED,
    AnalysisResult,
)
from tools.refactor_analyzer.reporting import MarkdownReportGenerator  # Markdown renderer.

logger = logging.getLogger(__name__)  # Module-scoped logger for action logging.


class RefactorCLI:
    """Parse arguments, run the analyzer, write the report, and print a summary."""

    def run(self, argv: list[str] | None = None) -> int:
        """Execute the full CLI workflow and return a process exit code (always 0)."""
        args = self._parse_args(argv)  # Parse the command-line arguments.
        self._configure_logging(args.quiet)  # Configure logging verbosity.
        logger.info("Starting refactor analysis of %s", args.entrypoint)  # Log start of run.
        analyzer = RefactorAnalyzer(  # Build the analysis engine with parsed options.
            src_root=Path(args.src_root),  # First-party src/ anchor for import resolution.
            extra_packages=tuple(args.extra_package),  # Additional first-party top-level names.
            min_lines=args.min_lines,  # Skip trivially small defs shorter than this.
            include_private=args.include_private,  # Whether to include _underscore symbols.
            skip_names=tuple(args.skip),  # User-supplied bootstrap-pin names merged with SKIP_ALWAYS.
        )
        result = analyzer.analyze(Path(args.entrypoint))  # Run the full analysis pipeline.
        report_text = MarkdownReportGenerator().generate(result)  # Render the Markdown report.
        self._write_report(args.output, report_text)  # Persist the report to disk.
        self._print_summary(result, args.output)  # Print a concise console summary.
        return 0  # Advisory tool: always exit success regardless of findings.

    @staticmethod
    def _parse_args(argv: list[str] | None) -> argparse.Namespace:
        """Build the argument parser and parse the provided arguments."""
        parser = argparse.ArgumentParser(  # Standalone parser instance for this CLI.
            prog="refactor-analyzer",  # Program name shown in help text.
            description="Rank top-level symbols in an entrypoint file for refactor extraction.",
        )
        parser.add_argument(  # Positional entrypoint file (default MistHelper.py).
            "entrypoint",
            nargs="?",
            default="MistHelper.py",
            help="Python entrypoint file to analyze (default: MistHelper.py).",
        )
        parser.add_argument(  # Output report path.
            "-o",
            "--output",
            default="refactor_candidates.md",
            help="Output path for the Markdown report (default: refactor_candidates.md).",
        )
        parser.add_argument(  # First-party src/ root directory.
            "--src-root",
            default="src",
            help="First-party source root used for import resolution (default: src).",
        )
        parser.add_argument(  # Repeatable extra first-party top-level packages.
            "--extra-package",
            action="append",
            default=[],
            metavar="NAME",
            help="Additional first-party top-level package name (may be repeated).",
        )
        parser.add_argument(  # Minimum def size for inclusion.
            "--min-lines",
            type=int,
            default=3,
            metavar="N",
            help="Skip definitions shorter than N lines (default: 3).",
        )
        parser.add_argument(  # Whether to include _leading_underscore names.
            "--include-private",
            action="store_true",
            help="Include private symbols (names starting with underscore).",
        )
        parser.add_argument(  # Repeatable bootstrap-pin names merged with SKIP_ALWAYS.
            "--skip",
            action="append",
            default=[],
            metavar="NAME",
            help="Symbol name to pin in the entrypoint (bootstrap/module-load order). May be repeated.",
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
        logger.info("Writing refactor report to %s", path)  # Log before writing.
        path.write_text(text, encoding="utf-8")  # Persist the report as UTF-8.
        logger.debug("Wrote %d characters to %s", len(text), path)  # Log bytes written.

    @staticmethod
    def _print_summary(result: AnalysisResult, output: str) -> None:
        """Print a concise console summary of the analysis outcome."""
        counts = {CATEGORY_UNUSED: 0, CATEGORY_SINGLE_USE: 0, CATEGORY_LOW_USE: 0, CATEGORY_HOT: 0, CATEGORY_SKIPPED: 0}
        for candidate in result.candidates:  # Tally candidates per category.
            counts[candidate.category] = counts.get(candidate.category, 0) + 1  # Bump bucket.
        print(f"Refactor report written to {output}")  # Tell the user where the report is.
        print(f"Entrypoint: {result.entrypoint}")  # Echo the analyzed file.
        print(f"Module graph: {result.module_graph_size} first-party files")  # Reach of analysis.
        print(f"Definitions analyzed: {len(result.definitions)}")  # Total defs considered.
        print(  # Category breakdown line.
            f"  unused={counts[CATEGORY_UNUSED]}  "
            f"single-use={counts[CATEGORY_SINGLE_USE]}  "
            f"low-use={counts[CATEGORY_LOW_USE]}  "
            f"hot={counts[CATEGORY_HOT]}  "
            f"skipped={counts[CATEGORY_SKIPPED]}"
        )
        print(f"LOC saveable (unused + single-use): {result.loc_saveable}")  # Headline extractable LOC.


if __name__ == "__main__":  # Allow direct module execution.
    raise SystemExit(RefactorCLI().run())  # Run the CLI and propagate its exit code.
