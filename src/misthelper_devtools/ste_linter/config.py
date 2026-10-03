"""Configuration for the STE linter.

Loads settings from the ``[tool.ste_linter]`` table in ``pyproject.toml`` and
merges command-line overrides. The configuration holds the sentence limits, the
rule weights, the section weights, the pass threshold, and the rule selection.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import logging  # Records the configuration load.
import os  # Tests whether the configuration file exists.
import tomllib  # Reads the TOML configuration, part of the standard library.
from dataclasses import dataclass, field  # Declares the configuration value type.
from typing import Any  # Types the parsed TOML data, which holds mixed value types.

# The logger for the configuration stage. The CLI configures the handlers.
_LOG = logging.getLogger("ste_linter.config")

# The default path to the dictionary file, which git ignores.
_DEFAULT_DICTIONARY = os.path.join("data", "ste_dictionary.json")

# The environment variable that names a dictionary file. It outranks every file path.
_ENV_DICTIONARY = "STE_DICTIONARY_PATH"

# The file name that every user-level dictionary location uses.
_DICTIONARY_FILENAME = "ste_dictionary.json"

# The default logging methods that carry operator-facing messages.
_DEFAULT_LOGGING_CALLS = (
    "logging.debug",
    "logging.info",
    "logging.warning",
    "logging.error",
    "logging.critical",
    "logging.exception",
)

# The default call names that carry prompts or printed text.
_DEFAULT_USER_FACING_CALLS = ("print", "safe_input")


def user_dictionary_paths() -> tuple[str, ...]:
    """Return the user-level dictionary locations, in lookup order."""
    paths: list[str] = []  # Collect the candidates in order.
    local = os.environ.get("LOCALAPPDATA")  # Read the Windows per-user data folder.
    if local:  # The variable exists only on Windows.
        paths.append(os.path.join(local, "ste-linter", _DICTIONARY_FILENAME))  # Add the Windows location.
    home = os.path.expanduser("~")  # Read the home folder on every platform.
    paths.append(os.path.join(home, ".local", "share", "ste-linter", _DICTIONARY_FILENAME))  # Add the XDG location.
    paths.append(os.path.join(home, ".ste-linter", _DICTIONARY_FILENAME))  # Add the simple home location.
    return tuple(paths)  # Give the caller an immutable order.


def resolve_dictionary_path(configured: str | None = None) -> str:
    """Return the first dictionary path that exists, or the configured default."""
    candidates: list[str] = []  # Collect every candidate in precedence order.
    env = os.environ.get(_ENV_DICTIONARY)  # Read the environment override first.
    if env:  # An empty value means the operator set no override.
        candidates.append(env)  # The environment value outranks every file path.
    if configured:  # The configuration file named a path.
        candidates.append(configured)  # The configured path outranks the built-in default.
    candidates.append(_DEFAULT_DICTIONARY)  # Try the repository copy next.
    candidates.extend(user_dictionary_paths())  # Fall back to the user-level copies.
    for candidate in candidates:  # Walk the candidates in order.
        if os.path.isfile(candidate):  # Stop at the first file that exists.
            _LOG.debug("Resolved dictionary path to %s", candidate)  # Record the choice.
            return candidate  # Give the caller a path that exists.
    fallback = configured or _DEFAULT_DICTIONARY  # No candidate exists, so keep the stated path.
    _LOG.debug("No dictionary file found, keeping %s", fallback)  # Record the miss.
    return fallback  # The caller reports the skip with this path.


@dataclass
class LinterConfig:
    """Holds the active linter settings."""

    procedural_limit: int = 20  # The word limit for a procedural sentence.
    descriptive_limit: int = 25  # The word limit for a descriptive sentence.
    noun_cluster_limit: int = 3  # The largest allowed noun cluster.
    paragraph_limit: int = 6  # The largest allowed sentence count in a paragraph.
    min_score: int | None = None  # The pass threshold, or None for no gate.
    dictionary_path: str = _DEFAULT_DICTIONARY  # The dictionary file path.
    prefer_spacy: bool = True  # Whether to use the spaCy backend when it is present.
    weights: dict[str, float] = field(default_factory=dict)  # Per-rule weight overrides.
    section_weights: dict[str, float] = field(default_factory=dict)  # Per-section weight overrides.
    selected: set[str] = field(default_factory=set)  # Only run these rules when not empty.
    ignored: set[str] = field(default_factory=set)  # Never run these rules.
    allowlist: set[str] = field(default_factory=set)  # Technical words the dictionary rules must not flag.
    grade_logging_strings: bool = False  # Logging strings are opt in to keep old scores stable.
    grade_user_facing_strings: bool = False  # Prompt and print strings are opt in to avoid old noise.
    logging_call_names: tuple[str, ...] = _DEFAULT_LOGGING_CALLS  # Calls that hold log message templates.
    user_facing_call_names: tuple[str, ...] = _DEFAULT_USER_FACING_CALLS  # Calls that hold user text.

    def limit_for(self, mode: str) -> int:
        """Return the word limit for a sentence mode."""
        if mode == "procedural":  # A procedural sentence is a step.
            return self.procedural_limit  # Use the tighter step limit.
        return self.descriptive_limit  # Otherwise use the description limit.

    def is_allowlisted(self, word: str) -> bool:
        """Return True when a word is an approved technical term the linter must skip."""
        return word.lower() in self.allowlist  # Compare in lower case so the match ignores letter case.

    def weight_for(self, rule_id: str, default: float) -> float:
        """Return the weight for a rule, or the default from its severity."""
        return self.weights.get(rule_id, default)  # Use the override when one exists.

    def section_weight_for(self, section: str) -> float:
        """Return the display weight for a section."""
        return self.section_weights.get(section, 1.0)  # Every section weighs the same by default.

    def is_enabled(self, rule_id: str) -> bool:
        """Return True when the configuration allows a rule to run."""
        if rule_id in self.ignored:  # The rule is on the ignore list.
            return False  # Do not run the rule.
        if self.selected and rule_id not in self.selected:  # A selection is set and excludes the rule.
            return False  # Do not run the rule.
        return self.weights.get(rule_id, 1.0) != 0  # A weight of zero turns the rule off.

    @classmethod
    def load(cls, path: str = "pyproject.toml") -> LinterConfig:
        """Return a configuration from the TOML file, or the defaults."""
        config = cls()  # Start from the built-in defaults.
        table = cls._read_table(path)  # Read the tool table from the file.
        if table:  # The file holds a tool section.
            cls._apply_table(config, table)  # Apply the file settings onto the defaults.
            _LOG.debug("Loaded linter configuration from %s", path)  # Record the load.
        stated = config.dictionary_path  # Read the path that the file or the default supplied.
        configured = stated if stated != _DEFAULT_DICTIONARY else None  # Keep only a path the file stated.
        config.dictionary_path = resolve_dictionary_path(configured)  # Fall back to a dictionary that exists.
        return config  # Return the merged configuration.

    @staticmethod
    def _read_table(path: str) -> dict[str, Any]:
        """Return the ``[tool.ste_linter]`` table, or an empty dictionary."""
        if not os.path.isfile(path):  # The configuration file is not present.
            return {}  # Return an empty table.
        try:  # The file may be malformed.
            with open(path, "rb") as handle:  # TOML must be read in binary mode.
                data = tomllib.load(handle)  # Parse the TOML content.
        except (OSError, tomllib.TOMLDecodeError) as error:  # A read or parse problem.
            _LOG.warning("Could not read configuration: %s", error)  # Record the problem.
            return {}  # Return an empty table.
        tool = data.get("tool", {})  # The tool section of the file.
        result = tool.get("ste_linter", {})  # The linter table inside the tool section.
        return result if isinstance(result, dict) else {}  # Return the table when it is valid.

    @staticmethod
    def _apply_table(config: LinterConfig, table: dict[str, Any]) -> None:
        """Copy known settings from the TOML table onto the configuration."""
        config.procedural_limit = int(table.get("procedural_limit", config.procedural_limit))  # Step limit.
        config.descriptive_limit = int(table.get("descriptive_limit", config.descriptive_limit))  # Prose.
        config.noun_cluster_limit = int(table.get("noun_cluster_limit", config.noun_cluster_limit))  # Nouns.
        config.paragraph_limit = int(table.get("paragraph_limit", config.paragraph_limit))  # Paragraph.
        if "min_score" in table:  # A threshold is set in the file.
            config.min_score = int(table["min_score"])  # Use the file threshold.
        config.dictionary_path = str(table.get("dictionary", config.dictionary_path))  # Dictionary path.
        config.prefer_spacy = bool(table.get("prefer_spacy", config.prefer_spacy))  # Backend choice.
        config.weights = {str(key): float(value) for key, value in table.get("weights", {}).items()}  # Weights.
        config.section_weights = {
            str(key): float(value) for key, value in table.get("section_weights", {}).items()
        }  # Section weights.
        config.allowlist = {str(word).lower() for word in table.get("allowlist", [])}  # Approved technical terms.
        config.grade_logging_strings = bool(
            table.get("grade_logging_strings", config.grade_logging_strings)
        )  # Enable log messages only when requested.
        config.grade_user_facing_strings = bool(
            table.get("grade_user_facing_strings", config.grade_user_facing_strings)
        )  # Enable prompts and prints only when requested.
        config.logging_call_names = tuple(
            str(name) for name in table.get("logging_call_names", config.logging_call_names)
        )  # Load custom logging call names.
        config.user_facing_call_names = tuple(
            str(name) for name in table.get("user_facing_call_names", config.user_facing_call_names)
        )  # Load custom user-facing call names.
