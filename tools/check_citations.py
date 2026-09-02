"""Check every ``path:line`` citation of the repository.

Why:
    A citation of the form ``contracts/http-api.md:132`` tells a reader where a
    rule lives. No gate checked one, so a citation went stale in silence.
    Commit ``1f8176a6`` added eight lines to one contract, and every citation
    below the insert then named the wrong line. Nothing failed and nothing
    warned. Issue #1998 records the case.

    This checker reads each citation, resolves the path, and reads the line
    number. It reports a citation whose file does not exist, and a citation
    whose line sits past the end of that file.

    Warning: this checker reads the number alone. It cannot read the claim. One
    stale citation of the earlier sweep named a real file and a real line, and
    the sentence at that line said the opposite of the claim. A reviewer must
    still read the claim.

Usage:
    python -m tools.check_citations [<path> ...]
"""

from __future__ import annotations

import os
import re
import sys

# A citation names a documentation file and one line, or a range of lines. The
# pattern needs a directory separator, so a plain sentence such as "the state
# is: 5" never matches. It accepts a backtick or a quote on either side.
_CITATION = re.compile(r"(?P<path>(?:[\w.\-]+/)+[\w.\-]+\.(?:md|py|html|css|js|yml|yaml|json|toml)):(?P<line>\d+)")

# The folders that hold a citation worth checking.
DEFAULT_ROOTS = ("src", "tests", "specs", "documentation", "tools")

# A file with one of these suffixes may hold a citation.
READ_SUFFIXES = (".md", ".py", ".html", ".yml", ".yaml")

# A folder that holds no source of this repository, or holds a generated copy.
SKIP_FOLDERS = frozenset({".git", ".venv", "node_modules", "__pycache__", "htmlcov", "site-packages"})

# The first part of a path that names an installed package and not this
# repository. A research note cites the SDK by its module path on purpose, so
# the reader can open the same file inside the virtual environment. This
# checker reads the repository alone, so it passes over such a citation.
EXTERNAL_ROOTS = frozenset({"mistapi", "flask", "redis", "arango", "werkzeug", "gunicorn", "waitress"})

# A part of a path that names the virtual environment. Such a citation resolves
# on a workstation that installed the packages, and it resolves nowhere else.
# The continuous integration runner holds no such folder, so a checker that
# read the disk alone would pass on a workstation and fail in the runner.
EXTERNAL_PARTS = ("/site-packages/", ".venv/", "/node_modules/")


def is_external(target: str) -> bool:
    """Report whether one citation names a file outside this repository.

    Args:
        target: The path that the citation names.

    Returns:
        True when the path names an installed package.
    """
    normalized = target.replace("\\", "/")  # One separator, whatever the writer typed.
    if normalized.split("/")[0] in EXTERNAL_ROOTS:  # A citation written as a module path.
        return True
    return any(part in normalized for part in EXTERNAL_PARTS)  # A citation written as an install path.


class Finding:
    """One citation that does not resolve."""

    def __init__(self, source: str, source_line: int, target: str, target_line: int, cause: str) -> None:
        """Record one unresolved citation.

        Args:
            source: The file that holds the citation.
            source_line: The line of that file.
            target: The path that the citation names.
            target_line: The line that the citation names.
            cause: Why the citation does not resolve.
        """
        self.source = source  # The file a reviewer must open.
        self.source_line = source_line  # The line a reviewer must read.
        self.target = target  # The path that the citation names.
        self.target_line = target_line  # The line that the citation names.
        self.cause = cause  # The reason the checker refused it.

    def __str__(self) -> str:
        """Return one report line.

        Returns:
            The source position, the citation, and the cause.
        """
        return f"{self.source}:{self.source_line}  {self.target}:{self.target_line}  {self.cause}"


def line_count(path: str) -> int:
    """Return the count of lines of one file.

    Args:
        path: The file to measure.

    Returns:
        The count of lines, or zero when the file cannot be read.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)  # A count needs no whole copy in memory.
    except OSError:
        return 0


def build_index(roots: tuple[str, ...]) -> dict[str, list[str]]:
    """Return every repository file, keyed by its last path part.

    Why:
        A citation is written from three different bases in this repository. It
        may read from the repository root, from the folder of the citing file,
        or from the feature folder that holds the contracts. The third form is
        the most common one, and a plain path test misses it.

        The index lets the checker match a citation against the tail of a real
        path, so ``contracts/http-api.md`` reaches the file that lives under
        the feature folder.

    Args:
        roots: The folders to walk.

    Returns:
        The real paths that share each file name.
    """
    index: dict[str, list[str]] = {}  # One entry for each file name.
    for root in roots:  # One walk for each root.
        for folder, subfolders, names in os.walk(root):
            subfolders[:] = [name for name in subfolders if name not in SKIP_FOLDERS]  # Prune in place.
            for name in names:  # Every file, whatever its suffix.
                index.setdefault(name, []).append(os.path.join(folder, name).replace("\\", "/"))
    return index


def resolve(source: str, target: str, index: dict[str, list[str]]) -> list[str]:
    """Return every real path that one citation may name.

    Why:
        Two feature folders may both hold a file of one name. This repository
        holds two files named ``contracts/http-api.md``, one under the portal
        feature and one under the version-defaults feature. A citation that
        starts at ``contracts/`` names either one.

        The checker therefore answers every candidate, and it refuses the
        citation only when no candidate holds the cited line. A stricter rule
        would report a citation that a reader follows without trouble.

    Args:
        source: The file that holds the citation.
        target: The path that the citation names.
        index: The repository files, keyed by file name.

    Returns:
        Every real path that the citation may name. An empty list means the
        citation names no real file.
    """
    if os.path.isfile(target):  # The form written from the repository root.
        return [target]
    beside = os.path.join(os.path.dirname(source), target)  # The form written from the citing folder.
    if os.path.isfile(beside):
        return [beside]
    tail = "/" + target.replace("\\", "/")  # The form written from a feature folder.
    return [path for path in index.get(os.path.basename(target), ()) if path.endswith(tail)]


def check_file(path: str, counts: dict[str, int], index: dict[str, list[str]]) -> tuple[int, list[Finding]]:
    """Check every citation of one file.

    Args:
        path: The file to read.
        counts: The line count of each target read so far.
        index: The repository files, keyed by file name.

    Returns:
        The count of citations found, and every finding.
    """
    findings: list[Finding] = []  # Every citation of this file that fails.
    found = 0  # Every citation of this file.
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return 0, findings  # A file the checker cannot read holds no citation it can check.
    for number, text in enumerate(lines, start=1):  # One pass over the file.
        for match in _CITATION.finditer(text):  # A line may hold several citations.
            found += 1
            target = match.group("path")  # The path that the citation names.
            cited = int(match.group("line"))  # The line that the citation names.
            if is_external(target):  # A path of an installed package, not of this repository.
                continue
            candidates = resolve(path, target, index)  # Every path that the citation may name.
            if not candidates:  # A citation to a file that does not exist.
                findings.append(Finding(path, number, target, cited, "the file does not exist"))
                continue
            for candidate in candidates:  # Measure each candidate one time.
                if candidate not in counts:
                    counts[candidate] = line_count(candidate)
            longest = max(counts[candidate] for candidate in candidates)  # The candidate that holds the most lines.
            if cited > longest:  # No candidate holds the cited line.
                findings.append(Finding(path, number, target, cited, f"the file holds {longest} lines"))
    return found, findings


def walk(roots: tuple[str, ...]) -> list[str]:
    """Return every readable file under the named roots.

    Args:
        roots: The folders to walk.

    Returns:
        The path of each file that may hold a citation.
    """
    paths: list[str] = []  # Every file the checker reads.
    for root in roots:  # One walk for each root.
        for folder, subfolders, names in os.walk(root):
            subfolders[:] = [name for name in subfolders if name not in SKIP_FOLDERS]  # Prune in place.
            paths.extend(
                os.path.join(folder, name).replace("\\", "/") for name in names if name.endswith(READ_SUFFIXES)
            )
    return paths


def main(argv: list[str]) -> int:
    """Check every citation under the named roots.

    Args:
        argv: The roots to check. An empty list reads the default roots.

    Returns:
        Zero when every citation resolves.
    """
    roots = tuple(argv) if argv else DEFAULT_ROOTS  # The caller may narrow the walk.
    # The index always covers every default root. A narrowed run still cites a
    # contract that lives under `specs/`, and an index of the narrowed roots
    # alone would report every such citation as a missing file.
    index = build_index(DEFAULT_ROOTS)  # One index serves every citation of the run.
    counts: dict[str, int] = {}  # One line count for each target.
    findings: list[Finding] = []  # Every citation that fails.
    total = 0  # Every citation the checker read.
    for path in walk(roots):  # One check for each file.
        found, failed = check_file(path, counts, index)
        total += found
        findings.extend(failed)
    for finding in findings:  # Name each failure, so a writer can repair it.
        print(str(finding))
    print(f"{total} citation(s) checked, {len(findings)} unresolved")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
