"""Word-level rules.

Checks for Latin abbreviations, phrasal verbs, and gendered pronouns. Each rule
scans the sentence text and reports one violation per match with a suggested
replacement.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the word and phrase searches.
from collections.abc import Iterator  # Types the check generators.

from ..models import Document, Severity, Violation  # The document, severity, and violation types.
from .base import Rule, RuleContext  # The rule base and the shared context.

# Maps a Latin abbreviation to the plain English words to use instead.
_LATIN = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "and so on",  # The most common ones.
    "viz.": "namely",
    "et al.": "and others",
    "cf.": "compare",
    "vs.": "versus",  # More of them.
    "n.b.": "note",
    "ibid.": "in the same place",  # More of them.
}

# Matches any Latin abbreviation from the map, with any case.
_LATIN_PATTERN = re.compile("(" + "|".join(re.escape(word) for word in _LATIN) + ")", re.IGNORECASE)

# Maps a phrasal verb to a single precise verb. STE avoids phrasal verbs because
# their meaning is not clear from the parts.
_PHRASAL = {
    "put out": "extinguish",
    "give off": "release",
    "look into": "investigate",  # Clear replacements.
    "carry out": "do",
    "find out": "learn",
    "set up": "prepare",
    "shut down": "stop",  # More.
    "take out": "remove",
    "go down": "decrease",
    "go up": "increase",
    "come back": "return",  # More.
    "bring back": "restore",
    "kick off": "start",
    "turn off": "stop",
    "turn on": "start",  # More.
}

# Matches any phrasal verb from the map, with any case.
_PHRASAL_PATTERN = re.compile(r"\b(" + "|".join(re.escape(phrase) for phrase in _PHRASAL) + r")\b", re.IGNORECASE)

# The gendered pronouns the rule flags. STE asks for gender-neutral language.
_GENDERED = frozenset({"he", "she", "him", "her", "his", "hers", "himself", "herself"})

# Matches a whole-word gendered pronoun, with any case.
_GENDERED_PATTERN = re.compile(r"\b(" + "|".join(_GENDERED) + r")\b", re.IGNORECASE)


class LatinAbbreviationRule(Rule):
    """Flags a Latin abbreviation such as "e.g." or "i.e."."""

    rule_id = "STE-S9-LATIN"  # The rule identifier.
    section = "9-practices"  # The writing-guide section.
    severity = Severity.WARNING  # A Latin abbreviation is a likely break.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each Latin abbreviation."""
        for sentence in document.sentences:  # Walk each sentence.
            for match in _LATIN_PATTERN.finditer(sentence.text):  # Each Latin abbreviation.
                found = match.group(1)  # The abbreviation as written.
                plain = _LATIN[found.lower()]  # The plain English words to use.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Latin abbreviation '{found}'. Use plain English.",  # The problem description.
                    f"Use '{plain}'.",  # The suggested fix.
                )  # Report the Latin abbreviation.


class PhrasalVerbRule(Rule):
    """Flags a phrasal verb and suggests one precise verb."""

    rule_id = "STE-S9-PHRASAL"  # The rule identifier.
    section = "9-practices"  # The writing-guide section.
    severity = Severity.INFO  # A phrasal verb is a guess the writer should confirm.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each phrasal verb."""
        for sentence in document.sentences:  # Walk each sentence.
            for match in _PHRASAL_PATTERN.finditer(sentence.text):  # Each phrasal verb.
                found = match.group(1)  # The phrasal verb as written.
                single = _PHRASAL[found.lower()]  # The single verb to use.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Phrasal verb '{found}'. Use one precise verb.",  # The problem description.
                    f"Use '{single}'.",  # The suggested fix.
                )  # Report the phrasal verb.


class GenderedPronounRule(Rule):
    """Flags a gendered pronoun and asks for gender-neutral language."""

    rule_id = "STE-S9-GENDER"  # The rule identifier.
    section = "9-practices"  # The writing-guide section.
    severity = Severity.INFO  # A gendered pronoun is a guess the writer should confirm.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each gendered pronoun."""
        for sentence in document.sentences:  # Walk each sentence.
            for match in _GENDERED_PATTERN.finditer(sentence.text):  # Each gendered pronoun.
                found = match.group(1)  # The pronoun as written.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Gendered pronoun '{found}'. Use gender-neutral language.",  # The problem.
                    "Use 'they', 'them', 'their', or name the role, for example 'the user'.",  # The fix.
                )  # Report the gendered pronoun.
