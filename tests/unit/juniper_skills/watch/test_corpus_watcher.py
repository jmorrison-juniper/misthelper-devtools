"""Tests for the live Juniper corpus watcher."""

import gc  # Release SQLite objects before Windows cleanup removes the proof root.
import shutil  # Copy real corpus files into isolated test roots.
import time  # Pause during slow-write simulation so mtime values can change.
import uuid  # Make each controlled proof root unique inside the repository data folder.
from pathlib import Path  # Use Path to create isolated corpus roots for each test.

import pytest  # Skip the real-corpus proof when the local corpus is absent.

from src.juniper_skills.watch import CorpusWatcher, WatcherConfig  # Exercise the production watcher.


class TestCorpusWatcher:
    """Verify quiet-period, hash, part-set, and shutdown behavior."""

    def test_waits_for_partial_write_before_enqueue(self, tmp_path: Path) -> None:
        repo_root = self._repo_root(tmp_path)  # Create a local repository root for the shared database.
        download_root = self._download_root(tmp_path)  # Create local source roots instead of the real corpus.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher with test roots.
        watcher.scan_once()  # Establish an empty baseline before the new file arrives.
        path = download_root / "juniper-harvest-md" / "guides" / "partial.md"  # Select one source Markdown path.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create the document folder for the simulated converter.
        path.write_text("---\nsource_file: guides/partial.pdf\n", encoding="utf-8")  # Write an incomplete document.
        watcher.scan_once()  # Observe the partial file before it is stable.
        path.write_text(self._document("Partial", "guides/partial.pdf"), encoding="utf-8")  # Finish the conversion.
        watcher.scan_once()  # Observe the changed size and reset the quiet-period counter.
        watcher.scan_once()  # Accept the second unchanged sample and enqueue the document.
        assert watcher.stats.files_added == 1  # A new stable file must count as one added file.
        assert watcher.stats.items_enqueued == 1  # The watcher must enqueue the logical document once.

    def test_ignores_touch_without_content_change(self, tmp_path: Path) -> None:
        repo_root = self._repo_root(tmp_path)  # Create a local repository root for the shared database.
        download_root = self._download_root(tmp_path)  # Create local source roots instead of the real corpus.
        path = download_root / "juniper-harvest-md" / "guides" / "touch.md"  # Select one source Markdown path.
        path.parent.mkdir(parents=True, exist_ok=True)  # Create the document folder for the baseline file.
        path.write_text(self._document("Touch", "guides/touch.pdf"), encoding="utf-8")  # Write stable content.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher after the file exists.
        watcher.scan_once()  # Take the first sample, which is not ready yet.
        watcher.scan_once()  # Take the baseline hash without queueing existing content.
        path.touch()  # Change only the timestamp to simulate OneDrive or converter churn.
        watcher.scan_once()  # Observe the timestamp change and reset stability.
        watcher.scan_once()  # Accept the stable touched file and compare the hash.
        assert watcher.stats.files_touched == 1  # The watcher must count the ignored touch.
        assert watcher.stats.items_enqueued == 0  # The queue must not rebuild when content is unchanged.

    def test_part_change_rebuilds_whole_part_set_after_peers_settle(self, tmp_path: Path) -> None:
        repo_root = self._repo_root(tmp_path)  # Create a local repository root for the shared database.
        download_root = self._download_root(tmp_path)  # Create local source roots instead of the real corpus.
        first = download_root / "juniper-harvest-md" / "cli-reference" / "cli-reference.md"  # Create part one.
        second = download_root / "juniper-harvest-md" / "cli-reference" / "cli-reference-2.md"  # Create part two.
        first.parent.mkdir(parents=True, exist_ok=True)  # Create the split document folder.
        first.write_text(self._document("CLI", "cli-reference/cli-reference.pdf"), encoding="utf-8")  # Write part one.
        second.write_text(self._document("CLI", "cli-reference/cli-reference.pdf"), encoding="utf-8")  # Write part two.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher with the completed part set.
        watcher.scan_once()  # Take the first baseline sample for both parts.
        watcher.scan_once()  # Hash both parts without queueing the existing backlog.
        first.write_text(self._document("CLI", "cli-reference/cli-reference.pdf", "new"), encoding="utf-8")  # Change.
        second.write_text(
            "---\nsource_file: cli-reference/cli-reference.pdf\n", encoding="utf-8"
        )  # Start part two rewrite.
        watcher.scan_once()  # Observe both active writes and enqueue nothing.
        watcher.scan_once()  # Part one is stable, but part two now has a stable partial value.
        second.write_text(self._document("CLI", "cli-reference/cli-reference.pdf", "done"), encoding="utf-8")  # Finish.
        watcher.scan_once()  # Observe the completed part two rewrite and reset its quiet-period counter.
        watcher.scan_once()  # Accept the file quiet period and start the whole-set quiet check.
        watcher.scan_once()  # Accept the whole-set quiet period and enqueue the logical document.
        rows = self._work_rows(repo_root)  # Read the shared queue rows that inventory and watcher wrote.
        assert len(rows) == 1  # The whole split PDF must rebuild as one logical document.
        assert watcher.stats.files_changed == 2  # Both rewritten parts became part of the same refresh.
        assert rows[0][1] == "live content changed"  # The queue reason must identify the live change.

    def test_clean_shutdown_returns_without_scanning(self, tmp_path: Path) -> None:
        repo_root = self._repo_root(tmp_path)  # Create a local repository root for the shared database.
        download_root = self._download_root(tmp_path)  # Create local source roots instead of the real corpus.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher service object.
        watcher.stop()  # Request shutdown before the service loop starts.
        stats = watcher.run(1.0)  # Run with a bound duration so the test cannot hang.
        assert stats.scans == 0  # The service must stop cleanly without another scan.

    def test_real_file_controlled_proof(self) -> None:
        corpus = self._real_corpus()  # Locate the real converted Markdown corpus on this workstation.
        self._require_real_file(corpus / "guides" / "junos-beginners-guide.md")  # Require one real guide.
        self._require_real_file(corpus / "uncategorized" / "qfx__switching" / "ptx10008" / "part-001.md")  # Require.
        proof_root = self._proof_root()  # Create an isolated root under this repository, not the real corpus.
        try:
            counts = self._controlled_counts(proof_root, corpus)  # Run five proofs against copied real files.
        finally:
            self._remove_tree(proof_root)  # Remove the controlled proof root after the proof finishes.
        print(f"watcher_controlled_counts={counts}")  # Report measured counts for issue and local review evidence.
        assert counts["new"] == (1, 1)  # A copied real file that arrives after baseline must enqueue once.
        assert counts["rewrite"] == (1, 1)  # A rewritten real file must enqueue one logical document.
        assert counts["touch"] == (1, 0)  # A touched real file must be counted and not enqueued.
        assert counts["slow"] == (0, 1, 1)  # A slow write must wait, then enqueue once after close.
        assert counts["part_set"] == (1, 1)  # One changed part set must enqueue one logical document.

    def _repo_root(self, tmp_path: Path) -> Path:
        repo_root = tmp_path / "repo"  # Keep the test database away from the real repository.
        repo_root.mkdir(parents=True, exist_ok=True)  # Create the repository root for inventory output.
        return repo_root  # Return the root used by WatcherConfig.

    def _download_root(self, tmp_path: Path) -> Path:
        download_root = tmp_path / "downloads"  # Keep source roots away from the real corpus.
        (download_root / "juniper-doc-archives" / "markdown").mkdir(parents=True, exist_ok=True)  # Create root one.
        (download_root / "juniper-doc-archives" / "markdown2").mkdir(parents=True, exist_ok=True)  # Create root two.
        (download_root / "juniper-harvest-md").mkdir(parents=True, exist_ok=True)  # Create the live harvest root.
        return download_root  # Return the root used by InventoryBuilder.

    def _watcher(self, repo_root: Path, download_root: Path) -> CorpusWatcher:
        config = WatcherConfig(repo_root, download_root, 0.01, 100_000)  # Use fast polling for unit tests.
        return CorpusWatcher(config)  # Return a real watcher with isolated roots.

    def _real_corpus(self) -> Path:
        return Path.home() / "Downloads" / "juniper-harvest-md"  # Use the real source root only as read-only input.

    def _require_real_file(self, path: Path) -> None:
        if not path.exists():  # Skip when this workstation does not hold the requested real corpus file.
            pytest.skip(f"Real corpus proof needs {path}")  # Report the missing input instead of writing real data.

    def _proof_root(self) -> Path:
        root = Path.cwd() / "data" / "juniper_skills" / "watch_proof_runs" / uuid.uuid4().hex  # Stay in the repo.
        root.mkdir(parents=True, exist_ok=True)  # Create the controlled proof root before copying real files.
        return root  # Return the root so the test can remove it in a finally block.

    def _controlled_counts(self, proof_root: Path, corpus: Path) -> dict[str, tuple[int, ...]]:
        return {
            "new": self._prove_real_new_file(proof_root, corpus),
            "rewrite": self._prove_real_rewrite(proof_root, corpus),
            "touch": self._prove_real_touch(proof_root, corpus),
            "slow": self._prove_real_slow_write(proof_root, corpus),
            "part_set": self._prove_real_part_set(proof_root, corpus),
        }  # Run the five deterministic proofs against copied real files.

    def _case_paths(self, proof_root: Path, name: str) -> tuple[Path, Path]:
        case_root = proof_root / name  # Isolate each proof so queue counts cannot leak across cases.
        repo_root = self._repo_root(case_root)  # Create a separate repository root for this proof case.
        download_root = self._download_root(case_root)  # Create a separate download root for this proof case.
        return repo_root, download_root  # Return both roots for watcher configuration and file copies.

    def _copy_real(self, corpus: Path, download_root: Path, relative: Path) -> Path:
        source = corpus / relative  # Read from the real corpus without modifying it.
        target = download_root / "juniper-harvest-md" / relative  # Write the controlled copy under tmp_path.
        target.parent.mkdir(parents=True, exist_ok=True)  # Create the controlled destination folder.
        shutil.copy2(source, target)  # Preserve real front matter, page markers, and metadata.
        return target  # Return the controlled copy path for later changes.

    def _baseline(self, watcher: CorpusWatcher) -> None:
        watcher.scan_once()  # Take size and mtime samples for the controlled corpus.
        watcher.scan_once()  # Hash stable files without queueing the existing backlog.

    def _prove_real_new_file(self, proof_root: Path, corpus: Path) -> tuple[int, int]:
        repo_root, download_root = self._case_paths(proof_root, "real-new")  # Create isolated proof roots.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher for this controlled case.
        watcher.scan_once()  # Establish an empty baseline.
        relative = Path("guides") / "junos-beginners-guide.md"  # Use a real guide with front matter and pages.
        self._copy_real(corpus, download_root, relative)  # Copy a real file after the baseline.
        watcher.scan_once()  # Observe the new file before the quiet period completes.
        watcher.scan_once()  # Accept the stable real file and enqueue it.
        return watcher.stats.files_added, watcher.stats.items_enqueued  # Report measured new-file counts.

    def _prove_real_rewrite(self, proof_root: Path, corpus: Path) -> tuple[int, int]:
        repo_root, download_root = self._case_paths(proof_root, "real-rewrite")  # Create isolated proof roots.
        path = self._copy_real(corpus, download_root, Path("guides") / "junos-beginners-guide.md")  # Copy input.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher after the initial file exists.
        self._baseline(watcher)  # Establish a stable hash baseline.
        path.write_text(
            path.read_text(encoding="utf-8") + "\n<!-- watcher rewrite proof -->\n", encoding="utf-8"
        )  # Rewrite.
        watcher.scan_once()  # Observe the changed file before it is quiet.
        watcher.scan_once()  # Accept the stable changed content and enqueue it.
        return watcher.stats.files_changed, watcher.stats.items_enqueued  # Report measured rewrite counts.

    def _prove_real_touch(self, proof_root: Path, corpus: Path) -> tuple[int, int]:
        repo_root, download_root = self._case_paths(proof_root, "real-touch")  # Create isolated proof roots.
        path = self._copy_real(corpus, download_root, Path("guides") / "junos-beginners-guide.md")  # Copy input.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher after the initial file exists.
        self._baseline(watcher)  # Establish a stable hash baseline.
        path.touch()  # Change the timestamp without changing content.
        watcher.scan_once()  # Observe the touched file before it is quiet.
        watcher.scan_once()  # Accept the stable touched file and compare the hash.
        return watcher.stats.files_touched, watcher.stats.items_enqueued  # Report measured touch counts.

    def _prove_real_slow_write(self, proof_root: Path, corpus: Path) -> tuple[int, int, int]:
        repo_root, download_root = self._case_paths(proof_root, "real-slow")  # Create isolated proof roots.
        source = (corpus / "guides" / "junos-beginners-guide.md").read_text(encoding="utf-8")  # Read real content.
        target = download_root / "juniper-harvest-md" / "guides" / "slow.md"  # Write only into the test root.
        target.parent.mkdir(parents=True, exist_ok=True)  # Create the controlled destination folder.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher for an empty controlled root.
        watcher.scan_once()  # Establish an empty baseline.
        with target.open("w", encoding="utf-8") as handle:  # Keep the file open while simulating converter output.
            handle.write(source[: len(source) // 3])  # Write the first chunk of a real Markdown file.
            handle.flush()  # Flush the partial content so the watcher can see it.
            watcher.scan_once()  # Prove the partial file does not enqueue.
            before_close = watcher.stats.items_enqueued  # Capture the queue count before the write completes.
            time.sleep(0.02)  # Pause so the file mtime can change between chunks.
            handle.write(source[len(source) // 3 :])  # Finish the real Markdown content.
        watcher.scan_once()  # Observe the completed write before the quiet period completes.
        watcher.scan_once()  # Accept the stable complete file and enqueue it.
        return before_close, watcher.stats.files_added, watcher.stats.items_enqueued  # Report measured slow counts.

    def _prove_real_part_set(self, proof_root: Path, corpus: Path) -> tuple[int, int]:
        repo_root, download_root = self._case_paths(proof_root, "real-part-set")  # Create isolated proof roots.
        folder = Path("uncategorized") / "qfx__switching" / "ptx10008"  # Use the smaller real PTX10008 part set.
        for name in ("part-001.md", "part-002.md", "part-003.md"):  # Copy enough real parts to prove grouping.
            self._copy_real(corpus, download_root, folder / name)  # Preserve real shared source_file and part fields.
        changed = download_root / "juniper-harvest-md" / folder / "part-001.md"  # Change only one real part copy.
        watcher = self._watcher(repo_root, download_root)  # Build the watcher after the part set exists.
        self._baseline(watcher)  # Establish a stable hash baseline for all copied parts.
        changed.write_text(
            changed.read_text(encoding="utf-8") + "\n<!-- watcher part proof -->\n", encoding="utf-8"
        )  # Rewrite.
        watcher.scan_once()  # Observe the changed part before file quiet completes.
        watcher.scan_once()  # Accept file quiet and start whole-set quiet.
        watcher.scan_once()  # Accept whole-set quiet and enqueue one logical document.
        return watcher.stats.files_changed, watcher.stats.items_enqueued  # Report measured part-set counts.

    def _remove_tree(self, root: Path) -> None:
        for attempt in range(5):  # Retry because Windows can hold SQLite files briefly after close.
            gc.collect()  # Release any final SQLite objects before removal.
            try:
                shutil.rmtree(root)  # Remove every copied real file and controlled database.
                return
            except PermissionError:
                if attempt == 4:  # Fail the proof if cleanup cannot remove its controlled root.
                    raise
                time.sleep(0.2)  # Wait briefly for the file handle to close.

    def _document(self, title: str, source_file: str, marker: str = "base") -> str:
        return "\n".join(
            [
                "---",
                f"title: {title}",
                f"source_file: {source_file}",
                "pages: 2",
                "---",
                f"body {marker}",
                "",
            ]
        )  # Build a minimal converter-like Markdown document.

    def _work_rows(self, repo_root: Path) -> list[tuple[str, str]]:
        import sqlite3  # Import locally because only this assertion reads the database.

        db_path = repo_root / "data" / "juniper_skills" / "factory.db"  # Use the contract database path.
        with sqlite3.connect(db_path) as connection:  # Open a short read transaction for assertions.
            rows = connection.execute("SELECT document_key, reason FROM work_item").fetchall()  # Read queue state.
        return [(str(row[0]), str(row[1])) for row in rows]  # Normalize sqlite rows for simple assertions.
