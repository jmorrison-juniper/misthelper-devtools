"""Tests for installed-workspace defaults in the guard-proof audit."""

from pathlib import Path

from misthelper_devtools.guard_proof_audit import GuardProofAuditor, GuardProofCli

_ANALYZER_REPORT = Path("test_quality_analyzer_output/report.json")


def test_auditor_and_cli_use_installed_analyzer_report_location(tmp_path: Path) -> None:
    """The defaults must match the installed analyzer's workspace output."""
    auditor = GuardProofAuditor(tmp_path)
    finding = auditor._analyzer_report_finding("missing report")
    arguments = GuardProofCli()._build_parser().parse_args([])

    assert finding.path == _ANALYZER_REPORT
    assert arguments.analyzer_report == _ANALYZER_REPORT
