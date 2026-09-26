"""Quarantine persistence for source quality failures."""

from __future__ import annotations  # Keep annotations cheap during quality imports.

import logging  # Record database actions for operator traceability.
import sqlite3  # Use the existing factory database without new dependencies.
from pathlib import Path  # Use platform-safe paths for the database location.

from .models import SourceQualityReport, SourceQualityScore  # Store measured gate results.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SourceQualityDatabase:
    """Record source quality decisions in the skill factory database."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the SourceQualityDatabase instance."""
        self.database_path = database_path  # Store the locked factory database path.

    def write_report(self, report: SourceQualityReport) -> None:
        """Create the write report output."""
        logger.info("Writing source quality report to %s", self.database_path)  # Log before database writes.
        self.database_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the factory data folder exists.
        connection = sqlite3.connect(self.database_path)  # Open one handle so Windows can close it explicitly.
        try:  # Ensure the database handle closes even when a SQL statement fails.
            self._create_schema(connection)  # Ensure quarantine tables exist before row writes.
            self._clear_previous_rows(connection)  # Remove stale rows before writing a full gate report.
            for score in report.scores:  # Persist every score so pass and fail counts are auditable.
                self._upsert_score(connection, score)  # Upsert the measured source quality row.
            self._quarantine_failures(connection, report.failed)  # Block failed sources in the work queue.
            connection.commit()  # Commit the quality transaction before the file handle closes.
        finally:  # Always release the file handle for cleanup and later factory runs.
            connection.close()  # Close SQLite explicitly because the context manager does not close handles.
        logger.debug("Wrote %s source quality rows", len(report.scores))  # Report durable row count.

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        logger.info("Creating source quality database schema")  # Log before DDL changes.
        connection.execute(self._quality_schema())  # Create the quality score table when missing.
        connection.execute(self._quarantine_schema())  # Create the quarantine table when missing.
        logger.debug("Source quality database schema is ready")  # Report DDL completion.

    def _clear_previous_rows(self, connection: sqlite3.Connection) -> None:
        logger.info("Clearing stale source quality quarantine rows")  # Log before replacing full-scan state.
        connection.execute("DELETE FROM source_quality")  # Remove prior measurements that used older keys.
        connection.execute("DELETE FROM source_quarantine")  # Remove prior quarantine rows before the new report.
        logger.debug("Cleared stale source quality quarantine rows")  # Report cleanup completion.

    def _quality_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS source_quality (
                document_key TEXT PRIMARY KEY, path TEXT NOT NULL, status TEXT NOT NULL,
                score INTEGER NOT NULL, reason TEXT NOT NULL, space_ratio REAL NOT NULL,
                text_chars INTEGER NOT NULL, repeated_line_ratio REAL NOT NULL,
                structured_ratio REAL NOT NULL, source_pdf TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """  # Store one quality decision for each source document.

    def _quarantine_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS source_quarantine (
                document_key TEXT PRIMARY KEY, reason TEXT NOT NULL, path TEXT NOT NULL,
                source_pdf TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """  # Store documents that must not produce a skill.

    def _upsert_score(self, connection: sqlite3.Connection, score: SourceQualityScore) -> None:
        logger.info("Upserting source quality row for %s", score.document_key)  # Log each durable decision.
        connection.execute(
            """
            INSERT OR REPLACE INTO source_quality
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            self._score_values(score),
        )  # Upsert the row by document key for repeatable scans.
        logger.debug("Upserted source quality row for %s", score.document_key)  # Confirm the row write.

    def _score_values(self, score: SourceQualityScore) -> tuple[object, ...]:
        pdf = str(score.source_pdf) if score.source_pdf else ""  # Store an empty value when no PDF is present.
        return (
            score.document_key,
            str(score.path),
            score.status,
            score.score,
            score.reason,
            score.space_ratio,
            score.text_chars,
            score.repeated_line_ratio,
            score.structured_ratio,
            pdf,
        )  # Keep SQL parameter order next to the schema.

    def _quarantine_failures(self, connection: sqlite3.Connection, failures: tuple[SourceQualityScore, ...]) -> None:
        logger.info("Quarantining %s failed source documents", len(failures))  # Log before blocking work items.
        for score in failures:  # Persist each failed or review source as a quarantine item.
            self._quarantine_one(connection, score)  # Write the quarantine row and queue state.
        logger.debug("Quarantined %s failed source documents", len(failures))  # Report blocked source count.

    def _quarantine_one(self, connection: sqlite3.Connection, score: SourceQualityScore) -> None:
        pdf = str(score.source_pdf) if score.source_pdf else ""  # Store PDF evidence for re-extraction planning.
        connection.execute(
            "INSERT OR REPLACE INTO source_quarantine VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (score.document_key, score.reason, str(score.path), pdf),
        )  # Record the exact quality failure so it appears in reports.
        self._mark_work_item(connection, score)  # Try to block queued work when the inventory table exists.

    def _mark_work_item(self, connection: sqlite3.Connection, score: SourceQualityScore) -> None:
        if not self._table_exists(connection, "work_item"):  # Unit tests can use only the quality schema.
            return  # Skip queue update when no queue table exists.
        connection.execute(
            "UPDATE work_item SET status = ?, reason = ?, updated_at = CURRENT_TIMESTAMP WHERE document_key = ?",
            ("quarantined", score.reason, score.document_key),
        )  # Prevent the orchestrator from leasing a failed source.

    def _table_exists(self, connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        return row is not None  # Return whether the table can be updated safely.
