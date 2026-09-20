"""Unit tests for the Juniper skill factory orchestrator."""

from __future__ import annotations  # Keep annotations cheap during test collection.

import sqlite3  # Build isolated queue databases for lease tests.
import threading  # Create real concurrent claim calls for the race test.
from pathlib import Path  # Use portable paths for pytest temporary folders.

from src.juniper_skills.orchestrate import OrchestratorJournal, PipelineRunner, WorkLeaseStore
from src.juniper_skills.orchestrate.pipeline import PipelinePaths
from src.juniper_skills.rewrite import CardClassMark, KnowledgeCard, RewriteBackend, RewriteResult, RewriteWorkPacket


class FakeRewriteBackend(RewriteBackend):
    """Return one safe card without calling a language model."""

    def rewrite(self, packet: RewriteWorkPacket) -> RewriteResult:
        card = KnowledgeCard(CardClassMark.INFO, "Use the documented Juniper behavior for this task.", "[DOC1 p.1]")
        return RewriteResult((card,), packet.detected_commands, tuple())  # Return enough output for pipeline tests.


class OrchestratorDatabaseFixture:
    """Create the minimum inventory tables that the orchestrator consumes."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path  # Store the pytest folder as the fake repository root.
        self.database = tmp_path / "factory.db"  # Store the queue database beside the fixture source.
        self.source = tmp_path / "source.md"  # Store one source Markdown document for tests.
        self.source.write_text(self._source_text(), encoding="utf-8")  # Write readable Markdown input.
        self._create()  # Create the inventory tables with version flags.

    def add_document(self, key: str = "doc-1", citation_only: int = 0) -> None:
        with sqlite3.connect(self.database) as connection:  # Use one transaction for the fixture row set.
            connection.execute(self._document_sql(), self._document_values(key, citation_only))  # Add document row.
            connection.execute(self._part_sql(), (f"part-{key}", key, "test", self.source.name))  # Add part row.
            connection.execute(self._work_sql(), (key, "pending", 10, "test"))  # Add pending queue row.

    def status(self, key: str = "doc-1") -> str:
        with sqlite3.connect(self.database) as connection:  # Open a short read connection.
            row = connection.execute("SELECT status FROM work_item WHERE document_key = ?", (key,)).fetchone()
        return str(row[0])  # Return the queue status for assertions.

    def _create(self) -> None:
        with sqlite3.connect(self.database) as connection:  # Create schema in one transaction.
            connection.execute(self._source_document_schema())  # Create the source document table.
            connection.execute(self._source_part_schema())  # Create the source part table.
            connection.execute(self._work_item_schema())  # Create the work queue table.

    def _source_text(self) -> str:
        return "---\ntitle: Test Guide\npages: 1\n---\n\n## Operate\n\nThe device has one port.\n"  # Keep text small.

    def _source_document_schema(self) -> str:
        return """
            CREATE TABLE source_document (
                document_key TEXT PRIMARY KEY, title TEXT, category TEXT, source_pdf TEXT, root_name TEXT,
                pages INTEGER, status TEXT, group_method TEXT, part_count INTEGER, text_chars INTEGER,
                priority INTEGER, duplicate_losers INTEGER, domain TEXT, citation_only INTEGER
            )
        """  # Include future inventory fields that the orchestrator must read.

    def _source_part_schema(self) -> str:
        return """
            CREATE TABLE source_part (
                part_key TEXT PRIMARY KEY, document_key TEXT, root_name TEXT, relative_path TEXT,
                content_hash TEXT DEFAULT 'hash', size_bytes INTEGER DEFAULT 1, text_chars INTEGER DEFAULT 1,
                has_front_matter INTEGER DEFAULT 1, is_winner INTEGER DEFAULT 1, duplicate_of TEXT DEFAULT ''
            )
        """  # Match the inventory part table columns that WorkLeaseStore reads.

    def _work_item_schema(self) -> str:
        return """
            CREATE TABLE work_item (
                document_key TEXT PRIMARY KEY, status TEXT, priority INTEGER, reason TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """  # Let WorkLeaseStore add lease columns during initialization.

    def _document_sql(self) -> str:
        return "INSERT INTO source_document VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"  # Insert all columns.

    def _document_values(self, key: str, citation_only: int) -> tuple[object, ...]:
        return (
            key,
            "Test Guide",
            "guides",
            "test.pdf",
            "test",
            1,
            "current",
            "title",
            1,
            10,
            10,
            0,
            "juniper-general-reference",
            citation_only,
        )

    def _part_sql(self) -> str:
        return "INSERT INTO source_part (part_key, document_key, root_name, relative_path) VALUES (?, ?, ?, ?)"

    def _work_sql(self) -> str:
        return "INSERT INTO work_item (document_key, status, priority, reason) VALUES (?, ?, ?, ?)"


class TestWorkLeaseStore:
    """Verify atomic queue leases and expiry behavior."""

    def test_two_workers_cannot_claim_the_same_item(self, tmp_path: Path) -> None:
        fixture = OrchestratorDatabaseFixture(tmp_path)  # Create one pending work item.
        fixture.add_document()  # Add the document after schema creation.
        store = WorkLeaseStore(fixture.database, fixture.root, lease_seconds=60)  # Initialize lease columns.
        results: list[str | None] = []  # Collect the claim result from each racing worker.
        lock = threading.Lock()  # Protect the shared result list.

        def claim(worker: str) -> None:
            item = store.claim_next(worker)  # Race for the single pending work item.
            with lock:  # Serialize writes to the result list.
                results.append(None if item is None else item.document_key)  # Store only the safe document key.

        threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]  # Build racers.
        for thread in threads:  # Start both claim attempts.
            thread.start()  # Let SQLite serialize the race.
        for thread in threads:  # Wait for both workers to finish.
            thread.join()  # Ensure assertions see both results.
        assert sorted(results, key=lambda value: value or "") == [None, "doc-1"]  # Prove one worker won.

    def test_failed_item_can_be_claimed_again(self, tmp_path: Path) -> None:
        fixture = OrchestratorDatabaseFixture(tmp_path)  # Create one pending work item.
        fixture.add_document()  # Add queue and source rows.
        store = WorkLeaseStore(fixture.database, fixture.root, lease_seconds=60)  # Initialize the queue.
        first = store.claim_next("worker-1")  # Claim the item once.
        assert first.document_key == "doc-1"  # Prove the fixture item was claimed.
        store.fail(first.document_key, "stage failed")  # Mark the row retryable.
        second = store.claim_next("worker-2")  # Claim the failed item again.
        assert second.document_key == "doc-1"  # Prove failed rows return to workers.
        assert second.document_key == first.document_key  # Prove the same document retried.


class TestOrchestratorRecovery:
    """Verify crash recovery checkpoints."""

    def test_crash_resume_reads_the_last_completed_stage(self, tmp_path: Path) -> None:
        journal = OrchestratorJournal(tmp_path / "factory.db")  # Create an isolated stage journal.
        journal.started("doc-1", "join", "worker-1")  # Simulate a crash-safe stage start.
        outcome = journal.stage_events("doc-1")  # Read the journal after the start event.
        assert outcome[-1]["status"] == "started"  # Prove the start event persisted before the stage completed.
        journal.completed(journal_event("doc-1", "join"))  # Mark the stage complete after work finishes.
        resumed = OrchestratorJournal(tmp_path / "factory.db")  # Reopen the journal like a new process.
        assert resumed.last_stage("doc-1") == "join"  # Prove restart resumes after the completed stage.


class TestPipelineFailurePaths:
    """Verify superseded skips and retryable stage failures."""

    def test_superseded_document_skips_topic_generation(self, tmp_path: Path) -> None:
        fixture = OrchestratorDatabaseFixture(tmp_path)  # Create a fake inventory database.
        fixture.add_document(citation_only=1)  # Mark the document as citation-only.
        queue = WorkLeaseStore(fixture.database, fixture.root)  # Initialize leases.
        item = queue.claim_next("worker-1")  # Claim the citation-only document.
        assert item.document_key == "doc-1"  # Prove the row was eligible for the worker.
        runner = PipelineRunner(
            PipelinePaths(fixture.root, tmp_path / "store"), queue, OrchestratorJournal(fixture.database)
        )
        assert runner.run(item, FakeRewriteBackend(), "worker-1", dry_run=True)  # Run the skip path.
        assert fixture.status() == "skipped"  # Prove the queue status is terminal.
        assert not (tmp_path / "store" / "skills").exists()  # Prove no topic package was generated.

    def test_stage_failure_returns_item_for_retry(self, tmp_path: Path) -> None:
        fixture = OrchestratorDatabaseFixture(tmp_path)  # Create a fake inventory database.
        fixture.add_document()  # Add one processable work item.
        fixture.source.unlink()  # Remove the source file to force the join stage to fail.
        queue = WorkLeaseStore(fixture.database, fixture.root)  # Initialize leases.
        item = queue.claim_next("worker-1")  # Claim the broken document.
        assert item.document_key == "doc-1"  # Prove the test claimed a row.
        runner = PipelineRunner(
            PipelinePaths(fixture.root, tmp_path / "store"), queue, OrchestratorJournal(fixture.database)
        )
        assert not runner.run(item, FakeRewriteBackend(), "worker-1", dry_run=True)  # Run the failure path.
        assert fixture.status() == "failed"  # Prove the item is retryable after failure.
        retried = queue.claim_next("worker-2")  # Claim failed work again on a later worker.
        assert retried.document_key == "doc-1"  # Prove the failed item returned to the queue.


def journal_event(document_key: str, stage: str):
    from src.juniper_skills.orchestrate.models import StageOutcome  # Import locally to keep the fixture small.

    return StageOutcome(document_key, stage, "completed", "stage complete", "worker-1")  # Return one completed event.
