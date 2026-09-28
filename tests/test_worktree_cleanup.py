"""Tests for the generic worktree cleanup command."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from misthelper_devtools.worktree_cleanup import GitWorktreeCleanup, main


def _git(repository: Path, *args: str) -> None:
    """Run git in a temporary repository."""
    subprocess.run(["git", *args], cwd=repository, check=True, timeout=60)


def _init_repository(repository: Path) -> None:
    """Create a temporary git repository with one commit."""
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / "README.md").write_text("# Test\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "seed")


def _create_merged_worktree(repository: Path, worktree: Path, branch: str) -> None:
    """Create a linked worktree whose branch is merged into main."""
    _git(repository, "worktree", "add", "-q", "-b", branch, str(worktree))
    (worktree / f"{branch}.txt").write_text(branch, encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", f"add {branch}")
    _git(repository, "merge", "-q", "--no-ff", branch, "-m", f"merge {branch}")


def test_worktree_cleanup_dry_run_keeps_merged_worktree(tmp_path: Path, capsys) -> None:
    """Dry run prints the removal plan without deleting the worktree."""
    repository = tmp_path / "repo"
    worktree = tmp_path / "merged"
    _init_repository(repository)
    _create_merged_worktree(repository, worktree, "feature")

    status = GitWorktreeCleanup(repository, apply=False).cleanup_merged("main", False, ())

    assert status == 0
    assert worktree.exists()
    assert "dry-run: would remove 1 merged worktree(s)" in capsys.readouterr().out


def test_worktree_cleanup_apply_removes_merged_worktree_and_branch(tmp_path: Path) -> None:
    """Apply mode removes a safe merged worktree and deletes its branch when asked."""
    repository = tmp_path / "repo"
    worktree = tmp_path / "merged"
    _init_repository(repository)
    _create_merged_worktree(repository, worktree, "feature")

    status = GitWorktreeCleanup(repository, apply=True).cleanup_merged("main", True, ())

    assert status == 0
    assert not worktree.exists()
    branches = subprocess.run(
        ["git", "branch", "--format", "%(refname:short)"],
        cwd=repository,
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    ).stdout.splitlines()
    assert "feature" not in branches


def test_worktree_cleanup_keeps_dirty_worktree(tmp_path: Path, capsys) -> None:
    """A dirty worktree is kept even when its branch is merged."""
    repository = tmp_path / "repo"
    worktree = tmp_path / "dirty"
    _init_repository(repository)
    _create_merged_worktree(repository, worktree, "dirty-feature")
    (worktree / "local.txt").write_text("uncommitted\n", encoding="utf-8")

    status = GitWorktreeCleanup(repository, apply=True).cleanup_merged("main", True, ())

    assert status == 0
    assert worktree.exists()
    assert "uncommitted changes" in capsys.readouterr().out


def test_worktree_cleanup_path_limits_candidates(tmp_path: Path, capsys) -> None:
    """An explicit --path target limits which merged worktree is removed."""
    repository = tmp_path / "repo"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init_repository(repository)
    _create_merged_worktree(repository, first, "first-feature")
    _create_merged_worktree(repository, second, "second-feature")

    status = GitWorktreeCleanup(repository, apply=True).cleanup_merged("main", False, (first,))

    assert status == 0
    assert not first.exists()
    assert second.exists()
    assert "not named by --path" in capsys.readouterr().out


def test_worktree_cleanup_stale_admin_dry_run_and_apply(tmp_path: Path, capsys) -> None:
    """Stale admin cleanup removes only when apply mode is set."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    admin = repository / ".git" / "worktrees" / "dead"
    admin.mkdir(parents=True)
    (admin / "gitdir").write_text(str(tmp_path / "missing" / ".git"), encoding="utf-8")
    readonly = admin / "readonly"
    readonly.write_text("x", encoding="utf-8")
    readonly.chmod(stat.S_IREAD)

    dry_status = GitWorktreeCleanup(repository, apply=False).cleanup_stale_admin()
    apply_status = GitWorktreeCleanup(repository, apply=True).cleanup_stale_admin()

    if os.name == "nt" and readonly.exists():
        readonly.chmod(stat.S_IWRITE)
    assert dry_status == 0
    assert apply_status == 0
    assert not admin.exists()
    assert "dry-run: would remove 1 stale admin dir(s)" in capsys.readouterr().out


def test_worktree_cleanup_cli_defaults_to_dry_run(tmp_path: Path, capsys) -> None:
    """The console command is safe by default."""
    repository = tmp_path / "repo"
    worktree = tmp_path / "merged"
    _init_repository(repository)
    _create_merged_worktree(repository, worktree, "feature")

    status = main(["--root", str(repository), "merged"])

    assert status == 0
    assert worktree.exists()
    assert "dry-run" in capsys.readouterr().out
