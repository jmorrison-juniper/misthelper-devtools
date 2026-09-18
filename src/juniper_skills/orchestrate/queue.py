"""Atomic work leases for the Juniper skill factory queue."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from src.juniper_skills.orchestrate.models import WorkItem

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SourceRootResolver:
    """Resolve inventory root names to the locked source paths."""

    def __init__(self, repo_root: Path) -> None:
        """Initialize the SourceRootResolver instance."""
        self.repo_root = repo_root  # Keep repository-relative roots testable.
        self.downloads = Path.home() / "Downloads"  # Use the operator downloads folder from the contract.
        self.roots = self._roots()  # Build the root table once for all work items.

    def resolve(self, root_name: str, relative_path: str) -> Path:
        """Run the resolve operation."""
        logger.info("Resolving source part path for root %s", root_name)  # Log before path resolution.
        root = self.roots.get(root_name, self.repo_root)  # Fall back to the repository for test fixtures.
        path = root / Path(relative_path)  # Combine paths with pathlib for Windows compatibility.
        logger.debug("Resolved source part path %s", path)  # Record the resolved path.
        return path  # Return the path without reading it.

    def _roots(self) -> dict[str, Path]:
        roots = {  # Map the known inventory root names from the locked contract.
            "juniper-harvest-md": self.downloads / "juniper-harvest-md",
            "archive-markdown": self.downloads / "juniper-doc-archives" / "markdown",
            "archive-markdown2": self.downloads / "juniper-doc-archives" / "markdown2",
        }
        roots["repo-corpus"] = self.repo_root / "data" / "juniper_corpus"  # Add the repository corpus root.
        return roots  # Return a small immutable-by-convention mapping.


class WorkLeaseStore:
    """Claim work items with SQLite leases that expire after worker loss."""

    def __init__(self, database_path: Path, repo_root: Path, lease_seconds: int = 900) -> None:
        """Initialize the WorkLeaseStore instance."""
        self.database_path = database_path  # Store the queue database path from the inventory contract.
        self.repo_root = repo_root  # Store the worktree root for source path resolution.
        self.lease_seconds = lease_seconds  # Bound how long a dead worker can hold a row.
        self.resolver = SourceRootResolver(repo_root)  # Reuse root resolution for every claimed item.
        self._initialize()  # Add lease columns without rebuilding inventory data.

    def claim_next(self, worker_id: str, domain: str | None = None) -> WorkItem | None:
        """Run the claim next operation."""
        logger.info("Claiming one Juniper skill work item")  # Log before the atomic queue transaction.
        now = self._now()  # Capture one timestamp for claim and expiry comparisons.
        expiry = self._expiry(now)  # Compute the lease expiry before the write transaction.
        with self._connect() as connection:  # Hold one SQLite write transaction for the claim.
            connection.execute("BEGIN IMMEDIATE")  # Lock the queue so workers cannot claim the same row.
            row = self._candidate(connection, now, domain)  # Read the highest-priority available row.
            if row is None:  # Return cleanly when no work is available.
                logger.debug("No work item was available for claim")  # Record the empty queue result.
                return None
            self._mark_claimed(connection, row["document_key"], worker_id, expiry)  # Persist the lease atomically.
            item = self._work_item(connection, row)  # Build the public work item while the row is stable.
        logger.debug("Claimed work item %s until %s", item.document_key, expiry)  # Record the safe lease details.
        return item  # Return the claimed work item to the runner.

    def complete(self, document_key: str, status: str = "complete") -> None:
        """Run the complete operation."""
        logger.info("Marking a Juniper skill work item as %s", status)  # Log before queue completion.
        self._set_status(document_key, status, None)  # Clear the lease so the row is terminal.
        logger.debug("Marked work item %s as %s", document_key, status)  # Record the terminal status.

    def fail(self, document_key: str, detail: str) -> None:
        """Run the fail operation."""
        logger.info("Marking a Juniper skill work item as failed")  # Log before queue failure.
        self._set_status(document_key, "failed", detail)  # Keep the row retryable for the next run.
        logger.debug("Marked work item %s as failed", document_key)  # Record the retryable status.

    def release(self, document_key: str) -> None:
        """Run the release operation."""
        logger.info("Releasing a Juniper skill work item lease")  # Log before an interrupt-safe release.
        self._set_status(document_key, "pending", "lease released")  # Return interrupted work to the queue.
        logger.debug("Released work item %s", document_key)  # Record the released row.

    def progress(self) -> dict[str, int]:
        """Run the progress operation."""
        logger.info("Reading Juniper skill queue progress")  # Log before the status aggregation.
        with self._connect() as connection:  # Use one read connection for the status summary.
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM work_item GROUP BY status").fetchall()
        progress = {str(row["status"]): int(row["count"]) for row in rows}  # Convert rows into a compact map.
        logger.debug("Read queue progress for %d states", len(progress))  # Record the number of states.
        return progress  # Return counts for CLI progress reports.

    def _initialize(self) -> None:
        logger.info("Initializing Juniper skill work leases")  # Log before schema extension.
        self.database_path.parent.mkdir(parents=True, exist_ok=True)  # Create the data directory for tests.
        with self._connect() as connection:  # Use one schema transaction for all required columns.
            self._ensure_work_table(connection)  # Let isolated tests create only the queue database.
            self._ensure_columns(connection)  # Add lease metadata when inventory did not create it.
        logger.debug("Initialized Juniper skill work leases at %s", self.database_path)  # Record the database path.

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)  # Open SQLite with a busy timeout for races.
        connection.row_factory = sqlite3.Row  # Return name-addressable rows for clear code.
        try:
            connection.execute("PRAGMA busy_timeout = 30000")  # Wait for a competing worker instead of failing.
            yield connection  # Give the caller a short-lived connection.
            connection.commit()  # Persist the transaction before Windows releases the handle.
        except Exception:
            connection.rollback()  # Roll back a partial claim or status update.
            raise  # Preserve the original failure for the caller.
        finally:
            connection.close()  # Release the database handle for other workers.

    def _ensure_work_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS work_item (document_key TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "priority INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )  # Create the queue in unit tests without running inventory.
        connection.execute(
            "CREATE TABLE IF NOT EXISTS source_document (document_key TEXT PRIMARY KEY, title TEXT NOT NULL, "
            "category TEXT NOT NULL, source_pdf TEXT NOT NULL, root_name TEXT NOT NULL, pages INTEGER NOT NULL, "
            "status TEXT NOT NULL, group_method TEXT NOT NULL, part_count INTEGER NOT NULL, "
            "text_chars INTEGER NOT NULL, priority INTEGER NOT NULL, duplicate_losers INTEGER NOT NULL)"
        )  # Create the document table in unit tests.
        connection.execute(
            "CREATE TABLE IF NOT EXISTS source_part (part_key TEXT PRIMARY KEY, document_key TEXT NOT NULL, "
            "root_name TEXT NOT NULL, relative_path TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, text_chars INTEGER NOT NULL, has_front_matter INTEGER NOT NULL, "
            "is_winner INTEGER NOT NULL, duplicate_of TEXT NOT NULL)"
        )  # Create the part table in unit tests.

    def _ensure_columns(self, connection: sqlite3.Connection) -> None:
        columns = self._columns(connection, "work_item")  # Read existing queue columns before ALTER statements.
        additions = (  # Define full trusted ALTER statements for older inventory databases.
            ("lease_owner", "ALTER TABLE work_item ADD COLUMN lease_owner TEXT"),
            ("lease_expires_at", "ALTER TABLE work_item ADD COLUMN lease_expires_at TEXT"),
            ("attempt_count", "ALTER TABLE work_item ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"),
            ("last_error", "ALTER TABLE work_item ADD COLUMN last_error TEXT"),
        )
        for name, statement in additions:  # Add only missing columns for backward compatibility.
            if name not in columns:  # Avoid duplicate-column errors on repeated runner starts.
                connection.execute(statement)  # Extend the queue with trusted schema text only.

    def _candidate(self, connection: sqlite3.Connection, now: str, domain: str | None) -> sqlite3.Row | None:
        where = "(w.status = 'pending' OR w.status = 'failed' OR (w.status = 'leased' AND w.lease_expires_at <= ?))"
        params: list[object] = [now]  # Bind the timestamp so expired leases return to the queue.
        if domain is not None:  # Apply a domain filter only when the caller requested it.
            where = f"{where} AND {self._domain_expr(connection)} = ?"  # Use inventory domain if it exists.
            params.append(domain)  # Bind the requested domain.
        sql = self._candidate_sql(connection, where)  # Build the query after optional domain filtering.
        row = connection.execute(sql, params).fetchone()  # Return the highest-priority available row.
        return cast(sqlite3.Row | None, row)  # Preserve sqlite row typing for strict mypy.

    def _candidate_sql(self, connection: sqlite3.Connection, where: str) -> str:
        domain_expr = self._domain_expr(connection)  # Use the inventory domain column only when it exists.
        citation_expr = self._citation_expr(connection)  # Use version columns only when the inventory adds them.
        return (
            "SELECT w.document_key, d.title, d.category, d.source_pdf, d.pages, w.priority, "
            f"{domain_expr} AS domain, {citation_expr} AS citation_only "
            "FROM work_item w JOIN source_document d ON d.document_key = w.document_key "
            f"WHERE {where} ORDER BY w.priority DESC, w.updated_at ASC, w.document_key ASC LIMIT 1"  # nosec B608 - Trusted fragments and bound parameters build this query.
        )  # Keep queue selection deterministic and fair.

    def _mark_claimed(self, connection: sqlite3.Connection, document_key: str, worker_id: str, expiry: str) -> None:
        connection.execute(
            "UPDATE work_item SET status = 'leased', lease_owner = ?, lease_expires_at = ?, "
            "attempt_count = attempt_count + 1, updated_at = CURRENT_TIMESTAMP WHERE document_key = ?",
            (worker_id, expiry, document_key),
        )  # Mark the row leased in the same transaction that selected it.

    def _work_item(self, connection: sqlite3.Connection, row: sqlite3.Row) -> WorkItem:
        paths = self._part_paths(connection, str(row["document_key"]))  # Read the winner part paths for joining.
        return WorkItem(
            str(row["document_key"]),
            str(row["title"]),
            str(row["category"]),
            str(row["source_pdf"]),
            int(row["pages"]),
            int(row["priority"]),
            str(row["domain"]),
            tuple(paths),
            bool(row["citation_only"]),
        )  # Return an immutable work item for the pipeline.

    def _part_paths(self, connection: sqlite3.Connection, document_key: str) -> list[Path]:
        rows = connection.execute(
            "SELECT root_name, relative_path FROM source_part WHERE document_key = ? AND is_winner = 1 "
            "ORDER BY relative_path ASC",
            (document_key,),
        ).fetchall()  # Read only active parts for this logical document.
        return [
            self.resolver.resolve(str(row["root_name"]), str(row["relative_path"])) for row in rows
        ]  # Resolve paths.

    def _set_status(self, document_key: str, status: str, detail: str | None) -> None:
        with self._connect() as connection:  # Keep each status update atomic.
            connection.execute(
                "UPDATE work_item SET status = ?, lease_owner = NULL, lease_expires_at = NULL, "
                "last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE document_key = ?",
                (status, detail, document_key),
            )  # Clear the lease whenever the worker stops owning the row.

    def _domain_expr(self, connection: sqlite3.Connection) -> str:
        if "domain" not in self._columns(connection, "source_document"):  # A guessed domain misroutes artifacts.
            raise RuntimeError("source_document.domain is missing; run the taxonomy classifier first")
        return "d.domain"  # Use only the persisted classifier output.

    def _citation_expr(self, connection: sqlite3.Connection) -> str:
        columns = self._columns(connection, "source_document")  # Read columns before building safe SQL.
        checks = self._citation_checks(columns)  # Build only expressions that reference present columns.
        if not checks:  # Inventory has not added version resolver flags yet.
            return "0"  # Treat rows as current until the resolver columns exist.
        return "CASE WHEN " + " OR ".join(checks) + " THEN 1 ELSE 0 END"  # Return safe SQL.

    def _citation_checks(self, columns: set[str]) -> list[str]:
        checks: list[str] = []  # Collect present-column predicates only.
        if "citation_only" in columns:  # Honor the contract name for citation-only rows.
            checks.append("COALESCE(d.citation_only, 0) = 1")  # Add citation-only predicate.
        if "superseded" in columns:  # Honor the inventory dependency name in the user request.
            checks.append("COALESCE(d.superseded, 0) = 1")  # Add superseded predicate.
        if "current" in columns:  # Honor the current-version flag when it exists.
            checks.append("COALESCE(d.current, 1) = 0")  # Add not-current predicate.
        return checks  # Return safe SQL fragments.

    def _columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}  # Read columns.

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")  # Use UTC so workers compare timestamps safely.

    def _expiry(self, now: str) -> str:
        started = datetime.fromisoformat(now)  # Reuse the claim timestamp for a stable lease window.
        return (started + timedelta(seconds=self.lease_seconds)).isoformat(timespec="seconds")  # Return expiry text.
