"""Long-running Juniper skill factory runner."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.juniper_skills.orchestrate.agent_backend import BackendDiscovery
from src.juniper_skills.orchestrate.journal import OrchestratorJournal
from src.juniper_skills.orchestrate.pipeline import PipelinePaths, PipelineRunner
from src.juniper_skills.orchestrate.queue import WorkLeaseStore

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class FactoryRunConfig:
    """Store command-line settings for one resumable factory run."""

    repo_root: Path  # Locate the working repository.
    database_path: Path  # Locate the inventory and journal database.
    store_path: Path  # Locate the canonical skill store.
    limit: int | None = None  # Bound the number of claimed documents for test runs.
    domain: str | None = None  # Restrict work to one domain when requested.
    dry_run: bool = False  # Avoid host installs and commits when true.
    workers: int = 1  # Control concurrent worker count.


class FactoryRunner:
    """Keep worker threads busy until the queue or limit stops the run."""

    def __init__(self, config: FactoryRunConfig) -> None:
        """Initialize the FactoryRunner instance."""
        self.config = config  # Store immutable run settings.
        self.stop_event = threading.Event()  # Let interrupts stop all workers cleanly.
        self.count_lock = threading.Lock()  # Protect the shared completed count.
        self.completed = 0  # Count successful documents across workers.
        self.handled = 0  # Count all claimed documents so one failed row cannot loop forever.
        self.backend, self.probes = BackendDiscovery(self._packet_dir()).choose()  # Select a real rewrite backend.
        self.queue = WorkLeaseStore(config.database_path, config.repo_root)  # Build the shared lease store.
        self.journal = OrchestratorJournal(config.database_path)  # Build the shared stage journal.

    def run(self) -> dict[str, object]:
        """Run the run operation."""
        logger.info("Starting the Juniper skill factory runner")  # Log before worker threads start.
        started = time.perf_counter()  # Measure throughput for the final report.
        try:
            self._run_workers()  # Start and wait for all worker loops.
        except KeyboardInterrupt:
            self.stop_event.set()  # Tell workers to stop before they claim more work.
            logger.info("Factory runner received an interrupt")  # Record clean shutdown.
        elapsed = time.perf_counter() - started  # Measure total runtime after workers stop.
        report = self._report(elapsed)  # Build a compact machine-readable result.
        logger.debug("Factory runner completed report: %s", report)  # Record the final report.
        return report  # Return progress and backend evidence.

    def _run_workers(self) -> None:
        count = max(1, self.config.workers)  # Ensure at least one worker exists.
        with ThreadPoolExecutor(max_workers=count) as executor:  # Use threads because SQLite and I/O dominate here.
            futures = [executor.submit(self._worker_loop, index) for index in range(count)]  # Start workers.
            for future in futures:  # Surface unexpected worker errors.
                future.result()  # Re-raise worker exceptions in the main thread.

    def _worker_loop(self, index: int) -> None:
        worker_id = f"worker-{index}-{uuid.uuid4().hex[:8]}"  # Give each worker a unique lease owner.
        pipeline = self._pipeline()  # Build per-thread pipeline dependencies.
        while not self.stop_event.is_set():  # Continue until the queue is empty, limit reached, or interrupted.
            if self._limit_reached():  # Stop claiming once the document limit is satisfied.
                return  # Leave the worker loop cleanly.
            item = self.queue.claim_next(worker_id, self.config.domain)  # Claim one available work item atomically.
            if item is None:  # Stop when no eligible work remains.
                return  # Leave the worker loop cleanly.
            success = pipeline.run(item, self.backend, worker_id, self.config.dry_run)  # Process the claimed document.
            self._increment(success)  # Count handled documents after the pipeline returns.

    def _pipeline(self) -> PipelineRunner:
        paths = PipelinePaths(self.config.repo_root, self._effective_store())  # Select canonical or dry-run store.
        return PipelineRunner(paths, self.queue, self.journal)  # Return a pipeline with shared durable state.

    def _effective_store(self) -> Path:
        if not self.config.dry_run:  # Use the real canonical store for production runs.
            return self.config.store_path  # Return the user-configured canonical store.
        return self.config.repo_root / "data" / "juniper_skills" / "dry_run_store"  # Keep dry-run output local.

    def _packet_dir(self) -> Path:
        return self.config.repo_root / "data" / "juniper_skills" / "rewrite_packets"  # Store external packets.

    def _limit_reached(self) -> bool:
        with self.count_lock:  # Read the shared count safely.
            return self.config.limit is not None and self.handled >= self.config.limit  # Enforce the run limit.

    def _increment(self, success: bool) -> None:
        with self.count_lock:  # Update the shared count safely.
            self.handled += 1  # Count each claimed row so failed rows do not loop.
            self.completed += 1 if success else 0  # Count only documents that reached a terminal success stage.

    def _report(self, elapsed: float) -> dict[str, object]:
        rate = 0.0 if elapsed <= 0 else self.completed / elapsed  # Compute documents per second safely.
        return {  # Return the values the CLI and final answer need.
            "completed": self.completed,
            "handled": self.handled,
            "elapsed_seconds": round(elapsed, 3),
            "documents_per_second": round(rate, 4),
            "backend": self.backend.__class__.__name__,
            "probes": [probe.__dict__ for probe in self.probes],
            "queue": self.queue.progress(),
        }
