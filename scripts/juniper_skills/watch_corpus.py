"""Run the Juniper corpus watcher service."""

from __future__ import annotations  # Permit modern type hints on Python 3.13.

import argparse  # Parse service options without an extra dependency.
import logging  # Configure long-running service logs.
import sys  # Add the repository root when this file runs as a direct script.
from pathlib import Path  # Resolve repository paths safely on Windows.

sys.path.insert(0, str(Path.cwd()))  # Let direct script execution import the project package.

from src.juniper_skills.watch import CorpusWatcher, WatcherConfig  # Run the watcher through its public API.


class WatchCorpusCommand:
    """Command object for the long-running corpus watcher."""

    def __init__(self) -> None:
        self.parser = self._parser()  # Build the parser once so tests can inspect behavior if needed.

    def run(self) -> None:
        args = self.parser.parse_args()  # Read command-line arguments for this service run.
        logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")  # Configure logs.
        config = WatcherConfig(
            args.repo_root, args.download_root, args.interval, args.priority_bonus, args.max_scan_duty
        )  # Build config.
        watcher = CorpusWatcher(config)  # Create the long-running service object.
        logging.info("Watcher uses polling because watchdog is not a project dependency")  # State dependency decision.
        try:
            stats = watcher.run(args.duration)  # Run until interrupted or until the proof duration ends.
        except KeyboardInterrupt:
            watcher.stop()  # Request a clean shutdown after Ctrl+C.
            stats = watcher.stats  # Preserve measured activity for the final report.
        self._print_stats(stats)  # Print a compact report for issue evidence.

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description="Watch Juniper Markdown roots and refresh the skill queue."
        )  # CLI.
        parser.add_argument("--repo-root", type=Path, default=Path.cwd())  # Let tests and worktrees set the root.
        parser.add_argument("--download-root", type=Path, default=None)  # Default to the user Downloads folder.
        parser.add_argument("--interval", type=float, default=10.0)  # Set polling cadence for long runs.
        parser.add_argument("--duration", type=float, default=None)  # Bound proof runs without another process manager.
        parser.add_argument("--priority-bonus", type=int, default=100_000)  # Keep the live priority rule configurable.
        parser.add_argument("--max-scan-duty", type=float, default=0.20)  # Keep polling from consuming the host.
        parser.add_argument("--log-level", default="INFO")  # Permit DEBUG when investigating a live converter.
        return parser

    def _print_stats(self, stats: object) -> None:
        print(f"files_added={stats.files_added}")  # Report new files for the issue proof.
        print(f"files_changed={stats.files_changed}")  # Report rewritten files for the issue proof.
        print(f"files_touched={stats.files_touched}")  # Report ignored timestamp-only touches for the issue proof.
        print(f"items_enqueued={stats.items_enqueued}")  # Report logical documents queued for rebuild.
        print(f"scans={stats.scans}")  # Report the number of scan cycles observed.
        print(f"errors={stats.errors}")  # Report recovered transient errors.
        print(f"last_scan_seconds={stats.last_scan_seconds:.3f}")  # Report the newest scan cost.
        print(f"total_scan_seconds={stats.total_scan_seconds:.3f}")  # Report the total service scan cost.


WatchCorpusCommand().run()  # Start the class-based command without a standalone wrapper.
