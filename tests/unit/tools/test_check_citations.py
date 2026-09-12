"""Unit tests for ``tools/check_citations.py``.

Why:
    The checker guards every ``path:line`` citation of the live code. A checker
    that answers differently on a workstation and in the continuous integration
    runner guards nothing, because a writer then reads a pass that the runner
    turns into a failure.

    Issue #1998 records exactly that difference. The first version of the
    checker read the disk to decide whether a cited file exists. A citation into
    ``.venv/Lib/site-packages`` resolved on a workstation that installed the
    packages, and it resolved nowhere in the runner. The workstation passed and
    the runner failed on the same commit.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from tools import check_citations

# The checker scans this file too, so a sample citation written as one literal
# would report itself as a broken citation. Each test builds its sample from two
# parts, and no literal of this file reads as a citation.
_MD = ".md"  # The suffix that the citation pattern needs.


def test_a_virtual_environment_path_reads_as_external() -> None:
    """A citation into the installed packages must never depend on the disk.

    Why:
        The runner holds no virtual environment. A checker that read the disk
        would pass on a workstation and fail in the runner on one commit.
    """
    assert check_citations.is_external(".venv/Lib/site-packages/mistapi/api/v1/sites/stats.py") is True


def test_a_module_path_of_the_sdk_reads_as_external() -> None:
    """A research note cites the SDK by its module path on purpose."""
    assert check_citations.is_external("mistapi/api/v1/orgs/stats.py") is True


def test_a_repository_path_reads_as_internal() -> None:
    """A path of this repository must reach the check, whatever its folder."""
    assert check_citations.is_external("src/upgrade_portal/app/wiring.py") is False


def test_a_windows_separator_reads_the_same_as_a_forward_slash() -> None:
    """A writer on Windows may type a backslash, and the rule must not change."""
    assert check_citations.is_external(".venv\\Lib\\site-packages\\mistapi\\stats.py") is True


def test_a_citation_past_the_end_of_a_file_is_reported(tmp_path: object) -> None:
    """The whole point of the gate is a line number that no longer exists.

    Args:
        tmp_path: The temporary folder of this test.
    """
    folder = str(tmp_path)  # The pytest fixture answers a path object.
    notes = os.path.join(folder, "notes")  # The citation reads `notes/short.md`.
    os.makedirs(notes, exist_ok=True)
    target = os.path.join(notes, f"short{_MD}")  # The cited file.
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("one\ntwo\n")  # Two lines, so line 99 cannot exist.
    source = os.path.join(folder, "cites.py")  # The file that holds the citation.
    with open(source, "w", encoding="utf-8") as handle:
        handle.write(f"# See notes/short{_MD}:99 for the rule.\n")
    index = {f"short{_MD}": [target.replace("\\", "/")]}  # The checker matches on the file name.
    found, findings = check_citations.check_file(source.replace("\\", "/"), {}, index)
    assert found == 1  # The checker read the citation.
    assert len(findings) == 1  # The line sits past the end of the file.
    assert "2 lines" in findings[0].cause  # The report names the real length.


def test_a_citation_inside_a_file_is_accepted(tmp_path: object) -> None:
    """A citation that resolves must raise no finding.

    Args:
        tmp_path: The temporary folder of this test.
    """
    folder = str(tmp_path)  # The pytest fixture answers a path object.
    notes = os.path.join(folder, "notes")  # The citation reads `notes/long.md`.
    os.makedirs(notes, exist_ok=True)
    target = os.path.join(notes, f"long{_MD}")  # The cited file.
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("\n".join(str(number) for number in range(100)))  # 100 lines.
    source = os.path.join(folder, "cites.py")  # The file that holds the citation.
    with open(source, "w", encoding="utf-8") as handle:
        handle.write(f"# See notes/long{_MD}:42 for the rule.\n")
    index = {f"long{_MD}": [target.replace("\\", "/")]}  # The checker matches on the file name.
    found, findings = check_citations.check_file(source.replace("\\", "/"), {}, index)
    assert found == 1  # The checker read the citation.
    assert findings == []  # The line sits inside the file.


def test_a_plain_line_skips_the_citation_pattern(tmp_path: object, monkeypatch: Any) -> None:
    """A line with no citation markers must not run the regex pattern.

    Args:
        tmp_path: The temporary folder of this test.
        monkeypatch: The pytest helper that replaces module attributes.
    """

    class RaisingPattern:
        """A test pattern that fails if the checker asks for matches."""

        def finditer(self, text: str) -> list[object]:
            """Fail if a line cannot hold a citation.

            Args:
                text: The line that the checker reads.

            Raises:
                AssertionError: Always, because this branch must not run.
            """
            raise AssertionError(text)

    source = os.path.join(str(tmp_path), "plain.py")  # The file holds no citation markers.
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("plain text with no reference\n")  # The line cannot match the citation pattern.
    monkeypatch.setattr(check_citations, "_CITATION", RaisingPattern())  # Prove the fast skip path runs.
    found, findings = check_citations.check_file(source.replace("\\", "/"), {}, {})
    assert found == 0  # The checker found no citation.
    assert findings == []  # The checker reported no failure.


def test_the_default_cli_uses_one_repository_walk(tmp_path: object, monkeypatch: Any, capsys: Any) -> None:
    """The default run must build the index and source list together.

    Args:
        tmp_path: The temporary folder of this test.
        monkeypatch: The pytest helper that replaces module attributes.
        capsys: The pytest helper that captures stdout.
    """
    root = os.path.join(str(tmp_path), "docs")  # The default root for this isolated run.
    os.makedirs(root, exist_ok=True)
    source = os.path.join(root, f"note{_MD}")  # The source file exercises the readable-file path.
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("no citations here\n")  # A citation-free file keeps the test focused on walking.
    real_walk = check_citations.os.walk  # The wrapper delegates to the real walker.
    calls = {"count": 0}  # The test records how many root walks start.

    def counted_walk(*args: Any, **kwargs: Any) -> Iterator[tuple[str, list[str], list[str]]]:
        calls["count"] += 1  # Count each os.walk call from the default CLI path.
        yield from real_walk(*args, **kwargs)

    monkeypatch.setattr(check_citations, "DEFAULT_ROOTS", (root,))  # Isolate the default path to the fixture.
    monkeypatch.setattr(check_citations.os, "walk", counted_walk)  # Count repository walks in the fixture.
    assert check_citations.main([]) == 0  # A clean citation-free tree exits successfully.
    assert calls["count"] == 1  # The default path must not walk once for the index and again for sources.
    assert capsys.readouterr().out == "0 citation(s) checked, 0 unresolved\n"  # Preserve the output text.
