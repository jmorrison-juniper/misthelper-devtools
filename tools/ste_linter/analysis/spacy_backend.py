"""Optional spaCy analysis backend.

Wraps a loaded spaCy pipeline and maps its tags to the linter token shape. The
factory in ``__init__`` builds this backend only when spaCy and a model are
present. The rest of the linter never imports spaCy directly.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from typing import Any  # The spaCy pipeline has no stub, so it is typed as Any.

from .backend import (  # The tags and token type this backend produces.
    ADJ,
    ADP,
    ADV,
    AUX,
    CONJ,
    DET,
    NOUN,
    NUM,
    OTHER,
    PRON,
    PROPN,
    PUNCT,
    VERB,
    Token,
)

# Maps a spaCy universal part-of-speech tag to the linter tag set.
_POS_MAP = {
    "NOUN": NOUN,  # Common noun.
    "PROPN": PROPN,  # Proper noun.
    "VERB": VERB,  # Main verb.
    "AUX": AUX,  # Auxiliary verb.
    "ADJ": ADJ,  # Adjective.
    "ADV": ADV,  # Adverb.
    "ADP": ADP,  # Preposition.
    "DET": DET,  # Determiner.
    "PRON": PRON,  # Pronoun.
    "CCONJ": CONJ,  # Coordinating conjunction.
    "SCONJ": CONJ,  # Subordinating conjunction.
    "NUM": NUM,  # Number.
    "PUNCT": PUNCT,  # Punctuation.
}


class SpacyBackend:
    """Analyzes a sentence with a loaded spaCy pipeline."""

    name = "spacy"  # The backend name shown in the report.

    def __init__(self, pipeline: Any) -> None:
        """Store the loaded spaCy pipeline."""
        self._pipeline = pipeline  # The callable spaCy language pipeline.

    def analyze(self, sentence: str) -> list[Token]:
        """Return the analyzed tokens for one sentence."""
        document = self._pipeline(sentence)  # Run the spaCy pipeline on the sentence.
        tokens: list[Token] = []  # Holds the mapped tokens.
        for word in document:  # Walk each spaCy token.
            pos = _POS_MAP.get(word.pos_, OTHER)  # Map the spaCy tag to the linter tag.
            tokens.append(
                Token(
                    text=word.text,  # The word text.
                    pos=pos,  # The mapped part of speech.
                    lemma=word.lemma_.lower(),  # The spaCy lemma in lower case.
                    is_participle=word.tag_ == "VBN",  # The Penn tag for a past participle.
                    is_gerund=word.tag_ == "VBG",  # The Penn tag for a gerund or present participle.
                )
            )  # Add the mapped token.
        return tokens  # Return the analyzed tokens.
