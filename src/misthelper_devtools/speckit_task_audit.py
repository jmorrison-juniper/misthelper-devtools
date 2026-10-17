"""Audit SpecKit task records for unchecked work.

Why: a delivered specification must not keep open task boxes.

Usage::

    speckit-task-audit --root path/to/repository
"""

from __future__ import annotations  # Keep type hints stable on supported Python releases.

import argparse  # Parse the command line for local and CI use.
import logging  # Record each audit action for operator review.
import re  # Match status lines and checkbox markers without a Markdown parser.
import sys  # Send input failures to the command error stream.
from dataclasses import dataclass  # Keep audit records explicit and typed.
from pathlib import Path  # Keep path handling portable across Windows and Linux.

logger = logging.getLogger(__name__)  # Use a module logger so tests can identify this log source.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers can configure output.
CHECKBOX_RE = re.compile(r"^\s*- \[([ xX])\]")  # Count task boxes and nested task boxes only.
FENCE_RE = re.compile(r"^\s*(```|~~~)")  # Ignore examples inside fenced code blocks.
STATUS_RE = re.compile(r"^\*\*Status\*\*:\s*(?P<status>.+)$", re.MULTILINE)  # Read the spec state.
CITATION_RE = re.compile(  # Find proof paths only in evidence notes, not in task descriptions.
    r"(?:delivered|evidence):\s*`?(?P<path>[^`)]+)`?",  # Capture the cited path after the proof label.
    re.IGNORECASE,  # Accept existing task records that use title-case labels.
)
COMPLETE_WORDS = ("implemented", "complete", "delivered", "merged")  # Treat these states as shipped.
INCOMPLETE_WORDS = ("partly", "partial", "specified", "specification", "draft")  # Treat these states as open.
MANAGED_ROOT_NAMES = frozenset({"indexes", "live", "numbered", "skills"})  # Skip managed route containers.


class AuditInputError(Exception):
    """Report an unreadable or invalid audit input.

    Why: the command must fail clearly instead of reporting a partial scan.
    """


class SpecDirectoryDiscovery:
    """Find legacy and grouped SpecKit feature directories.

    Why: managed route folders are containers, not feature records.
    """

    def __init__(self, specs_dir: Path) -> None:
        self._specs_dir = specs_dir  # Store the root used to label each record.

    def discover(self) -> list[Path]:
        logger.info("Discovering SpecKit feature records under %s", self._specs_dir)  # Log before traversal.
        records: list[Path] = []  # Collect direct and nested feature directories.
        for directory in self._children(self._specs_dir):  # Preserve direct legacy feature records.
            is_legacy_root = directory.name not in MANAGED_ROOT_NAMES  # Keep old direct feature folders.
            self._walk(directory, records, is_legacy_root)  # Discover grouped records beneath route containers.
        self._reject_nested_identities(records)  # Stop when a feature contains another feature record.
        logger.debug("Discovered %s SpecKit feature records", len(records))  # Report the examined record count.
        return sorted(records)  # Keep reports stable across operating systems.

    def _walk(self, directory: Path, records: list[Path], is_legacy_root: bool) -> bool:
        children = self._children(directory)  # Read children before classifying a possible group directory.
        has_marker = self._has_feature_marker(directory)  # Detect this directory's feature files.
        has_descendant_marker = False  # Track feature files below this directory.
        for child in children:  # Search nested route groups and validate readable folders.
            child_has_marker = self._walk(child, records, False)  # Nested records require feature markers.
            has_descendant_marker = child_has_marker or has_descendant_marker  # Preserve any nested marker.
        is_legacy_without_nested_record = is_legacy_root and not has_descendant_marker  # Keep old missing-file reports.
        if has_marker or is_legacy_without_nested_record:  # Identify records without treating groups as features.
            records.append(directory)  # Include this directory in the audit.
        return has_marker or has_descendant_marker  # Let parents distinguish groups from legacy records.

    def _children(self, directory: Path) -> list[Path]:
        try:
            children = (  # Exclude symbolic links so traversal remains inside the repository tree.
                child for child in directory.iterdir() if child.is_dir() and not child.is_symlink()
            )
            return sorted(children)  # Visit child folders in stable order.
        except OSError as error:
            raise AuditInputError(f"Cannot scan SpecKit directory {directory}: {error}") from error  # Fail closed.

    @staticmethod
    def _has_feature_marker(directory: Path) -> bool:
        has_spec = (directory / "spec.md").is_file()  # A specification file marks a feature directory.
        has_tasks = (directory / "tasks.md").is_file()  # A task file also marks a feature directory.
        return has_spec or has_tasks  # Ignore route folders without feature files.

    def _reject_nested_identities(self, records: list[Path]) -> None:
        record_set = set(records)  # Use discovered paths to detect a feature inside another feature.
        for record in records:  # Validate each record against its directory ancestors.
            for parent in record.parents:  # Only records below a feature can form an invalid identity.
                if parent in record_set:  # A parent and child cannot both represent independent features.
                    parent_label = self._label(parent)  # Format the parent as a repository-relative path.
                    record_label = self._label(record)  # Format the child as a repository-relative path.
                    raise AuditInputError(
                        f"Nested feature identity: {parent_label} contains {record_label}"
                    )  # Stop before returning a partial audit.

    def _label(self, directory: Path) -> str:
        relative = directory.relative_to(self._specs_dir).as_posix()  # Keep labels portable and repository-relative.
        return f"specs/{relative}"  # Match the audit report path format.


@dataclass(frozen=True)
class TaskCounts:
    """Store the checkbox counts for one task file.

    Why: the reporter and the gate use the same measured values.
    """

    checked: int  # Count finished tasks so drift reports show the total state.
    unchecked: int  # Count open tasks so the gate can find drift.
    malformed: int  # Count malformed boxes so authors can repair bad syntax.


@dataclass(frozen=True)
class CitationFinding:
    """Store one checked task citation that does not resolve.

    Why: the report must name the bad proof path and the source line.
    """

    line_number: int  # Show the exact task line so an author can repair the record.
    citation: str  # Keep the unresolved citation text from the task record.


@dataclass(frozen=True)
class SpecFinding:
    """Store the audit result for one specification directory.

    Why: one record keeps the report and the gate decision together.
    """

    relative_path: str  # Keep the record identity unique within grouped routes.
    status: str  # Keep the status text for the human report.
    counts: TaskCounts  # Keep all task counts together for later checks.
    missing_tasks: bool  # Report a missing task file as a record problem.
    allowed: bool  # Suppress the failure when the spec is truly in flight.
    citation_errors: tuple[CitationFinding, ...] = ()  # Keep unresolved proof paths for the guard rule.

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
        has_bad_citations = bool(self.citation_errors)  # Missing proof files are blocking record defects.
        has_blocking_records = has_open_tasks or has_bad_citations  # Combine all record-integrity failures.
        return self.is_complete and has_blocking_records and not self.allowed  # Allow real in-flight exceptions.


class CitationValidator:
    """Validate proof paths on checked task boxes.

    Why: a checked task must not point to evidence that the tree does not hold.
    """

    def __init__(self, root: Path) -> None:
        self._root = root  # Store the checkout root for repository-relative citations.

    def find_errors(self, path: Path) -> tuple[CitationFinding, ...]:
        logger.info("Validating checked task citations in %s", path)  # Log the file before path checks.
        try:
            lines = path.read_text(encoding="utf-8").splitlines()  # Read lines so reports can name line numbers.
        except OSError as error:
            raise AuditInputError(f"Cannot read required task file {path}: {error}") from error  # Fail closed.
        errors = tuple(self._errors_from_lines(path, lines))  # Collect missing paths outside fenced blocks.
        logger.debug("Found %s missing checked-task citations in %s", len(errors), path)  # Log the count.
        return errors  # Return immutable findings so callers do not mutate audit results.

    def _errors_from_lines(self, path: Path, lines: list[str]) -> list[CitationFinding]:
        errors: list[CitationFinding] = []  # Collect unresolved proof paths for this task file.
        in_fence = False  # Track fenced code so example citations do not count.
        for line_number, line in enumerate(lines, start=1):  # Keep one-based lines for reports.
            in_fence = self._toggle_fence(line, in_fence)  # Update code-fence state before citation checks.
            if in_fence or FENCE_RE.match(line):  # Skip example content and the fence markers.
                continue  # Do not validate citations that appear inside code examples.
            self._append_line_errors(errors, path, line, line_number)  # Validate one checked task line.
        return errors  # Return all missing citations for this task file.

    def _append_line_errors(self, errors: list[CitationFinding], path: Path, line: str, line_number: int) -> None:
        match = CHECKBOX_RE.match(line)  # Check whether this line is a task box.
        if not match or match.group(1) == " ":  # Only checked tasks need proof path validation.
            return  # Leave unchecked tasks and non-task lines unchanged.
        for citation in self._citations_from(line):  # Validate each proof path cited by the task.
            if not self._resolves(path, citation):  # A missing file makes the checked task untrusted.
                errors.append(CitationFinding(line_number, citation))  # Record the bad path for the report.

    def _citations_from(self, line: str) -> tuple[str, ...]:
        raw_values = (match.group("path").strip().rstrip(".") for match in CITATION_RE.finditer(line))  # Clean paths.
        citations = tuple(citation for citation in raw_values if self._is_path_like(citation))  # Keep paths only.
        return citations  # Return all explicit evidence paths from the checked task line.

    def _is_path_like(self, citation: str) -> bool:
        has_no_space = " " not in citation  # Plain evidence sentences are not file path citations.
        has_path_mark = "\\" in citation or "/" in citation or "." in citation  # Paths contain a separator or suffix.
        return has_no_space and has_path_mark  # Validate only strings that look like a path.

    def _resolves(self, path: Path, citation: str) -> bool:
        citation_path = re.sub(r":\d+$", "", citation)  # Ignore a line suffix while checking file existence.
        normalized = Path(*re.split(r"[\\/]+", citation_path))  # Treat Windows and POSIX separators the same.
        candidates = (self._root / normalized, path.parent / normalized)  # Accept root and spec-relative paths.
        return any(candidate.exists() for candidate in candidates)  # Pass only when a cited file exists.

    def _toggle_fence(self, line: str, in_fence: bool) -> bool:
        if FENCE_RE.match(line):  # A fence marker changes whether text is executable task prose.
            return not in_fence  # Enter or leave the fenced code block.
        return in_fence  # Keep the current fenced-code state.


class AllowList:
    """Read the specifications that may keep unchecked task boxes.

    Why: some shipped specifications keep manual tasks that must stay visible.
    """

    def __init__(self, entries: set[str]) -> None:
        self._entries = entries  # Store normalized keys for fast membership tests.

    @classmethod
    def from_path(cls, path: Path | None) -> AllowList:
        logger.info("Loading the SpecKit audit allow list from %s", path)  # Log the optional file read.
        if path is None or not path.exists():  # A missing optional file means no exceptions.
            logger.debug("Loaded %s allow-list entries", 0)  # Record the empty allow-list size.
            return cls(set())  # Return an empty set so callers have one code path.
        entries = cls._read_entries(path)  # Read only non-comment lines from the file.
        logger.debug("Loaded %s allow-list entries", len(entries))  # Record how many exceptions exist.
        return cls(entries)  # Return the allow-list object for the audit run.

    @staticmethod
    def _read_entries(path: Path) -> set[str]:
        text = path.read_text(encoding="utf-8")  # Read the file as text for portable CI behavior.
        lines = text.splitlines()  # Split once so comments and blank lines can be ignored.
        entries = {line.strip() for line in lines if line.strip() and not line.startswith("#")}  # Keep keys.
        return entries  # Return the normalized entries for lookup.

    def contains(self, spec_dir: Path, specs_dir: Path) -> bool:
        name_key = spec_dir.name  # The directory name is the preferred stable key.
        relative_key = spec_dir.relative_to(specs_dir).as_posix()  # Allow a stable nested record key.
        report_key = f"specs/{relative_key}"  # Let users copy the report path into the allow list.
        path_key = spec_dir.as_posix()  # Keep existing absolute path keys valid.
        path_keys = (relative_key, report_key, path_key)  # Match route-specific keys for unique records.
        keys = (*path_keys, name_key) if "/" not in relative_key else path_keys  # Keep basename keys flat-only.
        return any(key in self._entries for key in keys)  # Match all supported key forms.


class TaskFileScanner:
    """Count task checkboxes while it skips code examples.

    Why: examples in a task file must not affect the gate result.
    """

    def scan(self, path: Path) -> TaskCounts:
        logger.info("Scanning task file %s", path)  # Log the file that the scanner reads.
        try:
            text = path.read_text(encoding="utf-8")  # Read one file so the scanner can count each line.
        except OSError as error:
            raise AuditInputError(f"Cannot read required task file {path}: {error}") from error  # Fail closed.
        counts = self._count_lines(text.splitlines())  # Count only valid boxes outside code fences.
        logger.debug("Scanned %s with %s unchecked tasks", path, counts.unchecked)  # Log the result.
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
        self._citation_validator = CitationValidator(root)  # Reuse one validator for proof path checks.

    def run(self) -> int:
        logger.info("Starting the SpecKit task audit under %s", self._root)  # Log the audit start.
        try:
            findings = self._findings()  # Measure every specification directory.
        except OSError as error:
            raise AuditInputError(f"Cannot read required SpecKit audit input: {error}") from error  # Fail closed.
        self._report(findings)  # Print a human-readable report for CI logs.
        failures = [finding for finding in findings if finding.blocks_merge]  # Select blocking drift.
        logger.debug("The SpecKit task audit found %s blocking specs", len(failures))  # Log failure count.
        return 1 if failures else 0  # Fail only when shipped specs still hold open tasks.

    def _findings(self) -> list[SpecFinding]:
        specs_dir = self._root / "specs"  # Specs live below this directory by project convention.
        spec_dirs = SpecDirectoryDiscovery(specs_dir).discover()  # Find direct and grouped feature records.
        findings = [self._finding_for(spec_dir, specs_dir) for spec_dir in spec_dirs]  # Audit every record.
        return findings  # Return all findings so the report includes missing files.

    def _finding_for(self, spec_dir: Path, specs_dir: Path) -> SpecFinding:
        status = self._status_for(spec_dir)  # Read the status line before task validation.
        tasks_path = spec_dir / "tasks.md"  # The task record uses this fixed file name.
        allowed = self._allow_list.contains(spec_dir, specs_dir)  # Check whether the spec is an approved exception.
        relative_path = spec_dir.relative_to(specs_dir).as_posix()  # Preserve grouped identity in all findings.
        if not tasks_path.exists():  # Missing task records get a finding with zero counts.
            return SpecFinding(relative_path, status, TaskCounts(0, 0, 0), True, allowed)  # Report missing file.
        counts = self._scanner.scan(tasks_path)  # Count valid boxes outside code fences.
        citations = self._citation_validator.find_errors(tasks_path)  # Validate checked-task proof paths.
        return SpecFinding(relative_path, status, counts, False, allowed, citations)  # Return the finding.

    def _status_for(self, spec_dir: Path) -> str:
        spec_path = spec_dir / "spec.md"  # The status line lives in the feature specification.
        if not spec_path.exists():  # A spec folder can exist before a spec file is written.
            return "Unknown"  # Unknown status must not fail as complete.
        try:
            text = spec_path.read_text(encoding="utf-8")  # Read the spec file to find the status line.
        except OSError as error:
            raise AuditInputError(
                f"Cannot read required specification file {spec_path}: {error}"
            ) from error  # Stop when a required feature file is unreadable.
        match = STATUS_RE.search(text)  # Search for the standard SpecKit status field.
        return match.group("status").strip() if match else "Unknown"  # Return a safe default when absent.

    def _report(self, findings: list[SpecFinding]) -> None:
        open_findings = [finding for finding in findings if finding.counts.unchecked]  # Report open task specs.
        missing = [finding for finding in findings if finding.missing_tasks]  # Report folders with no tasks.
        citations = [finding for finding in findings if finding.citation_errors]  # Report missing proof files.
        print("SpecKit task audit")  # Start with a stable heading for CI logs.
        self._print_scan_summary(findings)  # Show the number of records and task markers examined.
        self._print_open_findings(open_findings)  # Print each unchecked-task finding.
        self._print_missing_findings(missing)  # Print each missing task record.
        self._print_citation_findings(citations)  # Print each unresolved checked-task proof path.

    @staticmethod
    def _print_scan_summary(findings: list[SpecFinding]) -> None:
        task_files = sum(not finding.missing_tasks for finding in findings)  # Count readable task records.
        checked = sum(finding.counts.checked for finding in findings)  # Count completed task markers.
        unchecked = sum(finding.counts.unchecked for finding in findings)  # Count open task markers.
        malformed = sum(finding.counts.malformed for finding in findings)  # Count invalid task markers.
        marker_count = checked + unchecked + malformed  # Include malformed markers in the examined total.
        print(
            f"Examined {len(findings)} feature record(s), {task_files} task file(s), "
            f"and {marker_count} task marker(s): {checked} checked, {unchecked} unchecked, "
            f"{malformed} malformed."
        )  # Give operators exact scan counts and task state.

    def _print_open_findings(self, findings: list[SpecFinding]) -> None:
        if not findings:  # A clean tree needs a clear report line.
            print("No unchecked task boxes found.")  # Tell CI readers that the scan ran.
            return  # Stop before the loop so the report stays short.
        for finding in findings:  # Print every spec with an unchecked task.
            marker = "allowed" if finding.allowed else "open"  # Show whether the allow list covers it.
            print(
                f"specs/{finding.relative_path}: {finding.counts.unchecked} unchecked tasks ({marker})"
            )  # Report count.

    def _print_missing_findings(self, findings: list[SpecFinding]) -> None:
        for finding in findings:  # Print each missing task file for repair visibility.
            print(f"specs/{finding.relative_path}: missing tasks.md")  # Name the missing task record.

    def _print_citation_findings(self, findings: list[SpecFinding]) -> None:
        for finding in findings:  # Print each spec that has an unresolved checked-task citation.
            marker = "allowed" if finding.allowed else "missing"  # Show whether the allow list covers it.
            for error in finding.citation_errors:  # Print each missing proof path with its source line.
                line = error.line_number  # Keep the print statement readable and below the line limit.
                print(f"specs/{finding.relative_path}: line {line}: {error.citation} ({marker})")  # Report path.


class Command:
    """Parse command arguments and start the audit.

    Why: the module can run as a script without a wrapper function.
    """

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        parser = cls._parser()  # Build the parser near the entry point.
        args = parser.parse_args(argv)  # Read the operator options from the command line.
        level = logging.INFO if args.verbose else logging.WARNING  # Keep normal reports concise unless asked.
        logging.basicConfig(level=level, format="%(levelname)s:%(message)s")  # Enable action logs for operators.
        try:
            allow_list = AllowList.from_path(args.allow_list)  # Load optional exceptions before the audit.
            audit = SpecTaskAudit(args.root, allow_list)  # Build the audit with the selected root.
            return audit.run()  # Return the audit exit code to the shell.
        except (AuditInputError, OSError) as error:
            logger.error("The SpecKit task audit stopped: %s", error)  # Preserve the full input error context.
            print(f"Error: {error}", file=sys.stderr)  # Tell the operator why the audit did not finish.
            return 2  # Distinguish an invalid input from task drift.

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Audit SpecKit task checkboxes.")  # Describe the tool.
        parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to scan.")  # Root.
        parser.add_argument("--allow-list", type=Path, default=None, help="File with allowed spec names.")  # List.
        parser.add_argument("--verbose", action="store_true", help="Show detailed audit logging.")  # Logs.
        return parser  # Return the configured parser to the command entry point.


if __name__ == "__main__":  # Run only when the file is invoked as a script.
    raise SystemExit(Command.main())  # Convert the audit result to the process exit code.
