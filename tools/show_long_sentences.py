"""Print every sentence that passes the STE length limit, with its text.

Why:
    The linter reports a line number and a word count. It never prints the
    sentence. A writer who repairs issue #1993 must find the sentence by hand,
    and a long docstring holds many candidates on nearby lines.

    This helper prints the sentence itself, so the writer reads the exact text
    that the linter counted.

Usage:
    .venv\\Scripts\\python.exe -m tools.show_long_sentences <path> [<path> ...]
"""

from __future__ import annotations

import sys

from tools.ste_linter.parsing import DocumentBuilder

LIMIT_DESCRIPTIVE = 25  # The limit that STE rule 4.4 sets for a descriptive sentence.
LIMIT_PROCEDURAL = 20  # The limit that STE rule 4.4 sets for an instruction.


def limit_for(mode: str) -> int:
    """Return the word limit of one writing mode.

    Args:
        mode: The writing mode that the parser decided.

    Returns:
        The word limit that applies to that mode.
    """
    return LIMIT_PROCEDURAL if mode == "procedural" else LIMIT_DESCRIPTIVE


def report(path: str) -> int:
    """Print every long sentence of one file and return the count.

    Args:
        path: The file to read.

    Returns:
        The count of sentences that pass the limit.
    """
    with open(path, encoding="utf-8") as handle:  # The linter reads UTF-8 only.
        document = DocumentBuilder().build(path, handle.read())  # Parse the prose the same way the linter does.
    found = 0  # Counts the long sentences of this file.
    for sentence in document.sentences:  # Walk every sentence the parser found.
        limit = limit_for(sentence.mode)  # An instruction carries a shorter limit.
        if sentence.word_count <= limit:  # A sentence inside the limit needs no repair.
            continue
        found += 1  # One more sentence to repair.
        print(f"L{sentence.line}  {sentence.word_count} words  ({sentence.mode})")  # The location and the count.
        print(f"    {sentence.text}")  # The exact text that the linter counted.
    return found


def main(argv: list[str]) -> int:
    """Print the long sentences of every named file.

    Args:
        argv: The file paths from the command line.

    Returns:
        The process exit code. Zero means no long sentence.
    """
    if not argv:  # A call with no path states its own usage.
        print("usage: python -m tools.show_long_sentences <path> [<path> ...]")
        return 2
    total = 0  # Counts the long sentences of every file.
    for path in argv:  # One report for each file.
        print(f"== {path}")  # The file heading separates the reports.
        total += report(path)  # Add the count of this file.
    print(f"total: {total}")  # The writer reads one number at the end.
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
