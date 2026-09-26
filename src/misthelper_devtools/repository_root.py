"""Find the repository a tool must read.

Every tool in this package reads the source of another repository. A tool that
derives its target from its own ``__file__`` works only while the tool sits
inside that repository. After the tool moves into an installed package, that
derivation points at the site-packages directory, and every read fails.

This module gives one answer for every tool. The caller can name the root. If
the caller names no root, the search starts at the current directory and walks
up to the first directory that holds a ``.git`` entry.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import logging  # Records each resolution, per the action logging rule.
from pathlib import Path  # Holds every path, so no code hardcodes a separator.

logger = logging.getLogger(__name__)  # Use a module logger so a caller can identify this log source.


def resolve_repository_root(explicit: Path | str | None = None) -> Path:
    """Return the repository root a tool must read.

    Args:
        explicit: A root named by the caller. A test passes its own checkout
            here, so the result never depends on the working directory.

    Returns:
        The named root, the nearest parent of the working directory that holds
        a ``.git`` entry, or the working directory when no parent holds one.
    """
    if explicit is not None:  # A named root always wins, because the caller knows its own checkout.
        resolved = Path(explicit).resolve()  # Resolve once, so every later join is absolute.
        logger.debug("Using the repository root named by the caller: %s", resolved)  # Log the choice.
        return resolved  # The caller reads this directory.
    start = Path.cwd().resolve()  # The search starts where the operator ran the tool.
    logger.info("Searching for a repository root above %s", start)  # Log before the search.
    for candidate in (start, *start.parents):  # Check the directory itself before each parent.
        if (candidate / ".git").exists():  # A .git file or directory marks a checkout or a worktree.
            logger.debug("Found the repository root at %s", candidate)  # Log after the search.
            return candidate  # The caller reads this directory.
    logger.warning("No parent of %s holds a .git entry, so the tool reads the working directory", start)
    return start  # A directory with no checkout still lets a relative path read work.
