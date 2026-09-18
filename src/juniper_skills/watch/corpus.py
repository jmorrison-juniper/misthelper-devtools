"""Polling watcher for live Juniper Markdown corpus changes."""

from __future__ import annotations  # Permit modern type hints on Python 3.13.

import hashlib  # Compare content so timestamp-only touches do not rebuild documents.
import logging  # Record every action for long-running operator visibility.
import re  # Detect converter split suffixes without a new dependency.
import sqlite3  # Update the shared SQLite queue with short transactions.
import time  # Sleep between polling scans and bound service runs.
from dataclasses import dataclass  # Keep watcher state explicit and testable.
from pathlib import Path  # Use portable path handling for every filesystem path.

from src.juniper_skills.inventory.engine import InventoryBuilder  # Reuse the locked inventory and priority rules.
from src.juniper_skills.inventory.metadata import FrontMatterParser, TextKey  # Reuse source metadata parsing.
from src.juniper_skills.inventory.models import SourceRoot  # Share the locked root record type.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class WatcherConfig:
    """Runtime settings for the corpus watcher."""

    repo_root: Path  # Store the worktree that owns the shared database.
    download_root: Path | None = None  # Permit tests to use a local corpus root.
    interval_seconds: float = 10.0  # Poll often enough without adding watchdog.
    priority_bonus: int = 100_000  # Put live changes ahead of untouched backlog items.
    max_scan_duty: float = 0.20  # Keep scan work below this fraction of service wall time.


@dataclass
class WatcherStats:
    """Measured watcher activity for one service run."""

    files_added: int = 0  # Count stable new Markdown files that entered the queue.
    files_changed: int = 0  # Count stable changed Markdown files that entered the queue.
    files_touched: int = 0  # Count timestamp-only touches that the hash check ignored.
    items_enqueued: int = 0  # Count logical documents placed back on the work queue.
    scans: int = 0  # Count scan cycles for the service progress log.
    errors: int = 0  # Count transient filesystem or database errors.
    last_scan_seconds: float = 0.0  # Store the newest full scan duration for performance reports.
    total_scan_seconds: float = 0.0  # Store all scan time so operators can see polling cost.


@dataclass
class FileSample:
    """One filesystem observation used by the quiet-period gate."""

    size_bytes: int  # Store size because partial writes change length.
    modified_ns: int  # Store nanosecond mtime because converter rewrites can be quick.
    stable_checks: int = 1  # Count repeated equal samples before a file is ready.


@dataclass
class ReadyChange:
    """A stable content change that can refresh the inventory."""

    root: SourceRoot  # Keep the source root so the database row can be found.
    path: Path  # Keep the physical Markdown file that changed.
    content_hash: str  # Store the measured hash for touch detection.
    group_key: str  # Store the part-set key used for batch readiness.
    is_new: bool  # Separate added files from rewritten files in the run report.


class PollingFileState:
    """Track Markdown file stability and content hashes across scans."""

    def __init__(self) -> None:
        """Initialize the PollingFileState instance."""
        self.samples: dict[Path, FileSample] = {}  # Remember the prior size and mtime for the quiet period.
        self.hashes: dict[Path, str] = {}  # Remember the last accepted content hash for touch detection.
        self.accepted_samples: dict[Path, tuple[int, int]] = {}  # Track accepted stat fields for touch counts.
        self.initialized = False  # Treat the first ready scan as the baseline.

    def sample(self, path: Path) -> FileSample | None:
        """Run the sample operation."""
        logger.info("Sampling Markdown file %s", path)  # Log the stat call before touching the filesystem.
        try:
            stat = path.stat()  # Read size and mtime together so the quiet-period gate is consistent.
        except OSError as error:
            logger.debug("Markdown file sample failed for %s: %s", path, error)  # Treat transient writes as not ready.
            return None
        current = FileSample(stat.st_size, stat.st_mtime_ns)  # Store only readiness fields, not file content.
        prior = self.samples.get(path)  # Read the previous sample to detect a quiet period.
        current.stable_checks = self._stable_count(prior, current)  # Require two equal samples before hashing.
        self.samples[path] = current  # Save the newest sample for the next scan.
        logger.debug("Markdown file %s has %s stable checks", path, current.stable_checks)  # Report readiness.
        return current

    def accept_hash(self, path: Path, content_hash: str) -> str:
        """Run the accept hash operation."""
        old_hash = self.hashes.get(path, "")  # Compare with the accepted hash, not the timestamp.
        self.hashes[path] = content_hash  # Store the accepted content for the next scan.
        return old_hash  # Return the old value so the caller classifies the change.

    def old_hash(self, path: Path) -> str:
        """Run the old hash operation."""
        return self.hashes.get(path, "")  # Return the accepted hash without accepting a blocked change.

    def store_hash(self, path: Path, content_hash: str) -> None:
        """Create the store hash output."""
        self.hashes[path] = content_hash  # Accept the hash only after the file or part set is ready.
        sample = self.samples.get(path, FileSample(0, 0))  # Read the stat sample accepted with this hash.
        self.accepted_samples[path] = (sample.size_bytes, sample.modified_ns)  # Store touch detection fields.

    def stat_changed_since_accept(self, path: Path) -> bool:
        """Run the stat changed since accept operation."""
        sample = self.samples.get(path, FileSample(0, 0))  # Read the latest stable stat sample.
        accepted = self.accepted_samples.get(path, (0, 0))  # Read the last stat sample accepted with a hash.
        return accepted != (sample.size_bytes, sample.modified_ns)  # True means only metadata may have changed.

    def stat_unchanged_since_accept(self, path: Path) -> bool:
        """Run the stat unchanged since accept operation."""
        sample = self.samples.get(path, FileSample(0, 0))  # Read the latest stable stat sample.
        accepted = self.accepted_samples.get(path, (-1, -1))  # Use impossible defaults before a hash exists.
        return accepted == (sample.size_bytes, sample.modified_ns)  # True means no content read is needed.

    def prune(self, paths: set[Path]) -> None:
        """Run the prune operation."""
        logger.info("Pruning watcher state for missing Markdown files")  # Log cleanup before mutating state.
        missing = set(self.samples) - paths  # Find files that disappeared since the last scan.
        for path in missing:  # Remove deleted files from in-memory readiness state.
            self.samples.pop(path, None)  # Drop stale samples so a recreated file starts fresh.
            self.hashes.pop(path, None)  # Drop stale hashes so a recreated file counts as new.
            self.accepted_samples.pop(path, None)  # Drop stale accepted samples for removed paths.
        logger.debug("Pruned %s missing Markdown files from watcher state", len(missing))  # Report cleanup count.

    def _stable_count(self, prior: FileSample | None, current: FileSample) -> int:
        if not prior:  # A first observation cannot prove the file is no longer being written.
            return 1
        unchanged = (
            prior.size_bytes == current.size_bytes and prior.modified_ns == current.modified_ns
        )  # Gate readiness.
        return prior.stable_checks + 1 if unchanged else 1  # Reset the counter when a write is still active.


class PartSetReadiness:
    """Delay inventory refresh until every related Markdown part is quiet."""

    _hash_suffix = re.compile(r"-[0-9a-f]{8,16}(?:-\d+)?$", re.IGNORECASE)  # Match converter hash suffixes.
    _number_suffix = re.compile(r"-\d+$")  # Match converter numeric part suffixes.

    def __init__(self) -> None:
        """Initialize the PartSetReadiness instance."""
        self.parser = FrontMatterParser()  # Use the same metadata parser as inventory.
        self.group_samples: dict[str, tuple[tuple[tuple[str, int, int], ...], int]] = {}  # Track quiet periods.

    def group_key(self, root: SourceRoot, path: Path, text: str) -> str:
        """Run the group key operation."""
        logger.info("Resolving watcher part-set key for %s", path)  # Log grouping before parsing metadata.
        fields = self.parser.parse_text(text)  # Read source_file and title from stable content.
        key = self._metadata_key(root, fields) or self._fallback_key(root, path)  # Prefer contract metadata.
        logger.debug("Resolved watcher part-set key with %s characters", len(key))  # Report key size, not prose.
        return key

    def ready_groups(
        self, changes: list[ReadyChange], samples: dict[Path, FileSample], paths: list[Path]
    ) -> list[ReadyChange]:
        """Run the ready groups operation."""
        logger.info("Checking quiet period for %s changed Markdown files", len(changes))  # Log group gate start.
        blocked = self._blocked_keys(changes, samples, paths)  # Find groups with a related in-progress part.
        ready = [change for change in changes if change.group_key not in blocked]  # Keep only settled groups.
        logger.debug("Quiet period gate released %s Markdown files", len(ready))  # Report released changes.
        return ready

    def _blocked_keys(self, changes: list[ReadyChange], samples: dict[Path, FileSample], paths: list[Path]) -> set[str]:
        blocked: set[str] = set()  # Store group keys that have an unstable related part.
        for change in changes:  # Check each changed document group independently.
            peers = [path for path in paths if self._could_share_set(change.path, path)]  # Find likely split peers.
            if any(samples.get(path, FileSample(0, 0)).stable_checks < 2 for path in peers):  # Wait for all peers.
                blocked.add(change.group_key)  # Delay the whole document when one part is still changing.
            if len(peers) > 1 and not self._group_is_quiet(change.group_key, peers, samples):  # Gate the whole set.
                blocked.add(change.group_key)  # Delay when the set changed since the last group check.
        return blocked

    def _group_is_quiet(self, key: str, peers: list[Path], samples: dict[Path, FileSample]) -> bool:
        signature = self._group_signature(peers, samples)  # Capture every related part size and mtime together.
        prior_signature, prior_count = self.group_samples.get(key, ((), 0))  # Read the prior whole-set sample.
        count = prior_count + 1 if prior_signature == signature else 1  # Require two equal whole-set samples.
        self.group_samples[key] = (signature, count)  # Store the group sample for the next polling cycle.
        logger.debug("Part set %s has %s quiet group checks", key, count)  # Report whole-set quiet progress.
        return count >= 2

    def _group_signature(self, peers: list[Path], samples: dict[Path, FileSample]) -> tuple[tuple[str, int, int], ...]:
        values: list[tuple[str, int, int]] = []  # Store each peer readiness field for the group signature.
        for path in peers:  # Include every related Markdown part in the quiet-period proof.
            sample = samples.get(path, FileSample(0, 0))  # Treat a missing sample as an unstable zero-sized part.
            values.append((str(path), sample.size_bytes, sample.modified_ns))  # Add fields that change during writes.
        return tuple(sorted(values))  # Sort the signature so filesystem order cannot affect readiness.

    def _metadata_key(self, root: SourceRoot, fields: dict[str, str]) -> str:
        source = fields.get("source_file", "")  # Use source_file because contract section 12 names it first.
        if source:  # Keep cross-root duplicate copies separate before inventory resolves them.
            return f"{root.name}:source:{TextKey.normalize(source)}"
        title = fields.get("title", "")  # Use title only when source_file is absent.
        return f"{root.name}:title:{TextKey.normalize(title)}" if title else ""

    def _fallback_key(self, root: SourceRoot, path: Path) -> str:
        stem = self._number_suffix.sub("", self._hash_suffix.sub("", path.stem))  # Remove split suffixes.
        parent = path.parent.as_posix()  # Keep sibling folders separate for unrelated documents.
        return f"{root.name}:stem:{parent}:{TextKey.normalize(stem)}"  # Build a stable fallback key.

    def _could_share_set(self, changed: Path, candidate: Path) -> bool:
        if changed.parent != candidate.parent:  # Split parts from this converter stay in one folder.
            return False
        changed_stem = self._number_suffix.sub("", self._hash_suffix.sub("", changed.stem))  # Normalize changed name.
        candidate_stem = self._number_suffix.sub("", self._hash_suffix.sub("", candidate.stem))  # Normalize peer name.
        return changed_stem == candidate_stem  # Treat equal normalized stems as one part set.


class QueueRefresher:
    """Run inventory and raise priority for live changes."""

    def __init__(self, config: WatcherConfig) -> None:
        """Initialize the QueueRefresher instance."""
        self.config = config  # Store configuration for database and inventory paths.
        self.db_path = config.repo_root / "data" / "juniper_skills" / "factory.db"  # Use the locked database path.

    def refresh(self, changes: list[ReadyChange]) -> int:
        """Run the refresh operation."""
        logger.info("Refreshing inventory for %s live Markdown changes", len(changes))  # Log refresh start.
        self._enable_wal()  # Enable readers and the orchestrator to continue during short writes.
        builder = InventoryBuilder(self.config.repo_root, self.config.download_root)  # Reuse inventory priority logic.
        builder.build()  # Let inventory scan, group, dedupe, and update the shared queue.
        keys = self._document_keys(changes)  # Map physical parts to logical documents after inventory writes.
        count = self._boost_queue(keys)  # Add the live-change bonus without replacing inventory priority.
        logger.debug("Inventory refresh boosted %s work items", count)  # Report queue update count.
        return count

    def _enable_wal(self) -> None:
        logger.info("Enabling WAL mode for the skill factory database")  # Log database mode change.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the data folder exists before connecting.
        with sqlite3.connect(self.db_path, timeout=5.0) as connection:  # Keep the WAL transaction short.
            connection.execute("PRAGMA journal_mode=WAL")  # Permit concurrent readers during watcher writes.
            connection.execute("PRAGMA busy_timeout=5000")  # Wait briefly instead of failing on a transient writer.
        logger.debug("WAL mode is enabled for %s", self.db_path)  # Report the database path.

    def _document_keys(self, changes: list[ReadyChange]) -> set[str]:
        logger.info("Resolving changed Markdown files to document keys")  # Log the database lookup.
        keys: set[str] = set()  # Store logical document keys so part sets rebuild as one item.
        with sqlite3.connect(self.db_path, timeout=5.0) as connection:  # Use a short read transaction.
            connection.execute("PRAGMA busy_timeout=5000")  # Wait briefly for another factory writer.
            for change in changes:  # Look up each stable changed physical part.
                keys.update(self._keys_for_change(connection, change))  # Add the logical document that owns this part.
        logger.debug("Resolved %s document keys from changed parts", len(keys))  # Report logical queue count.
        return keys

    def _keys_for_change(self, connection: sqlite3.Connection, change: ReadyChange) -> set[str]:
        relative = change.path.relative_to(change.root.path).as_posix()  # Match the inventory database key format.
        rows = connection.execute(
            "SELECT document_key FROM source_part WHERE root_name = ? AND relative_path = ?",
            (change.root.name, relative),
        ).fetchall()  # Query one physical part with the natural key used by inventory.
        return {str(row[0]) for row in rows}  # Return a set so duplicate rows cannot double count.

    def _boost_queue(self, keys: set[str]) -> int:
        logger.info("Boosting live-change queue priority for %s documents", len(keys))  # Log queue write.
        if not keys:  # Avoid opening a write transaction when no document key resolved.
            logger.debug("No live-change queue priority rows needed updates")  # Report the no-op state.
            return 0
        with sqlite3.connect(self.db_path, timeout=5.0) as connection:  # Keep the write transaction brief.
            connection.execute("PRAGMA busy_timeout=5000")  # Wait briefly if the orchestrator is writing.
            count = self._update_rows(connection, keys)  # Update queue rows with the testable priority rule.
        logger.debug("Boosted %s live-change queue rows", count)  # Report the changed row count.
        return count

    def _update_rows(self, connection: sqlite3.Connection, keys: set[str]) -> int:
        count = 0  # Count rows updated for the run report.
        for key in keys:  # Update one document at a time so each statement is small.
            cursor = connection.execute(
                """
                UPDATE work_item
                SET status = 'pending',
                    priority = priority + ?,
                    reason = 'live content changed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE document_key = ?
                """,
                (self.config.priority_bonus, key),
            )  # Add a bonus to the inventory score instead of replacing it.
            count += cursor.rowcount  # Add the changed row count for this document.
        return count


class CorpusWatcher:
    """Monitor source roots and enqueue changed Juniper documents."""

    def __init__(self, config: WatcherConfig) -> None:
        """Initialize the CorpusWatcher instance."""
        self.config = config  # Store caller-controlled runtime settings.
        self.state = PollingFileState()  # Track quiet-period and hash state.
        self.readiness = PartSetReadiness()  # Gate part sets until all parts are quiet.
        self.refresher = QueueRefresher(config)  # Coordinate with inventory and the shared queue.
        self.stats = WatcherStats()  # Accumulate measured run activity.
        self.stop_requested = False  # Allow clean shutdown from the command script.

    def run(self, duration_seconds: float | None = None) -> WatcherStats:
        """Run the run operation."""
        logger.info("Starting Juniper corpus watcher service")  # Log service start before the loop.
        deadline = time.monotonic() + duration_seconds if duration_seconds else None  # Bound proof runs when requested.
        while not self.stop_requested and not self._expired(deadline):  # Run until interrupt or proof duration ends.
            self.scan_once()  # Process one polling cycle with its own error boundary.
            time.sleep(self._sleep_seconds(deadline))  # Adapt sleep so scanning does not starve converter agents.
        logger.debug("Watcher service stopped after %s scans", self.stats.scans)  # Report service stop.
        return self.stats

    def stop(self) -> None:
        """Run the stop operation."""
        logger.info("Stopping Juniper corpus watcher service")  # Log clean shutdown request.
        self.stop_requested = True  # Ask the run loop to exit after the current scan.
        logger.debug("Watcher stop flag is set")  # Confirm the stop signal.

    def scan_once(self) -> WatcherStats:
        """Run the scan once operation."""
        logger.info("Running one Juniper corpus watcher scan")  # Log scan start.
        started = time.monotonic()  # Measure full scan cost so polling cannot hide excessive work.
        try:
            self._scan_once()  # Keep transient filesystem errors inside the long-running service.
        except OSError as error:
            self._record_error(error)  # Count and log recoverable filesystem failures.
        except sqlite3.Error as error:
            self._record_error(error)  # Count and log recoverable database failures.
        self._record_scan_time(started)  # Store scan duration before the caller reads stats.
        self.stats.scans += 1  # Count every attempted scan for progress reports.
        logger.debug("Watcher scan %s finished", self.stats.scans)  # Report scan completion.
        return self.stats

    def _scan_once(self) -> None:
        roots = InventoryBuilder(self.config.repo_root, self.config.download_root).roots  # Use inventory root order.
        paths = self._markdown_paths(roots)  # Discover current Markdown inputs under all locked roots.
        changes = self._ready_changes(roots, paths)  # Apply quiet-period and content-hash gates.
        ready = self.readiness.ready_groups(changes, self.state.samples, paths)  # Wait for related parts to settle.
        self._refresh_if_ready(ready)  # Refresh the queue only when stable changes exist.
        self.state.prune(set(paths))  # Remove state for deleted files after processing active paths.
        self._mark_initialized(paths)  # Baseline existing files before live events can enqueue work.

    def _markdown_paths(self, roots: list[SourceRoot]) -> list[Path]:
        logger.info("Discovering Markdown files across %s source roots", len(roots))  # Log discovery start.
        paths = [path for root in roots if root.path.exists() for path in root.path.rglob("*.md")]  # Find inputs.
        source_paths = sorted(path for path in paths if not path.name.startswith("_"))  # Exclude metadata reports.
        logger.debug("Discovered %s source Markdown files", len(source_paths))  # Report discovery count.
        return source_paths

    def _ready_changes(self, roots: list[SourceRoot], paths: list[Path]) -> list[ReadyChange]:
        logger.info("Checking %s Markdown files for stable content changes", len(paths))  # Log change pass start.
        root_map = self._root_map(roots)  # Build a map for fast root lookup.
        changes = [change for path in paths if (change := self._change_for_path(root_map, path))]  # Keep changes.
        logger.debug("Found %s stable Markdown content changes", len(changes))  # Report hash-based changes.
        return changes

    def _change_for_path(self, root_map: dict[Path, SourceRoot], path: Path) -> ReadyChange | None:
        sample = self.state.sample(path)  # Read size and mtime before any content read.
        if not sample or sample.stable_checks < 2:  # Require two equal observations before hashing.
            return None
        if self.state.stat_unchanged_since_accept(path):  # Skip file reads when size and mtime did not change.
            return None
        raw = path.read_bytes()  # Read only a stable file so partial writes do not enter the queue.
        content_hash = hashlib.sha256(raw).hexdigest()  # Compare exact content, not modified time.
        old_hash = self.state.old_hash(path)  # Compare against the last accepted ready hash.
        if self._is_unchanged(path, old_hash, content_hash):  # Ignore timestamp-only touches.
            return None
        root = self._root_for(root_map, path)  # Find the locked root that contains the path.
        text = raw.decode("utf-8", errors="ignore")  # Decode only after the file is stable.
        group_key = self.readiness.group_key(root, path, text)  # Resolve the document or part-set key.
        return ReadyChange(root, path, content_hash, group_key, old_hash == "")  # Return a stable work signal.

    def _is_unchanged(self, path: Path, old_hash: str, content_hash: str) -> bool:
        if not old_hash:  # First accepted hash is a baseline before initialization, or a new file after it.
            if not self.state.initialized:  # A startup baseline must not enqueue existing backlog files.
                self.state.store_hash(path, content_hash)  # Accept the stable startup hash as the baseline.
            return not self.state.initialized
        unchanged = old_hash == content_hash  # Hash equality proves a touch-only event.
        touched = unchanged and self.state.stat_changed_since_accept(path)  # Count only a changed stat with same hash.
        self.stats.files_touched += 1 if touched else 0  # Count ignored touches for the report.
        if touched:  # Accept the new stat fields so the same touch is not counted again.
            self.state.store_hash(path, content_hash)  # Keep future scans from recounting this unchanged content.
        return unchanged

    def _refresh_if_ready(self, changes: list[ReadyChange]) -> None:
        if not changes:  # Avoid inventory work when no stable content changed.
            logger.debug("No stable Markdown changes were ready for inventory refresh")  # Report no-op scan.
            return
        self._count_changes(changes)  # Update file-level counters before the database refresh.
        self.stats.items_enqueued += self.refresher.refresh(changes)  # Refresh inventory and boost changed documents.
        for change in changes:  # Accept content hashes only after the inventory refresh succeeds.
            self.state.store_hash(change.path, change.content_hash)  # Prevent the same ready change from repeating.

    def _count_changes(self, changes: list[ReadyChange]) -> None:
        self.stats.files_added += sum(1 for change in changes if change.is_new)  # Count new stable Markdown files.
        self.stats.files_changed += sum(1 for change in changes if not change.is_new)  # Count rewritten Markdown files.

    def _mark_initialized(self, paths: list[Path]) -> None:
        if self.state.initialized:  # Keep the existing baseline state after the first complete stable pass.
            return
        hashed = all(path in self.state.hashes for path in paths)  # Require a content hash for each discovered file.
        self.state.initialized = hashed  # Treat later missing hashes as new files after this baseline.
        logger.debug("Watcher baseline initialized=%s", self.state.initialized)  # Report baseline readiness.

    def _root_map(self, roots: list[SourceRoot]) -> dict[Path, SourceRoot]:
        return {root.path: root for root in roots}  # Map source root paths to their inventory metadata.

    def _root_for(self, roots: dict[Path, SourceRoot], path: Path) -> SourceRoot:
        matches = [
            root for root_path, root in roots.items() if path.is_relative_to(root_path)
        ]  # Find containing roots.
        return max(matches, key=lambda root: len(root.path.parts))  # Prefer the deepest root if paths overlap.

    def _expired(self, deadline: float | None) -> bool:
        return bool(deadline and time.monotonic() >= deadline)  # Stop duration-bound proof runs on time.

    def _sleep_seconds(self, deadline: float | None) -> float:
        duty_sleep = self._duty_sleep_seconds()  # Compute sleep that keeps scan duty under the configured limit.
        requested = max(self.config.interval_seconds, duty_sleep)  # Obey the operator interval and the duty limit.
        remaining = max(0.0, deadline - time.monotonic()) if deadline else requested  # Do not sleep past proof end.
        sleep_seconds = min(requested, remaining)  # Keep duration-bound runs responsive at the end.
        logger.debug("Watcher sleep is %.3f seconds", sleep_seconds)  # Report adaptive sleep choice.
        return sleep_seconds

    def _duty_sleep_seconds(self) -> float:
        if self.config.max_scan_duty <= 0:  # Guard invalid config so the watcher still sleeps normally.
            return self.config.interval_seconds
        active = self.stats.last_scan_seconds  # Use the measured scan cost from the just-finished scan.
        idle_ratio = (1.0 - self.config.max_scan_duty) / self.config.max_scan_duty  # Convert duty to idle time.
        return active * idle_ratio  # Return the idle time needed to hold the configured duty limit.

    def _record_scan_time(self, started: float) -> None:
        elapsed = time.monotonic() - started  # Measure wall time used by discovery, stat checks, and queue refresh.
        self.stats.last_scan_seconds = elapsed  # Store the latest value for live service reports.
        self.stats.total_scan_seconds += elapsed  # Accumulate time used by all scans in this process.
        logger.debug("Watcher scan used %.3f seconds", elapsed)  # Report the measured scan cost.

    def _record_error(self, error: Exception) -> None:
        self.stats.errors += 1  # Count transient failures without stopping the service.
        logger.error("Watcher scan recovered from transient error: %s", error)  # Log a safe, non-secret summary.
