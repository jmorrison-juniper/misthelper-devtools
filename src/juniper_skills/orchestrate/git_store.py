"""Commit generated skill packages in the canonical skill store."""

from __future__ import annotations

import logging
import subprocess  # nosec B404 - This module starts fixed git commands without a shell.
import time
from pathlib import Path

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CanonicalSkillStore:
    """Manage safe git commits for generated Juniper skill files."""

    def __init__(self, store_path: Path) -> None:
        """Initialize the CanonicalSkillStore instance."""
        self.store_path = store_path  # Store the canonical skill repository path.

    def commit_document(self, domain: str, document_key: str) -> str:
        """Run the commit document operation."""
        logger.info("Committing generated Juniper skill files for %s", document_key)  # Log before git writes.
        self._ensure_repository()  # Refuse to run git commands outside the canonical repository.
        dirty = self._dirty_paths()  # Read the tree state before staging generated files.
        if dirty and not self._only_generated_dirty(dirty, domain):  # Protect unrelated operator work.
            detail = "canonical store has unrelated dirty files"  # State why no commit occurred.
            logger.debug("Skipped canonical commit because %s", detail)  # Record the dirty-tree decision.
            return detail  # Return a blocker string for the pipeline journal.
        self._with_index_retry(("git", "add", "skills", "CATALOG.md"))  # Stage only canonical generated paths.
        if not self._staged_changes():  # Avoid empty commits during repeat runs.
            logger.debug("Skipped canonical commit because no generated files changed")  # Record empty result.
            return "no generated changes to commit"  # Return a stable detail for journal evidence.
        message = self._message(document_key)  # Build the Conventional Commit message.
        self._with_index_retry(("git", "commit", "-m", message))  # Commit generated files for durable progress.
        logger.debug("Committed generated skill files for %s", document_key)  # Record successful persistence.
        return "generated skill files committed"  # Return a compact outcome.

    def _ensure_repository(self) -> None:
        if not (self.store_path / ".git").exists():  # Require the canonical store to be a git repository.
            raise FileNotFoundError(f"Canonical store is not a git repository: {self.store_path}")  # Fail safely.

    def _dirty_paths(self) -> list[str]:
        result = self._git(("git", "status", "--porcelain"))  # Read porcelain output for stable parsing.
        paths = [line[3:].strip() for line in result.splitlines() if line.strip()]  # Extract changed paths only.
        logger.debug("Canonical store has %d dirty paths", len(paths))  # Record dirty path count.
        return paths  # Return changed paths for blocker checks.

    def _only_generated_dirty(self, paths: list[str], domain: str) -> bool:
        allowed = (f"skills/{domain}/", "CATALOG.md")  # Permit only this document package and catalog.
        return all(path.startswith(allowed[0]) or path == allowed[1] for path in paths)  # Protect unrelated files.

    def _staged_changes(self) -> bool:
        result = self._git(("git", "diff", "--cached", "--name-only"))  # Read staged files after git add.
        staged = bool(result.strip())  # Convert output to a Boolean for empty commit checks.
        logger.debug("Canonical store staged changes present: %s", staged)  # Record staged status.
        return staged  # Return whether commit can proceed.

    def _message(self, document_key: str) -> str:
        return (
            "feat(skills): add generated Juniper skill document\n\n"
            f"Refs #2925\n\nDocument: {document_key}\n\n"
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
        )  # Return the required commit message with trailer.

    def _with_index_retry(self, command: tuple[str, ...]) -> str:
        for attempt in range(5):  # Retry transient index.lock collisions as requested.
            try:
                return self._git(command)  # Run the git command from the canonical store.
            except RuntimeError as error:
                if "index.lock" not in str(error) or attempt == 4:  # Retry only the lock condition.
                    raise  # Preserve non-lock failures and exhausted retries.
                logger.info("Waiting for git index lock before retry")  # Log before the required wait.
                time.sleep(5)  # Wait for the other worker to release the index.
        return ""  # Satisfy static analysis after the retry loop.

    def _git(self, command: tuple[str, ...]) -> str:
        result = subprocess.run(  # nosec B603 - The command tuple is built from fixed git operations only.
            command, cwd=self.store_path, capture_output=True, text=True, timeout=60, check=False
        )
        output = (result.stdout + result.stderr).strip()  # Preserve stdout and stderr for diagnostics.
        if result.returncode != 0:  # Raise only after capturing git evidence.
            raise RuntimeError(output)  # Surface the exact git failure to the caller.
        return output  # Return command output for parser methods.
