"""Sentence-level rules.

Checks sentence length, passive voice, complex tenses, and contractions. These
rules read one sentence at a time and use the backend and the grammar analyzer
from the rule context.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the contraction search.
from collections.abc import Iterator  # Types the check generators.
from typing import TYPE_CHECKING

from ..models import Document, Severity, Violation  # The document, severity, and violation types.
from .base import Rule, RuleContext  # The rule base and the shared context.

if TYPE_CHECKING:  # Import the token type for annotations only.
    from ..analysis import Token  # The analyzed token type.

# Maps a contraction to its full form. The contraction rule reports the full form.
_CONTRACTIONS = {
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",  # Forms of "do".
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",  # Forms of "be".
    "can't": "cannot",
    "won't": "will not",
    "wouldn't": "would not",  # Modal forms.
    "shouldn't": "should not",
    "couldn't": "could not",
    "mustn't": "must not",  # More modal forms.
    "hasn't": "has not",
    "haven't": "have not",
    "hadn't": "had not",  # Forms of "have".
    "it's": "it is",
    "we're": "we are",
    "you're": "you are",
    "they're": "they are",  # Pronoun forms.
    "i'm": "I am",
    "let's": "let us",
    "that's": "that is",
    "there's": "there is",  # More forms.
}

# Matches any contraction from the map, with word boundaries and any case.
_CONTRACTION_PATTERN = re.compile(r"\b(" + "|".join(re.escape(word) for word in _CONTRACTIONS) + r")\b", re.IGNORECASE)


class SentenceLengthRule(Rule):
    """Flags a sentence that is longer than the STE word limit."""

    rule_id = "STE-S4-LEN"  # The rule identifier.
    section = "4-sentences"  # The writing-guide section.
    severity = Severity.ERROR  # A length break is a clear error.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each sentence over its length limit."""
        for sentence in document.sentences:  # Walk each sentence.
            limit = context.config.limit_for(sentence.mode)  # The word limit for the sentence mode.
            if sentence.word_count > limit:  # The sentence is too long.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Sentence has {sentence.word_count} words. The limit is {limit} "
                    f"for a {sentence.mode} sentence.",  # The problem description.
                    "Split the sentence into shorter sentences or remove words.",  # The suggested fix.
                )  # Report the long sentence.


class PassiveVoiceRule(Rule):
    """Flags a sentence that uses the passive voice."""

    rule_id = "STE-S3-PASSIVE"  # The rule identifier.
    section = "3-verbs"  # The writing-guide section.
    severity = Severity.WARNING  # Passive voice is a likely break.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each passive sentence."""
        for sentence in document.sentences:  # Walk each sentence.
            tokens = context.tokens(sentence.text)  # Analyze the sentence once, from the cache.
            if context.grammar.has_passive(tokens):  # The sentence is passive.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    "Passive voice. Name the actor and use the active voice.",  # The problem description.
                    "Rewrite so the actor is the subject of the sentence.",  # The suggested fix.
                )  # Report the passive sentence.


class ComplexTenseRule(Rule):
    """Flags a sentence that uses a perfect or progressive tense."""

    rule_id = "STE-S3-TENSE"  # The rule identifier.
    section = "3-verbs"  # The writing-guide section.
    severity = Severity.WARNING  # A complex tense is a likely break.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each sentence with a complex tense."""
        for sentence in document.sentences:  # Walk each sentence.
            tokens = context.tokens(sentence.text)  # Analyze the sentence once, from the cache.
            problems = self._named_tenses(context, tokens)  # Find the complex tenses present.
            if problems:  # At least one complex tense is present.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"{problems} tense. Use a simple tense.",  # The problem description.
                    "Use the simple present, past, or future.",  # The suggested fix.
                )  # Report the complex tense.

    def _named_tenses(self, context: RuleContext, tokens: list[Token]) -> str:
        """Return a label for the complex tenses found, or an empty string."""
        found: list[str] = []  # Holds the names of the complex tenses.
        if context.grammar.has_perfect(tokens):  # A perfect tense is present.
            found.append("Perfect")  # Record the perfect tense.
        if context.grammar.has_progressive(tokens):  # A progressive tense is present.
            found.append("Progressive")  # Record the progressive tense.
        return " and ".join(found)  # Join the names, or return an empty string.


class ContractionRule(Rule):
    """Flags a contraction, which STE does not allow."""

    rule_id = "STE-S4-CONTRACTION"  # The rule identifier.
    section = "4-sentences"  # The writing-guide section.
    severity = Severity.WARNING  # A contraction is a likely break.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each contraction."""
        for sentence in document.sentences:  # Walk each sentence.
            for match in _CONTRACTION_PATTERN.finditer(sentence.text):  # Each contraction.
                found = match.group(1)  # The contraction as written.
                full = _CONTRACTIONS[found.lower()]  # The full form to use.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Contraction '{found}'. Write the full form.",  # The problem description.
                    f"Use '{full}'.",  # The suggested fix.
                )  # Report the contraction.
