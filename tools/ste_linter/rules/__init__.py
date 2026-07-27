"""Rules package for the STE linter.

Holds every rule class and the registry that builds the active rule list from the
configuration. The registry lets a project select or ignore rules by identifier
and turn a rule off by setting its weight to zero.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from typing import TYPE_CHECKING  # Types the registry helpers without an import cycle.

from .base import Rule, RuleContext  # The rule base and the shared context.
from .dictionary import UnapprovedWordRule, WrongPartOfSpeechRule  # The dictionary rules.
from .sentences import (  # The sentence rules.
    ComplexTenseRule,
    ContractionRule,
    PassiveVoiceRule,
    SentenceLengthRule,
)
from .structure import (  # The structure rules.
    NounClusterRule,
    ParagraphLengthRule,
    SemicolonRule,
    WarningSignalRule,
)
from .words import GenderedPronounRule, LatinAbbreviationRule, PhrasalVerbRule  # The word rules.

if TYPE_CHECKING:  # Import the configuration type for annotations only.
    from ..config import LinterConfig  # The active configuration.

__all__ = ["Rule", "RuleContext", "load_rules", "all_rule_ids"]  # The public names.

# Every rule class the linter knows, in report order. The registry builds the
# active list from this master list.
_RULE_CLASSES: list[type[Rule]] = [
    SentenceLengthRule,  # Sentence length.
    PassiveVoiceRule,  # Passive voice.
    ComplexTenseRule,  # Perfect and progressive tense.
    ContractionRule,  # Contractions.
    LatinAbbreviationRule,  # Latin abbreviations.
    PhrasalVerbRule,  # Phrasal verbs.
    GenderedPronounRule,  # Gendered pronouns.
    SemicolonRule,  # Semicolons.
    NounClusterRule,  # Long noun clusters.
    ParagraphLengthRule,  # Long paragraphs.
    WarningSignalRule,  # Warnings without a consequence.
    UnapprovedWordRule,  # Unapproved dictionary words.
    WrongPartOfSpeechRule,  # Wrong part of speech.
]


def load_rules(config: LinterConfig) -> list[Rule]:
    """Return the active rules for the given configuration."""
    active: list[Rule] = []  # Holds the rules the configuration enables.
    for rule_class in _RULE_CLASSES:  # Walk each known rule class.
        rule = rule_class()  # Build one instance of the rule.
        if config.is_enabled(rule.rule_id):  # The configuration enables the rule.
            active.append(rule)  # Add the rule to the active list.
    return active  # Return the active rules.


def all_rule_ids() -> list[str]:
    """Return the identifier of every known rule."""
    return [rule_class.rule_id for rule_class in _RULE_CLASSES]  # Read the identifier from each class.
