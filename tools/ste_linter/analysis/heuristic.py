"""Standard-library heuristic backend.

Tags each word in a sentence with a part of speech from word lists and simple
suffix rules. The tags are a best effort. They favor few false alarms so the score
does not over-penalize a guess. A project that wants higher accuracy installs
spaCy, and the factory selects the spaCy backend instead.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the tokenizer.

from .backend import (  # The tags and token type this backend produces.
    ADP,
    ADV,
    AUX,
    CONJ,
    DET,
    NOUN,
    NUM,
    PRON,
    PROPN,
    PUNCT,
    VERB,
    Token,
)

# Auxiliary verbs: forms of "be", "have", and "do", plus the modal verbs.
_AUX = frozenset(
    {
        "be",
        "am",
        "is",
        "are",
        "was",
        "were",
        "been",
        "being",  # Forms of "be".
        "have",
        "has",
        "had",
        "having",  # Forms of "have".
        "do",
        "does",
        "did",  # Forms of "do".
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",  # Modal verbs.
    }
)

# Common prepositions. These break a noun cluster and mark a phrase boundary.
_ADP = frozenset(
    {
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",  # Common prepositions.
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",  # More prepositions.
        "up",
        "down",
        "over",
        "under",
        "again",
        "then",
        "once",
        "here",
        "there",
        "as",  # More prepositions.
    }
)

# Determiners, which mark the start of a noun phrase.
_DET = frozenset({"the", "a", "an", "this", "that", "these", "those", "each", "every", "some", "any", "no"})

# Pronouns, including the gendered pronouns the word rules check.
_PRON = frozenset(
    {
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",  # Personal pronouns.
        "his",
        "hers",
        "its",
        "our",
        "their",
        "your",
        "my",
        "mine",
        "yours",
        "theirs",  # Possessive pronouns.
        "who",
        "whom",
        "which",
        "what",
        "whose",  # Question and relative pronouns.
    }
)

# Conjunctions, which join clauses.
_CONJ = frozenset({"and", "or", "but", "nor", "so", "yet", "because", "although", "while", "if", "when"})

# Common irregular past participles. The suffix rule misses these, so the backend
# lists them to detect passive and perfect forms.
_IRREGULAR_PARTICIPLES = frozenset(
    {
        "done",
        "gone",
        "seen",
        "made",
        "run",
        "set",
        "put",
        "been",
        "written",
        "taken",  # Irregular participles.
        "given",
        "known",
        "shown",
        "found",
        "held",
        "kept",
        "sent",
        "built",
        "read",
        "understood",  # More.
        "begun",
        "broken",
        "chosen",
        "driven",
        "eaten",
        "fallen",
        "forgotten",
        "hidden",
        "left",  # More.
        "lost",
        "meant",
        "met",
        "paid",
        "said",
        "sold",
        "told",
        "thought",
        "won",
        "cut",  # More.
    }
)

# A small set of common base-form verbs the suffix rules miss.
_COMMON_VERBS = frozenset(
    {
        "set",
        "run",
        "put",
        "cut",
        "read",
        "click",
        "press",
        "type",
        "use",
        "make",  # Common verbs.
        "check",
        "install",
        "remove",
        "open",
        "close",
        "start",
        "stop",
        "add",
        "delete",  # More verbs.
        "connect",
        "select",
        "enter",
        "turn",
        "hold",
        "replace",
        "enable",
        "disable",  # More verbs.
    }
)

# Matches a word (with inner hyphen or apostrophe), a number, or a single symbol.
_TOKEN = re.compile(r"[A-Za-z]+(?:['\-][A-Za-z]+)*|\d+(?:\.\d+)?|[^\w\s]")


class HeuristicBackend:
    """Tags a sentence with parts of speech using word lists and suffix rules."""

    name = "heuristic"  # The backend name shown in the report.

    def analyze(self, sentence: str) -> list[Token]:
        """Return the analyzed tokens for one sentence."""
        tokens: list[Token] = []  # Holds the analyzed tokens.
        for index, raw in enumerate(_TOKEN.findall(sentence)):  # Walk each raw token.
            tokens.append(self._classify(raw, index))  # Classify the token and add it.
        return tokens  # Return the analyzed tokens.

    def _classify(self, raw: str, index: int) -> Token:
        """Return a ``Token`` with a part of speech, a lemma, and form flags."""
        lower = raw.lower()  # The lower-case form used for the word-list tests.
        is_gerund = len(lower) > 4 and lower.endswith("ing")  # An "-ing" word is a gerund.
        is_participle = lower in _IRREGULAR_PARTICIPLES or lower.endswith("ed")  # A past participle test.
        pos = self._part_of_speech(raw, lower, index, is_gerund)  # Decide the part of speech.
        return Token(text=raw, pos=pos, lemma=self._lemma(lower), is_participle=is_participle, is_gerund=is_gerund)

    def _part_of_speech(self, raw: str, lower: str, index: int, is_gerund: bool) -> str:
        """Return the part-of-speech tag for one token."""
        if not raw[0].isalnum():  # The token is a punctuation mark.
            return PUNCT  # Tag it as punctuation.
        if raw[0].isdigit():  # The token is a number.
            return NUM  # Tag it as a number.
        closed = self._closed_class_tag(lower)  # Check the closed word lists.
        if closed is not None:  # The word is in a closed list.
            return closed  # Return the closed-class tag.
        return self._open_class_tag(raw, lower, index, is_gerund)  # Fall back to open-class rules.

    def _closed_class_tag(self, lower: str) -> str | None:
        """Return a tag when the word is in a closed word list, else None."""
        for word_set, tag in self._lookup_table():  # Check each closed word list.
            if lower in word_set:  # The word is in this list.
                return tag  # Return the matching tag.
        return None  # The word is not in any closed list.

    def _open_class_tag(self, raw: str, lower: str, index: int, is_gerund: bool) -> str:
        """Return a tag for an open-class word from suffix and case rules."""
        if lower.endswith("ly"):  # A word that ends in "-ly" is usually an adverb.
            return ADV  # Tag it as an adverb.
        if is_gerund or lower.endswith("ed") or lower in _COMMON_VERBS:  # Verb-like forms.
            return VERB  # Tag it as a verb.
        if index > 0 and raw[0].isupper():  # A capitalized word inside the sentence.
            return PROPN  # Tag it as a proper noun.
        return NOUN  # Default every other word to a common noun.

    def _lookup_table(self) -> list[tuple[frozenset[str], str]]:
        """Return the closed word lists paired with their tags."""
        return [
            (_AUX, AUX),  # Auxiliary and modal verbs.
            (_ADP, ADP),  # Prepositions.
            (_DET, DET),  # Determiners.
            (_PRON, PRON),  # Pronouns.
            (_CONJ, CONJ),  # Conjunctions.
        ]  # The order does not matter because the lists do not overlap.

    def _lemma(self, lower: str) -> str:
        """Return a rough base form of the word."""
        for suffix in ("ing", "ed", "es", "s"):  # Try each common inflection suffix.
            if lower.endswith(suffix) and len(lower) - len(suffix) >= 3:  # Keep a real stem.
                return lower[: -len(suffix)]  # Strip the suffix to get the stem.
        return lower  # Return the word unchanged when no suffix applies.
