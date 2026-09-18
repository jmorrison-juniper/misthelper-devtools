"""Read persisted Juniper domain assignments."""

from __future__ import annotations  # Keep annotations cheap during import.

import logging  # Trace lookup calls for the factory consumers.
import sqlite3  # Read the existing factory database directly.
from pathlib import Path  # Store database paths portably.

from .models import DomainAssignment  # Return the same assignment contract that classification writes.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class DomainLookup:
    """Read domains from SourceDocument without reclassifying content."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the DomainLookup instance."""
        self.database_path = database_path  # Keep the database path for repeated consumer lookups.

    def get(self, document_key: str) -> DomainAssignment:
        """Run the get operation."""
        logger.info("Reading domain assignment for %s", document_key)  # Trace the lookup before opening the database.
        with sqlite3.connect(self.database_path) as connection:  # Keep the read transaction short for shared workers.
            row = connection.execute(self._query(), (document_key,)).fetchone()  # Read only the requested source row.
        if not row:  # Fail loudly so consumers do not invent a domain.
            raise KeyError(document_key)  # Signal that the source document does not exist.
        assignment = self._row_to_assignment(document_key, row)  # Convert the database row to the public contract.
        logger.debug("Read domain %s for %s", assignment.domain, document_key)  # Summarize the lookup result.
        return assignment

    def all(self) -> dict[str, DomainAssignment]:
        """Run the all operation."""
        logger.info("Reading all domain assignments")  # Trace bulk lookup for package builders.
        with sqlite3.connect(self.database_path) as connection:  # Use one read transaction for consistency.
            rows = connection.execute(self._all_query()).fetchall()  # Read persisted assignments only.
        assignments = {str(row[0]): self._row_to_assignment(str(row[0]), row[1:]) for row in rows}  # Build a key map.
        logger.debug("Read %s domain assignments", len(assignments))  # Report how many rows consumers can use.
        return assignments

    def _query(self) -> str:
        return (  # Keep the SQL text in one place for tests and audits.
            "SELECT domain, domain_confidence, domain_signal FROM source_document "  # Read the source-of-truth columns.
            "WHERE document_key = ? AND domain != ''"  # Require a completed classification run.
        )

    def _all_query(self) -> str:
        return (  # Keep bulk lookup SQL separate from the single-row query.
            "SELECT document_key, domain, domain_confidence, domain_signal FROM source_document "  # Read all domains.
            "WHERE domain != '' ORDER BY document_key"  # Return a deterministic map for consumers.
        )

    def _row_to_assignment(self, document_key: str, row: tuple[object, ...]) -> DomainAssignment:
        confidence = float(str(row[1]))  # Convert SQLite values through text for strict typing.
        signal = str(row[2])  # Preserve the stored signal so consumers can trace the decision.
        low_confidence = confidence < 0.70  # Recompute the public flag from the persisted score.
        return DomainAssignment(document_key, str(row[0]), confidence, signal, str(row[0]), low_confidence)
