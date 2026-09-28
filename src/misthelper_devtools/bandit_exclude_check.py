"""Check that Bandit excludes match on Windows and POSIX paths.

Bandit compares an exclude entry against the scanned path with a plain
substring test. It does not normalize path separators. A repository can then
pass on Linux and scan a different file set on Windows. This command checks
that each exclude entry with a separator also has the other spelling.

Each --include-sample path must stay inside the Bandit scan scope. Name a
product source file in both spellings, so an exclude entry that grows too wide
fails the check on Linux and on Windows.
"""

from __future__ import annotations

import argparse  # Parse the console flags.
import logging  # Record the checks that the command runs.
import sys  # Return process status through the console script.
import tomllib  # Read pyproject.toml without an extra dependency.
from pathlib import Path  # Hold caller paths without hard-coded separators.
from typing import Any  # Type the decoded TOML payload.

SUCCESS_MESSAGE = "bandit exclude_dirs pairs both path separator spellings"
ERROR_PREFIX = "bandit exclude check failed: "

logger = logging.getLogger(__name__)  # Use a module logger so callers can route output.


def read_excludes(pyproject: Path) -> list[str]:
    """Return the Bandit exclude list from one pyproject file."""
    logger.info("Reading Bandit excludes from %s", pyproject)  # Log before reading caller config.
    config: dict[str, Any] = tomllib.loads(pyproject.read_text(encoding="utf-8"))  # Parse the TOML config.
    tool = config.get("tool", {})  # The table can be absent in a fixture.
    if not isinstance(tool, dict):  # A non-table is invalid TOML shape for this check.
        raise ValueError("[tool] must be a TOML table.")
    bandit = tool.get("bandit", {})  # The bandit table can be absent in a fixture.
    if not isinstance(bandit, dict):  # A non-table is invalid TOML shape for this check.
        raise ValueError("[tool.bandit] must be a TOML table.")
    excludes = bandit.get("exclude_dirs", [])  # Match Bandit's key name.
    if not isinstance(excludes, list) or not all(isinstance(entry, str) for entry in excludes):
        raise ValueError("[tool.bandit].exclude_dirs must be a list of strings.")
    logger.debug("Read %d Bandit exclude entries", len(excludes))  # Log after reading caller config.
    return list(excludes)  # Return a plain list, so membership is stable in tests.


def separator_pair_failures(excludes: list[str]) -> list[str]:
    """Return messages for exclude entries that miss the paired separator."""
    logger.info("Checking Bandit exclude separator pairs")  # Log before the pure check.
    failures: list[str] = []  # Collect each problem, so one run reports all of them.
    for entry in excludes:  # Separator-free entries match on both platforms.
        if "/" not in entry and "\\" not in entry:
            continue  # No separator, so no platform gap.
        posix = entry.replace("\\", "/")  # The Linux spelling of this entry.
        windows = posix.replace("/", "\\")  # The Windows spelling of this entry.
        for spelling in (posix, windows):  # Both spellings must be present.
            if spelling not in excludes:
                failures.append(f"exclude_dirs misses the spelling {spelling!r}")
    logger.debug("Found %d Bandit separator pair failure(s)", len(failures))  # Log after the pure check.
    return failures


def sample_failures(samples: list[str], excludes: list[str]) -> list[str]:
    """Return messages for sample files that Bandit would exclude."""
    if not samples:  # No sample needs Bandit, so no optional import is required.
        return []
    logger.info("Checking %d Bandit include sample(s)", len(samples))  # Log before the lazy import.
    try:
        from bandit.core.manager import _is_file_included
    except ImportError as error:
        raise RuntimeError("Bandit is required when --include-sample is used. Install the dev extra.") from error
    failures = [
        f"bandit unexpectedly excludes {sample}"
        for sample in samples
        if not _is_file_included(sample, ["*.py"], excludes)
    ]  # Use the same matcher as Bandit.
    logger.debug("Found %d Bandit sample failure(s)", len(failures))  # Log after the sample checks.
    return failures


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="bandit-exclude-check",
        description="Check that Bandit exclude_dirs entries work with both path separators.",
    )  # Name the parser for help and errors.
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to the pyproject.toml file that holds [tool.bandit].",
    )  # Let a test or caller name its config.
    parser.add_argument(
        "--include-sample",
        action="append",
        default=[],
        help="Python path that Bandit must include. Repeat for each path and each separator spelling.",
    )  # No default sample: the caller names its own product files.
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Bandit exclude check."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Keep the CI output concise.
    args = build_parser().parse_args(argv)
    try:
        excludes = read_excludes(args.pyproject)
        samples = [str(item) for item in args.include_sample]
        failures = [*separator_pair_failures(excludes), *sample_failures(samples, excludes)]
    except (OSError, RuntimeError, ValueError) as error:
        print(f"{ERROR_PREFIX}{error}", file=sys.stderr)
        return 2
    if failures:
        print(ERROR_PREFIX + "; ".join(failures), file=sys.stderr)
        return 1
    print(SUCCESS_MESSAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
