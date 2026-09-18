"""Fine-grained SQLite journal for orchestrator stage recovery."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.juniper_skills.orchestrate.models import StageOutcome

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class OrchestratorJournal:
    """Record every pipeline stage before the next stage starts."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the OrchestratorJournal instance."""
        self.database_path = database_path  # Reuse the inventory database for durable factory state.
        self.database_path.parent.mkdir(parents=True, exist_ok=True)  # Create the data folder for isolated tests.
        self._initialize()  # Ensure the journal table exists before a run starts.

    def started(self, document_key: str, stage: str, worker_id: str) -> None:
        """Run the started operation."""
        logger.info("Recording start for orchestrator stage %s", stage)  # Log before writing the start event.
        outcome = StageOutcome(document_key, stage, "started", "stage started", worker_id)  # Build a durable event.
        self.record(outcome)  # Save the start event before work begins.
        logger.debug("Recorded start for stage %s on %s", stage, document_key)  # Record the journal result.

    def completed(self, outcome: StageOutcome) -> None:
        """Run the completed operation."""
        logger.info("Recording completion for orchestrator stage %s", outcome.stage)  # Log before writing completion.
        self.record(outcome)  # Save the completed outcome before the next stage can start.
        logger.debug("Recorded completion for stage %s", outcome.stage)  # Record the completed event.

    def failed(self, document_key: str, stage: str, worker_id: str, error: str) -> None:
        """Run the failed operation."""
        logger.info("Recording failure for orchestrator stage %s", stage)  # Log before writing a failure event.
        outcome = StageOutcome(document_key, stage, "failed", error, worker_id)  # Build a retryable failure event.
        self.record(outcome)  # Save the failure so a restart resumes this stage.
        logger.debug("Recorded failure for stage %s on %s", stage, document_key)  # Record the failure event.

    def record(self, outcome: StageOutcome) -> None:
        """Run the record operation."""
        logger.info("Writing an orchestrator journal event")  # Log before the durable SQLite write.
        with self._connect() as connection:  # Use a short transaction for the checkpoint.
            connection.execute(self._insert_sql(), self._values(outcome))  # Store the event with measured detail.
        logger.debug("Wrote an orchestrator journal event for %s", outcome.document_key)  # Record affected document.

    def last_stage(self, document_key: str) -> str | None:
        """Run the last stage operation."""
        logger.info("Reading the last completed orchestrator stage")  # Log before the recovery read.
        with self._connect() as connection:  # Use one read transaction for recovery.
            row = connection.execute(self._last_stage_sql(), (document_key,)).fetchone()  # Read newest completion.
        stage = None if row is None else str(row["stage"])  # Normalize the missing row for callers.
        logger.debug("Last completed stage for %s is %s", document_key, stage)  # Record the recovery point.
        return stage  # Return the completed stage name or None.

    def stage_events(self, document_key: str) -> list[dict[str, object]]:
        """Run the stage events operation."""
        logger.info("Reading orchestrator journal events for one document")  # Log before the audit read.
        with self._connect() as connection:  # Use one read transaction for ordered events.
            rows = connection.execute(self._events_sql(), (document_key,)).fetchall()  # Read all document events.
        events = [dict(row) for row in rows]  # Return dictionaries so tests can assert exact order.
        logger.debug("Read %d orchestrator journal events", len(events))  # Record event count.
        return events  # Return the event list for tests and reports.

    def _initialize(self) -> None:
        logger.info("Initializing the orchestrator stage journal")  # Log before schema creation.
        with self._connect() as connection:  # Use one transaction for schema creation.
            connection.execute(self._schema())  # Create the fine-grained stage table if absent.
        logger.debug("Initialized the orchestrator stage journal at %s", self.database_path)  # Record path.

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)  # Open SQLite with a race-safe timeout.
        connection.row_factory = sqlite3.Row  # Make results clear and column-addressable.
        try:
            yield connection  # Let the caller run one compact database operation.
            connection.commit()  # Commit only after the operation succeeds.
        finally:
            connection.close()  # Release the file handle for other workers.

    def _schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS orchestrator_stage_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_key TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """  # Store append-only checkpoints for crash recovery.

    def _insert_sql(self) -> str:
        return """
            INSERT INTO orchestrator_stage_event
            (document_key, stage, status, detail, worker_id, data_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """  # Keep all stage data in one append-only row.

    def _values(self, outcome: StageOutcome) -> tuple[object, ...]:
        return (  # Convert the event to SQLite values in the table order.
            outcome.document_key,
            outcome.stage,
            outcome.status,
            outcome.detail,
            outcome.worker_id,
            json.dumps(outcome.data, sort_keys=True),
            datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def _last_stage_sql(self) -> str:
        return """
            SELECT stage FROM orchestrator_stage_event
            WHERE document_key = ? AND status IN ('completed', 'skipped')
            ORDER BY id DESC LIMIT 1
        """  # Resume from the newest completed or skipped stage.

    def _events_sql(self) -> str:
        return """
            SELECT stage, status, detail, worker_id, data_json, created_at
            FROM orchestrator_stage_event WHERE document_key = ? ORDER BY id ASC
        """  # Return document events in the exact execution order.
