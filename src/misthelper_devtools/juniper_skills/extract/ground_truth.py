"""Independent ground-truth checks for coverage manifest quality."""

from __future__ import annotations  # Keep annotations cheap during factory imports.

import logging  # Record measurement actions for reports.
import re  # Normalize checklist values for recall and precision.

from .coverage import CoverageAnalyzer  # Build manifests from source regions under test.
from .models import CoverageEntry, GroundTruthMeasurement, GroundTruthRegion, GroundTruthReport  # Share models.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class GroundTruthEvaluator:
    """Measure manifests against facts found independently."""

    def __init__(self, analyzer: CoverageAnalyzer | None = None) -> None:
        """Store the analyzer used to build measured manifests."""
        self.analyzer = analyzer or CoverageAnalyzer()  # Allow tests to inject a configured analyzer.

    def evaluate(self, regions: tuple[GroundTruthRegion, ...], source_key: str) -> GroundTruthReport:
        """Return aggregate recall and precision for independent regions."""
        logger.info("Evaluating %d ground-truth regions", len(regions))  # Log before measurement starts.
        measurements = tuple(self._evaluate_region(region, source_key) for region in regions)  # Measure regions.
        logger.debug("Evaluated %d ground-truth regions", len(measurements))  # Log measured region count.
        return GroundTruthReport(measurements)  # Return aggregate report.

    def _evaluate_region(self, region: GroundTruthRegion, source_key: str) -> GroundTruthMeasurement:
        """Return recall and precision for one region."""
        manifest = self.analyzer.analyze_text(region.source_text, source_key, region.name)  # Build manifest.
        matched_truth = self._matched_truth(region.facts, manifest.entries)  # Match truth facts one time only.
        precise_manifest = self._precise_entries(manifest.entries, region.facts)  # Match entries one time only.
        missing = tuple(fact for fact in region.facts if fact not in matched_truth)  # Facts not found by manifest.
        noisy = tuple(entry for entry in manifest.entries if entry not in precise_manifest)  # Extra manifest facts.
        return GroundTruthMeasurement(  # Return the complete region measurement.
            region.name,
            len(region.facts),
            len(manifest.entries),
            len(matched_truth),
            len(precise_manifest),
            missing,
            noisy,
        )

    def _covered(self, expected: CoverageEntry, candidates: tuple[CoverageEntry, ...]) -> bool:
        """Return whether candidates cover one expected fact."""
        return any(self._matches(expected, candidate) for candidate in candidates)  # Accept one matching candidate.

    def _matched_truth(
        self, truths: tuple[CoverageEntry, ...], entries: tuple[CoverageEntry, ...]
    ) -> tuple[CoverageEntry, ...]:
        """Return ground-truth facts that match unused manifest entries."""
        unused = list(entries)  # Track manifest entries so duplicates cannot inflate recall.
        matched: list[CoverageEntry] = []  # Collect independent facts that the manifest found.
        for truth in truths:  # Evaluate independent facts in source order.
            match = self._first_match(truth, unused)  # Find one unused manifest entry.
            if match is not None:  # A matching manifest entry proves this truth fact was found.
                matched.append(truth)  # Keep this truth fact as a recall hit.
                unused.remove(match)  # Prevent duplicate truth facts from sharing one manifest entry.
        return tuple(matched)  # Return one-to-one recall matches.

    def _precise_entries(
        self, entries: tuple[CoverageEntry, ...], truths: tuple[CoverageEntry, ...]
    ) -> tuple[CoverageEntry, ...]:
        """Return manifest entries that match unused ground-truth facts."""
        unused = list(truths)  # Track ground-truth facts so duplicates cannot inflate precision.
        precise: list[CoverageEntry] = []  # Collect manifest entries that match real facts.
        for entry in entries:  # Evaluate manifest entries in source order.
            match = self._first_match(entry, unused)  # Find one unused ground-truth fact.
            if match is not None:  # A matching truth fact proves this entry is precise.
                precise.append(entry)  # Keep this manifest entry as precise.
                unused.remove(match)  # Prevent a duplicate entry from matching the same truth.
        return tuple(precise)  # Return one-to-one precision matches.

    def _first_match(self, entry: CoverageEntry, truths: list[CoverageEntry]) -> CoverageEntry | None:
        """Return the first matching ground-truth fact."""
        for truth in truths:  # Check unused truth facts in source order.
            if self._matches(entry, truth):  # The manifest entry matches this truth fact.
                return truth  # Return the matched truth for removal.
        return None  # Return no match when the entry is noise.

    def _matches(self, expected: CoverageEntry, candidate: CoverageEntry) -> bool:
        """Return whether two checklist entries state the same fact."""
        if expected.category != candidate.category:  # Categories must match for precision and recall.
            return False  # Reject cross-category matches.
        expected_tokens = self._tokens(expected.value)  # Normalize expected fact text.
        candidate_tokens = self._tokens(candidate.value)  # Normalize candidate fact text.
        if not expected_tokens:  # Empty ground-truth facts cannot be measured.
            return False  # Reject empty values.
        shared = expected_tokens.intersection(candidate_tokens)  # Count shared fact anchors.
        return len(shared) >= min(2, len(expected_tokens))  # Require enough anchors for a real match.

    def _tokens(self, value: str) -> set[str]:
        """Return comparable tokens for one checklist value."""
        raw_tokens = re.findall(r"[a-z0-9][a-z0-9._/-]*", value.lower())  # Keep identifiers and numbers.
        return {token for token in raw_tokens if len(token) > 1}  # Drop one-letter noise tokens.
