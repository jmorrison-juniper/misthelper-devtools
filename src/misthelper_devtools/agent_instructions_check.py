"""Compare the ``AGENTS.md`` of a repository with the canonical copy of this repository.

Each repository of the owner holds the same ``AGENTS.md``, byte for byte. The
canonical copy is ``templates/agent-instructions/AGENTS.md`` in this
repository. A repository copies that file, and it must not edit the copy. This
command reads the canonical copy at one commit of this repository, compares it
with the local file, and reports each difference.

The command exits with status 0 when the two files match, with status 1 when
they differ, and with status 2 when it cannot read one of the files. On a
difference, it prints a unified difference, so a reader sees each changed
line.

Usage::

    agent-instructions-check --commit <commit-sha>
    agent-instructions-check --canonical ../misthelper-devtools/templates/agent-instructions/AGENTS.md
"""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import argparse  # Parse the command-line options.
import difflib  # Render the difference between the two copies.
import logging  # Record each step for operator diagnostics.
import sys  # Write the report to standard output.
from dataclasses import dataclass  # Store the result in an explicit record.
from pathlib import Path  # Read the local copy.
from typing import TextIO  # Type the output stream.

import requests  # Read the canonical copy from GitHub.

from misthelper_devtools.repository_root import resolve_repository_root  # Find the repository that holds the copy.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control output and level.
DEFAULT_REPOSITORY = "jmorrison-juniper/misthelper-devtools"  # Name the repository that holds the canonical copy.
DEFAULT_COMMIT = "main"  # Compare with the newest canonical copy when the caller names no commit.
CANONICAL_PATH = "templates/agent-instructions/AGENTS.md"  # Name the canonical copy inside this repository.
LOCAL_NAME = "AGENTS.md"  # Name the copy at the root of a consumer repository.
RAW_HOST = "https://raw.githubusercontent.com"  # GitHub serves a file of a public repository from this host.
FETCH_TIMEOUT_SECONDS = 30  # Stop a read that GitHub does not answer, so the check cannot hang a job.
EXIT_MATCH = 0  # The two copies are the same.
EXIT_DRIFT = 1  # The two copies differ.
EXIT_ERROR = 2  # The check could not read a copy.


@dataclass(frozen=True)
class CheckResult:
    """Store the result of one comparison."""

    local_path: Path  # Name the local copy.
    source: str  # Name the canonical copy, as a URL or a path.
    local_text: str  # Keep the local text for the difference.
    canonical_text: str  # Keep the canonical text for the difference.

    @property
    def matches(self) -> bool:
        """Return True when the two copies are the same, byte for byte."""
        return self.local_text == self.canonical_text  # A text comparison of the decoded bytes finds each change.

    def difference(self) -> str:
        """Return the unified difference from the canonical copy to the local copy."""
        lines = difflib.unified_diff(
            self.canonical_text.splitlines(keepends=True),
            self.local_text.splitlines(keepends=True),
            fromfile=self.source,
            tofile=self.local_path.as_posix(),
        )  # Read the canonical copy as the old side, so a plus sign marks a local edit.
        return "".join(lines)  # Join the lines, which keep their own line ends.


def canonical_url(repository: str, commit: str) -> str:
    """Return the URL of the canonical copy at one commit."""
    return f"{RAW_HOST}/{repository}/{commit}/{CANONICAL_PATH}"  # The raw host serves the file text itself.


def read_canonical(repository: str, commit: str) -> str:
    """Return the text of the canonical copy at one commit of the repository."""
    url = canonical_url(repository, commit)  # Build the address of the copy.
    LOGGER.info("Reading the canonical copy from %s", url)  # Log before the network read.
    response = requests.get(url, timeout=FETCH_TIMEOUT_SECONDS)  # Read the file text.
    if response.status_code != 200:  # GitHub answers 404 for an unknown commit or a private repository.
        raise OSError(f"GitHub answered {response.status_code} for {url}.")  # Name the address and the status.
    LOGGER.debug("Read %d bytes from %s", len(response.content), url)  # Log the size after the read.
    return response.content.decode("utf-8")  # Decode the bytes, so the comparison reads the same text as the file.


def read_local(path: Path) -> str:
    """Return the text of the local copy."""
    LOGGER.info("Reading the local copy %s", path)  # Log before the file read.
    text = path.read_text(encoding="utf-8")  # Read the copy with the encoding that the canonical copy uses.
    LOGGER.debug("Read %d characters from %s", len(text), path)  # Log the size after the read.
    return text  # Give the text to the comparison.


def compare(local_path: Path, canonical: Path | None, repository: str, commit: str) -> CheckResult:
    """Read the two copies and return the result of the comparison."""
    if canonical is not None:  # A local canonical path needs no network, for example in a test.
        source = canonical.as_posix()  # Name the path in the report.
        canonical_text = read_local(canonical)  # Read the canonical copy from the disk.
    else:  # The default source is the GitHub repository at the commit.
        source = canonical_url(repository, commit)  # Name the URL in the report.
        canonical_text = read_canonical(repository, commit)  # Read the canonical copy from GitHub.
    local_text = read_local(local_path)  # Read the copy of the consumer repository.
    return CheckResult(local_path, source, local_text, canonical_text)  # Give the result to the report.


def report(result: CheckResult, output: TextIO) -> int:
    """Write the result and return the exit status."""
    if result.matches:  # The copy is current.
        print(f"{result.local_path.as_posix()} matches the canonical copy at {result.source}.", file=output)
        return EXIT_MATCH  # Report no difference.
    print(f"{result.local_path.as_posix()} differs from the canonical copy at {result.source}.", file=output)
    print(result.difference(), end="", file=output)  # The difference lines keep their own line ends.
    print("Copy the canonical file over the local copy, and do not edit the copy.", file=output)
    return EXIT_DRIFT  # Report the difference to the caller.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the check."""
    parser = argparse.ArgumentParser(
        prog="agent-instructions-check",
        description="Compare the AGENTS.md of a repository with the canonical copy of misthelper-devtools.",
    )  # Name the command in the help text.
    parser.add_argument("--commit", default=DEFAULT_COMMIT, help="The commit or branch of misthelper-devtools.")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, help="The repository with the canonical copy.")
    parser.add_argument("--canonical", type=Path, default=None, help="A local canonical copy. Skips the network read.")
    parser.add_argument("--root", type=Path, default=None, help="The repository root that holds the local AGENTS.md.")
    parser.add_argument("--verbose", action="store_true", help="Log each step.")  # Show the log lines.
    return parser  # Give the parser to main.


def main(argv: list[str] | None = None) -> int:
    """Run the check and return the exit status."""
    args = build_parser().parse_args(argv)  # Read the options.
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")  # Set the log.
    local_path = resolve_repository_root(args.root) / LOCAL_NAME  # Find the copy at the root of the repository.
    try:  # A missing file or a failed network read must give a clear message, not a traceback.
        result = compare(local_path, args.canonical, args.repository, args.commit)  # Read and compare the copies.
    except (OSError, UnicodeDecodeError, requests.RequestException) as error:  # Name the fault for the reader.
        print(f"The check could not read a copy: {error}", file=sys.stderr)  # Write the cause to standard error.
        return EXIT_ERROR  # Report the read error.
    return report(result, sys.stdout)  # Write the result and return the status.


if __name__ == "__main__":  # pragma: no cover - the console script calls main directly.
    sys.exit(main())  # Run the check from the module.
