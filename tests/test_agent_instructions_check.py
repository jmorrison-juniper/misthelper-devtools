"""Tests for the ``agent-instructions-check`` command.

The command compares the ``AGENTS.md`` of a repository with the canonical copy
of this repository. The tests use a local canonical path, so no test reads the
network. One test replaces the HTTP read, so the URL path and the status
handling are also covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from misthelper_devtools import agent_instructions_check as module
from misthelper_devtools.agent_instructions_check import (
    CANONICAL_PATH,
    EXIT_DRIFT,
    EXIT_ERROR,
    EXIT_MATCH,
    canonical_url,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / CANONICAL_PATH


def _consumer(tmp_path: Path, text: str) -> Path:
    """Write a consumer repository with one AGENTS.md and return its root."""
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "AGENTS.md").write_text(text, encoding="utf-8")
    return root


def test_a_byte_identical_copy_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _consumer(tmp_path, CANONICAL.read_text(encoding="utf-8"))
    code = main(["--root", str(root), "--canonical", str(CANONICAL)])
    assert code == EXIT_MATCH
    assert "matches the canonical copy" in capsys.readouterr().out


def test_an_edited_copy_reports_the_difference(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = CANONICAL.read_text(encoding="utf-8").replace("Do not push to `main`.", "Push to `main` when you must.")
    root = _consumer(tmp_path, text)
    code = main(["--root", str(root), "--canonical", str(CANONICAL)])
    lines = capsys.readouterr().out.splitlines()
    assert code == EXIT_DRIFT
    assert lines[0].endswith(f"differs from the canonical copy at {CANONICAL.as_posix()}.")
    # The canonical copy is the old side of the difference, so a minus marks the canonical text
    # and a plus marks the local edit.
    assert any(line.startswith("-") and "Do not push to `main`." in line for line in lines)
    assert any(line.startswith("+") and "Push to `main` when you must." in line for line in lines)
    assert lines[-1] == "Copy the canonical file over the local copy, and do not edit the copy."


def test_a_trailing_newline_change_counts_as_drift(tmp_path: Path) -> None:
    # The copy must match byte for byte, so a lost final newline is a difference.
    root = _consumer(tmp_path, CANONICAL.read_text(encoding="utf-8").rstrip("\n"))
    assert main(["--root", str(root), "--canonical", str(CANONICAL)]) == EXIT_DRIFT


def test_a_missing_local_copy_returns_the_error_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    code = main(["--root", str(root), "--canonical", str(CANONICAL)])
    assert code == EXIT_ERROR
    assert "could not read a copy" in capsys.readouterr().err


def test_the_url_names_the_repository_the_commit_and_the_canonical_path() -> None:
    url = canonical_url("jmorrison-juniper/misthelper-devtools", "0a7f18bbaceb224f88f31e36ac8c0cd61d69a1b5")
    assert url == (
        "https://raw.githubusercontent.com/jmorrison-juniper/misthelper-devtools/"
        "0a7f18bbaceb224f88f31e36ac8c0cd61d69a1b5/templates/agent-instructions/AGENTS.md"
    )


class _Response:
    """Stand in for the HTTP response of the raw host."""

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


def test_the_network_read_compares_the_remote_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url: str, timeout: int) -> _Response:
        seen.append(url)
        assert timeout > 0
        return _Response(200, CANONICAL.read_bytes())

    monkeypatch.setattr(module.requests, "get", fake_get)
    root = _consumer(tmp_path, CANONICAL.read_text(encoding="utf-8"))
    assert main(["--root", str(root), "--commit", "abc123"]) == EXIT_MATCH
    assert seen == [canonical_url("jmorrison-juniper/misthelper-devtools", "abc123")]


def test_a_failed_network_read_returns_the_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(module.requests, "get", lambda url, timeout: _Response(404, b""))
    root = _consumer(tmp_path, "text\n")
    assert main(["--root", str(root), "--commit", "nope"]) == EXIT_ERROR
    assert "GitHub answered 404" in capsys.readouterr().err


def test_a_connection_error_returns_the_error_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: int) -> _Response:
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(module.requests, "get", fake_get)
    root = _consumer(tmp_path, "text\n")
    assert main(["--root", str(root)]) == EXIT_ERROR
