"""Cached similarity guard runner for depth extraction measurements."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

import logging  # Record guard measurement actions for operators.
import re  # Remove inline verbatim classes before guard tokenization.
from pathlib import Path  # Keep generated file identities platform safe.

from src.juniper_skills.rewrite import (  # Reuse the locked copyright guard models.
    SimilarityFileResult,
    SimilarityGuardReport,
    VerbatimSimilarityGuard,
)

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CachedSourceSimilarityGuard:
    """Run the similarity guard when many topic parts share one source."""

    def __init__(self, guard: VerbatimSimilarityGuard | None = None) -> None:
        """Store the guard implementation that defines token rules."""
        self.guard = guard or VerbatimSimilarityGuard()  # Reuse the contract thresholds and token filters.
        self._source_hashes: dict[int, set[int]] = {}  # Cache source n-gram hashes by size.
        self._source_windows: dict[int, set[tuple[str, ...]]] = {}  # Cache exact source windows on demand.
        self._source_words: tuple[str, ...] = tuple()  # Store the active source token stream.

    def check_texts(self, generated: tuple[tuple[Path, str], ...], source_text: str) -> SimilarityGuardReport:
        """Return guard results for generated texts that share one source."""
        logger.info("Running cached-source similarity guard")  # Log before guard measurement.
        if not generated:  # A guard that measures no topic files must fail.
            return self.guard.check(tuple())  # Reuse the locked zero-file failure report.
        self._prepare_source(source_text)  # Normalize and index the shared source one time.
        results = tuple(self._check_one(path, text) for path, text in generated)  # Measure topics.
        report = SimilarityGuardReport(len(results), self.guard.threshold, self.guard.warn_threshold, results, 0.0)
        logger.debug("Cached-source guard checked %d generated texts", len(results))  # Log the measured count.
        return report  # Return the same report model as the base guard.

    def check_files(self, paths: tuple[Path, ...], source_text: str) -> SimilarityGuardReport:
        """Return guard results for generated files that share one source."""
        logger.info("Reading generated files for cached-source guard")  # Log before generated file input.
        generated = tuple((path, path.read_text(encoding="utf-8")) for path in paths)  # Read each topic file once.
        logger.debug("Read %d generated files for cached-source guard", len(generated))  # Log input count.
        return self.check_texts(generated, source_text)  # Measure the generated text values.

    def _prepare_source(self, source_text: str) -> None:
        """Build bounded source indexes for repeated topic checks."""
        logger.info("Indexing shared source text for cached guard")  # Log before source indexing.
        self._source_words = self._policy_words(source_text)  # Normalize the source with contract rules.
        self._source_hashes = self._hash_sets(self._source_words)  # Build threshold-bounded hash indexes.
        self._source_windows = {}  # Clear exact-window cache from a prior source.
        logger.debug("Indexed %d source guard words", len(self._source_words))  # Log source index size.

    def _hash_sets(self, words: tuple[str, ...]) -> dict[int, set[int]]:
        """Return source hash sets through the hard-fail probe size."""
        limit = self.guard.threshold + 1  # A match at this size proves a hard-fail band.
        sizes = range(1, min(limit, len(words)) + 1)  # Avoid impossible window sizes.
        return {size: set(self.guard._rolling_hashes(words, size)) for size in sizes}  # Cache hash probes.

    def _check_one(self, path: Path, text: str) -> SimilarityFileResult:
        """Return the similarity result for one generated text."""
        generated_words = self._policy_words(text)  # Normalize one generated topic with guard rules.
        longest, phrase = self._bounded_longest(generated_words)  # Measure up to the hard-fail threshold.
        status = self.guard._status(longest)  # Classify the run with the locked thresholds.
        passed = status != "failed"  # Only the hard band stops publication.
        return SimilarityFileResult(path, longest, phrase, status, passed)  # Return the shared result model.

    def _bounded_longest(self, generated_words: tuple[str, ...]) -> tuple[int, str]:
        """Return the longest shared run needed by the guard threshold."""
        limit = min(self.guard.threshold + 1, len(generated_words), len(self._source_words))  # Bound probes.
        for size in range(limit, 0, -1):  # Search from fail size down to one word.
            phrase = self._shared_generated_phrase(generated_words, size)  # Look for this shared size.
            if phrase:  # The first hit is the bounded longest run.
                return size, phrase  # Return the exact phrase for the report.
        return 0, ""  # Return no shared prose for empty generated text.

    def _shared_generated_phrase(self, words: tuple[str, ...], size: int) -> str:
        """Return one generated phrase that also exists in the source."""
        source_hashes = self._source_hashes.get(size, set())  # Read the source hash set for this size.
        for offset, digest in enumerate(self.guard._rolling_hashes(words, size)):  # Scan generated windows once.
            if digest in source_hashes and self._source_has(words[offset : offset + size]):  # Verify the hash hit.
                return " ".join(words[offset : offset + size])  # Return the shared phrase.
        return ""  # Return no phrase when the size has no exact hit.

    def _source_has(self, window: tuple[str, ...]) -> bool:
        """Return whether the source has an exact token window."""
        size = len(window)  # Use the candidate length as the source-window key.
        if size not in self._source_windows:  # Build exact windows only when a hash hit occurs.
            self._source_windows[size] = self._window_set(size)  # Cache exact windows for later topics.
        return window in self._source_windows[size]  # Verify the candidate with exact tokens.

    def _window_set(self, size: int) -> set[tuple[str, ...]]:
        """Return exact source token windows for one size."""
        stop = len(self._source_words) - size + 1  # Compute the last valid source window start.
        return {self._source_words[index : index + size] for index in range(stop)}  # Build exact windows.

    def _policy_words(self, text: str) -> tuple[str, ...]:
        """Return guard words after contract inline-code exclusion."""
        without_inline = re.sub(r"`[^`\n]+`", " ", text)  # Exclude inline verbatim classes by contract.
        return self.guard.prose_words(without_inline)  # Reuse the base guard for the remaining prose.
