"""Scan tracked Markdown files for disallowed control bytes."""

from __future__ import annotations

import os
import subprocess  # nosec B404  # WHY: run one fixed git command, never a shell.
from dataclasses import dataclass
from pathlib import Path

ALLOWED_CONTROL_BYTES = frozenset({0x09, 0x0A, 0x0D})  # Permit tab, newline, and carriage return only.
DISALLOWED_CONTROL_BYTES = frozenset(range(0x20)) - ALLOWED_CONTROL_BYTES | {0x7F}  # Reject hidden controls.


@dataclass(frozen=True)
class ControlByteFinding:
    """Describe one file that contains at least one disallowed control byte."""

    path: Path  # Store the file path so the report names the bad Markdown file.
    offsets: tuple[tuple[int, int], ...]  # Store byte and offset pairs for exact repair evidence.

    def line(self) -> str:
        joined = ", ".join(f"0x{byte:02x}@{offset}" for offset, byte in self.offsets)  # Build compact evidence.
        return f"{self.path}: {joined}"  # Return one stable report line for this file.


@dataclass(frozen=True)
class ControlByteScanReport:
    """Hold the scan result and the measured file count."""

    checked_count: int  # Store how many Markdown files the guard measured.
    findings: tuple[ControlByteFinding, ...]  # Store every file that contains bad control bytes.

    def summary(self) -> str:
        lines = [f"Checked {self.checked_count} Markdown files for disallowed control bytes."]  # State count.
        if self.findings:  # Add exact evidence only when the guard found a bad byte.
            lines.append(f"Found {len(self.findings)} affected files.")  # State affected file count.
            lines.extend(finding.line() for finding in self.findings)  # Add each file and byte offset.
        else:
            lines.append("Found 0 affected files.")  # State that the scan found no affected files.
        return "\n".join(lines)  # Return a report that pytest can print and use in assertions.


class MarkdownControlByteScanner:
    """Scan Markdown files for disallowed control bytes."""

    EXCLUDED_DIRECTORIES = frozenset(
        {".git", ".venv", "node_modules", "__pycache__"}
    )  # Exclude tool, dependency, and cache trees from fallback walks.

    def scan_repository(self, repo_root: Path) -> ControlByteScanReport:
        root = repo_root.resolve()  # Normalize the checkout path before file discovery.
        files = self._tracked_markdown_files(root)  # Prefer the tracked file list for exact repository coverage.
        findings = tuple(finding for path in files if (finding := self._scan_file(path)) is not None)  # Scan files.
        return ControlByteScanReport(len(files), findings)  # Return counts and exact byte offsets.

    def scan_roots(self, roots: tuple[Path, ...]) -> ControlByteScanReport:
        files = tuple(self._markdown_files(roots))  # Materialize the file list so zero scans are detectable.
        findings = tuple(finding for path in files if (finding := self._scan_file(path)) is not None)  # Scan files.
        return ControlByteScanReport(len(files), findings)  # Return counts and exact byte offsets.

    def _tracked_markdown_files(self, repo_root: Path) -> tuple[Path, ...]:
        command = ("git", "ls-files", "*.md")  # Ask Git for tracked Markdown files only.
        environment = os.environ.copy()  # Start from the current environment so Git can find its configuration.
        environment.pop("GIT_DIR", None)  # Ignore test-modified Git state that points outside this checkout.
        environment.pop("GIT_WORK_TREE", None)  # Let Git derive the work tree from the repository path.
        result = subprocess.run(  # nosec B603  # WHY: the command is a fixed tuple with no input, and no shell runs.
            command,
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )  # Run without a shell so no escape sequence can execute.
        paths = [repo_root / line for line in result.stdout.splitlines() if line]  # Convert Git paths to Path objects.
        return tuple(sorted(path for path in paths if path.is_file()))  # Return only files that exist in this checkout.

    def _markdown_files(self, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        files: list[Path] = []  # Accumulate files from every existing skill root.
        for root in roots:  # Visit each root independently so a missing optional root does not hide another root.
            if root.exists():  # Ignore absent roots because some checkouts omit generated skills.
                files.extend(sorted(self._walk_markdown_files(root)))  # Add Markdown files from this root.
        return tuple(files)  # Return an immutable list for deterministic scans.

    def _walk_markdown_files(self, root: Path) -> tuple[Path, ...]:
        files: list[Path] = []  # Accumulate fallback Markdown files for a non-Git scan root.
        for path in root.rglob("*.md"):  # Walk the supplied root when a test scans project-local files.
            if self._is_excluded(path):  # Skip directories that cannot hold shipped Markdown.
                continue  # Do not scan dependency, tool, or cache directories.
            if path.is_file():  # Keep regular files only.
                files.append(path)  # Add the Markdown file to the fallback scan list.
        return tuple(files)  # Return the sorted files to the caller.

    def _is_excluded(self, path: Path) -> bool:
        return any(part in self.EXCLUDED_DIRECTORIES for part in path.parts)  # Match excluded directory names.

    def _scan_file(self, path: Path) -> ControlByteFinding | None:
        data = path.read_bytes()  # Read bytes so Python never normalizes control characters.
        offsets = tuple(
            (offset, byte) for offset, byte in enumerate(data) if byte in DISALLOWED_CONTROL_BYTES
        )  # Keep each offending byte and its byte offset.
        if not offsets:  # Return no finding when this file is clean.
            return None  # Keep the report compact for clean files.
        return ControlByteFinding(path, offsets)  # Return exact repair evidence for the bad file.
