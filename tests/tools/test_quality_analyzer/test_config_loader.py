"""Unit tests for ConfigLoader (T012).

Covers the five failure/fallback paths mandated by the acceptance signal:
    - missing file -> defaults
    - empty file -> defaults
    - unknown rule id in [rules] -> ConfigError
    - non-taxonomy severity in [severity] -> ConfigError
    - malformed TOML -> ConfigError
Plus a happy-path override test to lock in merge semantics.
"""

from __future__ import annotations  # Postponed annotations for consistency.

from pathlib import Path  # Path type used by ConfigLoader.load().

import pytest  # pytest.raises for ConfigError assertions.

from tools.test_quality_analyzer.config import ConfigError, ConfigLoader  # SUT.
from tools.test_quality_analyzer.detection import Severity  # For override assertions.


def _write(tmp_path: Path, text: str) -> Path:
    """Write `text` to tmp_path/config.toml and return the path."""
    # Keep the filename fixed so log lines are legible in test output.
    target = tmp_path / "config.toml"  # Standard filename mirrors production usage.
    target.write_text(text, encoding="utf-8")  # UTF-8 matches tomllib requirements.
    return target  # Caller feeds this straight into ConfigLoader.load.


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    """Missing config path must fall back to built-in defaults, not raise."""
    missing = tmp_path / "nope.toml"  # Path that does not exist on disk.
    snap = ConfigLoader().load(missing)  # Should succeed via defaults branch.
    assert len(snap.rules_enabled) == 18  # 18 canonical rule ids present.
    assert all(snap.rules_enabled.values())  # All rules default to enabled.
    assert snap.mist_api_predicate.banned_imports == ("mistapi",)  # Predicate default.


def test_empty_file_falls_back_to_defaults(tmp_path: Path) -> None:
    """Empty (or whitespace-only) config file must behave like a missing file."""
    empty = _write(tmp_path, "   \n\t\n")  # Whitespace-only content.
    snap = ConfigLoader().load(empty)  # Should hit the empty-body fallback.
    assert len(snap.rules_enabled) == 18  # Defaults still populate all 18.
    assert snap.exclusion_globs == ()  # No globs configured -> empty tuple.


def test_unknown_rule_id_in_rules_raises(tmp_path: Path) -> None:
    """Any rule id outside the 18 canonical ids must raise ConfigError."""
    bad = _write(tmp_path, "[rules]\nnot_a_real_rule = true\n")  # Bogus rule name.
    with pytest.raises(ConfigError, match="Unknown rule id in \\[rules\\]"):
        ConfigLoader().load(bad)  # Exit-2 path per FR-021.


def test_non_taxonomy_severity_raises(tmp_path: Path) -> None:
    """[severity] values must be members of Severity; otherwise ConfigError."""
    bad = _write(
        tmp_path,
        '[severity]\nweak_bare_truthy = "catastrophic"\n',  # Not in taxonomy.
    )
    with pytest.raises(ConfigError, match="unknown severity"):
        ConfigLoader().load(bad)  # Malformed severity halts the run.


def test_malformed_toml_raises(tmp_path: Path) -> None:
    """A syntactically invalid TOML file must surface as ConfigError."""
    bad = _write(tmp_path, "this is = not = valid = toml [[[\n")  # Garbage TOML.
    with pytest.raises(ConfigError, match="Malformed TOML"):
        ConfigLoader().load(bad)  # Exception is wrapped with file context.


def test_severity_override_applied(tmp_path: Path) -> None:
    """Happy path: a valid override changes the resulting severity mapping."""
    ok = _write(
        tmp_path,
        '[severity]\nweak_bare_truthy = "critical"\n',  # Bump one rule up.
    )
    snap = ConfigLoader().load(ok)  # Should merge cleanly.
    assert snap.severity_overrides["weak_bare_truthy"] == Severity.CRITICAL  # Override took effect.
    # Unrelated rule should keep its documented default (MEDIUM for weak_assert_not_none).
    assert snap.severity_overrides["weak_assert_not_none"] == Severity.MEDIUM


def test_rules_disable_flag_respected(tmp_path: Path) -> None:
    """Disabling a rule via [rules] must be honored in the merged mapping."""
    ok = _write(tmp_path, "[rules]\nweak_bare_truthy = false\n")  # Disable one rule.
    snap = ConfigLoader().load(ok)  # Loader should accept boolean false.
    assert snap.rules_enabled["weak_bare_truthy"] is False  # Override took effect.
    assert snap.rules_enabled["weak_no_assertions"] is True  # Other rules unchanged.


def test_non_bool_rule_value_raises(tmp_path: Path) -> None:
    """[rules] values must be booleans; strings/ints must fail validation."""
    bad = _write(tmp_path, '[rules]\nweak_bare_truthy = "yes"\n')  # String, not bool.
    with pytest.raises(ConfigError, match="must be boolean"):
        ConfigLoader().load(bad)  # Type-checking catches the bad value.
