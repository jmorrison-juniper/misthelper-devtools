"""Grammar analysis helpers.

Reads the analyzed tokens of a sentence and reports voice, tense, and noun-cluster
facts. Both backends produce the same token shape, so these helpers work with the
heuristic backend and the spaCy backend without a change.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from .backend import ADJ, NOUN, PROPN, Token  # The tags and token type the helpers read.

# The forms of the verb "be". A form of "be" plus a past participle is passive. A
# form of "be" plus an "-ing" word is progressive.
_BE_FORMS = frozenset({"be", "am", "is", "are", "was", "were", "been", "being"})

# The forms of the verb "have". A form of "have" plus a past participle is perfect.
_HAVE_FORMS = frozenset({"have", "has", "had", "having"})

# The tags that can form a noun cluster.
_CLUSTER_TAGS = frozenset({ADJ, NOUN, PROPN})


class GrammarAnalyzer:
    """Reports voice, tense, and noun-cluster facts for a sentence."""

    def has_passive(self, tokens: list[Token]) -> bool:
        """Return True when the sentence uses the passive voice."""
        return self._be_followed_by(tokens, participle=True)  # A be form plus a participle is passive.

    def has_progressive(self, tokens: list[Token]) -> bool:
        """Return True when the sentence uses a progressive tense."""
        return self._be_followed_by(tokens, participle=False)  # A be form plus an -ing word is progressive.

    def has_perfect(self, tokens: list[Token]) -> bool:
        """Return True when the sentence uses a perfect tense."""
        for index, token in enumerate(tokens):  # Walk the tokens with their positions.
            if token.lemma in _HAVE_FORMS:  # Found a form of "have".
                if self._participle_within(tokens, index):  # A participle follows soon.
                    return True  # A have form plus a participle is perfect.
        return False  # No perfect tense was found.

    def noun_clusters(self, tokens: list[Token], limit: int = 3) -> list[list[Token]]:
        """Return each run of adjective and noun tokens longer than ``limit``."""
        clusters: list[list[Token]] = []  # Holds the long clusters.
        run: list[Token] = []  # Holds the current run of cluster tokens.
        for token in tokens:  # Walk each token.
            if token.pos in _CLUSTER_TAGS:  # The token can join a noun cluster.
                run.append(token)  # Extend the current run.
            else:  # The token breaks the run.
                if len(run) > limit:  # The run is longer than the limit.
                    clusters.append(run)  # Record the long cluster.
                run = []  # Start a new run.
        if len(run) > limit:  # Check a run that reached the sentence end.
            clusters.append(run)  # Record the final long cluster.
        return clusters  # Return every long cluster.

    def _be_followed_by(self, tokens: list[Token], participle: bool) -> bool:
        """Return True when a "be" form is followed by a participle or a gerund."""
        for index, token in enumerate(tokens):  # Walk the tokens with their positions.
            if token.lemma in _BE_FORMS:  # Found a form of "be".
                target = self._next_content(tokens, index)  # The next content word.
                if target is None:  # No content word follows.
                    continue  # Move to the next token.
                if participle and target.is_participle:  # Looking for passive voice.
                    return True  # A be form plus a participle is passive.
                if not participle and target.is_gerund:  # Looking for a progressive tense.
                    return True  # A be form plus an -ing word is progressive.
        return False  # No matching construction was found.

    def _participle_within(self, tokens: list[Token], start: int, window: int = 3) -> bool:
        """Return True when a past participle appears within ``window`` tokens."""
        for token in tokens[start + 1 : start + 1 + window]:  # Look at the next few tokens.
            if token.is_participle:  # Found a past participle.
                return True  # A participle is within the window.
        return False  # No participle was near.

    def _next_content(self, tokens: list[Token], start: int, window: int = 3) -> Token | None:
        """Return the next word within ``window`` that is not an adverb."""
        for token in tokens[start + 1 : start + 1 + window]:  # Look at the next few tokens.
            if token.pos != "ADV":  # Skip adverbs, for example "quickly".
                return token  # Return the first content word.
        return None  # No content word was near.
