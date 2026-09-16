"""Audit guard tests that can report green while they measure nothing."""

from __future__ import annotations  # Keep type hints stable on the supported Python versions.

import argparse  # Parse command arguments for local and CI runs.
import ast  # Read test modules without importing them or running fixtures.
import json  # Read analyzer report metrics without importing the analyzer CLI.
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
    checked_analyzer_rules: int = 0  # Prove that the audit read analyzer rule scope metrics.
    checked_dependencies: int = 0  # Prove that the audit read dependency decisions.

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
        analyzer_report: Path | None = None,
    ) -> None:
        self._root = root or Path.cwd()  # Default to the caller checkout for command-line use.
        self._known_guards = known_guards or KNOWN_UNMEASURED_GUARDS  # Allow tests to override known debt.
        self._analyzer_report = self._resolve_report_path(analyzer_report)  # Optional analyzer scope report path.

    def _resolve_report_path(self, analyzer_report: Path | None) -> Path | None:
        """Return the analyzer report path relative to the audit root when needed."""
        if analyzer_report is None:  # Unit tests can disable analyzer scope auditing.
            return None  # No analyzer report will be read.
        if analyzer_report.is_absolute():  # Absolute paths already name their checkout or test file.
            return analyzer_report  # Keep the caller-supplied path unchanged.
        return self._root / analyzer_report  # Relative report paths belong to the audited checkout.

    def audit(self) -> GuardProofReport:
        """Audit guard files in the repository checkout."""
        logging.info("Searching for guard test files under %s", self._root)  # Show the scan root.
        paths = tuple(self._candidate_paths())  # Materialize the list so the report can count it.
        logging.debug("Found %d guard test candidate(s)", len(paths))  # Show whether the gate measured files.
        sources = {path: (self._root / path).read_text(encoding="utf-8") for path in paths}  # Read each file once.
        report = self.audit_sources(sources)  # Reuse the same decision path that the unit tests exercise.
        dependency_report = self.audit_dependencies()  # Add dependency default checks to this guard.
        return self._merge_reports(report, dependency_report)  # Return one report for all no-evidence guard checks.

    def audit_sources(self, sources: Mapping[Path, str]) -> GuardProofReport:
        """Audit a supplied source map for test-only proof cases."""
        logging.info("Auditing %d guard source file(s)", len(sources))  # Log the measured input size.
        findings = tuple(  # Build an immutable record so callers cannot change the result.
            finding for path, source in sources.items() if (finding := self.analyze_source(path, source)) is not None
        )
        active = tuple(finding for finding in findings if finding.issue is None)  # New debt blocks the gate.
        known = tuple(finding for finding in findings if finding.issue is not None)  # Known debt remains visible.
        logging.debug("Audit found %d active and %d known finding(s)", len(active), len(known))  # Summarize results.
        report = GuardProofReport(active, known, len(sources))  # Return one complete source-only audit report.
        scope_report = self._audit_analyzer_report()  # Add analyzer scope checks when the caller requests them.
        return self._merge_reports(report, scope_report)  # Return one complete audit report.

    def _audit_analyzer_report(self) -> GuardProofReport:
        """Return findings for analyzer rules that inspected zero real files."""
        if self._analyzer_report is None:  # Unit tests can audit skip logic without an analyzer report.
            return GuardProofReport((), (), 0, 0)  # No analyzer scope input was requested.
        logging.info("Auditing analyzer scope report %s", self._analyzer_report)  # Log before reading the report.
        payload = self._load_analyzer_payload(self._analyzer_report)  # Read the report or create an input finding.
        if payload is None:  # Missing or invalid report already became a finding.
            finding = self._analyzer_report_finding("analyzer report cannot be read")  # Required input is absent.
            return GuardProofReport((finding,), (), 0, 0)  # Block merge because scope is unknown.
        findings = tuple(self._analyzer_scope_findings(payload))  # Convert zero-scope metrics to findings.
        logging.debug("Analyzer scope audit found %d finding(s)", len(findings))  # Summarize analyzer scope.
        return GuardProofReport(findings, (), 0, len(payload.get("detector_metrics", {})))  # Return scope result.

    def _load_analyzer_payload(self, report_path: Path) -> Mapping[str, object] | None:
        """Return analyzer report JSON, or None when the required input is invalid."""
        try:
            text = report_path.read_text(encoding="utf-8")  # Read the report written by the analyzer command.
            payload = json.loads(text)  # Parse JSON without importing analyzer internals.
        except OSError as exc:
            logging.debug("Analyzer report read failed: %s", exc)  # Record the missing report before generation.
            return self._generate_and_load_analyzer_payload(report_path)  # Build the required scope input once.
        except json.JSONDecodeError as exc:
            logging.debug("Analyzer report parse failed: %s", exc)  # Invalid input must fail visibly.
            return None  # The caller turns this into a blocking finding.
        return payload if isinstance(payload, dict) else None  # The report envelope must be a JSON object.

    def _generate_and_load_analyzer_payload(self, report_path: Path) -> Mapping[str, object] | None:
        """Generate the analyzer report and return its parsed payload."""
        logging.info("Generating analyzer scope report %s", report_path)  # Explain the extra guard input step.
        self._run_analyzer(report_path)  # Generate the detector metrics in the repository output path.
        try:
            text = report_path.read_text(encoding="utf-8")  # Read the generated report after the analyzer exits.
            payload = json.loads(text)  # Parse the generated JSON report.
        except (OSError, json.JSONDecodeError) as exc:
            logging.debug("Generated analyzer report read failed: %s", exc)  # Preserve the generation failure reason.
            return None  # The guard must fail when it cannot prove analyzer scope.
        return payload if isinstance(payload, dict) else None  # Reject unexpected report shapes.

    def _run_analyzer(self, report_path: Path) -> None:
        """Run the analyzer command that writes detector scope metrics."""
        from tools.test_quality_analyzer.__main__ import TestQualityCLI  # Reuse the analyzer without a shell command.

        summary_path = report_path.with_name("summary.md")  # Keep the analyzer summary beside the JSON report.
        arguments = [  # Keep each argument separate so paths with spaces work on Windows.
            "--roots",
            str(self._root / "tests"),
            "--config",
            str(self._root / "tools" / "test_quality_analyzer" / "config.toml"),
            "--baseline",
            str(self._root / "tools" / "test_quality_analyzer" / "baseline.json"),
            "--report",
            str(report_path),
            "--summary",
            str(summary_path),
        ]
        return_code = TestQualityCLI().run(arguments)  # Run the analyzer in the current Python process.
        logging.debug("Analyzer report generation exit code: %s", return_code)  # Keep the result visible.

    def _analyzer_scope_findings(self, payload: Mapping[str, object]) -> tuple[GuardProofFinding, ...]:
        """Return one finding for each analyzer metric that measured zero real files."""
        metrics = payload.get("detector_metrics")  # The analyzer writes detector proof counts here.
        if not isinstance(metrics, dict):  # A missing metric block means the guard cannot measure the rule.
            return (self._analyzer_report_finding("analyzer report lacks detector_metrics"),)  # Block merge.
        findings = [  # Build findings for all zero inspector metrics.
            self._analyzer_report_finding(f"{key} inspected zero real files")
            for key, value in metrics.items()
            if key.endswith(".inspected_modules") and value == 0
        ]
        return tuple(findings)  # Return an immutable finding set.

    def _analyzer_report_finding(self, reason: str) -> GuardProofFinding:
        """Return a guard finding that names the analyzer report scope failure."""
        path = self._analyzer_report or Path("tools/test_quality_analyzer/output/report.json")  # Default path.
        try:
            relative = path if not path.is_absolute() else path.relative_to(self._root)  # Prefer repository path.
        except ValueError:
            relative = path  # External test reports keep their absolute path.
        return GuardProofFinding(relative, reason, 0)  # Analyzer-scope findings block the gate.

    def _merge_reports(self, first: GuardProofReport, second: GuardProofReport) -> GuardProofReport:
        """Merge guard-skip and analyzer-scope audit reports."""
        active = first.active_findings + second.active_findings  # Both finding sets block the audit.
        known = first.known_findings + second.known_findings  # Preserve known skip debt if any returns.
        checked_files = first.checked_files + second.checked_files  # Sum measured guard test files.
        checked_rules = first.checked_analyzer_rules + second.checked_analyzer_rules  # Sum analyzer metrics.
        checked_deps = first.checked_dependencies + second.checked_dependencies  # Sum dependency decisions.
        return GuardProofReport(  # Return the combined report for the command-line output.
            active, known, checked_files, checked_rules, checked_deps
        )

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

    def audit_dependencies(self) -> GuardProofReport:
        """Audit repository dependency declarations for silent upgrade drift."""
        logging.info("Auditing dependency bounds under %s", self._root)  # Log the repository path before reading files.
        requirements = self._read_optional("requirements.txt")  # Read pip dependencies when the file exists.
        pyproject = self._read_optional("pyproject.toml")  # Read package metadata when the file exists.
        return self.audit_dependency_sources(requirements, pyproject)  # Reuse the source-only path for unit tests.

    def audit_dependency_sources(self, requirements: str | None, pyproject: str | None) -> GuardProofReport:
        """Audit supplied dependency text without writing files."""
        entries = self._dependency_entries(requirements, pyproject)  # Normalize each dependency source.
        findings = tuple(  # Convert each missing dependency ceiling to a blocking finding.
            self._dependency_finding(entry) for entry in entries if not self._has_upper_bound(entry[2])
        )
        if not entries:  # A guard that measures no dependency decisions cannot prove safety.
            findings = (GuardProofFinding(Path("requirements.txt"), "dependency audit inspected zero entries", 0),)
        logging.debug("Dependency audit checked %d entries", len(entries))  # Report the measured dependency count.
        return GuardProofReport(  # Return dependency results through the common report.
            findings, (), 0, 0, len(entries)
        )

    def _read_optional(self, name: str) -> str | None:
        path = self._root / name  # Keep repository paths relative to the root.
        if not path.exists():  # Some test fixtures provide only one dependency file.
            return None  # Missing optional inputs are handled by the zero-count rule.
        return path.read_text(encoding="utf-8")  # Read the file once for deterministic analysis.

    def _dependency_entries(self, requirements: str | None, pyproject: str | None) -> tuple[tuple[Path, int, str], ...]:
        entries = list(self._requirements_entries(requirements))  # Start with pip dependency declarations.
        entries.extend(self._pyproject_entries(pyproject))  # Add package dependency declarations.
        return tuple(entries)  # Freeze the entries so reports are stable.

    def _requirements_entries(self, source: str | None) -> tuple[tuple[Path, int, str], ...]:
        if source is None:  # A missing requirements file is allowed when pyproject has dependencies.
            return ()  # The zero-count rule catches a repository with no dependency input.
        return tuple(  # Parse only dependency lines from the requirements file.
            self._line_entry(line, number)
            for number, line in enumerate(source.splitlines(), 1)
            if self._is_dependency_line(line)
        )

    def _line_entry(self, line: str, number: int) -> tuple[Path, int, str]:
        requirement = line.split("#", 1)[0].strip()  # Remove inline comments without changing the requirement.
        return (Path("requirements.txt"), number, requirement)  # Keep the source path and line for the finding.

    def _pyproject_entries(self, source: str | None) -> tuple[tuple[Path, int, str], ...]:
        if source is None:  # Some projects use requirements.txt only.
            return ()  # The caller combines all dependency sources.
        import tomllib  # Read project dependency declarations without third-party helpers.

        payload = tomllib.loads(source)  # Parse TOML so comments and formatting do not affect the audit.
        dependencies = payload.get("project", {}).get("dependencies", [])  # Scope the guard to runtime dependencies.
        return tuple(  # Normalize project dependency entries for the common guard path.
            (Path("pyproject.toml"), 0, value) for value in dependencies if isinstance(value, str)
        )

    def _is_dependency_line(self, line: str) -> bool:
        stripped = line.strip()  # Normalize whitespace before rule checks.
        return bool(  # Ignore comments and pip options because they are not dependency decisions.
            stripped and not stripped.startswith("#") and not stripped.startswith("-")
        )

    def _has_upper_bound(self, requirement_text: str) -> bool:
        from packaging.requirements import InvalidRequirement, Requirement  # Parse dependency specifiers consistently.

        try:
            requirement = Requirement(requirement_text)  # Let packaging handle markers and extras.
        except InvalidRequirement:
            return False  # Invalid requirements hide dependency intent and must fail visibly.
        return any(  # Require a ceiling or an exact pin for each dependency decision.
            spec.operator in {"<", "<=", "~=", "==", "==="} for spec in requirement.specifier
        )

    def _dependency_finding(self, entry: tuple[Path, int, str]) -> GuardProofFinding:
        path, line, requirement = entry  # Unpack the normalized dependency declaration.
        name = self._dependency_name(requirement)  # Use the package name so the repair is direct.
        reason = f"{name} lacks an upper bound"  # State the missing decision in plain text.
        return GuardProofFinding(path, reason, line)  # Use the test-count field as the source line.

    def _dependency_name(self, requirement_text: str) -> str:
        from packaging.requirements import InvalidRequirement, Requirement  # Parse dependency names consistently.

        try:
            return Requirement(requirement_text).name  # Report the canonical package name when parsing succeeds.
        except InvalidRequirement:
            return requirement_text  # Preserve invalid text so the author can find it.


class GuardProofCli:
    """Run the guard proof audit from the command line."""

    def run(self, argv: Sequence[str] | None = None) -> int:
        """Return a process exit code for one audit run."""
        parser = self._build_parser()  # Build the parser at run time for test isolation.
        arguments = parser.parse_args(argv)  # Parse the caller arguments.
        self._configure_logging(arguments.verbose)  # Configure logs before the first audit action.
        report = GuardProofAuditor(arguments.root, analyzer_report=arguments.analyzer_report).audit()  # Run audit.
        self._print_report(report, arguments.include_known)  # Print the measured result for pull request evidence.
        return 1 if report.blocks_merge else 0  # Active findings fail the command.

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Fail new guard tests that can skip every test.")  # CLI help text.
        parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root to audit.")  # Checkout path.
        parser.add_argument(  # Analyzer report path used to reject detector rules with empty real scope.
            "--analyzer-report",
            type=Path,
            default=Path("tools/test_quality_analyzer/output/report.json"),
            help="Analyzer JSON report to audit for detector scope metrics.",
        )
        parser.add_argument("--include-known", action="store_true", help="Print known findings.")  # Show baseline debt.
        parser.add_argument("--verbose", action="store_true", help="Print DEBUG log lines.")  # Control log detail.
        return parser  # Return the parser to the caller.

    def _configure_logging(self, verbose: bool) -> None:
        level = logging.DEBUG if verbose else logging.INFO  # The flag selects detailed diagnostics.
        logging.basicConfig(level=level, format="%(levelname)s %(message)s")  # Keep output ASCII and compact.

    def _print_report(self, report: GuardProofReport, include_known: bool) -> None:
        print(f"Checked guard files: {report.checked_files}")  # Show that the audit measured a nonzero file set.
        print(f"Checked analyzer rules: {report.checked_analyzer_rules}")  # Show detector scope input was measured.
        print(f"Checked dependency entries: {report.checked_dependencies}")  # Show dependency defaults were measured.
        for finding in report.active_findings:  # Print every blocking finding.
            print(f"FAIL {finding.path}: {finding.reason}")  # Name the file and the missing proof.
        if include_known:  # Known findings are useful in reports but do not block this pull request.
            for finding in report.known_findings:  # Print baseline debt with its issue number.
                print(f"KNOWN {finding.issue} {finding.path}: {finding.reason}")  # Link the existing repair issue.
        if not report.blocks_merge:  # Make a clean result easy to paste into the pull request.
            print("No new guard proof failures found.")  # State the active gate result.


if __name__ == "__main__":  # Run only when called as a module or script.
    raise SystemExit(GuardProofCli().run())  # Exit with the audit result for CI use.
