"""Dictionary-based rules.

Checks each word against a loaded STE dictionary. One rule flags an unapproved
word and names an approved alternative. The other flags a word used as the wrong
part of speech. Both rules do nothing when no dictionary is loaded.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from collections.abc import Iterator  # Types the check generators and helpers.
from typing import TYPE_CHECKING

from ..analysis.backend import ADJ, ADV, NOUN, NUM, PROPN, PUNCT, VERB  # The tag constants.
from ..models import Document, Severity, Violation  # The document, severity, and violation types.
from .base import Rule, RuleContext  # The rule base and the shared context.

if TYPE_CHECKING:  # Import these types for annotations only.
    from ..analysis import Token  # The analyzed token type.
    from ..dictionary.loader import DictionaryEntry  # The dictionary entry type.

# Tags the dictionary rules do not check, because they are not dictionary words.
_SKIP_TAGS = frozenset({PROPN, NUM, PUNCT})

# Maps a dictionary part-of-speech label to the linter tag it matches.
_POS_CATEGORY = {
    "n": NOUN,
    "noun": NOUN,  # Noun labels.
    "v": VERB,
    "verb": VERB,  # Verb labels.
    "adj": ADJ,
    "adjective": ADJ,  # Adjective labels.
    "adv": ADV,
    "adverb": ADV,  # Adverb labels.
}


class UnapprovedWordRule(Rule):
    """Flags a word the dictionary marks as unapproved."""

    rule_id = "STE-S1-WORD"  # The rule identifier.
    section = "1-words"  # The writing-guide section.
    severity = Severity.WARNING  # An unapproved word is a likely break.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each unapproved word."""
        if context.dictionary is None:  # No dictionary is loaded.
            return  # Skip the dictionary check.
        for sentence in document.sentences:  # Walk each sentence.
            for token in context.tokens(sentence.text):  # Each analyzed token.
                if token.pos in _SKIP_TAGS or not token.text.isalpha():  # Skip non-words.
                    continue  # Move to the next token.
                if context.config.is_allowlisted(token.text):  # Skip an approved technical term.
                    continue  # The allowlist marks this word as correct for the project.
                entries = context.dictionary.lookup(token.text.lower())  # Look up the word.
                if entries and all(not entry.approved for entry in entries):  # The word is unapproved.
                    yield self._violation(
                        document.path,  # The file path.
                        sentence.line,  # The source line.
                        f"Unapproved word '{token.text}'.",  # The problem description.
                        self._alternative(entries),  # The suggested fix.
                    )  # Report the unapproved word.

    def _alternative(self, entries: list[DictionaryEntry]) -> str:
        """Return a suggestion that names an approved alternative when one exists."""
        for entry in entries:  # Walk the dictionary entries for the word.
            if entry.alternatives:  # The entry names an approved alternative.
                return f"Use an approved word such as '{entry.alternatives[0]}'."  # Suggest it.
        return "Use an approved word from the STE dictionary."  # A general suggestion.


class WrongPartOfSpeechRule(Rule):
    """Flags a word used as a part of speech the dictionary does not approve."""

    rule_id = "STE-S1-POS"  # The rule identifier.
    section = "1-words"  # The writing-guide section.
    severity = Severity.INFO  # A part-of-speech guess needs the writer to confirm.
    scope = "word"  # The rule applies at the word level.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each word used as the wrong part of speech."""
        if context.dictionary is None:  # No dictionary is loaded.
            return  # Skip the dictionary check.
        for sentence in document.sentences:  # Walk each sentence.
            for token in context.tokens(sentence.text):  # Each analyzed token.
                if token.pos not in (NOUN, VERB, ADJ, ADV):  # Only check content words.
                    continue  # Move to the next token.
                if context.config.is_allowlisted(token.text):  # Skip an approved technical term.
                    continue  # The allowlist marks this word as correct for the project.
                if self._is_wrong(context, token):  # The word is used as the wrong part of speech.
                    yield self._violation(
                        document.path,  # The file path.
                        sentence.line,  # The source line.
                        f"Word '{token.text}' is used as a {token.pos.lower()}.",  # The problem.
                        "Use the word only as its approved part of speech.",  # The suggested fix.
                    )  # Report the wrong part of speech.

    def _is_wrong(self, context: RuleContext, token: Token) -> bool:
        """Return True when the dictionary approves the word only as another part of speech."""
        entries = context.dictionary.lookup(token.text.lower()) if context.dictionary else []  # Look up.
        approved = [entry for entry in entries if entry.approved and entry.part_of_speech]  # Approved uses.
        if not approved:  # The dictionary has no approved part of speech for the word.
            return False  # Do not flag the word.
        categories = {_POS_CATEGORY.get(entry.part_of_speech.lower()) for entry in approved}  # The uses.
        categories.discard(None)  # Drop labels the map does not know.
        return bool(categories) and token.pos not in categories  # Wrong when the used tag is not approved.
