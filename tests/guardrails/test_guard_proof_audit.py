"""Guardrail tests for issue #2654 guard proof enforcement."""

from __future__ import annotations  # Keep annotations stable on the supported Python versions.

from pathlib import Path  # Build repository-relative paths without hardcoded separators.

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
        assert report.blocks_merge  # The enforcement must reject the guard that measures nothing.
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


class TestRepositoryGuardProofAudit:
    """Repository checks that enforce the rule for new guard files."""

    def test_no_new_guard_file_skips_every_test(self) -> None:
        """Only known debt may keep a guard that skips every test."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit()  # Scan the real repository guard files.
        messages = [f"{finding.path}: {finding.reason}" for finding in report.active_findings]  # Build failures.
        assert not messages, "\n".join(messages)  # A new all-skipped guard must fail this test.

    def test_sdk_compatibility_gap_no_longer_stays_visible(self) -> None:
        """Issue #2689 removed the known no-measurement SDK guard baseline."""
        report = GuardProofAuditor(REPOSITORY_ROOT).audit()  # Scan the same file set as the enforcement.
        known_paths = {finding.path: finding.issue for finding in report.known_findings}  # Index known findings.
        compatibility_path = Path("tests/integration/test_mistapi_sdk_compatibility.py")  # Name issue #2689 file.
        assert compatibility_path not in known_paths  # Prove the repaired guard no longer appears as known debt.
