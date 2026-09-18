"""Source document quality gate for the Juniper skill factory."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

import hashlib  # Create compact stable keys for full source paths.
import logging  # Record gate actions and measured outcomes.
import re  # Detect text, tables, commands, and repeated document lines.
from collections import Counter  # Count repeated lines without quadratic scans.
from pathlib import Path  # Use portable paths for corpus scans.

from .models import SourceQualityReport, SourceQualityScore, SpaceRatioDistribution  # Share report records.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SourceQualityGate:
    """Score source Markdown before any skill output is built."""

    MIN_TEXT_CHARS = 400  # Match the harvester review class for nearly empty extraction.
    REPEATED_LINE_THRESHOLD = 0.45  # Fail documents dominated by repeated headers or footers.
    STRUCTURED_RATIO_THRESHOLD = 0.70  # Avoid failing table-heavy or output-heavy sources near the threshold.
    SAMPLE_CHARS = 200_000  # Match the measured corpus scan while bounding very large documents.

    def __init__(self, threshold: float | None = None) -> None:
        """Initialize the SourceQualityGate instance."""
        self.threshold = threshold  # Store an override only for deterministic unit tests.
        self.command_words = {"set", "show", "delete", "edit", "commit", "request", "clear"}  # Detect CLI lines.

    def check_paths(self, paths: tuple[Path, ...], pdf_roots: tuple[Path, ...] = ()) -> SourceQualityReport:
        """Validate the check paths requirement."""
        logger.info("Checking source quality for %s documents", len(paths))  # Log before reading source files.
        texts = self._read_texts(paths)  # Read each file once for distribution and scoring.
        distribution = self._distribution(tuple(texts.values()))  # Calibrate the threshold from real source text.
        scores = tuple(self._score_path(path, text, distribution.threshold, pdf_roots) for path, text in texts.items())
        errors = () if scores else ("source quality gate checked zero documents",)  # Fail an empty guard run.
        logger.debug(
            "Source quality checked %s documents with %s failures",
            len(scores),
            len([s for s in scores if not s.passed]),
        )
        return SourceQualityReport(scores, distribution, errors)  # Return measured status and reasons.

    def score_text(self, document_key: str, text: str, path: Path | None = None) -> SourceQualityScore:
        """Run the score text operation."""
        logger.info("Scoring source quality for %s", document_key)  # Log the direct scoring operation.
        active_path = path or Path(document_key)  # Give in-memory tests a stable path value.
        threshold = self.threshold if self.threshold is not None else 0.08  # Use the safe default without a corpus.
        score = self._score(
            document_key, active_path, text, threshold, None
        )  # Score one text with the active threshold.
        logger.debug("Source quality score for %s is %s with status %s", document_key, score.score, score.status)
        return score  # Return the specific reason and measured values.

    def _read_texts(self, paths: tuple[Path, ...]) -> dict[Path, str]:
        logger.info("Reading source documents for quality scoring")  # Log file input before it starts.
        texts = {path: self._read_sample(path) for path in paths if path.exists()}  # Read bounded source samples.
        logger.debug("Read %s source documents for quality scoring", len(texts))  # Report successful input count.
        return texts  # Return only readable paths so missing paths do not crash the distribution.

    def _read_sample(self, path: Path) -> str:
        logger.info("Reading a bounded quality sample from %s", path)  # Log before one file read.
        with path.open("r", encoding="utf-8", errors="ignore") as handle:  # Open text safely for Windows paths.
            text = handle.read(self.SAMPLE_CHARS)  # Bound the scan to keep the gate fast on large PDFs.
        logger.debug("Read %s sampled characters from %s", len(text), path)  # Report sample size.
        return text  # Return the bounded text sample.

    def _distribution(self, texts: tuple[str, ...]) -> SpaceRatioDistribution:
        logger.info("Calibrating the source quality space-ratio threshold")  # Log before calculating statistics.
        ratios = sorted(self._space_ratio(text) for text in texts)  # Measure every source with one rule.
        if not ratios:  # A zero-document gate must fail but still needs a report object.
            return SpaceRatioDistribution(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, self.threshold or 0.08)
        threshold = (
            self.threshold if self.threshold is not None else self._calibrated_threshold(ratios)
        )  # Choose threshold.
        result = SpaceRatioDistribution(
            len(ratios),
            ratios[0],
            self._percentile(ratios, 5),
            self._percentile(ratios, 25),
            self._percentile(ratios, 50),
            self._percentile(ratios, 75),
            self._percentile(ratios, 95),
            ratios[-1],
            threshold,
        )  # Build report.
        logger.debug(
            "Calibrated source quality threshold %.4f from %s documents", threshold, len(ratios)
        )  # Report rule.
        return result  # Return distribution proof values.

    def _calibrated_threshold(self, ratios: list[float]) -> float:
        logger.info("Selecting the largest low-ratio distribution gap")  # Log threshold selection before scanning gaps.
        candidates = [
            (after - before, before, after)
            for before, after in zip(ratios, ratios[1:], strict=False)
            if 0.01 <= before <= 0.14
        ]
        best = max(candidates, default=(0.0, 0.06, 0.10))  # Fall back to the known conservative valley.
        threshold = max(0.035, min(0.09, (best[1] + best[2]) / 2.0))  # Keep the threshold in the safe defect band.
        logger.debug("Selected source quality threshold %.4f from gap %.4f", threshold, best[0])  # Report gap evidence.
        return threshold  # Return the corruption cutoff.

    def _score_path(self, path: Path, text: str, threshold: float, pdf_roots: tuple[Path, ...]) -> SourceQualityScore:
        logger.info("Scoring source path %s", path)  # Log before per-file scoring.
        source_pdf = self._source_pdf(path, pdf_roots)  # Check whether a better source exists for re-extraction.
        score = self._score(self._document_key(path), path, text, threshold, source_pdf)  # Apply all quality rules.
        logger.debug("Scored source path %s as %s", path, score.status)  # Report pass, review, or fail.
        return score  # Return the row for quarantine and reports.

    def _document_key(self, path: Path) -> str:
        path_bytes = str(path).encode("utf-8")  # Hash the path text to keep generated keys stable and short.
        digest = hashlib.sha1(path_bytes, usedforsecurity=False).hexdigest()[:12]  # Use SHA1 only as a label.
        key = re.sub(r"[^a-z0-9]+", "-", str(path).lower()).strip("-")  # Include path context to avoid collisions.
        return f"{digest}-{key[-200:]}" if key else digest  # Keep SQLite keys bounded and unique enough.

    def _score(
        self, document_key: str, path: Path, text: str, threshold: float, source_pdf: Path | None
    ) -> SourceQualityScore:
        letters, spaces = self._text_counts(text)  # Count letters and spaces in one pass for large documents.
        ratio = spaces / letters if letters else 0.0  # Measure the defect signal before exemptions.
        text_chars = len(re.sub(r"\s+", "", text))  # Count extracted non-space text for empty-source detection.
        repeated = self._repeated_line_ratio(text)  # Measure repeated headers and footers.
        structured = self._structured_ratio(text)  # Measure table and output share for false-positive control.
        status, reason = self._status_reason(
            ratio, text_chars, repeated, structured, threshold, letters
        )  # Classify result.
        score = self._numeric_score(ratio, text_chars, repeated, threshold, status)  # Give operators a sortable value.
        return SourceQualityScore(
            document_key, path, score, status, reason, ratio, text_chars, repeated, structured, source_pdf
        )

    def _status_reason(
        self, ratio: float, text_chars: int, repeated: float, structured: float, threshold: float, letters: int
    ) -> tuple[str, str]:
        if text_chars < self.MIN_TEXT_CHARS or letters < 80:  # Detect almost empty extraction before ratio tests.
            return "fail", "almost no extractable text"  # Quarantine sources that cannot support a skill.
        if repeated >= self.REPEATED_LINE_THRESHOLD:  # Detect repeated headers and footers that dominate content.
            return "fail", "text is mostly repeated headers or footers"  # Report the specific corruption mode.
        if (
            ratio < threshold and structured < self.STRUCTURED_RATIO_THRESHOLD
        ):  # Detect stripped spaces in prose and CLI.
            return "fail", "space-to-letter ratio indicates stripped spaces"  # Quarantine spaceless converter output.
        if ratio < threshold:  # Do not ship borderline structured output without review.
            return "review", "low space ratio in mostly structured text"  # Ask for review instead of false failure.
        return "pass", "source text passed quality checks"  # Permit skill output only after all rules clear.

    def _space_ratio(self, text: str) -> float:
        letters, spaces = self._text_counts(text)  # Count letters and spaces in one pass for speed.
        return spaces / letters if letters else 0.0  # Return zero when no alphabetic evidence exists.

    def _text_counts(self, text: str) -> tuple[int, int]:
        logger.info("Counting letters and spaces in a source sample")  # Log the bounded character scan.
        letters = 0  # Count alphabetic characters for the ratio denominator.
        spaces = 0  # Count literal spaces for the defect numerator.
        for character in text:  # Inspect each sampled character once.
            letters += character.isalpha()  # Add one when the character is a source letter.
            spaces += character == " "  # Add one when the converter preserved a word space.
        logger.debug("Counted %s letters and %s spaces", letters, spaces)  # Report ratio inputs.
        return letters, spaces  # Return both values for ratio calculation.

    def _repeated_line_ratio(self, text: str) -> float:
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]  # Ignore short separators.
        counts = Counter(lines)  # Count each line once so large documents stay fast.
        repeated = sum(1 for line in lines if counts[line] > 2)  # Count lines repeated enough to be boilerplate.
        return repeated / len(lines) if lines else 0.0  # Return a safe zero for empty documents.

    def _structured_ratio(self, text: str) -> float:
        lines = [line.strip() for line in text.splitlines() if line.strip()]  # Inspect only meaningful lines.
        structured = sum(1 for line in lines if self._structured_line(line))  # Count CLI, tables, and output rows.
        return structured / len(lines) if lines else 0.0  # Return zero when no text exists.

    def _structured_line(self, line: str) -> bool:
        first = line.split(maxsplit=1)[0].lower().strip("`|") if line.split() else ""  # Read a command or table token.
        checks = [first in self.command_words, line.startswith("|"), bool(re.search(r"\s{2,}\S", line))]  # Signals.
        return any(checks)  # Classify the line as structured when a strong signal exists.

    def _numeric_score(self, ratio: float, text_chars: int, repeated: float, threshold: float, status: str) -> int:
        if status == "pass":  # Passing rows should sort above quarantined rows.
            return 100  # Give a clean score to passed sources.
        ratio_penalty = 45 if ratio < threshold else 0  # Penalize stripped space evidence.
        text_penalty = 35 if text_chars < self.MIN_TEXT_CHARS else 0  # Penalize almost empty text.
        repeat_penalty = int(repeated * 50)  # Penalize repeated boilerplate proportionally.
        return max(0, 100 - ratio_penalty - text_penalty - repeat_penalty)  # Keep score inside the report range.

    def _source_pdf(self, path: Path, pdf_roots: tuple[Path, ...]) -> Path | None:
        candidates = [
            path.with_suffix(".pdf"),
            *(root / path.with_suffix(".pdf").name for root in pdf_roots),
        ]  # Guess PDFs.
        match = next(
            (candidate for candidate in candidates if candidate.exists()), None
        )  # Prefer the first existing PDF.
        return match  # Return None when re-extraction cannot use a local PDF.

    def _percentile(self, values: list[float], percent: int) -> float:
        index = min(len(values) - 1, max(0, round((percent / 100) * (len(values) - 1))))  # Pick a bounded rank.
        return values[index]  # Return the observed value without interpolation.
