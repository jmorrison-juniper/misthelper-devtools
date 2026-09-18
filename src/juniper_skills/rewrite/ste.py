"""STE validation seam for generated Juniper skill topics."""

from __future__ import annotations  # Keep annotations from evaluating during imports.

import logging  # Record validation actions for operators.
from pathlib import Path  # Handle validation paths safely.

from tools.ste_linter.analysis import Backend, GrammarAnalyzer, get_backend  # Reuse the existing grammar backend.
from tools.ste_linter.config import LinterConfig  # Reuse the existing linter configuration.
from tools.ste_linter.dictionary import Dictionary  # Reuse the existing controlled dictionary loader.
from tools.ste_linter.models import Score  # Name the linter score for strict type checks.
from tools.ste_linter.parsing import DocumentBuilder  # Reuse the existing Markdown and Python parser.
from tools.ste_linter.rules import Rule, RuleContext, load_rules  # Reuse the existing STE rule registry.
from tools.ste_linter.scoring import ScoringModel  # Reuse the existing scoring model.

from .models import SteFileReport, SteValidationReport  # Use rewrite-stage report models.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

_LOG = logging.getLogger(__name__)  # Give the validator a stable logger name.


class SteValidator:
    """Validate generated prose with the repository STE linter."""

    def __init__(self, minimum_score: int = 80, config_path: Path | None = None) -> None:
        """Store the STE pass threshold and optional config path."""
        self.minimum_score = minimum_score  # Keep the repository default threshold configurable.
        self.config_path = config_path or Path("pyproject.toml")  # Use the project config by default.

    def validate(self, paths: tuple[Path, ...]) -> SteValidationReport:
        """Return one STE score for each file."""
        logger.info("Starting STE validation for generated files")  # Log before validation starts.
        if not paths:  # A validator run with no files proves nothing.
            return SteValidationReport(0, tuple(), self.minimum_score, ("The STE validator checked zero files.",))
        config = self._config()  # Load the linter configuration once for all files.
        reports, errors = self._score_paths(paths, config)  # Score every requested file.
        logger.debug("STE validator checked %d files with %d errors", len(reports), len(errors))  # Log the result.
        return SteValidationReport(len(reports), tuple(reports), self.minimum_score, tuple(errors))  # Return scores.

    def _config(self) -> LinterConfig:
        """Return the repository STE linter configuration."""
        logger.info("Loading STE linter configuration from %s", self.config_path)  # Log before config I/O.
        config = LinterConfig.load(str(self.config_path))  # Read pyproject settings or defaults.
        config.min_score = self.minimum_score  # Apply this validator threshold.
        logger.debug("Loaded STE linter configuration with minimum score %d", config.min_score)  # Log threshold.
        return config  # Return the configured linter settings.

    def _score_paths(self, paths: tuple[Path, ...], config: LinterConfig) -> tuple[list[SteFileReport], list[str]]:
        """Return STE scores and file-level validation errors."""
        engine = self._engine(config)  # Build shared linter dependencies once.
        reports: list[SteFileReport] = []  # Collect successful file scores.
        errors: list[str] = []  # Collect readable validation errors.
        for path in paths:  # Walk each generated file.
            self._score_one(path, config, engine, reports, errors)  # Score the file or record an error.
        return reports, errors  # Return both lists for the report.

    def _engine(
        self,
        config: LinterConfig,
    ) -> tuple[DocumentBuilder, Backend, GrammarAnalyzer, list[Rule], ScoringModel]:
        """Return the existing STE linter dependencies."""
        builder = DocumentBuilder(config)  # Parse Markdown and Python exactly like the CLI.
        backend = get_backend(config.prefer_spacy)  # Use the same grammar backend as the CLI.
        grammar = GrammarAnalyzer()  # Share the grammar helper across rules.
        rules = load_rules(config)  # Load the configured measurable STE rules.
        scorer = ScoringModel()  # Use the CLI scoring model.
        return builder, backend, grammar, rules, scorer  # Return a compact dependency tuple.

    def _score_one(
        self,
        path: Path,
        config: LinterConfig,
        engine: tuple[DocumentBuilder, Backend, GrammarAnalyzer, list[Rule], ScoringModel],
        reports: list[SteFileReport],
        errors: list[str],
    ) -> None:
        """Score one file and append the result or error."""
        logger.info("Scoring STE compliance for %s", path)  # Log before file I/O and scoring.
        if not path.is_file():  # Missing files cannot receive a valid score.
            errors.append(f"{path}: file not found")  # Record a clear error for the report.
            logger.debug("STE validation skipped missing file %s", path)  # Log the skip reason.
            return  # Stop this file only.
        score = self._score_text(path, config, engine)  # Run the existing linter on this file.
        reports.append(SteFileReport(path, score.score, score.word_count, len(score.violations)))  # Store summary data.
        violation_count = len(score.violations)  # Count violations once for the report and log.
        logger.debug("STE score for %s is %d with %d violations", path, score.score, violation_count)  # Log score.

    def _score_text(
        self,
        path: Path,
        config: LinterConfig,
        engine: tuple[DocumentBuilder, Backend, GrammarAnalyzer, list[Rule], ScoringModel],
    ) -> Score:
        """Return the existing linter score for one file."""
        builder, backend, grammar, rules, scorer = engine  # Unpack shared linter dependencies.
        text = path.read_text(encoding="utf-8", errors="replace")  # Read text the same way as the CLI.
        document = builder.build(str(path), text)  # Parse gradable prose from the file.
        dictionary = Dictionary.load(config.dictionary_path)  # Load the dictionary for word checks.
        context = RuleContext(backend=backend, grammar=grammar, config=config, dictionary=dictionary)  # Build context.
        violations = [item for rule in rules for item in rule.check(document, context)]  # Run all configured rules.
        return scorer.score(document, violations, rules, dictionary is not None, config)  # Return the linter score.
