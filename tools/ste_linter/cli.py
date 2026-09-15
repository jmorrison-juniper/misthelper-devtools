"""Command-line interface for the STE linter.

Parses the arguments, grades each file, prints the report, and returns the exit
code. See ``specs/1026-ste-linter/contracts/cli.md`` for the full contract.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import argparse  # Parses the command-line arguments.
import logging  # Configures the log output.
import os  # Tests paths and reads file extensions.
from typing import TYPE_CHECKING  # Types the rule and backend parameters.

from tools.analyzer_coverage import AnalyzerCoverageSummary, AnalyzerCoverageTracker  # Shared coverage records.

from . import __version__  # The version shown by the --version flag.
from .analysis import GrammarAnalyzer, get_backend  # The backend factory and grammar helper.
from .config import LinterConfig  # The configuration loader.
from .dictionary import Dictionary  # The optional dictionary loader.
from .models import Score  # The score type collected per file.
from .parsing import DocumentBuilder  # The document builder.
from .report import JsonReporter, TextReporter  # The report writers.
from .rules import RuleContext, load_rules  # The rule context and the registry.
from .scoring import ScoringModel  # The scoring model.

if TYPE_CHECKING:  # Import these types for annotations only.
    from .analysis import Backend  # The analysis backend type.
    from .rules import Rule  # The rule base type.

# The file types the linter grades.
_SUPPORTED = frozenset({".md", ".py"})


class LinterCLI:
    """Runs the linter from the command line."""

    def run(self, argv: list[str] | None = None) -> int:
        """Parse the arguments, grade the files, and return the exit code."""
        args = self._parse_args(argv)  # Read the command-line arguments.
        logging.basicConfig(level=logging.WARNING, format="%(message)s")  # Keep the output clean.
        config = self._build_config(args)  # Load and adjust the configuration.
        scores, usage_error, coverage = self._grade_paths(args.path, config)  # Grade every path.
        self._print_report(scores, args, config, coverage)  # Print the report in the chosen format.
        return self._exit_code(scores, config, usage_error)  # Return the exit code.

    def _parse_args(self, argv: list[str] | None) -> argparse.Namespace:
        """Return the parsed command-line arguments."""
        parser = argparse.ArgumentParser(prog="ste-linter", description="Grade a file against STE rules.")
        parser.add_argument("path", nargs="+", help="One or more .md or .py files to grade.")  # The inputs.
        parser.add_argument("--format", choices=["text", "json"], default="text", help="Report format.")
        parser.add_argument("--min-score", type=int, default=None, help="The pass threshold, 0 to 100.")
        parser.add_argument("--dictionary", default=None, help="The dictionary file path.")  # The override.
        parser.add_argument("--config", default="pyproject.toml", help="The configuration file path.")
        parser.add_argument("--select", action="append", default=[], help="Only run these rule ids.")
        parser.add_argument("--ignore", action="append", default=[], help="Do not run these rule ids.")
        parser.add_argument(
            "--grade-logging-strings",
            action="store_true",
            help="Grade configured logging message strings.",
        )
        parser.add_argument(
            "--grade-user-facing-strings",
            action="store_true",
            help="Grade configured print and prompt strings.",
        )
        parser.add_argument("--quiet", action="store_true", help="Print only the score line.")  # Short output.
        parser.add_argument("--version", action="version", version=f"ste-linter {__version__}")  # The version.
        return parser.parse_args(argv)  # Parse and return the arguments.

    def _build_config(self, args: argparse.Namespace) -> LinterConfig:
        """Return the configuration with the command-line overrides applied."""
        config = LinterConfig.load(args.config)  # Load the file settings or the defaults.
        if args.min_score is not None:  # The user set a threshold.
            config.min_score = args.min_score  # Use the command-line threshold.
        if args.dictionary is not None:  # The user set a dictionary path.
            config.dictionary_path = args.dictionary  # Use the command-line path.
        if args.grade_logging_strings:  # The user opted in to logging messages for this run.
            config.grade_logging_strings = True  # Enable logging strings without editing TOML.
        if args.grade_user_facing_strings:  # The user opted in to prompts and printed text for this run.
            config.grade_user_facing_strings = True  # Enable user-facing strings without editing TOML.
        config.selected.update(self._split(args.select))  # Add any selected rule ids.
        config.ignored.update(self._split(args.ignore))  # Add any ignored rule ids.
        return config  # Return the merged configuration.

    def _split(self, values: list[str]) -> list[str]:
        """Return the rule ids from repeated or comma-joined option values."""
        result: list[str] = []  # Holds the split rule ids.
        for value in values:  # Walk each option value.
            result.extend(part.strip() for part in value.split(",") if part.strip())  # Split on commas.
        return result  # Return the rule ids.

    def _grade_paths(self, paths: list[str], config: LinterConfig) -> tuple[list[Score], bool, AnalyzerCoverageSummary]:
        """Return the scores for every path and whether a usage error happened."""
        coverage = AnalyzerCoverageTracker("ste_linter")  # Track files that the linter reads or skips.
        backend = get_backend(config.prefer_spacy)  # Pick the analysis backend.
        grammar = GrammarAnalyzer()  # The shared grammar helper.
        dictionary = Dictionary.load(config.dictionary_path)  # Load the dictionary, or None.
        if dictionary is None:  # The dictionary can be absent in a minimal checkout.
            coverage.record_skip(config.dictionary_path, "dictionary_unavailable")  # Report reduced measurement.
        rules = load_rules(config)  # Build the active rule list.
        builder = DocumentBuilder(config)  # The document builder needs the string grading switches.
        scorer = ScoringModel()  # The scoring model.
        scores: list[Score] = []  # Holds the per-file scores.
        usage_error = False  # True when a path is missing or unreadable.
        for path in paths:  # Walk each input path.
            score = self._grade_one(path, builder, rules, scorer, backend, grammar, config, dictionary, coverage)
            if score is None:  # The path could not be graded.
                usage_error = True  # Record the usage error.
            else:  # The path graded cleanly.
                scores.append(score)  # Keep the score.
        return scores, usage_error, coverage.summary()  # Return scores, error flag, and coverage.

    def _grade_one(
        self,
        path: str,
        builder: DocumentBuilder,
        rules: list[Rule],
        scorer: ScoringModel,
        backend: Backend,
        grammar: GrammarAnalyzer,
        config: LinterConfig,
        dictionary: Dictionary | None,
        coverage: AnalyzerCoverageTracker,
    ) -> Score | None:
        """Return the score for one file, or None when it cannot be graded."""
        if not os.path.isfile(path):  # The path does not point to a file.
            print(f"{path}\n  Error: file not found.")  # Report the missing file.
            coverage.record_skip(path, "missing_file", explicit=True)  # Explicit missing paths fail the run.
            return None  # Signal a usage error.
        if os.path.splitext(path)[1].lower() not in _SUPPORTED:  # The file type is not graded.
            print(f"{path}\n  Skipped: only .md and .py files are graded.")  # Report the skip.
            coverage.record_skip(path, "unsupported_file_type", explicit=True)  # Explicit unsupported files fail.
            return None  # Signal that no score was produced.
        text = self._read(path)  # Read the file text.
        coverage.record_read(path)  # Record the file as measured after a successful read.
        document = builder.build(path, text)  # Parse the file into a document.
        context = RuleContext(backend=backend, grammar=grammar, config=config, dictionary=dictionary)
        violations = [item for rule in rules for item in rule.check(document, context)]  # Run every rule.
        return scorer.score(document, violations, rules, dictionary is not None, config)  # Score the file.

    def _read(self, path: str) -> str:
        """Return the file text, replacing bytes that do not decode."""
        with open(path, encoding="utf-8", errors="replace") as handle:  # Open the file for reading.
            return handle.read()  # Return the whole text.

    def _print_report(
        self,
        scores: list[Score],
        args: argparse.Namespace,
        config: LinterConfig,
        coverage: AnalyzerCoverageSummary,
    ) -> None:
        """Print the report in the chosen format."""
        if args.format == "json":  # The user asked for JSON.
            print(JsonReporter().render(scores, config.min_score, args.quiet, coverage))  # Print JSON.
        else:  # The default is the text report.
            print(TextReporter().render(scores, config.min_score, args.quiet, coverage))  # Print text.

    def _exit_code(self, scores: list[Score], config: LinterConfig, usage_error: bool) -> int:
        """Return the process exit code from the scores and the threshold."""
        if usage_error:  # A path was missing or not gradable.
            return 2  # Return the usage-error code.
        if config.min_score is not None:  # A threshold is set.
            if any(score.score < config.min_score for score in scores):  # A file scored too low.
                return 1  # Return the gate-failure code.
        return 0  # Every file passed.


def main(argv: list[str] | None = None) -> int:
    """Run the linter command-line interface."""
    return LinterCLI().run(argv)  # Build the CLI and run it.
