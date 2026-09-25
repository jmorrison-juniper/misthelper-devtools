"""Sample module for the STE linter tests.

This module docstring is clear. It uses short sentences. Each sentence has one
idea. The parser reads this text and grades it.
"""

from __future__ import annotations


# Set the flag to true when the cache is ready.
def prepare_cache(size: int) -> bool:
    """Prepare the cache and return the ready state.

    The function makes the cache. It returns true when the cache is ready.
    """
    ready = size > 0  # The cache is ready when the size is positive.
    return ready  # Return the ready state.


# The results are collected by the service and they have been stored for a long time.
def process(items: list[str]) -> int:
    """Return the count of items."""
    return len(items)  # Count the items.
