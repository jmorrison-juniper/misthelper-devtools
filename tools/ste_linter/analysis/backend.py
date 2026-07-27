"""Analysis backend protocol and token model.

Defines the ``Token`` type and the ``Backend`` protocol that the rules use to read
part-of-speech, lemma, and form information for a sentence. Two backends implement
the protocol: a standard-library heuristic backend and an optional spaCy backend.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from dataclasses import dataclass  # Declares the Token value type.
from typing import Protocol  # Declares the Backend interface without inheritance.

# The small part-of-speech tag set the backends produce. The names follow the
# universal part-of-speech style so the heuristic and spaCy backends agree.
NOUN: str = "NOUN"  # A common noun.
PROPN: str = "PROPN"  # A proper noun.
VERB: str = "VERB"  # A main verb.
AUX: str = "AUX"  # An auxiliary verb, for example a form of "be" or "have".
ADJ: str = "ADJ"  # An adjective.
ADV: str = "ADV"  # An adverb.
ADP: str = "ADP"  # A preposition.
DET: str = "DET"  # A determiner, for example "the".
PRON: str = "PRON"  # A pronoun.
CONJ: str = "CONJ"  # A conjunction.
NUM: str = "NUM"  # A number.
PUNCT: str = "PUNCT"  # A punctuation mark.
OTHER: str = "OTHER"  # Anything the backend cannot label.


@dataclass(frozen=True)
class Token:
    """One analyzed word in a sentence."""

    text: str  # The word as it appears in the sentence.
    pos: str  # The part-of-speech tag from the set above.
    lemma: str  # The base form of the word, in lower case.
    is_participle: bool = False  # True when the word is a past participle.
    is_gerund: bool = False  # True when the word is an "-ing" form.


class Backend(Protocol):
    """The interface a rule uses to analyze a sentence."""

    name: str  # A short name shown in the report, for example "heuristic".

    def analyze(self, sentence: str) -> list[Token]:
        """Return the analyzed tokens for one sentence."""
