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
