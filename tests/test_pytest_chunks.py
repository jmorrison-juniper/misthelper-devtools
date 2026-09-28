"""Tests for the generic pytest chunk runner."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from misthelper_devtools.pytest_chunks import PytestChunkRunner, main
from misthelper_devtools.pytest_chunks import TestChunk as Chunk


def test_pytest_chunks_builds_parent_and_split_children(tmp_path: Path) -> None:
    """A split path is ignored in the parent chunk and split one level lower."""
    root = tmp_path / "repo"
    portal = root / "tests" / "unit" / "upgrade_portal"
    (portal / "api").mkdir(parents=True)
    (portal / "ui").mkdir()
    (portal / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (portal / "test_b.py").write_text("def test_b():\n    assert True\n", encoding="utf-8")

    runner = PytestChunkRunner(
        root,
        (Path("tests") / "unit",),
        (Path("tests") / "unit" / "upgrade_portal",),
        300,
        120,
        0,
        lambda command, cwd, timeout: 0,
    )

    assert runner.build_chunks() == (
        Chunk((root / "tests" / "unit",), (portal,)),
        Chunk((portal / "test_a.py", portal / "test_b.py")),
        Chunk((portal / "api",)),
        Chunk((portal / "ui",)),
    )


def test_pytest_chunks_command_adds_timeout_only_when_available(tmp_path: Path) -> None:
    """The generic command omits --timeout when pytest-timeout is not installed."""
    root = tmp_path / "repo"
    root.mkdir()
    runner = PytestChunkRunner(root, (Path("tests"),), (), 300, 120, 5, lambda command, cwd, timeout: 0)
    runner.timeout_available = False

    command = runner.pytest_command((Path("tests"),), ())

    assert "--timeout=120" not in command
    assert "--durations=5" in command


def test_pytest_chunks_returns_non_zero_after_any_failed_chunk(tmp_path: Path, capsys) -> None:
    """The runner reaches the final summary when a later chunk fails."""
    root = tmp_path / "repo"
    split = root / "tests" / "split"
    (split / "child").mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_runner(command: Sequence[str], cwd: Path, timeout: int) -> int:
        calls.append(list(command))
        return 1 if len(calls) == 2 else 0

    runner = PytestChunkRunner(root, (Path("tests"),), (Path("tests") / "split",), 300, 120, 0, fake_runner)

    assert runner.run() == 1
    assert len(calls) == 2
    assert "pytest-chunks: failed 2 chunks" in capsys.readouterr().out


def test_pytest_chunks_timeout_returns_common_status(tmp_path: Path, capsys) -> None:
    """A wall-clock timeout becomes a failed run with status 124."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)

    def fake_runner(command: Sequence[str], cwd: Path, timeout: int) -> int:
        raise subprocess.TimeoutExpired(command, timeout)

    runner = PytestChunkRunner(root, (Path("tests"),), (), 1, 120, 0, fake_runner)

    assert runner.run() == 124
    assert "timed out after 1s" in capsys.readouterr().out


def test_pytest_chunks_cli_rejects_missing_split_path(tmp_path: Path, capsys) -> None:
    """The CLI reports a usage error when a split path does not exist."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "tests").mkdir()

    status = main(["--root", str(root), "--split", "tests/missing", "tests"])

    assert status == 2
    assert "does not exist" in capsys.readouterr().err


def test_pytest_chunks_cli_accepts_misthelper_unit_preset(tmp_path: Path, capsys) -> None:
    """The original MistHelper shard name still selects its default paths."""
    root = tmp_path / "repo"
    portal = root / "tests" / "unit" / "upgrade_portal"
    portal.mkdir(parents=True)
    (root / ".git").mkdir()

    status = main(["--root", str(root), "unit"])

    assert status == 5
    assert "tests" in capsys.readouterr().out
