"""Tests for the configuration."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import pathlib  # Writes a temporary configuration file.

import pytest  # Supplies the monkeypatch fixture.

from misthelper_devtools.ste_linter.config import (
    LinterConfig,  # The configuration under test.
    resolve_dictionary_path,  # The lookup order under test.
)


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


def test_loads_string_grading_settings(tmp_path: pathlib.Path) -> None:
    """The config file can opt in to Python string grading."""
    content = "\n".join(
        [
            "[tool.ste_linter]",  # Create the linter table.
            "grade_logging_strings = true",  # Enable logging string spans.
            "grade_user_facing_strings = true",  # Enable prompt and print spans.
            'logging_call_names = ["logger.info"]',  # Use a custom logger name.
            'user_facing_call_names = ["ask_user"]',  # Use a custom prompt helper.
        ]
    )  # Keep the fixture readable in the test.
    path = tmp_path / "pyproject.toml"  # Use pytest storage for an isolated config file.
    path.write_text(content, encoding="utf-8")  # Write the config file.
    config = LinterConfig.load(str(path))  # Load the custom string grading settings.
    assert config.grade_logging_strings is True  # Logging strings were enabled.
    assert config.grade_user_facing_strings is True  # User-facing strings were enabled.
    assert config.logging_call_names == ("logger.info",)  # The custom logging list loaded.
    assert config.user_facing_call_names == ("ask_user",)  # The custom user-facing list loaded.


def test_load_missing_file_uses_defaults() -> None:
    """The loader returns defaults when the file is missing."""
    config = LinterConfig.load("does-not-exist.toml")  # Load a missing file.
    assert config.min_score is None  # The defaults have no threshold.


def _isolate_dictionary_env(monkeypatch: pytest.MonkeyPatch, home: pathlib.Path) -> None:
    """Point every dictionary location at an empty temporary home."""
    monkeypatch.delenv("STE_DICTIONARY_PATH", raising=False)  # Remove any operator override.
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))  # Redirect the Windows location.
    monkeypatch.setenv("HOME", str(home))  # Redirect the home folder on Linux and macOS.
    monkeypatch.setenv("USERPROFILE", str(home))  # Redirect the home folder on Windows.
    monkeypatch.chdir(home)  # Remove the relative repository copy from the lookup.


def test_resolve_dictionary_prefers_environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment variable outranks every file path."""
    _isolate_dictionary_env(monkeypatch, tmp_path)  # Start from an empty set of locations.
    wanted = tmp_path / "from-env.json"  # Name the file that the operator chose.
    wanted.write_text("{}", encoding="utf-8")  # Create the file so the existence test passes.
    other = tmp_path / "from-toml.json"  # Name a second file that must lose.
    other.write_text("{}", encoding="utf-8")  # Create the second file as well.
    monkeypatch.setenv("STE_DICTIONARY_PATH", str(wanted))  # Set the operator override.
    assert resolve_dictionary_path(str(other)) == str(wanted)  # The environment value wins.


def test_resolve_dictionary_uses_configured_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured path wins when no environment override exists."""
    _isolate_dictionary_env(monkeypatch, tmp_path)  # Start from an empty set of locations.
    wanted = tmp_path / "from-toml.json"  # Name the file that the configuration chose.
    wanted.write_text("{}", encoding="utf-8")  # Create the file so the existence test passes.
    assert resolve_dictionary_path(str(wanted)) == str(wanted)  # The configured path wins.


def test_resolve_dictionary_falls_back_to_user_level(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing configured path falls through to the user-level copy."""
    _isolate_dictionary_env(monkeypatch, tmp_path)  # Start from an empty set of locations.
    user_copy = tmp_path / "local" / "ste-linter" / "ste_dictionary.json"  # Name the Windows location.
    user_copy.parent.mkdir(parents=True)  # Create the folder that holds the user-level copy.
    user_copy.write_text("{}", encoding="utf-8")  # Create the user-level dictionary file.
    missing = str(tmp_path / "absent.json")  # Name a configured path that does not exist.
    assert resolve_dictionary_path(missing) == str(user_copy)  # The user-level copy wins.


def test_resolve_dictionary_keeps_stated_path_on_miss(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete miss keeps the stated path so the report can name it."""
    _isolate_dictionary_env(monkeypatch, tmp_path)  # Start from an empty set of locations.
    missing = str(tmp_path / "absent.json")  # Name a configured path that does not exist.
    assert resolve_dictionary_path(missing) == missing  # The stated path survives the miss.


def test_load_resolves_user_level_dictionary(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loader falls back to the user-level dictionary when the stated file is absent."""
    _isolate_dictionary_env(monkeypatch, tmp_path)  # Start from an empty set of locations.
    user_copy = tmp_path / "local" / "ste-linter" / "ste_dictionary.json"  # Name the Windows location.
    user_copy.parent.mkdir(parents=True)  # Create the folder that holds the user-level copy.
    user_copy.write_text("{}", encoding="utf-8")  # Create the user-level dictionary file.
    path = tmp_path / "pyproject.toml"  # Name the temporary configuration file.
    path.write_text('[tool.ste_linter]\ndictionary = "absent.json"\n', encoding="utf-8")  # State a missing path.
    config = LinterConfig.load(str(path))  # Load the configuration under test.
    assert config.dictionary_path == str(user_copy)  # The loader found the user-level copy.
