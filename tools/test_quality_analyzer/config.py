"""Config loader for the test quality analyzer (T011).

Uses stdlib `tomllib`. Fail-fast on unknown rule ids, non-boolean rule values,
non-taxonomy severity strings, and invalid TOML. Missing/empty files fall
back to the built-in defaults with an info log.

The CLI translates `ConfigError` into exit code 2 per FR-021.
"""

from __future__ import annotations  # Postponed annotations.

import logging  # info/debug logging per Principle VII.
import tomllib  # Stdlib TOML parser (Python 3.11+).
from pathlib import Path  # Path type for load().
from types import MappingProxyType  # Read-only mapping wrapper for immutability.

from tools.test_quality_analyzer.detection import (  # Types come from detection package.
    ConfigSnapshot,
    MistApiPredicate,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module logger.

# The 18 canonical rule ids (matches config.toml [rules] table exactly).
_KNOWN_RULE_IDS: frozenset[str] = frozenset(
    {
        "untested_public_function",
        "weak_bare_truthy",
        "weak_assert_not_none",
        "weak_mock_called",
        "weak_broad_raises",
        "weak_no_assertions",
        "weak_self_mock",
        "missing_timeout",
        "missing_connection_error",
        "missing_http_4xx",
        "missing_http_5xx",
        "missing_malformed_json",
        "missing_empty_body",
        "edge_empty_value",
        "edge_none_value",
        "edge_oversized_value",
        "edge_unicode_value",
        "tautological_return_echo",
    }
)

# Built-in defaults used when config.toml is missing/empty (mirrors the file).
_DEFAULT_SEVERITIES: dict[str, Severity] = {
    "untested_public_function": Severity.HIGH,
    "weak_bare_truthy": Severity.MEDIUM,
    "weak_assert_not_none": Severity.MEDIUM,
    "weak_mock_called": Severity.HIGH,
    "weak_broad_raises": Severity.MEDIUM,
    "weak_no_assertions": Severity.HIGH,
    "weak_self_mock": Severity.HIGH,
    "missing_timeout": Severity.HIGH,
    "missing_connection_error": Severity.HIGH,
    "missing_http_4xx": Severity.MEDIUM,
    "missing_http_5xx": Severity.HIGH,
    "missing_malformed_json": Severity.MEDIUM,
    "missing_empty_body": Severity.LOW,
    "edge_empty_value": Severity.LOW,
    "edge_none_value": Severity.LOW,
    "edge_oversized_value": Severity.LOW,
    "edge_unicode_value": Severity.LOW,
    "tautological_return_echo": Severity.HIGH,
}


class ConfigError(Exception):
    """Raised on any config parse or validation failure. CLI maps to exit 2."""


class ConfigLoader:
    """Reads and validates the analyzer's TOML configuration file."""

    def load(self, path: Path) -> ConfigSnapshot:
        """Parse `path` and return a validated ConfigSnapshot."""
        # Announce the load attempt so operators can trace which file was used.
        _LOGGER.info("Loading config from %s", path)
        # Missing or empty file: fall back to defaults with an info log.
        raw = self._read_or_default(path)
        # Split into three tables with defaults applied for missing keys.
        rules_enabled = self._parse_rules(raw.get("rules", {}))
        severity_overrides = self._parse_severities(raw.get("severity", {}))
        exclusions = raw.get("exclusions", {})
        predicate = self._parse_predicate(exclusions)
        globs = self._parse_globs(exclusions)
        # Debug log after with rule count so we can trace default vs override runs.
        _LOGGER.debug("Config loaded: %s rules enabled", sum(rules_enabled.values()))
        # Build and return the immutable snapshot (Mapping wrappers freeze reads).
        return ConfigSnapshot(
            rules_enabled=MappingProxyType(rules_enabled),
            severity_overrides=MappingProxyType(severity_overrides),
            exclusion_globs=globs,
            mist_api_predicate=predicate,
        )

    def _read_or_default(self, path: Path) -> dict:
        """Return parsed TOML dict, or an empty dict when file is missing/empty."""
        # Empty dict path: file does not exist -> defaults per contracts/config.schema.md.
        if not path.exists():
            _LOGGER.info("Config file %s missing; using built-in defaults", path)
            return {}
        # Empty-body path: same fallback as missing file.
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            _LOGGER.info("Config file %s empty; using built-in defaults", path)
            return {}
        # Try parsing; malformed TOML surfaces as ConfigError.
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Malformed TOML in {path}: {exc}") from exc

    def _parse_rules(self, table: dict) -> dict[str, bool]:
        """Validate [rules] table and merge with built-in defaults (all enabled)."""
        # Start from a defaults dict so missing keys stay enabled per contract.
        merged = {rule_id: True for rule_id in _KNOWN_RULE_IDS}
        # Iterate user-supplied keys and validate id + value.
        for key, value in table.items():
            if key not in _KNOWN_RULE_IDS:
                raise ConfigError(f"Unknown rule id in [rules]: {key}")
            if not isinstance(value, bool):
                raise ConfigError(f"[rules].{key} must be boolean, got {type(value).__name__}")
            merged[key] = value  # Apply the override.
        return merged

    def _parse_severities(self, table: dict) -> dict[str, Severity]:
        """Validate [severity] table and merge with built-in defaults."""
        # Start from documented defaults so missing keys use the FR-009 mapping.
        merged: dict[str, Severity] = dict(_DEFAULT_SEVERITIES)
        # Iterate overrides and validate rule id + severity string.
        for key, value in table.items():
            if key not in _KNOWN_RULE_IDS:
                raise ConfigError(f"Unknown rule id in [severity]: {key}")
            if not isinstance(value, str):
                raise ConfigError(f"[severity].{key} must be a taxonomy string")
            try:
                merged[key] = Severity(value)  # Enum coerce validates the taxonomy.
            except ValueError as exc:
                raise ConfigError(f"[severity].{key}: unknown severity '{value}'") from exc
        return merged

    def _parse_predicate(self, exclusions: dict) -> MistApiPredicate:
        """Validate Mist-API predicate parameters from [exclusions]."""
        # Default values match contracts/config.schema.md.
        banned = exclusions.get("banned_imports", ["mistapi"])
        prefixes = exclusions.get("excluded_src_prefixes", ["src/api/"])
        # Type-check both lists so a bad config fails fast.
        if not isinstance(banned, list) or not all(isinstance(x, str) for x in banned):
            raise ConfigError("[exclusions].banned_imports must be a list of strings")
        if not isinstance(prefixes, list) or not all(isinstance(x, str) for x in prefixes):
            raise ConfigError("[exclusions].excluded_src_prefixes must be a list of strings")
        # Freeze into tuples so the snapshot is immutable.
        return MistApiPredicate(banned_imports=tuple(banned), excluded_src_prefixes=tuple(prefixes))

    def _parse_globs(self, exclusions: dict) -> tuple[str, ...]:
        """Validate [exclusions].path_globs -> tuple[str, ...]."""
        # Missing key -> empty tuple per contract.
        globs = exclusions.get("path_globs", [])
        # Must be a list of strings; anything else is an error.
        if not isinstance(globs, list) or not all(isinstance(x, str) for x in globs):
            raise ConfigError("[exclusions].path_globs must be a list of strings")
        return tuple(globs)
