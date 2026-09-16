"""Audit guard tests that can report green while they measure nothing."""

from __future__ import annotations  # Keep type hints stable on the supported Python versions.

import argparse  # Parse command arguments for local and CI runs.
import ast  # Read test modules without importing them or running fixtures.
import logging  # Record each audit step for operator diagnostics.
from collections.abc import Mapping, Sequence  # Type the public seams used by tests.
from dataclasses import dataclass  # Store findings in explicit records.
from pathlib import Path  # Keep path handling portable across Windows and Linux.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control the output format.
GUARD_WORDS = frozenset({"guard", "compatibility"})  # Limit the audit to test files that claim a guard role.
KNOWN_UNMEASURED_GUARDS: Mapping[Path, str] = {}  # Keep no baseline after issue #2689 removes the dead guard.


@dataclass(frozen=True)
class GuardProofFinding:
    """Store one guard file that can skip all tests without a measured path."""

    path: Path  # Name the file so an author can repair the guard.
    reason: str  # Explain why the file does not prove a measured path.
    tests: int  # Show how many tests the skip hides.
    issue: str | None = None  # Link known debt to its repair issue.


@dataclass(frozen=True)
class GuardProofReport:
    """Store active and known findings from one audit run."""

    active_findings: tuple[GuardProofFinding, ...]  # New findings fail the gate.
    known_findings: tuple[GuardProofFinding, ...]  # Known findings stay visible in reports.
    checked_files: int  # Prove that the audit read guard files.

    @property
    def blocks_merge(self) -> bool:
        """Return true when a new guard can measure nothing."""
        return bool(self.active_findings)  # Only active findings block this issue's gate.


class GuardProofAuditor:
    """Find guard tests that can skip every test without an environmental reason."""

    def __init__(
        self,
        root: Path | None = None,
        known_guards: Mapping[Path, str] | None = None,
    ) -> None:
        self._root = root or Path.cwd()  # Default to the caller checkout for command-line use.
        self._known_guards = known_guards or KNOWN_UNMEASURED_GUARDS  # Allow tests to override known debt.

    def audit(self) -> GuardProofReport:
        """Audit guard files in the repository checkout."""
        logging.info("Searching for guard test files under %s", self._root)  # Show the scan root.
        paths = tuple(self._candidate_paths())  # Materialize the list so the report can count it.
        logging.debug("Found %d guard test candidate(s)", len(paths))  # Show whether the gate measured files.
        sources = {path: (self._root / path).read_text(encoding="utf-8") for path in paths}  # Read each file once.
        return self.audit_sources(sources)  # Reuse the same decision path that the unit tests exercise.

    def audit_sources(self, sources: Mapping[Path, str]) -> GuardProofReport:
        """Audit a supplied source map for test-only proof cases."""
        logging.info("Auditing %d guard source file(s)", len(sources))  # Log the measured input size.
        findings = tuple(  # Build an immutable record so callers cannot change the result.
            finding for path, source in sources.items() if (finding := self.analyze_source(path, source)) is not None
        )
        active = tuple(finding for finding in findings if finding.issue is None)  # New debt blocks the gate.
        known = tuple(finding for finding in findings if finding.issue is not None)  # Known debt remains visible.
        logging.debug("Audit found %d active and %d known finding(s)", len(active), len(known))  # Summarize results.
        return GuardProofReport(active, known, len(sources))  # Return one complete audit report.

    def analyze_source(self, relative_path: Path, source: str) -> GuardProofFinding | None:
        """Return a finding when one guard source can skip every test unconditionally."""
        logging.info("Analyzing guard source %s", relative_path)  # Name the file before parsing it.
        tree = ast.parse(source, filename=str(relative_path))  # Parse only syntax, so no test side effect runs.
        test_names = self._test_names(tree)  # Count tests so a finding proves a hidden measured path.
        if not test_names:  # A helper-only file is not a guard test module.
            logging.debug("Guard source %s has no test functions", relative_path)  # Explain the quiet decision.
            return None  # Leave helper modules to other repository checks.
        reason = self._skip_reason(tree, test_names)  # Decide whether every test is unconditionally skipped.
        logging.debug("Guard source %s skip reason is %s", relative_path, reason or "none")  # Record the outcome.
        if reason is None:  # A guard with no all-skip defect does not need a finding.
            return None  # Keep measured or environmental guards green.
        return self._finding(relative_path, reason, len(test_names))  # Convert a defect to a report record.

    def _candidate_paths(self) -> tuple[Path, ...]:
        tests_root = self._root / "tests"  # Guard tests live under the repository test tree.
        paths = sorted(path.relative_to(self._root) for path in tests_root.rglob("test_*.py"))  # Read pytest modules.
        return tuple(path for path in paths if self._is_guard_path(path))  # Keep only files with guard language.

    def _is_guard_path(self, path: Path) -> bool:
        parts = {part.lower() for part in path.parts}  # Normalize path parts for a stable match.
        name = path.name.lower()  # Match the file name because many guards live outside tests/guardrails.
        return "guardrails" in parts or any(word in name for word in GUARD_WORDS)  # Select declared guard files.

    def _skip_reason(self, tree: ast.Module, test_names: frozenset[str]) -> str | None:
        module_reason = self._module_skip_reason(tree)  # A module-level skip hides every test in the file.
        if module_reason is not None:  # This is the exact defect in issue #2689.
            return f"module-level unconditional skip hides {len(test_names)} test(s): {module_reason}"
        skipped_names = self._unconditional_skipped_tests(tree)  # Check whether decorators skip every test.
        if skipped_names == test_names:  # Every test has the same no-measurement defect.
            return f"all {len(test_names)} test(s) use unconditional skip decorators"
        return None  # At least one test has a measured path or an environmental condition.

    def _module_skip_reason(self, tree: ast.Module) -> str | None:
        for node in tree.body:  # Search only module statements for pytestmark assignments.
            if isinstance(node, ast.Assign) and self._assigns_pytestmark(node):  # Match pytestmark = pytest.mark.skip.
                return self._skip_call_reason(node.value)  # Extract the reason string when possible.
        return None  # No module-level unconditional skip exists.

    def _assigns_pytestmark(self, node: ast.Assign) -> bool:
        has_pytestmark = any(  # Match assignments that configure pytest for the whole module.
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        return has_pytestmark and self._is_unconditional_skip_value(node.value)  # Ignore conditional skip values.

    def _is_unconditional_skip_value(self, value: ast.AST) -> bool:
        if isinstance(value, ast.Call):  # A single marker is the usual form.
            return self._is_unconditional_skip_call(value)  # Check the call name.
        if isinstance(value, ast.List | ast.Tuple):  # Multiple markers can include a skip.
            return any(self._is_unconditional_skip_value(item) for item in value.elts)  # Find any unconditional skip.
        return False  # Other pytestmark shapes do not skip the whole module.

    def _skip_call_reason(self, value: ast.AST) -> str:
        if isinstance(value, ast.List | ast.Tuple):  # Search marker collections for the skip reason.
            reasons = tuple(self._skip_call_reason(item) for item in value.elts)  # Extract reasons from each marker.
            return next((reason for reason in reasons if reason), "no reason supplied")  # Prefer the first real reason.
        if not isinstance(value, ast.Call):  # A non-call has no reason argument.
            return "no reason supplied"  # Keep the report actionable even without source detail.
        keyword = next((item for item in value.keywords if item.arg == "reason"), None)  # Read the reason keyword.
        if keyword is not None and isinstance(keyword.value, ast.Constant):  # Literal reasons are safe to print.
            return str(keyword.value.value)  # Preserve the author's reason text.
        return "no reason supplied"  # Report missing or dynamic reasons directly.

    def _is_unconditional_skip_call(self, call: ast.Call) -> bool:
        return self._call_name(call.func) in {"pytest.mark.skip", "mark.skip"}  # skipif is environmental and allowed.

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):  # A bare imported marker has one name.
            return node.id  # Return the name so callers can compare it.
        if isinstance(node, ast.Attribute):  # Attribute chains hold pytest.mark.skip.
            parent = self._call_name(node.value)  # Resolve the left side first.
            return f"{parent}.{node.attr}" if parent else node.attr  # Join the dotted call name.
        return ""  # Other call forms are not a plain pytest marker.

    def _test_names(self, tree: ast.Module) -> frozenset[str]:
        names: set[str] = set()  # Use a set so duplicate names do not inflate the measured count.
        for node in tree.body:  # Read only top-level tests and methods inside top-level test classes.
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):  # Match one test function.
                names.add(node.name)  # Record the function as a measured path.
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):  # Match pytest test classes.
                names.update(self._class_test_names(node))  # Add the class test methods with their class name.
        return frozenset(names)  # Freeze the names so comparisons are stable.

    def _class_test_names(self, node: ast.ClassDef) -> set[str]:
        return {  # Build stable names for each test method in the class.
            f"{node.name}.{item.name}"
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
        }

    def _unconditional_skipped_tests(self, tree: ast.Module) -> frozenset[str]:
        names: set[str] = set()  # Collect test names hidden by decorators.
        for node in tree.body:  # Inspect each top-level node once.
            if isinstance(node, ast.FunctionDef) and self._has_skip_decorator(node):  # Match skipped test functions.
                names.add(node.name)  # Record the skipped test function.
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):  # Test classes can skip all methods.
                names.update(self._skipped_class_tests(node))  # Add skipped methods from this class.
        return frozenset(names)  # Freeze the set for equality checks.

    def _skipped_class_tests(self, node: ast.ClassDef) -> set[str]:
        class_skips = self._has_skip_decorator(node)  # A skipped class skips every method inside it.
        names: set[str] = set()  # Collect the hidden methods for this class.
        for item in node.body:  # Read the class body once.
            if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):  # Only pytest methods count.
                if class_skips or self._has_skip_decorator(item):  # Either decorator hides this method.
                    names.add(f"{node.name}.{item.name}")  # Store the same shape used by _test_names.
        return names  # Return only methods that an unconditional skip hides.

    def _has_skip_decorator(self, node: ast.FunctionDef | ast.ClassDef) -> bool:
        return any(  # A single unconditional skip decorator hides this test.
            isinstance(decorator, ast.Call) and self._is_unconditional_skip_call(decorator)
            for decorator in node.decorator_list
        )

    def _finding(self, path: Path, reason: str, tests: int) -> GuardProofFinding:
        issue = self._known_guards.get(path)  # Known debt stays reported without blocking unrelated work.
        return GuardProofFinding(path, reason, tests, issue)  # Return the complete finding.


class GuardProofCli:
    """Run the guard proof audit from the command line."""

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Return a process exit code for one audit run."""
        parser = self._build_parser()  # Build the parser at run time for test isolation.
        arguments = parser.parse_args(argv)  # Parse the caller arguments.
        self._configure_logging(arguments.verbose)  # Configure logs before the first audit action.
        report = GuardProofAuditor(arguments.root).audit()  # Run the same enforcement used by the tests.
        self._print_report(report, arguments.include_known)  # Print the measured result for pull request evidence.
        return 1 if report.blocks_merge else 0  # Active findings fail the command.

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Fail new guard tests that can skip every test.")  # CLI help text.
        parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to audit.")  # Checkout path.
        parser.add_argument("--include-known", action="store_true", help="Print known findings.")  # Show baseline debt.
        parser.add_argument("--verbose", action="store_true", help="Print DEBUG log lines.")  # Control log detail.
        return parser  # Return the parser to the caller.

    def _configure_logging(self, verbose: bool) -> None:
        level = logging.DEBUG if verbose else logging.INFO  # The flag selects detailed diagnostics.
        logging.basicConfig(level=level, format="%(levelname)s %(message)s")  # Keep output ASCII and compact.

    def _print_report(self, report: GuardProofReport, include_known: bool) -> None:
        print(f"Checked guard files: {report.checked_files}")  # Show that the audit measured a nonzero file set.
        for finding in report.active_findings:  # Print every blocking finding.
            print(f"FAIL {finding.path}: {finding.reason}")  # Name the file and the missing proof.
        if include_known:  # Known findings are useful in reports but do not block this pull request.
            for finding in report.known_findings:  # Print baseline debt with its issue number.
                print(f"KNOWN {finding.issue} {finding.path}: {finding.reason}")  # Link the existing repair issue.
        if not report.blocks_merge:  # Make a clean result easy to paste into the pull request.
            print("No new guard proof failures found.")  # State the active gate result.


if __name__ == "__main__":  # Run only when called as a module or script.
    raise SystemExit(GuardProofCli().run())  # Exit with the audit result for CI use.
