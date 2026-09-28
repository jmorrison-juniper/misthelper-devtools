"""Remove safe git worktrees and stale git worktree admin directories.

Local cleanup scripts in a product checkout had hard-coded paths and target
lists. This command reads any repository that the caller names. It defaults to
a dry run, so the operator can review the removals before it changes files.
"""

from __future__ import annotations  # Keep annotations stable for console use.

import argparse  # Parse the command line for the console script.
import logging  # Record each cleanup action.
import os  # Change file mode before a retry.
import shutil  # Remove worktree and admin directory trees.
import stat  # Name the write bit for the Windows read-only fix.
import subprocess  # Read git state and prune admin metadata.
import sys  # Return command status and print diagnostics.
import time  # Sleep between delete retries.
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from misthelper_devtools.repository_root import resolve_repository_root

logger = logging.getLogger(__name__)  # Use a module logger so callers can control verbosity.

GIT_TIMEOUT_SECONDS = 60  # Bound each git command so cleanup cannot hang.
DELETE_RETRIES = 3  # Retry transient OneDrive and antivirus locks.
DELETE_RETRY_SLEEP_SECONDS = 0.5  # Keep retries short but useful.


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    """One row from git worktree list --porcelain."""

    path: Path  # The checkout path.
    branch: str | None  # The short branch name, or None for a detached worktree.
    bare: bool = False  # True when git marks the row as bare.


@dataclass(frozen=True, slots=True)
class RemovalPlan:
    """One worktree candidate and the reason for its decision."""

    worktree: WorktreeRecord  # The worktree that the command considered.
    remove: bool  # True when apply mode can remove it.
    reason: str  # State why the command removes or keeps it.


def _on_rm_error(function: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Clear the read-only bit and retry one failed remove operation."""
    del exc  # The retry does not need the original exception object.
    os.chmod(path, stat.S_IWRITE)  # Git objects can be read-only on Windows.
    function(path)  # Retry the operation that rmtree attempted.


def force_rmtree(target: Path, retries: int = DELETE_RETRIES) -> bool:
    """Delete a directory tree and clear read-only files when needed."""
    logger.info("Removing directory tree %s", target)  # Log before a destructive action.
    if not target.exists():  # A missing target is already clean.
        logger.info("Directory tree is already absent: %s", target)  # Log the no-op.
        return True  # Treat absence as success.
    last_error: Exception | None = None  # Store the final error for diagnostics.
    for attempt in range(1, retries + 1):  # Retry transient locks a few times.
        try:
            shutil.rmtree(target, onexc=_on_rm_error)  # Python 3.13 uses onexc for rmtree retries.
            logger.info("Removed directory tree %s", target)  # Log after a successful removal.
            return not target.exists()  # Confirm the path is gone.
        except Exception as error:
            last_error = error  # Keep the error for the final report.
            logger.warning("Remove attempt %d/%d failed for %s: %s", attempt, retries, target, error)
            time.sleep(DELETE_RETRY_SLEEP_SECONDS)  # Wait for transient locks to clear.
    logger.error("Could not remove %s: %s", target, last_error)  # Log the final fault.
    return False  # Tell the caller that cleanup failed.


class GitWorktreeCleanup:
    """Clean linked worktrees and stale admin directories for one repository."""

    def __init__(self, root: Path, apply: bool) -> None:
        self.root = root.resolve()  # Store the target repository root.
        self.apply = apply  # False means dry run.

    def cleanup_merged(self, base: str, delete_branch: bool, paths: tuple[Path, ...]) -> int:
        """Remove merged linked worktrees that are safe to delete."""
        logger.info("Planning merged worktree cleanup for base %s", base)  # Log before git reads.
        worktrees = self._worktrees()  # Read linked worktree metadata.
        main_path = worktrees[0].path.resolve() if worktrees else self.root  # The first row is the main worktree.
        merged = self._merged_branches(base)  # Read branches merged into the base.
        targets = tuple(self._resolve_path(path) for path in paths)  # Normalize explicit targets.
        plans = tuple(
            self._plan_worktree(record, main_path, merged, targets, base) for record in worktrees
        )  # Plan actions.
        chosen = tuple(plan for plan in plans if plan.remove)  # Only removable plans change files in apply mode.
        for plan in plans:  # Print every decision so dry run explains what it did.
            action = "remove" if plan.remove else "keep"  # Name the action.
            print(f"{action}: {plan.worktree.path} ({plan.reason})")  # Print one decision.
        if not self.apply:  # Dry run must not change the checkout.
            print(f"dry-run: would remove {len(chosen)} merged worktree(s)")  # Summarize planned removals.
            return 0  # A dry run succeeds after planning.
        failures = 0  # Count failed removals and branch deletes.
        removed: list[RemovalPlan] = []  # Only removed worktrees can have their branch deleted.
        for plan in chosen:  # Apply each safe removal.
            if not force_rmtree(plan.worktree.path):  # Remove the checkout directory.
                failures += 1  # Record the failed directory removal.
                continue  # Do not delete a branch when the worktree remains.
            removed.append(plan)  # Record the removed worktree.
        failures += self._prune()  # Reap admin dirs after checkout removal.
        if delete_branch:  # The caller asked to delete merged branches too.
            for plan in removed:  # Delete branches for the removed worktrees.
                if plan.worktree.branch is not None:  # Detached worktrees have no branch to delete.
                    failures += self._delete_branch(plan.worktree.branch)  # Delete the local branch.
        print(f"removed {len(removed)} merged worktree(s); failures={failures}")  # Summarize apply mode.
        return 1 if failures else 0  # Fail only when a removal or branch delete failed.

    def cleanup_stale_admin(self) -> int:
        """Remove admin dirs whose gitdir file points at a missing worktree."""
        admin_root = self._admin_root()  # Locate .git/worktrees for the common dir.
        logger.info("Planning stale admin cleanup under %s", admin_root)  # Log before directory scan.
        if not admin_root.exists():  # A repository can have no linked worktrees.
            print(f"no admin root: {admin_root}")  # State the no-op.
            return 0  # Nothing to remove.
        stale = tuple(path for path in sorted(admin_root.iterdir()) if path.is_dir() and self._stale_admin(path))
        for path in stale:  # Print each stale admin directory.
            print(f"remove admin: {path}")  # Dry run and apply share the same listing.
        if not self.apply:  # Dry run must not change files.
            print(f"dry-run: would remove {len(stale)} stale admin dir(s)")  # Summarize planned removals.
            return 0  # Planning succeeded.
        failures = sum(0 if force_rmtree(path) else 1 for path in stale)  # Remove each stale admin dir.
        failures += self._prune()  # Run git prune after stale admin removal.
        print(f"removed {len(stale) - failures} stale admin dir(s); failures={failures}")  # Summarize apply mode.
        return 1 if failures else 0  # Fail only when a remove or prune failed.

    def _plan_worktree(
        self,
        worktree: WorktreeRecord,
        main_path: Path,
        merged: frozenset[str],
        targets: tuple[Path, ...],
        base: str,
    ) -> RemovalPlan:
        """Return the cleanup decision for one worktree."""
        if targets and worktree.path.resolve() not in targets:  # Explicit paths limit the candidate list.
            return RemovalPlan(worktree, False, "not named by --path")  # Skip unlisted worktrees.
        if worktree.path.resolve() == main_path:  # The main worktree must never be removed.
            return RemovalPlan(worktree, False, "main worktree")  # Keep the main checkout.
        if worktree.bare:  # A bare row has no checkout directory to remove.
            return RemovalPlan(worktree, False, "bare worktree")  # Keep bare rows.
        if worktree.branch is None:  # A detached worktree has no merged branch signal.
            return RemovalPlan(worktree, False, "detached HEAD")  # Keep detached rows.
        if worktree.branch in merged:  # The base holds the branch tip.
            reason = f"branch {worktree.branch} is merged"
        elif self._squash_merged(base, worktree.branch):  # The base holds the branch change as one commit.
            reason = f"branch {worktree.branch} is squash-merged"
        else:  # Only branches merged into the base are safe for this command.
            return RemovalPlan(worktree, False, f"branch {worktree.branch} is not merged into base")
        if self._dirty(worktree.path):  # Uncommitted changes can hold work not in the merge.
            return RemovalPlan(worktree, False, "uncommitted changes")  # Keep dirty worktrees.
        return RemovalPlan(worktree, True, reason)  # Safe to remove.

    def _worktrees(self) -> tuple[WorktreeRecord, ...]:
        """Read git worktree list --porcelain."""
        completed = self._git(["worktree", "list", "--porcelain"], cwd=self.root)  # Read the worktree list.
        records: list[WorktreeRecord] = []  # Collect parsed records.
        current_path: Path | None = None  # Store the row path while parsing.
        current_branch: str | None = None  # Store the row branch while parsing.
        current_bare = False  # Store whether the row is bare.
        for line in [*completed.stdout.splitlines(), ""]:  # Add a blank line to flush the last row.
            if not line:  # Blank line ends one porcelain record.
                if current_path is not None:  # A complete row has a path.
                    records.append(WorktreeRecord(current_path, current_branch, current_bare))  # Store the row.
                current_path = None  # Reset for the next record.
                current_branch = None  # Reset branch state.
                current_bare = False  # Reset bare state.
                continue  # Move to the next line.
            if line.startswith("worktree "):  # Worktree path row.
                current_path = Path(line.removeprefix("worktree ")).resolve()  # Store absolute path.
            elif line.startswith("branch "):  # Branch ref row.
                ref = line.removeprefix("branch ")  # Store the full ref.
                current_branch = ref.removeprefix("refs/heads/")  # Use the short branch name.
            elif line == "bare":  # Bare worktree marker.
                current_bare = True  # Store the marker.
        return tuple(records)  # Return immutable records for planning.

    def _merged_branches(self, base: str) -> frozenset[str]:
        """Return branches that git says are merged into the base."""
        completed = self._git(["branch", "--merged", base, "--format", "%(refname:short)"], cwd=self.root)
        branches = frozenset(line.strip() for line in completed.stdout.splitlines() if line.strip())  # Parse names.
        logger.info("Found %d branch(es) merged into %s", len(branches), base)  # Log the merged set size.
        return branches  # Return branch names.

    def _squash_merged(self, base: str, branch: str) -> bool:
        """Return True when the base holds the whole branch change as one commit.

        A squash merge writes a new commit, so ``git branch --merged`` misses it.
        This check writes one probe commit that holds the branch tree on top of
        the merge base. ``git cherry`` marks the probe with ``-`` when the base
        has a commit with the same patch. The probe is a loose object with no
        reference, so a dry run changes no branch and no checkout file.
        """
        try:
            merge_base = self._git(["merge-base", base, branch], cwd=self.root).stdout.strip()
            tree = self._git(["rev-parse", f"{branch}^{{tree}}"], cwd=self.root).stdout.strip()
            probe = self._git(
                [
                    "-c",
                    "user.name=worktree-cleanup",
                    "-c",
                    "user.email=worktree-cleanup@localhost",
                    "commit-tree",
                    tree,
                    "-p",
                    merge_base,
                    "-m",
                    "worktree-cleanup squash probe",
                ],
                cwd=self.root,
            ).stdout.strip()
            cherry = self._git(["cherry", base, probe], cwd=self.root).stdout
        except RuntimeError as error:
            logger.warning("Could not test branch %s for a squash merge: %s", branch, error)  # Keep on doubt.
            return False
        squashed = cherry.startswith("-")  # A minus sign means the base already has this patch.
        logger.info("Branch %s squash-merged into %s: %s", branch, base, squashed)  # Log the decision.
        return squashed

    def _dirty(self, path: Path) -> bool:
        """Return True when a worktree has uncommitted changes."""
        completed = self._git(["status", "--porcelain"], cwd=path)  # Ask git for dirty state.
        dirty = bool(completed.stdout.strip())  # Any porcelain row means uncommitted state.
        logger.info("Worktree %s dirty=%s", path, dirty)  # Log the safety check result.
        return dirty  # Return the safety check.

    def _delete_branch(self, branch: str) -> int:
        """Delete one merged local branch."""
        logger.info("Deleting branch %s", branch)  # Log before the delete.
        try:
            self._git(["branch", "-D", branch], cwd=self.root)  # Delete the local branch.
        except RuntimeError as error:
            print(f"branch delete failed: {branch}: {error}", file=sys.stderr)  # Print the failure.
            return 1  # Count the failed delete.
        print(f"branch deleted: {branch}")  # Report the delete.
        return 0  # Delete succeeded.

    def _prune(self) -> int:
        """Run git worktree prune and return zero on success."""
        logger.info("Running git worktree prune")  # Log before pruning admin metadata.
        try:
            self._git(["worktree", "prune", "-v"], cwd=self.root)  # Let git clean admin metadata.
        except RuntimeError as error:
            print(f"git worktree prune failed: {error}", file=sys.stderr)  # Print the failure.
            return 1  # Count the failed prune.
        print("git worktree prune completed")  # Report the prune.
        return 0  # Prune succeeded.

    def _admin_root(self) -> Path:
        """Return the common .git/worktrees directory."""
        completed = self._git(["rev-parse", "--git-common-dir"], cwd=self.root)  # Read the common git dir.
        raw = Path(completed.stdout.strip())  # Parse the path that git printed.
        common = raw if raw.is_absolute() else (self.root / raw).resolve()  # Resolve relative git paths.
        return common / "worktrees"  # Admin dirs live under the common git dir.

    def _stale_admin(self, admin_dir: Path) -> bool:
        """Return True when an admin gitdir file points at a missing checkout."""
        gitdir_file = admin_dir / "gitdir"  # Git writes the linked checkout .git file path here.
        try:
            raw = gitdir_file.read_text(encoding="utf-8").strip()  # Read the gitdir target.
        except OSError:
            logger.warning("Admin dir %s has no readable gitdir file", admin_dir)  # Log the malformed admin dir.
            return True  # A malformed admin dir is stale.
        gitdir = Path(raw)  # Parse the target path.
        if not gitdir.is_absolute():  # Git usually writes absolute paths, but accept relative ones.
            gitdir = (admin_dir / gitdir).resolve()  # Resolve relative to the admin dir.
        worktree_path = gitdir.parent if gitdir.name == ".git" else gitdir  # Convert .git file path to checkout path.
        stale = not worktree_path.exists()  # Missing checkout means stale admin dir.
        logger.info("Admin dir %s stale=%s", admin_dir, stale)  # Log the decision.
        return stale  # Return the decision.

    def _resolve_path(self, path: Path) -> Path:
        """Resolve a caller path relative to the repository root."""
        candidate = path if path.is_absolute() else self.root / path  # Interpret relative paths under root.
        return candidate.resolve()  # Return the normalized path.

    @staticmethod
    def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        """Run one git command with a timeout and decoded output."""
        command = ["git", *args]  # Build a safe argument list.
        logger.debug("Running %s in %s", command, cwd)  # Log the exact git command.
        try:
            return subprocess.run(
                command,  # Do not invoke a shell.
                cwd=cwd,  # Run inside the requested checkout.
                capture_output=True,  # Keep output for decisions and errors.
                encoding="utf-8",  # Git writes UTF-8 branch names.
                check=True,  # Raise on git faults.
                timeout=GIT_TIMEOUT_SECONDS,  # Bound every subprocess call.
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(str(error)) from error  # Convert git errors for CLI reporting.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for worktree-cleanup."""
    parser = argparse.ArgumentParser(
        prog="worktree-cleanup",
        description="Dry-run or remove safe git worktrees and stale worktree admin dirs.",
    )  # Name the console script in help output.
    parser.add_argument("--root", type=Path, default=None, help="Repository root to clean.")
    parser.add_argument("--apply", action="store_true", help="Perform removals. Default is a dry run.")
    subparsers = parser.add_subparsers(dest="command", required=True)  # Require a cleanup mode.
    merged = subparsers.add_parser("merged", help="Remove linked worktrees whose branch is merged or squash-merged.")
    merged.add_argument("--base", default="main", help="Base branch that must hold each branch change.")
    merged.add_argument("--delete-branch", action="store_true", help="Delete each removed local branch.")
    merged.add_argument("--path", action="append", default=[], type=Path, help="Limit cleanup to this worktree path.")
    subparsers.add_parser("stale-admin", help="Remove stale .git/worktrees admin directories.")
    return parser  # The CLI parses this object once.


def main(argv: list[str] | None = None) -> int:
    """Run the worktree cleanup command."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Print concise progress messages.
    parser = build_parser()  # Build the parser once for CLI and tests.
    args = parser.parse_args(argv)  # Let argparse handle usage errors with status 2.
    root = resolve_repository_root(args.root)  # Resolve the target repository root.
    cleanup = GitWorktreeCleanup(root, bool(args.apply))  # Store root and dry-run mode.
    try:
        if args.command == "merged":  # Remove safe merged worktrees.
            return cleanup.cleanup_merged(str(args.base), bool(args.delete_branch), tuple(args.path))
        if args.command == "stale-admin":  # Remove stale admin dirs.
            return cleanup.cleanup_stale_admin()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)  # Print git errors without a traceback.
        return 2  # Reserve status 2 for git or usage errors.
    return 2  # argparse should make this path unreachable.


if __name__ == "__main__":  # Console module entry point.
    raise SystemExit(main())  # Return the CLI status.
