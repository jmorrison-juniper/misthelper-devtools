"""Guardrail tests for issue #2654 guard proof enforcement."""

from __future__ import annotations  # Keep annotations stable on the supported Python versions.

import json  # Parse generated analyzer reports for detector metric coverage checks.
from pathlib import Path  # Build repository-relative paths without hardcoded separators.

import pytest  # Type pytest fixtures used by repository guard tests.

from tools.guard_proof_audit import GuardProofAuditor  # Exercise the same auditor used by the command line.

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # Point the audit at the checked out repository.


class TestGuardProofAuditDecisions:
    """Unit tests for the no-measurement guard decision."""

    def test_unconditional_module_skip_is_rejected(self) -> None:
        """A guard with a module skip can report green without measuring its rule."""
        source = "\n".join(  # Build a minimal guard source without writing a file.
            (
                "import pytest",
                'pytestmark = pytest.mark.skip(reason="the guarded surface no longer exists")',
                "def test_guard_rule():",
                "    assert False",
            )
        )
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={})  # Remove the baseline to test a new guard.
        report = auditor.audit_sources({Path("tests/guardrails/test_dead_guard.py"): source})  # Audit fake source.
        assert report.blocks_merge is True  # The enforcement must reject the guard that measures nothing.
        assert "module-level unconditional skip" in report.active_findings[0].reason  # Explain the rejected state.

    def test_environmental_import_skip_is_allowed(self) -> None:
        """An environmental skip can stay green because it states a missing local capability."""
        source = "\n".join(  # Build a guard that skips only when a dependency is absent.
            (
                "import pytest",
                'pytest.importorskip("podman", reason="The Podman package is not installed.")',
                "def test_container_guard_rule():",
                "    assert True",
            )
        )
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={})  # Use an empty baseline for a direct decision.
        report = auditor.audit_sources({Path("tests/guardrails/test_container_guard.py"): source})  # Audit fake source.
        assert not report.blocks_merge  # The enforcement must allow a stated environmental skip.

    def test_conditional_skip_is_allowed(self) -> None:
        """A conditional skip has a measured path when the condition permits the test."""
        source = "\n".join(  # Build a guard with a condition instead of a permanent skip.
            (
                "import sys",
                "import pytest",
                '@pytest.mark.skipif(sys.platform != "win32", reason="The test validates Windows behavior.")',
                "def test_windows_guard_rule():",
                "    assert True",
            )
        )
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={})  # Remove baseline noise from this unit test.
        report = auditor.audit_sources({Path("tests/guardrails/test_windows_guard.py"): source})  # Audit fake source.
        assert not report.blocks_merge  # The enforcement must not reject a legitimate conditional skip.

    def test_zero_scope_analyzer_rule_is_rejected(self) -> None:
        """An analyzer rule that inspects zero real files must fail the audit."""
        payload = {"detector_metrics": {"MissingEdgeCaseDetector.inspected_modules": 0}}  # Plant zero scope.
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={})  # Scope audit uses the parsed payload directly.
        findings = auditor._analyzer_scope_findings(payload)  # Exercise the same zero-scope decision path.
        assert len(findings) > 0  # The audit must reject a detector that measures no real files.
        assert "inspected zero real files" in findings[0].reason  # Explain the scope failure.

    def test_empty_analyzer_report_blocks_merge(self, tmp_path: Path) -> None:
        """An empty analyzer report body must become a blocking audit finding."""
        report_path = tmp_path / "report.json"  # Use a temporary report path outside the repository.
        report_path.write_bytes(b"")  # Model a zero-byte analyzer JSON report.
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={}, analyzer_report=report_path)  # Audit it.
        report = auditor.audit_sources({})  # Drive the product analyzer-report path.
        assert report.blocks_merge is True  # A missing body must not report a green guard.
        assert report.active_findings[0].reason == "analyzer report cannot be read"  # Name the input defect.

    def test_malformed_analyzer_report_blocks_merge(self, tmp_path: Path) -> None:
        """A malformed analyzer report body must become a blocking audit finding."""
        report_path = tmp_path / "report.json"  # Use a temporary report path outside the repository.
        report_path.write_text("{not valid JSONDecodeError", encoding="utf-8")  # Model a damaged report body.
        auditor = GuardProofAuditor(REPOSITORY_ROOT, known_guards={}, analyzer_report=report_path)  # Audit it.
        report = auditor.audit_sources({})  # Drive the product analyzer-report path.
        assert report.blocks_merge is True  # A malformed body must not report a green guard.
        assert report.active_findings[0].reason == "analyzer report cannot be read"  # Name the input defect.

    def test_unbounded_dependency_is_rejected(self) -> None:
        """A dependency floor without a ceiling can install an unverified version."""
        source = "\n".join(("mistapi>=0.64.0", "requests>=2.28.0,<3"))  # Plant one bad and one good decision.
        report = GuardProofAuditor(REPOSITORY_ROOT).audit_dependency_sources(source, None)  # Audit synthetic text only.
        assert report.blocks_merge is True  # The guard must reject a dependency with no upper bound.
        assert report.checked_dependencies == 2  # The report must prove that it measured both dependencies.
        assert "mistapi lacks an upper bound" in report.active_findings[0].reason  # Name the unsafe default.

    def test_zero_dependency_input_is_rejected(self) -> None:
        """A dependency guard that measures no entries must fail visibly."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit_dependency_sources(None, None)  # Simulate missing inputs.
        assert report.blocks_merge is True  # The guard must fail when it has no measured dependency input.
        assert "inspected zero entries" in report.active_findings[0].reason  # Explain why the guard failed.


class TestRepositoryGuardProofAudit:
    """Repository checks that enforce the rule for new guard files."""

    def _registered_detector_modules(self) -> set[str]:
        """Return detector modules that register an analyzer detector."""
        detection_root = REPOSITORY_ROOT / "tools" / "test_quality_analyzer" / "detection"  # Detector package.
        modules: set[str] = set()  # Accumulate real detector module names only.
        for path in detection_root.glob("*.py"):  # Scan the package so a new detector changes the count.
            source = path.read_text(encoding="utf-8")  # Read without import side effects.
            if "DetectorRegistry.append(" in source:  # Registration marks a module as a detector.
                modules.add(path.stem)  # Store the module name for clear failure output.
        return modules  # Return the measured detector module set.

    def test_no_new_guard_file_skips_every_test(self) -> None:
        """Only known debt may keep a guard that skips every test."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit()  # Scan the real repository guard files.
        messages = [f"{finding.path}: {finding.reason}" for finding in report.active_findings]  # Build failures.
        assert not messages, "\n".join(messages)  # A new all-skipped guard must fail this test.

    def test_analyzer_scope_metrics_cover_registered_detectors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The analyzer report must emit one scope metric for each registered detector."""
        monkeypatch.chdir(REPOSITORY_ROOT)  # Make analyzer relative paths match command-line audit behavior.
        report_path = tmp_path / "analyzer-report.json"  # Keep generated proof outside tracked files.
        auditor = GuardProofAuditor(REPOSITORY_ROOT, analyzer_report=report_path)  # Use the real audit generator.
        report = auditor.audit_sources({})  # Generate and audit the analyzer scope report.
        payload = json.loads(report_path.read_text(encoding="utf-8"))  # Read the generated report for metric names.
        detector_modules = self._registered_detector_modules()  # Count detector modules from the package.
        metric_keys = {key for key in payload["detector_metrics"] if key.endswith(".inspected_modules")}  # Scope only.
        messages = [f"{finding.path}: {finding.reason}" for finding in report.active_findings]  # Build failures.
        assert report.checked_analyzer_rules == len(detector_modules)  # The audit must measure every detector.
        assert len(metric_keys) == len(detector_modules)  # The report must hold one metric per detector.
        assert not messages, "\n".join(messages)  # No registered detector may inspect zero modules.

    def test_sdk_compatibility_gap_no_longer_stays_visible(self) -> None:
        """Issue #2689 removed the known no-measurement SDK guard baseline."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit()  # Scan the same file set as the enforcement.
        known_paths = {finding.path: finding.issue for finding in report.known_findings}  # Index known findings.
        compatibility_path = Path("tests/integration/test_mistapi_sdk_compatibility.py")  # Name issue #2689 file.
        assert compatibility_path not in known_paths  # Prove the repaired guard no longer appears as known debt.

    def test_runtime_dependencies_have_upper_bounds(self) -> None:
        """Runtime dependencies must name the last verified major release."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit_dependencies()  # Scan the real repository dependency files.
        messages = [f"{finding.path}: {finding.reason}" for finding in report.active_findings]  # Build failures.
        assert report.checked_dependencies > 0  # The guard must prove it measured dependency declarations.
        assert not messages, "\n".join(messages)  # A runtime dependency without a ceiling must fail this test.
