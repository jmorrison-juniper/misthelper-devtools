"""Audit SpecKit task records for unchecked work.

Why: a delivered specification must not keep open task boxes.
"""

from __future__ import annotations  # Keep type hints stable on supported Python releases.

import argparse  # Parse the command line for local and CI use.
import logging  # Record each audit action for operator review.
import re  # Match status lines and checkbox markers without a Markdown parser.
from dataclasses import dataclass  # Keep audit records explicit and typed.
from pathlib import Path  # Keep path handling portable across Windows and Linux.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers can configure output.
CHECKBOX_RE = re.compile(r"^\s*- \[([ xX])\]")  # Count task boxes and nested task boxes only.
FENCE_RE = re.compile(r"^\s*(```|~~~)")  # Ignore examples inside fenced code blocks.
STATUS_RE = re.compile(r"^\*\*Status\*\*:\s*(?P<status>.+)$", re.MULTILINE)  # Read the spec state.
COMPLETE_WORDS = ("implemented", "complete", "delivered", "merged")  # Treat these states as shipped.
INCOMPLETE_WORDS = ("partly", "partial", "specified", "specification", "draft")  # Treat these states as open.


@dataclass(frozen=True)
class TaskCounts:
    """Store the checkbox counts for one task file.

    Why: the reporter and the gate use the same measured values.
    """

    checked: int  # Count finished tasks so drift reports show the total state.
    unchecked: int  # Count open tasks so the gate can find drift.
    malformed: int  # Count malformed boxes so authors can repair bad syntax.


@dataclass(frozen=True)
class SpecFinding:
    """Store the audit result for one specification directory.

    Why: one record keeps the report and the gate decision together.
    """

    name: str  # Use the directory name as the stable allow-list key.
    status: str  # Keep the status text for the human report.
    counts: TaskCounts  # Keep all task counts together for later checks.
    missing_tasks: bool  # Report a missing task file as a record problem.
    allowed: bool  # Suppress the failure when the spec is truly in flight.

    @property
    def is_complete(self) -> bool:
        """Return true when the status says the work shipped.

        Why: the gate must fail only on shipped specifications.
        """
        lowered = self.status.lower()  # Normalize once so word checks match consistently.
        has_complete = any(word in lowered for word in COMPLETE_WORDS)  # Detect a shipped status.
        has_incomplete = any(word in lowered for word in INCOMPLETE_WORDS)  # Detect an open status.
        return has_complete and not has_incomplete  # A partly delivered spec remains in flight.

    @property
    def blocks_merge(self) -> bool:
        """Return true when this finding must fail the audit.

        Why: an unchecked task in a complete spec is the drift to prevent.
        """
        has_open_tasks = self.counts.unchecked > 0  # Only unchecked boxes prove this failure mode.
        return self.is_complete and has_open_tasks and not self.allowed  # Allow real in-flight exceptions.


class AllowList:
    """Read the specifications that may keep unchecked task boxes.

    Why: some shipped specifications keep manual tasks that must stay visible.
    """

    def __init__(self, entries: set[str]) -> None:
        self._entries = entries  # Store normalized keys for fast membership tests.

    @classmethod
    def from_path(cls, path: Path | None) -> AllowList:
        logging.info("Loading the SpecKit audit allow list from %s", path)  # Log the optional file read.
        if path is None or not path.exists():  # A missing optional file means no exceptions.
            logging.debug("Loaded %s allow-list entries", 0)  # Record the empty allow-list size.
            return cls(set())  # Return an empty set so callers have one code path.
        entries = cls._read_entries(path)  # Read only non-comment lines from the file.
        logging.debug("Loaded %s allow-list entries", len(entries))  # Record how many exceptions exist.
        return cls(entries)  # Return the allow-list object for the audit run.

    @staticmethod
    def _read_entries(path: Path) -> set[str]:
        text = path.read_text(encoding="utf-8")  # Read the file as text for portable CI behavior.
        lines = text.splitlines()  # Split once so comments and blank lines can be ignored.
        entries = {line.strip() for line in lines if line.strip() and not line.startswith("#")}  # Keep keys.
        return entries  # Return the normalized entries for lookup.

    def contains(self, spec_dir: Path) -> bool:
        key = spec_dir.name  # The directory name is the preferred stable key.
        path_key = spec_dir.as_posix()  # A path key lets users paste a report line into the file.
        return key in self._entries or path_key in self._entries  # Accept either key form.


class TaskFileScanner:
    """Count task checkboxes while it skips code examples.

    Why: examples in a task file must not affect the gate result.
    """

    def scan(self, path: Path) -> TaskCounts:
        logging.info("Scanning task file %s", path)  # Log the file that the scanner reads.
        text = path.read_text(encoding="utf-8")  # Read one file so the scanner can count each line.
        counts = self._count_lines(text.splitlines())  # Count only valid boxes outside code fences.
        logging.debug("Scanned %s with %s unchecked tasks", path, counts.unchecked)  # Log the result.
        return counts  # Return counts for the reporter and the gate.

    def _count_lines(self, lines: list[str]) -> TaskCounts:
        checked = 0  # Track checked task boxes for the report total.
        unchecked = 0  # Track unchecked task boxes for the drift decision.
        malformed = 0  # Track malformed task boxes for a repair hint.
        in_fence = False  # Track fenced code so examples do not count.
        for line in lines:  # Read the task file in order.
            in_fence = self._toggle_fence(line, in_fence)  # Update code-fence state before count checks.
            if in_fence or FENCE_RE.match(line):  # Skip fence lines and the lines inside the fence.
                continue  # Leave code examples out of the task count.
            checked, unchecked, malformed = self._count_line(line, checked, unchecked, malformed)  # Add line.
        return TaskCounts(checked=checked, unchecked=unchecked, malformed=malformed)  # Return the count set.

    def _toggle_fence(self, line: str, in_fence: bool) -> bool:
        if FENCE_RE.match(line):  # A fence marker flips the scanner state.
            return not in_fence  # Enter or leave the fenced code block.
        return in_fence  # Keep the current state for normal lines.

    def _count_line(self, line: str, checked: int, unchecked: int, malformed: int) -> tuple[int, int, int]:
        match = CHECKBOX_RE.match(line)  # Match only well-formed task boxes.
        if match and match.group(1) == " ":  # A blank mark means the task is open.
            return checked, unchecked + 1, malformed  # Add one unchecked task.
        if match:  # A lowercase or uppercase x means the task is done.
            return checked + 1, unchecked, malformed  # Add one checked task.
        if re.match(r"^\s*- \[[^\]]*\]", line):  # A bracketed marker that is not valid is malformed.
            return checked, unchecked, malformed + 1  # Report the malformed marker.
        return checked, unchecked, malformed  # Leave non-task lines unchanged.


class SpecTaskAudit:
    """Audit all SpecKit task records under one repository root.

    Why: one class owns scanning, reporting, and the exit code.
    """

    def __init__(self, root: Path, allow_list: AllowList) -> None:
        self._root = root  # Store the checkout root for relative report paths.
        self._allow_list = allow_list  # Store approved exceptions for the run.
        self._scanner = TaskFileScanner()  # Reuse one scanner for all task files.

    def run(self) -> int:
        logging.info("Starting the SpecKit task audit under %s", self._root)  # Log the audit start.
        findings = self._findings()  # Measure every specification directory.
        self._report(findings)  # Print a human-readable report for CI logs.
        failures = [finding for finding in findings if finding.blocks_merge]  # Select blocking drift.
        logging.debug("The SpecKit task audit found %s blocking specs", len(failures))  # Log failure count.
        return 1 if failures else 0  # Fail only when shipped specs still hold open tasks.

    def _findings(self) -> list[SpecFinding]:
        specs_dir = self._root / "specs"  # Specs live below this directory by project convention.
        spec_dirs = sorted(path for path in specs_dir.iterdir() if path.is_dir())  # Inspect each spec folder.
        findings = [self._finding_for(spec_dir) for spec_dir in spec_dirs]  # Build one finding per folder.
        return findings  # Return all findings so the report includes missing files.

    def _finding_for(self, spec_dir: Path) -> SpecFinding:
        status = self._status_for(spec_dir)  # Read the status line before task validation.
        tasks_path = spec_dir / "tasks.md"  # The task record uses this fixed file name.
        allowed = self._allow_list.contains(spec_dir)  # Check whether the spec is an approved exception.
        if not tasks_path.exists():  # Missing task records get a finding with zero counts.
            return SpecFinding(spec_dir.name, status, TaskCounts(0, 0, 0), True, allowed)  # Report missing file.
        counts = self._scanner.scan(tasks_path)  # Count valid boxes outside code fences.
        return SpecFinding(spec_dir.name, status, counts, False, allowed)  # Return the completed finding.

    def _status_for(self, spec_dir: Path) -> str:
        spec_path = spec_dir / "spec.md"  # The status line lives in the feature specification.
        if not spec_path.exists():  # A spec folder can exist before a spec file is written.
            return "Unknown"  # Unknown status must not fail as complete.
        text = spec_path.read_text(encoding="utf-8")  # Read the spec file to find the status line.
        match = STATUS_RE.search(text)  # Search for the standard SpecKit status field.
        return match.group("status").strip() if match else "Unknown"  # Return a safe default when absent.

    def _report(self, findings: list[SpecFinding]) -> None:
        open_findings = [finding for finding in findings if finding.counts.unchecked]  # Report open task specs.
        missing = [finding for finding in findings if finding.missing_tasks]  # Report folders with no tasks.
        print("SpecKit task audit")  # Start with a stable heading for CI logs.
        self._print_open_findings(open_findings)  # Print each unchecked-task finding.
        self._print_missing_findings(missing)  # Print each missing task record.

    def _print_open_findings(self, findings: list[SpecFinding]) -> None:
        if not findings:  # A clean tree needs a clear report line.
            print("No unchecked task boxes found.")  # Tell CI readers that the scan ran.
            return  # Stop before the loop so the report stays short.
        for finding in findings:  # Print every spec with an unchecked task.
            marker = "allowed" if finding.allowed else "open"  # Show whether the allow list covers it.
            print(f"specs/{finding.name}: {finding.counts.unchecked} unchecked tasks ({marker})")  # Report count.

    def _print_missing_findings(self, findings: list[SpecFinding]) -> None:
        for finding in findings:  # Print each missing task file for repair visibility.
            print(f"specs/{finding.name}: missing tasks.md")  # Name the missing task record.


class Command:
    """Parse command arguments and start the audit.

    Why: the module can run as a script without a wrapper function.
    """

    @classmethod
    def main(cls) -> int:
        parser = cls._parser()  # Build the parser near the entry point.
        args = parser.parse_args()  # Read the operator options from the command line.
        level = logging.INFO if args.verbose else logging.WARNING  # Keep normal reports concise unless asked.
        logging.basicConfig(level=level, format="%(levelname)s:%(message)s")  # Enable action logs for operators.
        allow_list = AllowList.from_path(args.allow_list)  # Load optional exceptions before the audit.
        audit = SpecTaskAudit(args.root, allow_list)  # Build the audit with the selected root.
        return audit.run()  # Return the audit exit code to the shell.

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Audit SpecKit task checkboxes.")  # Describe the tool.
        parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to scan.")  # Root.
        parser.add_argument("--allow-list", type=Path, default=None, help="File with allowed spec names.")  # List.
        parser.add_argument("--verbose", action="store_true", help="Show detailed audit logging.")  # Logs.
        return parser  # Return the configured parser to the command entry point.


if __name__ == "__main__":  # Run only when the file is invoked as a script.
    raise SystemExit(Command.main())  # Convert the audit result to the process exit code.
