"""Quality harness for the dictionary extractor.

Compares an extracted dictionary against a set of hand-verified golden entries and
reports a per-field accuracy score with every mismatch. The score turns the phrase
"near flawless" into a number the parser can be tuned against.

Run it with:

    python -m tools.ste_linter.dictionary.quality data/ste_dictionary.json \
        tests/fixtures/ste_linter/dictionary_golden.json
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import argparse  # Parses the command-line arguments.
import json  # Reads the dictionary and the golden set.
import logging  # Records the harness stages.
from dataclasses import dataclass, field  # Declares the report value type.
from typing import Any  # Types the loaded JSON records.

# The logger for the quality harness. The main function configures the handler.
_LOG = logging.getLogger("ste_linter.dictionary.quality")

# The field accuracy that counts as a pass, from 0 to 1.
_TARGET_ACCURACY = 0.95


@dataclass
class QualityReport:
    """The result of comparing the dictionary against the golden set."""

    total: int = 0  # The number of golden entries checked.
    keyword_found: int = 0  # How many golden keywords appear in the dictionary.
    pos_correct: int = 0  # How many parts of speech match.
    approved_correct: int = 0  # How many approved flags match.
    alternatives_correct: int = 0  # How many alternative lists match.
    mismatches: list[str] = field(default_factory=list)  # Each mismatch, described.

    @property
    def field_accuracy(self) -> float:
        """Return the mean correctness across the four measured fields, 0 to 1."""
        if self.total == 0:  # Guard an empty golden set.
            return 0.0  # No entries means no accuracy.
        measured = self.keyword_found + self.pos_correct + self.approved_correct + self.alternatives_correct
        return measured / (self.total * 4)  # Average across the four fields.


class QualityHarness:
    """Scores an extracted dictionary against golden entries."""

    def evaluate(self, dictionary: list[dict[str, Any]], golden: list[dict[str, Any]]) -> QualityReport:
        """Return a quality report for the dictionary against the golden set."""
        _LOG.info("Evaluating %d golden entries", len(golden))  # Log before the comparison.
        index = self._index(dictionary)  # Build a keyword and part-of-speech lookup.
        report = QualityReport(total=len(golden))  # Start an empty report.
        for entry in golden:  # Compare each golden entry against the dictionary.
            self._compare_one(entry, index, report)  # Update the report for this entry.
        _LOG.debug("Field accuracy %.3f", report.field_accuracy)  # Log the accuracy after comparison.
        return report  # Return the finished report.

    def _index(self, dictionary: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        """Return a lookup from a keyword and part-of-speech pair to a record."""
        index: dict[tuple[str, str], dict[str, Any]] = {}  # The lookup map.
        for record in dictionary:  # Walk each dictionary record.
            key = (str(record.get("keyword", "")), str(record.get("part_of_speech", "")))  # The pair.
            index.setdefault(key, record)  # Keep the first record for a pair.
        return index  # Return the lookup.

    def _compare_one(
        self,
        golden: dict[str, Any],
        index: dict[tuple[str, str], dict[str, Any]],
        report: QualityReport,
    ) -> None:
        """Update the report for one golden entry."""
        keyword = str(golden["keyword"])  # The golden keyword.
        pos = str(golden["part_of_speech"])  # The golden part of speech.
        record = index.get((keyword, pos))  # The matching dictionary record.
        if record is None:  # The keyword and part of speech are missing.
            report.mismatches.append(f"{keyword} ({pos})  missing from dictionary")  # Record the miss.
            return  # Nothing else to score for a missing entry.
        report.keyword_found += 1  # The keyword and part of speech were found.
        report.pos_correct += 1  # The part of speech matches by construction of the key.
        self._score_approved(golden, record, keyword, report)  # Score the approved flag.
        self._score_alternatives(golden, record, keyword, report)  # Score the alternatives.

    def _score_approved(
        self, golden: dict[str, Any], record: dict[str, Any], keyword: str, report: QualityReport
    ) -> None:
        """Score the approved flag for one entry."""
        if bool(golden["approved"]) == bool(record.get("approved")):  # The flags match.
            report.approved_correct += 1  # Count a correct approved flag.
        else:  # The flags differ.
            report.mismatches.append(
                f"{keyword}  approved  expected {golden['approved']}  actual {record.get('approved')}"
            )  # Record the mismatch.

    def _score_alternatives(
        self, golden: dict[str, Any], record: dict[str, Any], keyword: str, report: QualityReport
    ) -> None:
        """Score the alternatives list for one entry."""
        expected = {str(item).lower() for item in golden.get("alternatives", [])}  # The golden set.
        actual = {str(item).lower() for item in record.get("alternatives", [])}  # The dictionary set.
        if expected == actual:  # The alternative sets match exactly.
            report.alternatives_correct += 1  # Count a correct alternatives list.
        else:  # The sets differ.
            report.mismatches.append(
                f"{keyword}  alternatives  expected {sorted(expected)}  actual {sorted(actual)}"
            )  # Record the mismatch.


def _load_entries(path: str) -> list[dict[str, Any]]:
    """Return the entries list from a dictionary or golden JSON file."""
    with open(path, encoding="utf-8") as handle:  # Open the JSON file.
        data = json.load(handle)  # Parse the JSON content.
    entries = data.get("entries", data) if isinstance(data, dict) else data  # Accept both shapes.
    return list(entries)  # Return the entries list.


def _print_report(report: QualityReport) -> None:
    """Print the report totals and every mismatch."""
    total = report.total  # The golden entry count.
    _LOG.info("Golden entries: %d", total)  # Print the total.
    _LOG.info("  keyword found:   %d/%d", report.keyword_found, total)  # Print the found count.
    _LOG.info("  part of speech:  %d/%d", report.pos_correct, total)  # Print the part-of-speech count.
    _LOG.info("  approved status: %d/%d", report.approved_correct, total)  # Print the approved count.
    _LOG.info("  alternatives:    %d/%d", report.alternatives_correct, total)  # Print the alternatives count.
    _LOG.info("Field accuracy: %.1f%%", report.field_accuracy * 100)  # Print the accuracy.
    if report.mismatches:  # There were mismatches to show.
        _LOG.info("Mismatches:")  # Print the mismatch header.
        for line in report.mismatches:  # Each mismatch on its own line.
            _LOG.info("  %s", line)  # Print the mismatch.


def main(argv: list[str] | None = None) -> int:
    """Run the quality harness from the command line."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Show the report.
    parser = argparse.ArgumentParser(description="Score the STE dictionary against golden entries.")  # Parser.
    parser.add_argument("dictionary", help="Path to the extracted dictionary JSON.")  # The dictionary.
    parser.add_argument("golden", help="Path to the golden-set JSON.")  # The golden set.
    args = parser.parse_args(argv)  # Parse the arguments.
    dictionary = _load_entries(args.dictionary)  # Load the dictionary entries.
    golden = _load_entries(args.golden)  # Load the golden entries.
    report = QualityHarness().evaluate(dictionary, golden)  # Score the dictionary.
    _print_report(report)  # Print the report.
    return 0 if report.field_accuracy >= _TARGET_ACCURACY else 1  # Pass when the target is met.


if __name__ == "__main__":  # Allow "python -m tools.ste_linter.dictionary.quality".
    raise SystemExit(main())  # Run the harness and use its exit code.
