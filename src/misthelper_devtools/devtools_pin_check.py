"""Compare the misthelper-devtools requirement pin with the commit of a shared workflow.

A consumer repository can pin this package in two places. Its workflows call
the shared workflows at a commit, and its ``requirements-dev.txt`` file
installs the package from Git at a commit. Dependabot moves the workflow pins,
but it does not move a requirement that names a Git commit.

The shared workflows install this package from their own commit, so the gates
still pass. But the local tooling of the repository then stays at the old
commit. This command finds that difference. For each requirement line that
installs the package from Git at a different commit, it writes a GitHub Actions
warning. The warning names the file, the line, and the new text of the line.
The new text leaves out an end-of-line comment, such as ``# v0.5.2``, because
that comment can name the old release.

The command never fails a build, because the difference does not break CI. The
reviewer of the Dependabot pull request changes the line. The command reads
only the files that it names, and it does not follow a ``-r`` include.

Usage::

    devtools-pin-check --commit "${WORKFLOW_SHA}" --repository "${WORKFLOW_REPOSITORY}"
"""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import argparse  # Parse the command-line options.
import logging  # Record each check step for operator diagnostics.
import os  # Read the GitHub Actions variables of the job.
import re  # Find the Git requirement of the package in each line.
import sys  # Write the report to standard output.
from collections.abc import Mapping  # Type the environment that a test can supply.
from dataclasses import dataclass  # Store each pin in an explicit record.
from pathlib import Path  # Read the requirement files.
from typing import TextIO  # Type the output stream.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control output and level.
DEFAULT_REPOSITORY = "jmorrison-juniper/misthelper-devtools"  # Name the repository that publishes this package.
DEFAULT_FILES = ("requirements-dev.txt",)  # Read the file where MistHelper and MistCircuitStats pin the package.
MIN_COMMIT_LENGTH = 7  # Git abbreviates a commit name to seven hexadecimal digits at least.
ANNOTATION_TITLE = "Stale misthelper-devtools pin"  # Give each warning the same title, so a reader can find it.
HEX_DIGITS = re.compile(r"[0-9a-f]+")  # Match a lower-case commit name.
COMMENT_START = re.compile(r"(?:^|\s)#")  # Pip reads a # at the line start or after a space as a comment.


@dataclass(frozen=True)
class RequirementPin:
    """Store one requirement line that installs the package from Git."""

    path: Path  # Name the file that holds the line.
    lineno: int  # Name the line, counted from 1.
    code: str  # Keep the part of the line that pip reads, so the new text keeps the name, the URL, and the markers.
    ref: str  # Store the Git reference after the @ sign.
    start: int  # Store the column where the reference starts.
    end: int  # Store the column after the reference.
    comment: str = ""  # Keep the end-of-line comment, because it can name the old release.

    def matches(self, commit: str) -> bool:
        """Return True when the reference names the given commit."""
        ref = self.ref.lower()  # Git accepts a commit name in either case.
        if len(ref) < MIN_COMMIT_LENGTH or HEX_DIGITS.fullmatch(ref) is None:  # A tag or a branch is no commit name.
            return False  # A tag or a branch can move, so it never matches the commit of the workflow.
        return commit.lower().startswith(ref)  # An abbreviated commit name matches the start of the full name.

    def replacement(self, commit: str) -> str:
        """Return the part of the line that pip reads, with the reference changed to the given commit."""
        return self.code[: self.start] + commit + self.code[self.end :]  # The comment can be old, so leave it out.


@dataclass(frozen=True)
class PinCheckResult:
    """Store the result of one check."""

    commit: str  # Store the commit of the shared workflow.
    pins: tuple[RequirementPin, ...]  # Store each pin in file order.
    notes: tuple[str, ...]  # Store one line for each file that added no pin because the check could not read it.

    @property
    def stale(self) -> tuple[RequirementPin, ...]:
        """Return each pin that names a different commit."""
        return tuple(pin for pin in self.pins if not pin.matches(self.commit))  # Keep the file order.


def pin_pattern(repository: str) -> re.Pattern[str]:
    """Return the pattern that finds a Git requirement of the repository."""
    return re.compile(
        r"git\+(?:https://|ssh://git@)github\.com[/:]" + re.escape(repository) + r"(?:\.git)?@(?P<ref>[^\s#;@]+)",
        re.IGNORECASE,
    )  # Match the HTTPS form and the SSH form, with or without the .git suffix.


def find_pins(path: Path, text: str, pattern: re.Pattern[str]) -> tuple[RequirementPin, ...]:
    """Return each pin in the text of one requirement file."""
    pins: list[RequirementPin] = []  # Collect the pins in line order.
    for lineno, line in enumerate(text.splitlines(), start=1):  # Pip reads one requirement on each line.
        comment = COMMENT_START.search(line)  # Find the comment, because pip ignores it.
        code = (line if comment is None else line[: comment.start()]).rstrip()  # Keep the part that pip reads.
        remark = "" if comment is None else line[comment.start() :].strip()  # Keep the comment for the message.
        for match in pattern.finditer(code):  # Read each match, so a second URL on a line is not lost.
            pins.append(
                RequirementPin(path, lineno, code, match.group("ref"), match.start("ref"), match.end("ref"), remark)
            )  # The columns of the code part are the columns of the full line too.
    return tuple(pins)  # Return an immutable list for stable tests.


def check_pins(commit: str, repository: str, files: tuple[Path, ...]) -> PinCheckResult:
    """Read each requirement file and return the pins of the repository."""
    pattern = pin_pattern(repository)  # Build the pattern once for all files.
    pins: list[RequirementPin] = []  # Collect the pins of all files in order.
    notes: list[str] = []  # Collect one line for each file that the check could not read.
    for path in files:  # Read the files in the order that the caller gives.
        if not path.is_file():  # A repository without the file has no pin in it.
            notes.append(f"The file {path.as_posix()} does not exist.")  # Tell the reader why the file adds no pin.
            continue  # Read the next file.
        try:  # A bad file must not stop the check of the other files.
            text = path.read_text(encoding="utf-8-sig")  # Pip accepts a byte order mark, so remove it.
        except (OSError, UnicodeDecodeError) as error:  # Name the fault and continue.
            notes.append(f"The check could not read {path.as_posix()}: {error}")  # Keep the reason for the reader.
            continue  # Read the next file.
        pins.extend(find_pins(path, text, pattern))  # Add the pins of this file.
    LOGGER.info("Found %d pin(s) of %s in %d file(s)", len(pins), repository, len(files))  # Log the scope.
    return PinCheckResult(commit, tuple(pins), tuple(notes))  # Return the result to main and to the tests.


def escape_data(value: str) -> str:
    """Escape the message of a workflow command."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")  # Use the rules of the Actions toolkit.


def escape_property(value: str) -> str:
    """Escape a property value of a workflow command."""
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")  # A property value cannot hold : or , either.


def annotation_path(path: Path, workspace: str | None) -> str:
    """Return the path of the file from the repository root, as an annotation needs it."""
    if workspace:  # In a job, GitHub reads an annotation path from the workspace root.
        try:  # A file outside the workspace keeps the path that the caller gave.
            return path.resolve().relative_to(Path(workspace).resolve()).as_posix()  # Give the path from the root.
        except ValueError:  # The file is not below the workspace.
            LOGGER.debug("%s is outside the workspace %s", path, workspace)  # Log why the path stays the same.
    return path.as_posix()  # Use forward slashes on each platform.


def report(result: PinCheckResult, repository: str, workspace: str | None, output: TextIO) -> None:
    """Write one line for each pin, and one warning for each stale pin."""
    for note in result.notes:  # Name each file that added no pin.
        print(note, file=output)  # Write the note to the job log.
    if not result.pins:  # A repository that does not pin the package has nothing to compare.
        print(f"No requirement line installs {repository} from Git. The check has nothing to compare.", file=output)
        return  # Write no warning, because there is no pin to change.
    for pin in result.pins:  # Report each pin in file order.
        location = annotation_path(pin.path, workspace)  # Name the file from the repository root.
        if pin.matches(result.commit):  # A pin at the workflow commit needs no change.
            print(
                f"{location} line {pin.lineno} installs {repository} at {pin.ref}, as this workflow does.", file=output
            )
            continue  # Check the next pin.
        remark = (
            f'The line also holds the comment "{pin.comment}". Keep the comment only if it is still correct. '
            if pin.comment
            else ""
        )  # A comment such as "# v0.5.2" names the old release, so the new text leaves it out.
        message = (
            f"{location} line {pin.lineno} installs {repository} at {pin.ref}, but this workflow runs commit "
            f"{result.commit}. {remark}Change the line to: {pin.replacement(result.commit)}"
        )  # Put the new text last, so the reader can copy it.
        properties = f"file={escape_property(location)},line={pin.lineno},title={escape_property(ANNOTATION_TITLE)}"
        print(f"::warning {properties}::{escape_data(message)}", file=output)  # Attach the warning to the line.
    LOGGER.info("%d of %d pin(s) differ from %s", len(result.stale), len(result.pins), result.commit)  # Log counts.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the check."""
    parser = argparse.ArgumentParser(
        prog="devtools-pin-check",
        description=(
            "Compare each Git requirement pin of misthelper-devtools with the commit of a shared workflow. "
            "The command writes a warning for each pin that differs, and it always exits with status 0."
        ),
    )  # Name the command as the console script names it.
    parser.add_argument(
        "--commit",
        required=True,
        help="The commit of the shared workflow, for example the job.workflow_sha value of the job.",
    )  # Compare each pin with this commit.
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"The owner/name of the repository that publishes the package (default: {DEFAULT_REPOSITORY}).",
    )  # Let a fork compare the pins of its own repository.
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="The requirement files to read (default: requirements-dev.txt).",
    )  # Let a repository name a different requirement file.
    parser.add_argument("-v", "--verbose", action="store_true", help="Log each check step.")  # Show the check steps.
    return parser  # Return the parser to main and to the tests.


def main(argv: list[str] | None = None, stdout: TextIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the check and return the exit status, which is always 0."""
    arguments = build_parser().parse_args(argv)  # Read the options from the command line or the test.
    logging.basicConfig(
        level=logging.INFO if arguments.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )  # Keep the log on standard error, so standard output holds the report only.
    output = stdout if stdout is not None else sys.stdout  # Write to the caller stream in a test.
    environment = environ if environ is not None else os.environ  # Read the job variables of the caller in a test.
    commit = arguments.commit.strip().lower()  # The job context gives the full name in lower case.
    if len(commit) < MIN_COMMIT_LENGTH or HEX_DIGITS.fullmatch(commit) is None:  # An empty context gives no commit.
        print(
            f"The workflow commit {arguments.commit!r} is not a commit name. The check has nothing to compare.",
            file=output,
        )
        return 0  # A missing commit must not fail the gate that runs the check.
    files = tuple(arguments.files) or tuple(Path(name) for name in DEFAULT_FILES)  # Read the default file.
    result = check_pins(commit, arguments.repository, files)  # Find each pin in the files.
    report(result, arguments.repository, environment.get("GITHUB_WORKSPACE"), output)  # Write the lines.
    return 0  # A stale pin is a notice for the reviewer and not a gate failure.


if __name__ == "__main__":  # Run the check only when the module is executed as a program.
    raise SystemExit(main())  # Return the status to the shell.
