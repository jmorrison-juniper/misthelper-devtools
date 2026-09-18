"""Read real measurement values for Juniper skill factory audit comments."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from src.juniper_skills.extract.models import DepthExtractionResult
from src.juniper_skills.rewrite.models import SimilarityGuardReport, SteValidationReport
from src.juniper_skills.tracking.models import DocumentRecord

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class StageMeasurementInputs:
    """Upstream outputs that can prove one audit measurement set."""

    document_key: str
    package_dir: Path | None = None
    extraction_result: DepthExtractionResult | None = None
    guard_report: SimilarityGuardReport | None = None
    ste_report: SteValidationReport | None = None


class StageMeasurementReader:
    """Build audit details only from upstream components and disk state."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the StageMeasurementReader instance."""
        self.database_path = database_path  # Store the factory database path for identity and source size reads.

    def document_from_factory(self, document_key: str, domain: str, skill_name: str) -> DocumentRecord:
        """Return document identity from the factory database."""
        logger.info("Reading document identity from the factory database")  # Record the database read.
        row = self._source_document_row(document_key)  # Read the authoritative source document row.
        citation_key = self._citation_key(domain, self._document_slug(row))  # Read the generated citation key.
        document = self._document_from_row(row, domain, skill_name, citation_key)  # Build the tracker identity.
        logger.debug("Read factory identity for document key %s", document_key)  # Record safe identity.
        return document

    def details(self, inputs: StageMeasurementInputs) -> dict[str, object]:
        """Return audit details with no synthesized measurement values."""
        logger.info("Reading measured audit values for a stage comment")  # Record the measurement action.
        details = self._not_measured_details()  # Start with explicit unknown values.
        self._apply_extraction(details, inputs.extraction_result)  # Fill extraction values only when provided.
        self._apply_package(details, inputs.package_dir, inputs.document_key)  # Fill package values only from disk.
        self._apply_guard(details, inputs.guard_report)  # Fill guard values only from the guard report object.
        self._apply_ste(details, inputs.ste_report)  # Fill STE values only from the validator report object.
        logger.debug("Built measured audit details for document key %s", inputs.document_key)  # Record key.
        return details

    def _apply_extraction(self, details: dict[str, object], result: DepthExtractionResult | None) -> None:
        """Copy values from the extraction result."""
        if result is None:  # Do not invent extraction values when the stage lacks a result object.
            logger.debug("Extraction result not available for audit details")  # Record the missing evidence.
            return
        details["topic_count"] = len(result.topics)  # Use the extractor's rendered topic count.
        details["card_count"] = len(result.cards)  # Use the extractor's deduplicated card count.
        details["retention_percentage"] = round(result.retention_percent, 1)  # Use the extractor retention metric.

    def _apply_package(self, details: dict[str, object], package_dir: Path | None, document_key: str) -> None:
        """Copy values from the built package on disk."""
        if package_dir is None or not package_dir.exists():  # Do not report disk values without a package.
            logger.debug("Built package not available for audit details")  # Record the missing evidence.
            return
        topic_paths = self._topic_paths(package_dir)  # Read generated Markdown topics from the package.
        details["topic_count"] = len(topic_paths)  # Report the actual topic files on disk.
        details["card_count"] = self._card_count(topic_paths)  # Count cards that the package actually contains.
        details["retention_percentage"] = self._package_retention(topic_paths, document_key)  # Measure retention.

    def _apply_guard(self, details: dict[str, object], report: SimilarityGuardReport | None) -> None:
        """Copy values from the similarity guard result."""
        if report is None:  # Do not invent a guard result for early stages.
            logger.debug("Similarity guard report not available for audit details")  # Record missing evidence.
            return
        longest = max((result.longest_run for result in report.results), default=0)  # Read the max shared word run.
        details["guard_result"] = self._guard_result(report)  # Report cleared, warned, failed, or errored.
        details["longest_guard_run"] = longest  # Report words, not seconds.

    def _apply_ste(self, details: dict[str, object], report: SteValidationReport | None) -> None:
        """Copy values from the STE validator result."""
        if report is None:  # Do not invent a score before STE validation runs.
            logger.debug("STE validation report not available for audit details")  # Record missing evidence.
            return
        scores = [file_report.score for file_report in report.reports]  # Read real file scores from the report.
        details["ste_score"] = min(scores) if scores else "not measured"  # Use the worst file score as the proof.

    def _topic_paths(self, package_dir: Path) -> tuple[Path, ...]:
        """Return generated topic files from a package document directory."""
        paths = tuple(path for path in sorted(package_dir.glob("*.md")) if path.name != "INDEX.md")  # Exclude index.
        logger.debug("Read %d topic files from %s", len(paths), package_dir)  # Record the disk count.
        return paths

    def _card_count(self, paths: tuple[Path, ...]) -> int:
        """Return the count of knowledge cards in generated topic files."""
        pattern = re.compile(r"^- (?:\*\*)?(MUST|SHOULD|INFO)(?:\*\*)?:", re.MULTILINE)  # Match card syntax.
        count = sum(len(pattern.findall(path.read_text(encoding="utf-8"))) for path in paths)  # Count real cards.
        logger.debug("Counted %d knowledge cards in generated topic files", count)  # Record the card count.
        return count

    def _package_retention(self, paths: tuple[Path, ...], document_key: str) -> float | str:
        """Return generated topic size as a percentage of source text size."""
        source_chars = self._source_text_chars(document_key)  # Read the source size from the factory database.
        if source_chars <= 0:  # Missing source size means retention cannot be measured.
            return "not measured"  # Return explicit unknown instead of an invented percentage.
        retained = sum(path.stat().st_size for path in paths)  # Measure generated file bytes from disk.
        return round(retained * 100.0 / source_chars, 1)  # Return the same percentage definition as extraction.

    def _guard_result(self, report: SimilarityGuardReport) -> str:
        """Return the summary status from a guard report."""
        if report.errors:  # Guard-level errors are stronger than file statuses.
            return "errored"  # Report that the guard did not produce a clean pass or fail.
        if report.files_failed:  # A failed file blocks the package.
            return "failed"  # Report the hard failure.
        if report.files_warned:  # A warned file needs human review.
            return "warned"  # Report the review band.
        return "cleared"  # Every measured file sits below the warning band.

    def _not_measured_details(self) -> dict[str, object]:
        """Return the required fields with explicit unknown values."""
        return {  # Never synthesize a measurement when an upstream result is absent.
            "topic_count": "not measured",
            "card_count": "not measured",
            "retention_percentage": "not measured",
            "guard_result": "not measured",
            "longest_guard_run": "not measured",
            "ste_score": "not measured",
        }

    def _source_document_row(self, document_key: str) -> sqlite3.Row:
        """Read one source document row from the factory database."""
        with self._connect() as connection:  # Use one short read transaction.
            row = connection.execute("SELECT * FROM source_document WHERE document_key = ?", (document_key,)).fetchone()
        if row is None:  # A missing document means the caller used a bad key.
            raise KeyError(document_key)
        return cast(sqlite3.Row, row)  # Return the row with all identity fields.

    def _source_text_chars(self, document_key: str) -> int:
        """Return the source text character count from the factory database."""
        logger.info("Reading source text size from the factory database")  # Record the metric read.
        row = self._source_document_row(document_key)  # Reuse the authoritative source row.
        value = int(row["text_chars"])  # Convert the database value for arithmetic.
        logger.debug("Read %d source text characters for %s", value, document_key)  # Record safe metric.
        return value

    def _citation_key(self, domain: str, document_slug: str) -> str:
        """Return the allocated citation key from the factory database."""
        with self._connect() as connection:  # Use a short read transaction.
            row = connection.execute(self._citation_sql(), (domain, document_slug)).fetchone()  # Read citation key.
        return "" if row is None else str(row["key"])  # Let the document model fall back when no key exists.

    def _citation_sql(self) -> str:
        """Return the citation-key lookup SQL."""
        return "SELECT key FROM citation_keys WHERE domain = ? AND document_slug = ?"  # Keep SQL in one location.

    def _document_slug(self, row: sqlite3.Row) -> str:
        """Return the document slug from a source document row."""
        return str(Path(str(row["source_pdf"])).with_suffix("").name)  # Convert the PDF path to its stem.

    def _document_from_row(self, row: sqlite3.Row, domain: str, skill_name: str, citation_key: str) -> DocumentRecord:
        """Return a tracker document record from a factory row."""
        return DocumentRecord(  # Keep GitHub issue identity tied to the factory database.
            Path(str(row["source_pdf"])),
            str(row["title"]),
            str(row["category"]),
            int(row["pages"]),
            domain,
            citation_key,
            int(row["part_count"]),
            skill_name,
            str(row["priority"]),
            str(row["version_status"]),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a row-based SQLite connection."""
        connection = sqlite3.connect(self.database_path)  # Open the factory database for one read.
        connection.row_factory = sqlite3.Row  # Make column reads clear and name-based.
        try:
            yield connection  # Let the caller perform one read transaction.
        finally:
            connection.close()  # Release the file handle so Windows tests can delete the database.
