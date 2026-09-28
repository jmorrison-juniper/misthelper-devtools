"""Decision tests for the guard-proof audit.

These tests moved here from the MistHelper repository. They feed synthetic
sources to the auditor, so they test the tool and not one repository. Each
test uses a temporary root, because the decisions do not read the checkout.
"""

from __future__ import annotations  # Keep annotations stable on the supported Python versions.

from pathlib import Path  # Build relative paths without hardcoded separators.

from misthelper_devtools.guard_proof_audit import GuardProofAuditor  # The auditor that the command line runs.


class TestGuardProofAuditDecisions:
    """Unit tests for the no-measurement guard decision."""

    def test_unconditional_module_skip_is_rejected(self, tmp_path: Path) -> None:
        """A guard with a module skip can report green without measuring its rule."""
        source = "\n".join(  # Build a minimal guard source without writing a file.
            (
                "import pytest",
                'pytestmark = pytest.mark.skip(reason="the guarded surface no longer exists")',
                "def test_guard_rule():",
                "    assert False",
            )
        )
        auditor = GuardProofAuditor(tmp_path, known_guards={})  # Remove the baseline to test a new guard.
        report = auditor.audit_sources({Path("tests/guardrails/test_dead_guard.py"): source})  # Audit fake source.
        assert report.blocks_merge is True  # The enforcement must reject the guard that measures nothing.
        assert "module-level unconditional skip" in report.active_findings[0].reason  # Explain the rejected state.

    def test_environmental_import_skip_is_allowed(self, tmp_path: Path) -> None:
        """An environmental skip can stay green because it states a missing local capability."""
        source = "\n".join(  # Build a guard that skips only when a dependency is absent.
            (
                "import pytest",
                'pytest.importorskip("podman", reason="The Podman package is not installed.")',
                "def test_container_guard_rule():",
                "    assert True",
            )
        )
        auditor = GuardProofAuditor(tmp_path, known_guards={})  # Use an empty baseline for a direct decision.
        report = auditor.audit_sources({Path("tests/guardrails/test_container_guard.py"): source})  # Audit fake source.
        assert not report.blocks_merge  # The enforcement must allow a stated environmental skip.

    def test_conditional_skip_is_allowed(self, tmp_path: Path) -> None:
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
        auditor = GuardProofAuditor(tmp_path, known_guards={})  # Remove baseline noise from this unit test.
        report = auditor.audit_sources({Path("tests/guardrails/test_windows_guard.py"): source})  # Audit fake source.
        assert not report.blocks_merge  # The enforcement must not reject a legitimate conditional skip.

    def test_zero_scope_analyzer_rule_is_rejected(self, tmp_path: Path) -> None:
        """An analyzer rule that inspects zero real files must fail the audit."""
        payload = {"detector_metrics": {"MissingEdgeCaseDetector.inspected_modules": 0}}  # Plant zero scope.
        auditor = GuardProofAuditor(tmp_path, known_guards={})  # Scope audit uses the parsed payload directly.
        findings = auditor._analyzer_scope_findings(payload)  # Exercise the same zero-scope decision path.
        assert len(findings) > 0  # The audit must reject a detector that measures no real files.
        assert "inspected zero real files" in findings[0].reason  # Explain the scope failure.

    def test_empty_analyzer_report_blocks_merge(self, tmp_path: Path) -> None:
        """An empty analyzer report body must become a blocking audit finding."""
        report_path = tmp_path / "report.json"  # Use a temporary report path.
        report_path.write_bytes(b"")  # Model a zero-byte analyzer JSON report.
        auditor = GuardProofAuditor(tmp_path, known_guards={}, analyzer_report=report_path)  # Audit it.
        report = auditor.audit_sources({})  # Drive the analyzer-report path.
        assert report.blocks_merge is True  # A missing body must not report a green guard.
        assert report.active_findings[0].reason == "analyzer report cannot be read"  # Name the input defect.

    def test_malformed_analyzer_report_blocks_merge(self, tmp_path: Path) -> None:
        """A malformed analyzer report body must become a blocking audit finding."""
        report_path = tmp_path / "report.json"  # Use a temporary report path.
        report_path.write_text("{not valid JSONDecodeError", encoding="utf-8")  # Model a damaged report body.
        auditor = GuardProofAuditor(tmp_path, known_guards={}, analyzer_report=report_path)  # Audit it.
        report = auditor.audit_sources({})  # Drive the analyzer-report path.
        assert report.blocks_merge is True  # A malformed body must not report a green guard.
        assert report.active_findings[0].reason == "analyzer report cannot be read"  # Name the input defect.

    def test_unbounded_dependency_is_rejected(self, tmp_path: Path) -> None:
        """A dependency floor without a ceiling can install an unverified version."""
        source = "\n".join(("mistapi>=0.64.0", "requests>=2.28.0,<3"))  # Plant one bad and one good decision.
        report = GuardProofAuditor(tmp_path).audit_dependency_sources(source, None)  # Audit synthetic text only.
        assert report.blocks_merge is True  # The guard must reject a dependency with no upper bound.
        assert report.checked_dependencies == 2  # The report must prove that it measured both dependencies.
        assert "mistapi lacks an upper bound" in report.active_findings[0].reason  # Name the unsafe default.

    def test_zero_dependency_input_is_rejected(self, tmp_path: Path) -> None:
        """A dependency guard that measures no entries must fail visibly."""
        report = GuardProofAuditor(tmp_path).audit_dependency_sources(None, None)  # Simulate missing inputs.
        assert report.blocks_merge is True  # The guard must fail when it has no measured dependency input.
        assert "inspected zero entries" in report.active_findings[0].reason  # Explain why the guard failed.
