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


def _option_value(arguments: list[str], option: str) -> str:
    """Return the value that follows one option in an argument list."""
    return arguments[arguments.index(option) + 1]


def test_analyzer_run_uses_package_config_without_repository_settings(tmp_path: Path) -> None:
    """A checkout with no settings file must run the analyzer with the package config and no baseline."""
    arguments = GuardProofAuditor(tmp_path)._analyzer_arguments(tmp_path / "out" / "report.json")
    config = Path(_option_value(arguments, "--config"))

    assert config.is_file(), "The package config must exist so the analyzer measures the default rules."
    assert config.parts[-2:] == ("test_quality_analyzer", "config.toml")
    assert _option_value(arguments, "--baseline") == "", "Detector metrics must not depend on a baseline file."
    assert Path(_option_value(arguments, "--roots")) == tmp_path / "tests"
    assert Path(_option_value(arguments, "--summary")) == tmp_path / "out" / "summary.md"


def test_analyzer_run_uses_repository_settings_when_present(tmp_path: Path) -> None:
    """A checkout that owns .github/test-quality-config.toml must audit the rules that its gate runs."""
    local_config = tmp_path / ".github" / "test-quality-config.toml"
    local_config.parent.mkdir()
    local_config.write_text("[rules]\n", encoding="utf-8")

    arguments = GuardProofAuditor(tmp_path)._analyzer_arguments(tmp_path / "report.json")

    assert Path(_option_value(arguments, "--config")) == local_config
