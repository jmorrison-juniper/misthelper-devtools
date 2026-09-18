"""Command entry point for the Juniper corpus inventory."""

from __future__ import annotations  # Permit modern type hints on Python 3.13.

import logging  # Configure command logging for operators.
from pathlib import Path  # Resolve the current worktree as a Path.

from src.juniper_skills.inventory.engine import InventoryBuilder  # Run the inventory build workflow.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class InventoryCommand:
    """Run the inventory builder from the command line."""

    def run(self) -> None:
        """Run the run operation."""
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")  # Configure clear CLI logging.
        repo_root = Path.cwd()  # Use the current worktree as the repository root.
        logger.info("Starting inventory command in %s", repo_root)  # Log command start and worktree.
        result = InventoryBuilder(repo_root).build()  # Build the database and report from the configured roots.
        logger.info("Inventory command finished with %s documents", result.logical_documents)  # Log final count.
        print(f"logical_documents={result.logical_documents}")  # Print the key count for automation.
        print(f"physical_parts={result.physical_parts}")  # Print the part count for automation.
        print(f"part_sets={result.part_sets}")  # Print the split set count for automation.
        print(f"duplicate_losers={result.duplicate_losers}")  # Print duplicate loser count for automation.


InventoryCommand().run()  # Run the class-based command without a wrapper function.
