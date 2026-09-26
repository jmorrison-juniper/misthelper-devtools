"""Live watcher for the Juniper documentation skill factory."""

from misthelper_devtools.juniper_skills.watch.corpus import (  # Export the small public API.
    CorpusWatcher,
    WatcherConfig,
    WatcherStats,
)

__all__ = ["CorpusWatcher", "WatcherConfig", "WatcherStats"]  # Keep imports stable for the factory scripts.
