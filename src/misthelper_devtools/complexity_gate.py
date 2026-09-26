"""Fail a build when a code block exceeds a cyclomatic complexity limit.

The gate reads the JSON report of ``radon cc -j``. It checks each top-level
block in the report, and it fails when one block has a complexity above the
limit. The report form and the exit status match the inline gate script that
the Mist repositories used before this tool.

Usage::

    radon cc . -j --exclude '.github/*,docs/*' | complexity-gate --max 15
"""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import argparse  # Parse the command-line options.
import json  # Decode the radon JSON report.
import logging  # Record each gate step for operator diagnostics.
import sys  # Read standard input and write the report.
from dataclasses import dataclass  # Store gate results in explicit records.
from pathlib import Path  # Read a saved report from a file.
from typing import Any, TextIO  # Type the decoded JSON and the output stream.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control output and level.
DEFAULT_MAX_COMPLEXITY = 15  # Match the limit that MistCircuitStats used before this tool.
PASS_MESSAGE = "All functions within complexity threshold."  # Keep the pass line of the earlier inline script.


@dataclass(frozen=True)
class ComplexityViolation:
    """Store one block whose complexity is above the limit."""

    path: str  # Name the file so the reader can open it.
    lineno: int  # Name the first line of the block.
    name: str  # Name the function, method, or class.
    complexity: int  # Store the measured cyclomatic complexity.

    def line(self) -> str:
        """Return the report line for this violation."""
        return f"{self.path}:{self.lineno} {self.name} CC={self.complexity}"  # Match the earlier inline format.


@dataclass(frozen=True)
class AnalysisError:
    """Store one file that radon could not analyze."""

    path: str  # Name the file that radon could not read.
    message: str  # Keep the radon error text for the reader.

    def line(self) -> str:
        """Return the report line for this error."""
        return f"{self.path}: {self.message}"  # Give the file and the radon error on one line.


@dataclass(frozen=True)
class ComplexityGateResult:
    """Store the result of one gate run."""

    max_complexity: int  # Store the limit that the gate applied.
    checked_count: int  # Prove that the gate read blocks.
    violations: tuple[ComplexityViolation, ...]  # Store the blocks above the limit in report order.
    errors: tuple[AnalysisError, ...]  # Store the files that radon could not analyze.

    @property
    def passed(self) -> bool:
        """Return True when no block is above the limit and each file was analyzed."""
        return not self.violations and not self.errors  # A skipped file hides its complexity, so it fails too.

    def report_lines(self) -> tuple[str, ...]:
        """Return the report lines in the order that the gate prints them."""
        lines: list[str] = []  # Collect the lines so each section stays in order.
        if self.violations:  # Print the violation section only when a block is above the limit.
            lines.append(f"Cyclomatic complexity violations (>{self.max_complexity}):")  # Match the earlier header.
            lines.extend(f"  {violation.line()}" for violation in self.violations)  # Indent each violation line.
        if self.errors:  # Print the error section only when radon could not analyze a file.
            lines.append("Files that radon could not analyze:")  # Name the fault before the file list.
            lines.extend(f"  {error.line()}" for error in self.errors)  # Indent each error line.
        if self.passed:  # A clean run prints the pass line only.
            lines.append(PASS_MESSAGE)  # Keep the pass line of the earlier inline script.
        return tuple(lines)  # Return an immutable list for stable tests.


class ComplexityGate:
    """Apply a complexity limit to a decoded radon report."""

    def __init__(self, max_complexity: int = DEFAULT_MAX_COMPLEXITY) -> None:
        if max_complexity < 1:  # A limit below one fails every block, so it is a caller mistake.
            raise ValueError(f"The complexity limit must be 1 or more. It is {max_complexity}.")
        self.max_complexity = max_complexity  # Store the limit for each evaluation.

    def evaluate(self, report: object) -> ComplexityGateResult:
        """Return the gate result for one decoded radon report."""
        if not isinstance(report, dict):  # The radon report maps each path to a block list.
            raise ValueError("The radon report must be a JSON object that maps each path to its blocks.")
        LOGGER.info("Checking %d file(s) against the limit %d", len(report), self.max_complexity)  # Log the scope.
        violations: list[ComplexityViolation] = []  # Collect the blocks above the limit.
        errors: list[AnalysisError] = []  # Collect the files that radon could not analyze.
        checked = 0  # Count the blocks so a report with no blocks is visible.
        for path, blocks in report.items():  # Visit each file in report order, as the earlier script did.
            if isinstance(blocks, dict):  # Radon writes an object with an error key for a file it cannot parse.
                errors.append(AnalysisError(str(path), str(blocks.get("error", blocks))))  # Keep the radon text.
                continue  # A file with no block list has no block to check.
            for block in self._top_level_blocks(path, blocks):  # Check only the top-level entries.
                checked += 1  # Count each block that the gate measured.
                if block.complexity > self.max_complexity:  # A value equal to the limit passes.
                    violations.append(block)  # Record the block above the limit.
        LOGGER.info("Checked %d block(s); %d above the limit", checked, len(violations))  # Log the measured counts.
        return ComplexityGateResult(self.max_complexity, checked, tuple(violations), tuple(errors))  # Return result.

    @staticmethod
    def _top_level_blocks(path: object, blocks: object) -> tuple[ComplexityViolation, ...]:
        """Return the top-level blocks of one file as violation records."""
        if not isinstance(blocks, list):  # Radon writes a list of blocks for each file that it parsed.
            raise ValueError(f"The radon entry for {path} is not a block list.")
        records: list[ComplexityViolation] = []  # Collect one record for each block.
        for block in blocks:  # Radon lists each class and each method at the top level, but not closures.
            entry: dict[str, Any] = block  # Name the decoded JSON object for the field reads.
            records.append(
                ComplexityViolation(str(path), int(entry["lineno"]), str(entry["name"]), int(entry["complexity"]))
            )  # Keep the fields that the report line needs.
        return tuple(records)  # Return an immutable list for stable iteration.


def load_report(stream: TextIO) -> object:
    """Decode one radon JSON report from a text stream."""
    LOGGER.debug("Reading the radon report")  # Log before the read, so a stalled pipe is visible.
    return json.load(stream)  # Decode the whole report, because radon writes one JSON object.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the gate."""
    parser = argparse.ArgumentParser(
        prog="complexity-gate",
        description="Fail when a block in a radon cc JSON report exceeds a cyclomatic complexity limit.",
    )  # Name the command as the console script names it.
    parser.add_argument(
        "--max",
        dest="max_complexity",
        type=int,
        default=DEFAULT_MAX_COMPLEXITY,
        help=f"The highest complexity that passes (default: {DEFAULT_MAX_COMPLEXITY}).",
    )  # Let each repository keep its own limit.
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Read the report from this file. The default is standard input.",
    )  # Let a caller save the radon report first.
    parser.add_argument("-v", "--verbose", action="store_true", help="Log each gate step.")  # Show the gate steps.
    return parser  # Return the parser to main and to the tests.


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stdin: TextIO | None = None) -> int:
    """Run the gate and return the exit status."""
    arguments = build_parser().parse_args(argv)  # Read the options from the command line or the test.
    logging.basicConfig(
        level=logging.INFO if arguments.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )  # Keep the log on standard error, so standard output holds the report only.
    output = stdout if stdout is not None else sys.stdout  # Write to the caller stream in a test.
    try:  # Report a bad input as one clear line and not as a traceback.
        gate = ComplexityGate(arguments.max_complexity)  # Check the limit before the read.
        if arguments.input is not None:  # Read the saved report when the caller names a file.
            with arguments.input.open(encoding="utf-8") as handle:  # Radon writes UTF-8 JSON.
                report = load_report(handle)  # Decode the saved report.
        else:
            report = load_report(stdin if stdin is not None else sys.stdin)  # Decode the piped report.
        result = gate.evaluate(report)  # Apply the limit to each top-level block.
    except (OSError, ValueError, KeyError, TypeError) as error:  # A JSON decode fault is a ValueError too.
        LOGGER.error("The complexity gate could not read the radon report: %s", error)  # Name the fault.
        return 2  # Use a separate status, so a caller can tell a bad input from a violation.
    for line in result.report_lines():  # Print each report line in order.
        print(line, file=output)  # Write the report to standard output, as the earlier script did.
    return 0 if result.passed else 1  # Fail the build when a block is above the limit.


if __name__ == "__main__":  # Run the gate only when the module is executed as a program.
    raise SystemExit(main())  # Return the gate status to the shell.
