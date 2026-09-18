"""SQLite mirror for the Juniper skill factory journal."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.juniper_skills.tracking.models import DocumentRecord, StageEvent

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class FactoryJournalStore:
    """Persist document journal state before GitHub receives it."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the FactoryJournalStore instance."""
        self.database_path = database_path  # Keep the factory database path explicit for tests and production.
        self.database_path.parent.mkdir(parents=True, exist_ok=True)  # Create the data directory when it is absent.
        self._initialize()  # Ensure the required tables exist before any write.

    def save_document(self, document: DocumentRecord, issue_shape: str) -> None:
        """Upsert one source document record."""
        logger.info("Saving document metadata in the local journal mirror")  # Record the local database action.
        with self._connect() as connection:  # Open a short transaction for this upsert.
            connection.execute(  # Store enough metadata to rebuild the issue title and body.
                """
                INSERT INTO skill_documents
                (document_key, source_path, title, category, page_count, domain, issue_shape,
                 citation_key, part_count, skill_name, priority, version_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_key) DO UPDATE SET
                source_path=excluded.source_path, title=excluded.title,
                category=excluded.category, page_count=excluded.page_count,
                domain=excluded.domain, issue_shape=excluded.issue_shape,
                citation_key=excluded.citation_key, part_count=excluded.part_count,
                skill_name=excluded.skill_name, priority=excluded.priority,
                version_status=excluded.version_status, updated_at=CURRENT_TIMESTAMP
                """,
                self._document_values(document, issue_shape),
            )
        logger.debug("Saved document metadata for key %s", document.document_key)  # Record the affected key.

    def unsynced_documents(self, limit: int) -> list[dict[str, Any]]:
        """Return document issues that GitHub still needs."""
        logger.info("Reading unsynced document issues from the local mirror")  # Record the queue read.
        with self._connect() as connection:  # Open a read transaction for a bounded batch.
            rows = connection.execute(  # Keep issue creation in a measured background queue.
                "SELECT * FROM skill_documents WHERE issue_created_synced = 0 ORDER BY updated_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        documents = [dict(row) for row in rows]  # Convert rows for the GitHub reconciler.
        logger.debug("Read %d unsynced document issues", len(documents))  # Record the batch size.
        return documents

    def mark_document_issue_synced(self, document_key: str, issue_number: int) -> None:
        """Record that GitHub has the issue for one document."""
        logger.info("Marking a document issue as synced")  # Record the local queue update.
        with self._connect() as connection:  # Keep the issue link and sync marker atomic.
            connection.execute(  # Save the remote issue number and stop future create attempts.
                """
                UPDATE skill_documents
                SET issue_number = ?, issue_created_synced = 1, updated_at = CURRENT_TIMESTAMP
                WHERE document_key = ?
                """,
                (issue_number, document_key),
            )
        logger.debug("Marked document key %s as issue %d", document_key, issue_number)  # Record safe link details.

    def save_issue_number(self, document_key: str, issue_number: int) -> None:
        """Record the GitHub issue number for a document."""
        logger.info("Saving the GitHub issue number in the local journal mirror")  # Record the local database action.
        with self._connect() as connection:  # Keep the issue update atomic.
            connection.execute(  # Store the GitHub link for idempotent retries.
                "UPDATE skill_documents SET issue_number = ? WHERE document_key = ?",
                (issue_number, document_key),
            )
        logger.debug("Saved issue %d for document key %s", issue_number, document_key)  # Record safe link details.

    def get_issue_number(self, document_key: str) -> int | None:
        """Return the stored GitHub issue number for a document."""
        logger.info("Reading the local GitHub issue number")  # Record the read action.
        with self._connect() as connection:  # Use a short-lived connection for thread safety.
            row = connection.execute(  # Read only the issue number that idempotency needs.
                "SELECT issue_number FROM skill_documents WHERE document_key = ?",
                (document_key,),
            ).fetchone()
        issue_number = None if row is None or row["issue_number"] is None else int(row["issue_number"])  # Normalize.
        logger.debug("Read local issue %s for document key %s", issue_number, document_key)  # Record the result.
        return issue_number

    def enqueue_stage_event(self, event: StageEvent, comment_body: str) -> int:
        """Store a stage event and queue its GitHub comment."""
        logger.info("Saving a stage event in the local journal mirror")  # Record the durable local write.
        with self._connect() as connection:  # Keep the event and queue row in one transaction.
            cursor = connection.execute(  # Store the exact state before any network call.
                """
                INSERT INTO skill_stage_events
                (document_key, stage, next_action, issue_number, created_at, details_json, comment_body, github_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                self._event_values(event, comment_body),
            )
            if cursor.lastrowid is None:  # SQLite must return the inserted row ID for the event queue.
                raise RuntimeError("stage event insert did not return a row ID")  # Fail before GitHub sync.
            event_id = cursor.lastrowid  # Read the row ID before the connection closes.
        logger.debug("Queued stage event row %d for GitHub sync", event_id)  # Record the queue position.
        return event_id

    def unsynced_stage_events(self, limit: int) -> list[dict[str, Any]]:
        """Return queued stage events that GitHub still needs."""
        logger.info("Reading unsynced stage events from the local mirror")  # Record the queue read.
        with self._connect() as connection:  # Open a read transaction for a bounded batch.
            rows = connection.execute(  # Keep the batch small to respect rate limits.
                """
                SELECT e.id, d.issue_number, e.comment_body, e.stage, e.document_key
                FROM skill_stage_events e
                JOIN skill_documents d ON d.document_key = e.document_key
                WHERE e.github_synced = 0 AND d.issue_number IS NOT NULL
                ORDER BY id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events = [dict(row) for row in rows]  # Convert sqlite rows into test-friendly dictionaries.
        logger.debug("Read %d unsynced stage events", len(events))  # Record the batch size.
        return events

    def mark_stage_event_synced(self, event_id: int) -> None:
        """Mark one queued stage event as sent to GitHub."""
        logger.info("Marking a stage event as synced")  # Record the local update action.
        with self._connect() as connection:  # Keep the update scoped to one row.
            connection.execute(  # Mark the row only after GitHub accepts the comment.
                "UPDATE skill_stage_events SET github_synced = 1 WHERE id = ?",
                (event_id,),
            )
        logger.debug("Marked stage event %d as synced", event_id)  # Record the updated row.

    def mark_stage_events_synced(self, event_ids: tuple[int, ...]) -> None:
        """Mark several queued stage events as sent to GitHub."""
        logger.info("Marking stage events as synced")  # Record the local update action.
        if not event_ids:  # Avoid invalid SQL when no event reached GitHub.
            logger.debug("No stage events needed a synced update")  # Record the no-op result.
            return
        with self._connect() as connection:  # Keep the batch update in one transaction.
            connection.executemany(  # Mark each row only after GitHub accepts the consolidated comment.
                "UPDATE skill_stage_events SET github_synced = 1 WHERE id = ?",
                [(event_id,) for event_id in event_ids],
            )
        logger.debug("Marked %d stage events as synced", len(event_ids))  # Record the updated row count.

    def stage_events_for_document(self, document_key: str) -> list[dict[str, Any]]:
        """Return all stage events for one document."""
        logger.info("Reading stage events for one document")  # Record the recovery and consolidation read.
        with self._connect() as connection:  # Use one read transaction for consistent stage order.
            rows = connection.execute(  # Read all fields needed to rebuild stage events and queue IDs.
                """
                SELECT id, document_key, stage, next_action, issue_number, created_at,
                       details_json, comment_body, github_synced
                FROM skill_stage_events
                WHERE document_key = ?
                ORDER BY id ASC
                """,
                (document_key,),
            ).fetchall()
        events = [self._stage_row(row) for row in rows]  # Decode JSON details for callers.
        logger.debug("Read %d stage events for document key %s", len(events), document_key)  # Record count.
        return events

    def unsynced_document_keys(self, limit: int) -> list[str]:
        """Return document keys with queued stage events."""
        logger.info("Reading document keys with unsynced stage events")  # Record the queue read.
        with self._connect() as connection:  # Read one bounded list for the reconciler.
            rows = connection.execute(  # Group by document so GitHub can receive consolidated comments.
                """
                SELECT DISTINCT e.document_key
                FROM skill_stage_events e
                JOIN skill_documents d ON d.document_key = e.document_key
                WHERE e.github_synced = 0 AND d.issue_number IS NOT NULL
                ORDER BY e.id ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        keys = [str(row["document_key"]) for row in rows]  # Convert rows into plain keys.
        logger.debug("Read %d unsynced document keys", len(keys))  # Record queue size.
        return keys

    def update_document_stage(self, document_key: str, stage: str, status: str) -> None:
        """Store the current stage and status for the generated index."""
        logger.info("Updating the document stage in the local mirror")  # Record the state update.
        with self._connect() as connection:  # Keep the progress board state atomic.
            connection.execute(  # Store the current board fields for label and index generation.
                """
                UPDATE skill_documents
                SET current_stage = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE document_key = ?
                """,
                (stage, status, document_key),
            )
        logger.debug("Updated document key %s to stage %s", document_key, stage)  # Record the new stage.

    def documents_for_index(self) -> list[dict[str, Any]]:
        """Return document progress rows for index generation."""
        logger.info("Reading document progress rows for the index")  # Record the index read.
        with self._connect() as connection:  # Use one read transaction for a consistent index.
            rows = connection.execute("""
                SELECT document_key, title, domain, current_stage, status, issue_number
                FROM skill_documents
                ORDER BY domain, title, document_key
                """).fetchall()  # Sort by domain and title so the index stays stable.
        documents = [dict(row) for row in rows]  # Convert rows for renderer tests and callers.
        logger.debug("Read %d documents for the index", len(documents))  # Record the row count.
        return documents

    def document_row(self, document_key: str) -> dict[str, Any] | None:
        """Return one local document row by key."""
        logger.info("Reading one document row from the local mirror")  # Record the lookup.
        with self._connect() as connection:  # Use a short read transaction.
            row = connection.execute("SELECT * FROM skill_documents WHERE document_key = ?", (document_key,)).fetchone()
        document = None if row is None else dict(row)  # Return None when the row is absent.
        logger.debug("Document row lookup for %s returned %s", document_key, document is not None)  # Record result.
        return document

    def local_comment_bodies(self, document_key: str) -> list[str]:
        """Return stored comment bodies for one document."""
        logger.info("Reading local journal comments for crash recovery")  # Record the recovery read action.
        with self._connect() as connection:  # Read local events if GitHub was unavailable.
            rows = connection.execute(  # Order by creation sequence for deterministic recovery.
                "SELECT comment_body FROM skill_stage_events WHERE document_key = ? ORDER BY id ASC",
                (document_key,),
            ).fetchall()
        comments = [str(row["comment_body"]) for row in rows]  # Return only the comment text that the codec parses.
        logger.debug("Read %d local journal comments", len(comments))  # Record the number of local comments.
        return comments

    def _initialize(self) -> None:
        """Create the journal tables if they do not exist."""
        logger.info("Initializing the local journal mirror database")  # Record the schema action.
        with self._connect() as connection:  # Use one transaction for all schema statements.
            connection.execute(self._documents_schema())  # Create the document mirror table.
            connection.execute(self._events_schema())  # Create the durable comment queue table.
            self._migrate_documents(connection)  # Add new audit columns to older local mirrors.
        logger.debug("Initialized the local journal mirror at %s", self.database_path)  # Record the database path.

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a row-based SQLite connection."""
        connection = sqlite3.connect(self.database_path)  # Open the factory database on demand.
        connection.row_factory = sqlite3.Row  # Make query results clear and name-based.
        try:
            yield connection  # Let the caller run one compact transaction.
            connection.commit()  # Commit successful writes before closing the handle.
        finally:
            connection.close()  # Release the file handle so Windows tests can delete the database.

    def _document_values(self, document: DocumentRecord, issue_shape: str) -> tuple[Any, ...]:
        """Return the document upsert values."""
        return (  # Keep the SQL call compact and repeatable.
            document.document_key,
            str(document.source_path),
            document.title,
            document.category,
            document.page_count,
            document.domain,
            issue_shape,
            document.audit_citation_key,
            document.part_count,
            document.audit_skill_name,
            document.priority,
            document.version_status,
        )

    def _event_values(self, event: StageEvent, comment_body: str) -> tuple[Any, ...]:
        """Return the stage event insert values."""
        return (  # Keep the SQLite insert small and deterministic.
            event.document_key,
            event.stage.value,
            event.next_action,
            event.issue_number,
            event.created_at,
            json.dumps(event.details, sort_keys=True),
            comment_body,
        )

    def _stage_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """Return one stage event row with decoded details."""
        event = dict(row)  # Convert the SQLite row to a mutable dictionary for decoding.
        event["details"] = json.loads(str(event["details_json"]))  # Decode measured fields for summary comments.
        return event  # Return the enriched queue row.

    def _documents_schema(self) -> str:
        """Return the document table schema."""
        return """
            CREATE TABLE IF NOT EXISTS skill_documents (
                document_key TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                domain TEXT NOT NULL,
                issue_shape TEXT NOT NULL,
                issue_number INTEGER,
                citation_key TEXT NOT NULL DEFAULT '',
                part_count INTEGER NOT NULL DEFAULT 0,
                skill_name TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'normal',
                version_status TEXT NOT NULL DEFAULT 'current',
                current_stage TEXT NOT NULL DEFAULT 'queued',
                status TEXT NOT NULL DEFAULT 'queued',
                issue_created_synced INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """

    def _events_schema(self) -> str:
        """Return the stage event table schema."""
        return """
            CREATE TABLE IF NOT EXISTS skill_stage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_key TEXT NOT NULL,
                stage TEXT NOT NULL,
                next_action TEXT NOT NULL,
                issue_number INTEGER,
                created_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                comment_body TEXT NOT NULL,
                github_synced INTEGER NOT NULL DEFAULT 0
            )
        """

    def _migrate_documents(self, connection: sqlite3.Connection) -> None:
        """Add audit columns when an old journal database exists."""
        logger.info("Checking the document table schema for migrations")  # Record the schema check.
        existing = self._document_column_names(connection)  # Read existing columns before any ALTER statement.
        for name, definition in self._document_migrations().items():  # Apply only missing columns.
            if name not in existing:  # Avoid duplicate column errors on repeat runs.
                connection.execute(f"ALTER TABLE skill_documents ADD COLUMN {name} {definition}")  # Add one column.
        logger.debug("Document table migration check finished with %d columns", len(existing))  # Record result size.

    def _document_column_names(self, connection: sqlite3.Connection) -> set[str]:
        """Return the current document table column names."""
        rows = connection.execute("PRAGMA table_info(skill_documents)").fetchall()  # Read SQLite schema metadata.
        return {str(row["name"]) for row in rows}  # Convert metadata rows into a fast lookup set.

    def _document_migrations(self) -> dict[str, str]:
        """Return column definitions for older journal databases."""
        return {  # Keep migration definitions beside the table schema.
            "citation_key": "TEXT NOT NULL DEFAULT ''",
            "part_count": "INTEGER NOT NULL DEFAULT 0",
            "skill_name": "TEXT NOT NULL DEFAULT ''",
            "priority": "TEXT NOT NULL DEFAULT 'normal'",
            "version_status": "TEXT NOT NULL DEFAULT 'current'",
            "current_stage": "TEXT NOT NULL DEFAULT 'queued'",
            "status": "TEXT NOT NULL DEFAULT 'queued'",
            "issue_created_synced": "INTEGER NOT NULL DEFAULT 0",
        }
