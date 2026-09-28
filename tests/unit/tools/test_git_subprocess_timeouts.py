"""Tests that the git calls in two devtools modules carry a timeout.

A child process that never exits holds its parent forever. The symbol diff
and the compliance analyzer run inside CI quality gates, so a stalled git call
held the gate until the six-hour job limit ended it (MistHelper issue #1943).

These tests moved here from the MistHelper repository. They read the real
call keywords through a recording double, and they fail when a caller drops
the bound again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from misthelper_devtools.compliance_analyzer import engine
from misthelper_devtools.symbol_diff import comparator


class _RecordingRun:
    """Stand in for ``subprocess.run`` and keep the keywords it received."""

    def __init__(self, returncode: int = 0, stdout: Any = "") -> None:
        # The caller reads returncode and stdout, so the double carries both.
        self.returncode = returncode
        self.stdout = stdout
        # The test asserts on this map after the call under test returns.
        self.captured: dict[str, Any] = {}

    def __call__(self, *args: Any, **kwargs: Any) -> _RecordingRun:
        """Record the keywords and return this object as the result."""
        self.captured = dict(kwargs)  # Copy, so a later call cannot mutate it.
        return self  # The caller reads .returncode and .stdout off the result.


class _TimeoutRun:
    """Stand in for ``subprocess.run`` and always raise the timeout error."""

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        """Fail the way a wedged child process fails."""
        raise subprocess.TimeoutExpired(cmd="stub", timeout=1)


class TestSymbolDiffTimeout:
    """The symbol comparator must bound its git call."""

    def test_git_show_passes_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _RecordingRun(returncode=0, stdout="x = 1\n")
        monkeypatch.setattr(comparator.subprocess, "run", recorder)

        comparator.SymbolTableComparator().read_revision("HEAD", Path("a.py"))

        assert recorder.captured["timeout"] == comparator._GIT_TIMEOUT_SECONDS

    def test_a_stalled_git_show_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(comparator.subprocess, "run", _TimeoutRun())

        result = comparator.SymbolTableComparator().read_revision("HEAD", Path("a.py"))

        # The caller must skip the file, not crash the whole gate.
        assert result is None


class TestComplianceAnalyzerTimeout:
    """The compliance analyzer must bound its git call."""

    def test_check_ignore_passes_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _RecordingRun(returncode=0, stdout=b"")
        monkeypatch.setattr(engine.subprocess, "run", recorder)
        monkeypatch.setattr(engine.ComplianceAnalyzer, "_resolve_git_executable", staticmethod(lambda: "git"))

        engine.ComplianceAnalyzer._filter_git_ignored([Path("a.py")])

        assert recorder.captured["timeout"] == engine._GIT_TIMEOUT_SECONDS

    def test_a_stalled_check_ignore_keeps_every_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine.subprocess, "run", _TimeoutRun())
        monkeypatch.setattr(engine.ComplianceAnalyzer, "_resolve_git_executable", staticmethod(lambda: "git"))
        files = [Path("a.py"), Path("b.py")]

        result = engine.ComplianceAnalyzer._filter_git_ignored(files)

        # The analyzer fails open, so it must never hide a file after a stall.
        assert result == files
