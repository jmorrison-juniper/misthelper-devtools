"""Tests for the configuration."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import pathlib  # Writes a temporary configuration file.

from tools.ste_linter.config import LinterConfig  # The configuration under test.


def test_defaults() -> None:
    """The default configuration uses the STE limits."""
    config = LinterConfig()  # Build the defaults.
    assert config.procedural_limit == 20  # The step limit is twenty words.
    assert config.descriptive_limit == 25  # The description limit is twenty-five words.


def test_limit_for_mode() -> None:
    """The limit depends on the sentence mode."""
    config = LinterConfig()  # Build the defaults.
    assert config.limit_for("procedural") == 20  # A step uses the tighter limit.
    assert config.limit_for("descriptive") == 25  # A description uses the wider limit.


def test_ignored_rule_is_disabled() -> None:
    """A rule on the ignore list is disabled."""
    config = LinterConfig(ignored={"STE-S3-PASSIVE"})  # Ignore the passive rule.
    assert not config.is_enabled("STE-S3-PASSIVE")  # The rule is disabled.


def test_selection_limits_rules() -> None:
    """A selection turns off every rule that is not selected."""
    config = LinterConfig(selected={"STE-S8-SEMICOLON"})  # Select one rule.
    assert config.is_enabled("STE-S8-SEMICOLON")  # The selected rule runs.
    assert not config.is_enabled("STE-S3-PASSIVE")  # The other rule does not run.


def test_zero_weight_disables_rule() -> None:
    """A weight of zero turns a rule off."""
    config = LinterConfig(weights={"STE-S3-PASSIVE": 0})  # Set a zero weight.
    assert not config.is_enabled("STE-S3-PASSIVE")  # The rule is disabled.


def test_is_allowlisted_ignores_case() -> None:
    """The allowlist match ignores letter case."""
    config = LinterConfig(allowlist={"api", "log"})  # Two approved technical terms.
    assert config.is_allowlisted("API")  # The upper-case form matches.
    assert config.is_allowlisted("log")  # The lower-case form matches.
    assert not config.is_allowlisted("via")  # A word not in the list does not match.


def test_load_allowlist_from_toml(tmp_path: pathlib.Path) -> None:
    """The loader reads the allowlist from a TOML file."""
    content = '[tool.ste_linter]\nallowlist = ["API", "Log"]\n'  # An allowlist with mixed case.
    path = tmp_path / "pyproject.toml"  # The temporary file path.
    path.write_text(content, encoding="utf-8")  # Write the config file.
    config = LinterConfig.load(str(path))  # Load the config.
    assert config.is_allowlisted("api") and config.is_allowlisted("log")  # Both load in lower case.


def test_load_from_toml(tmp_path: pathlib.Path) -> None:
    """The loader reads settings from a TOML file."""
    content = "[tool.ste_linter]\nmin_score = 85\nprocedural_limit = 15\n"  # A small config.
    path = tmp_path / "pyproject.toml"  # The temporary file path.
    path.write_text(content, encoding="utf-8")  # Write the config file.
    config = LinterConfig.load(str(path))  # Load the config.
    assert config.min_score == 85  # The threshold was read.
    assert config.procedural_limit == 15  # The limit was read.


def test_load_missing_file_uses_defaults() -> None:
    """The loader returns defaults when the file is missing."""
    config = LinterConfig.load("does-not-exist.toml")  # Load a missing file.
    assert config.min_score is None  # The defaults have no threshold.
