"""Live watcher for the Juniper documentation skill factory."""

from src.juniper_skills.watch.corpus import CorpusWatcher, WatcherConfig, WatcherStats  # Export the small public API.

__all__ = ["CorpusWatcher", "WatcherConfig", "WatcherStats"]  # Keep imports stable for the factory scripts.
