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
        if not table:  # The file or section is missing.
            return config  # Return the defaults.
        cls._apply_table(config, table)  # Apply the file settings onto the defaults.
        _LOG.debug("Loaded linter configuration from %s", path)  # Record the load.
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
