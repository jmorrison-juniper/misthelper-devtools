"""Find Markdown links that cannot resolve inside a repository.

Mist repositories need the same link guard in each checkout. A pytest file that
derives the root from its own path works only in one repository. This command
uses the target repository root, reads tracked Markdown files with git, and
checks only links that stay inside that repository.
"""

from __future__ import annotations  # Keep annotations stable for console use.

import argparse  # Parse the command line for the console script.
import fnmatch  # Match caller exclude patterns against repository paths.
import logging  # Record each scan step for operator diagnostics.
import os  # Sanitize git environment variables from pre-commit.
import re  # Parse Markdown links with the same narrow rules as the source test.
import subprocess  # Ask git for tracked Markdown files.
import sys  # Return command status and print diagnostics.
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from misthelper_devtools.repository_root import resolve_repository_root

logger = logging.getLogger(__name__)  # Use a module logger so callers can control verbosity.

GIT_TIMEOUT_SECONDS = 30  # Bound each git read so a broken checkout cannot hang the command.
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")  # Inline and image links.
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+)>?\s*$")  # Reference target definitions.
FENCE = re.compile(r"^\s*(```|~~~)")  # Fenced code starts and ends with backticks or tildes.
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")  # Inline code spans, which hold examples.
ATX_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.MULTILINE)  # Headings that GitHub anchors.
HTML_ANCHOR = re.compile(r"<a\s+[^>]*(?:name|id)=[\"']([^\"']+)[\"']", re.IGNORECASE)  # Named HTML anchors.
EXTERNAL_SCHEME = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#|mailto:)", re.IGNORECASE)  # Non-repository targets.


@dataclass(frozen=True, slots=True)
class LinkFailure:
    """One Markdown link that points at a missing file or anchor."""

    path: Path  # Store the repository-relative Markdown path.
    line: int  # Store the source line that holds the broken target.
    target: str  # Store the target exactly as the author wrote it.
    reason: str  # Store the reason for tests and future reports.

    def report_line(self) -> str:
        """Return the console line for this broken link."""
        return f"{self.path.as_posix()}:{self.line}: {self.target} ({self.reason})"  # Match the command contract.


class MarkdownLinkChecker:
    """Check tracked Markdown files for broken repository-local links."""

    def __init__(self, root: Path, excludes: tuple[str, ...] = ()) -> None:
        self.root = root.resolve()  # Keep all file checks under one absolute root.
        self.excludes = excludes  # Store git-style path globs from the caller.
        self._anchor_cache: dict[Path, set[str]] = {}  # Avoid reading the same target file many times.

    def tracked_markdown_files(self, limits: tuple[Path, ...] = ()) -> tuple[Path, ...]:
        """Return the tracked Markdown files that the command must scan."""
        logger.info("Listing tracked Markdown files under %s", self.root)  # Log before the git read.
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "*.md"],  # Ask git for tracked Markdown files only.
                capture_output=True,
                encoding="utf-8",
                env=_git_env(),
                check=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as error:
            stderr = error.stderr.strip() if error.stderr else str(error)
            raise RuntimeError(f"git ls-files failed: {stderr}") from error  # Let the CLI return usage status.
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"git ls-files failed: {error}") from error  # Let the CLI return usage status.
        relative_limits = tuple(self._relative_path(limit) for limit in limits)  # Normalize optional scan roots.
        files = tuple(
            self.root / Path(name)
            for name in completed.stdout.splitlines()
            if name and self._included(Path(name), relative_limits)
        )  # Keep only caller-selected files.
        logger.info("Found %d tracked Markdown file(s) to scan", len(files))  # Log after the git read.
        return files

    def broken_links(self, limits: tuple[Path, ...] = ()) -> tuple[LinkFailure, ...]:
        """Return every repository-local link that points at nothing."""
        failures: list[LinkFailure] = []  # Collect failures in file order for stable reports.
        for path in self.tracked_markdown_files(limits):  # Read each tracked Markdown file once.
            logger.debug("Checking Markdown links in %s", path)  # Log before reading a document.
            try:
                body = _strip_code(path.read_text(encoding="utf-8", errors="replace"))  # Ignore examples.
            except OSError as error:
                logger.warning("Cannot read %s: %s", path, error)  # Keep going when a tracked file vanished.
                continue
            failures.extend(self._failures_in_file(path, body))  # Add broken links from this file.
        logger.info("Markdown link scan found %d broken link(s)", len(failures))  # Log after the scan.
        return tuple(failures)

    def _failures_in_file(self, path: Path, body: str) -> tuple[LinkFailure, ...]:
        """Return the broken links in one stripped Markdown file."""
        failures: list[LinkFailure] = []  # Collect failures from inline links and reference definitions.
        relative = path.relative_to(self.root)  # Print paths relative to the repository root.
        for line_number, line in enumerate(body.splitlines(), start=1):  # Preserve source line numbers.
            for target in _targets_in_line(line):  # A line can hold more than one Markdown link.
                failure = self._failure_for_target(path, relative, line_number, target)  # Resolve the target.
                if failure is not None:  # A present file and anchor creates no report.
                    failures.append(failure)  # Store the broken target.
        return tuple(failures)  # Return immutable failures for tests.

    def _failure_for_target(self, path: Path, relative: Path, line_number: int, target: str) -> LinkFailure | None:
        """Return a failure if one target does not resolve."""
        if EXTERNAL_SCHEME.match(target):  # External links and same-page anchors stay outside this command.
            return None  # A URL or a same-page anchor needs no repository file.
        file_part, _, anchor = target.partition("#")  # Split the path from an optional anchor.
        if file_part:
            resolved = self._resolve_file_part(path, unquote(file_part))  # Decode percent escapes first.
            if not _is_under_root(resolved, self.root) or not resolved.exists():  # Missing or escaping path.
                return LinkFailure(relative, line_number, target, "no such file")  # Report the bad target.
        else:
            resolved = path  # This path is unreachable today because EXTERNAL_SCHEME skips # anchors.
        if anchor and resolved.is_file() and resolved.suffix.lower() == ".md":  # Check anchors inside Markdown.
            if resolved not in self._anchor_cache:  # Build anchors once per target file.
                self._anchor_cache[resolved] = _anchors_of(resolved)  # Read target headings.
            if unquote(anchor).lower() not in self._anchor_cache[resolved]:  # GitHub anchors are lower case.
                return LinkFailure(relative, line_number, target, "no such anchor")  # Report the bad anchor.
        return None  # The target exists.

    def _resolve_file_part(self, path: Path, file_part: str) -> Path:
        """Return the absolute path that the path part of a link names.

        GitHub resolves a link that starts with a slash against the repository
        root, not against the root of the file system.
        """
        if file_part.startswith("/"):  # A root-relative link, such as /docs/guide.md.
            return (self.root / file_part.lstrip("/")).resolve()  # Start from the repository root.
        return (path.parent / file_part).resolve()  # Start from the folder of the linking file.

    def _included(self, relative: Path, limits: tuple[Path, ...]) -> bool:
        """Return True when a relative path passes caller filters."""
        posix = relative.as_posix()  # Git pathspec output uses forward slashes.
        if any(fnmatch.fnmatch(posix, pattern) for pattern in self.excludes):  # Apply repeatable excludes.
            return False  # Skip this file.
        if not limits:  # With no positional paths, scan all tracked Markdown files.
            return True  # Keep the file.
        return any(relative == limit or _is_relative_to(relative, limit) for limit in limits)  # Limit the scan.

    def _relative_path(self, path: Path) -> Path:
        """Return a repository-relative form of a caller path."""
        candidate = path if path.is_absolute() else self.root / path  # Resolve relative paths under the root.
        try:
            return candidate.resolve().relative_to(self.root)  # Keep the normalized path under the root.
        except ValueError:
            return Path("__outside_repository__")  # A path outside the root matches no tracked file.


def _targets_in_line(line: str) -> tuple[str, ...]:
    """Return Markdown link targets found on one line."""
    inline = [match.group(1) for match in INLINE_LINK.finditer(line)]  # Read inline links and image links.
    reference = [match.group(1) for match in REFERENCE_DEFINITION.finditer(line)]  # Read reference definitions.
    return tuple([*inline, *reference])  # Preserve the order found on the line.


def _git_env() -> dict[str, str]:
    """Return an environment that lets `git -C` choose the repository."""
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
        env.pop(name, None)
    for name in tuple(env):
        if name == "GIT_CONFIG_COUNT" or name.startswith("GIT_CONFIG_KEY_") or name.startswith("GIT_CONFIG_VALUE_"):
            env.pop(name, None)
    return env


def _strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code without moving line numbers."""
    lines: list[str] = []  # Keep one output line for each input line.
    inside = False  # Track whether the parser is inside a fenced code block.
    for line in text.splitlines():  # Walk source lines in order.
        if FENCE.match(line):  # A fence toggles the example block state.
            inside = not inside  # Enter or leave the code fence.
            lines.append("")  # Remove the fence line itself.
            continue  # Do not scan the fence as a link.
        lines.append("" if inside else line)  # Blank the code block body.
    stripped = "\n".join(lines)  # Rebuild the document with the same line count.
    return INLINE_CODE.sub(lambda match: " " * len(match.group(0)), stripped)  # Hide inline examples.


def _heading_slug(text: str) -> str:
    """Return the anchor that GitHub makes for a heading."""
    text = re.sub(r"<[^>]+>", "", text)  # Drop inline HTML before slug creation.
    text = re.sub(r"[`*_~]", "", text)  # Drop common Markdown marker characters.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # Keep only linked text.
    text = text.strip().lower()  # GitHub lowercases heading anchors.
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)  # Remove punctuation.
    return re.sub(r"\s", "-", text)  # Replace each whitespace character with one hyphen.


def _anchors_of(path: Path) -> set[str]:
    """Return every anchor that a reader can jump to inside one file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")  # Read the target Markdown file.
    except OSError:
        return set()  # A missing file has no anchors.
    seen: Counter[str] = Counter()  # Track repeated headings for GitHub suffixes.
    names: set[str] = set()  # Collect heading and HTML anchors.
    for heading in ATX_HEADING.findall(_strip_code(raw)):  # Only real headings create anchors.
        base = _heading_slug(heading)  # Convert the heading to its GitHub slug.
        if not base:  # Empty headings create no useful anchor.
            continue  # Skip the empty slug.
        index = seen[base]  # GitHub suffixes repeated headings with a one-based counter after the first.
        seen[base] += 1  # Record that this base slug appeared.
        names.add(base if index == 0 else f"{base}-{index}")  # Store the anchor that GitHub creates.
    names.update(HTML_ANCHOR.findall(raw))  # Named anchors are valid link targets.
    return names  # Return the set for cache storage.


def _is_relative_to(path: Path, other: Path) -> bool:
    """Return True when a relative path is inside another relative path."""
    try:
        path.relative_to(other)  # pathlib has this helper, but this form is explicit for tests.
    except ValueError:
        return False  # The path is not below the requested root.
    return True  # The path is inside the requested root.


def _is_under_root(path: Path, root: Path) -> bool:
    """Return True when a resolved target stays in the repository."""
    try:
        path.relative_to(root)  # Do not let ../ links leave the checkout.
    except ValueError:
        return False  # The resolved path escapes the repository root.
    return True  # The resolved path stays below the root.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for markdown-link-check."""
    parser = argparse.ArgumentParser(
        prog="markdown-link-check",
        description="Check tracked Markdown files for repository-local links that point at nothing.",
    )  # Name the console script in help output.
    parser.add_argument("--root", type=Path, default=None, help="Repository root to scan.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Skip tracked files that match this path glob, such as a wiki tree. Repeat for more globs.",
    )  # No default glob: each caller names its own exempt tree.
    parser.add_argument("paths", nargs="*", type=Path, help="Optional files or folders to scan.")
    return parser  # The CLI parses this object once.


def main(argv: list[str] | None = None) -> int:
    """Run the Markdown link checker and return a process status."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Print concise progress messages.
    parser = build_parser()  # Build the parser once for CLI and tests.
    args = parser.parse_args(argv)  # Let argparse handle usage errors with status 2.
    root = resolve_repository_root(args.root)  # Use the shared repository root resolver.
    checker = MarkdownLinkChecker(root, tuple(args.exclude))  # Store root and exclude settings.
    try:
        failures = checker.broken_links(tuple(args.paths))  # Run the scan.
    except RuntimeError as error:
        print(str(error), file=sys.stderr)  # Print git errors as usage/tool faults.
        return 2  # Reserve status 2 for git or usage errors.
    for failure in failures:  # Print one line per dead link.
        print(failure.report_line())  # Keep the report easy to grep.
    return 1 if failures else 0  # A broken link fails the command.


if __name__ == "__main__":  # Console module entry point.
    raise SystemExit(main())  # Return the CLI status.
