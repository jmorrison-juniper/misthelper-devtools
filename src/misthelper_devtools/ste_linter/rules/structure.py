"""Structure-level rules.

Checks for semicolons, long noun clusters, long paragraphs, and warnings that do
not state a consequence. These rules read sentences and paragraphs and use the
grammar analyzer for the noun-cluster check.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import re  # Drives the consequence-cue search.
from collections.abc import Iterator  # Types the check generators.

from ..models import Document, Severity, Violation  # The document, severity, and violation types.
from .base import Rule, RuleContext  # The rule base and the shared context.

# Cues that show a warning states a consequence. A warning without one of these is
# incomplete.
_CONSEQUENCE_CUE = re.compile(
    r"\b(can cause|causes|results? in|leads? to|otherwise|will|can|may|could|damage|injury|death)\b",
    re.IGNORECASE,
)


class SemicolonRule(Rule):
    """Flags a semicolon, which STE does not allow."""

    rule_id = "STE-S8-SEMICOLON"  # The rule identifier.
    section = "8-punctuation"  # The writing-guide section.
    severity = Severity.WARNING  # A semicolon is a likely break.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each semicolon."""
        for sentence in document.sentences:  # Walk each sentence.
            if ";" in sentence.text:  # The sentence holds a semicolon.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    "Semicolon. STE does not allow the semicolon.",  # The problem description.
                    "Write two separate sentences instead.",  # The suggested fix.
                )  # Report the semicolon.


class NounClusterRule(Rule):
    """Flags a multi-word noun cluster longer than three words."""

    rule_id = "STE-S2-NOUNCLUSTER"  # The rule identifier.
    section = "2-multiword-nouns"  # The writing-guide section.
    severity = Severity.WARNING  # A long noun cluster is a likely break.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each long noun cluster."""
        limit = context.config.noun_cluster_limit  # The largest allowed cluster size.
        for sentence in document.sentences:  # Walk each sentence.
            tokens = context.tokens(sentence.text)  # Analyze the sentence once, from the cache.
            for cluster in context.grammar.noun_clusters(tokens, limit):  # Each long cluster.
                text = " ".join(token.text for token in cluster)  # Join the cluster words.
                yield self._violation(
                    document.path,  # The file path.
                    sentence.line,  # The source line.
                    f"Noun cluster '{text}' has {len(cluster)} words. The limit is {limit}.",  # Problem.
                    "Break the cluster apart with prepositions such as 'of' or 'for'.",  # The fix.
                )  # Report the long noun cluster.


class ParagraphLengthRule(Rule):
    """Flags a paragraph with more than six sentences."""

    rule_id = "STE-S6-PARA"  # The rule identifier.
    section = "6-descriptive"  # The writing-guide section.
    severity = Severity.WARNING  # A long paragraph is a likely break.
    scope = "paragraph"  # The rule applies to each paragraph.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each paragraph over the sentence limit."""
        limit = context.config.paragraph_limit  # The largest allowed sentence count.
        for paragraph in document.paragraphs:  # Walk each paragraph.
            if len(paragraph) > limit:  # The paragraph has too many sentences.
                yield self._violation(
                    document.path,  # The file path.
                    paragraph[0].line,  # The line of the first sentence.
                    f"Paragraph has {len(paragraph)} sentences. The limit is {limit}.",  # Problem.
                    "Split the paragraph so each one keeps to a single topic.",  # The fix.
                )  # Report the long paragraph.


class WarningSignalRule(Rule):
    """Flags a warning or caution that does not state a consequence."""

    rule_id = "STE-S7-WARNING"  # The rule identifier.
    section = "7-safety"  # The writing-guide section.
    severity = Severity.WARNING  # A warning without a consequence is a likely break.
    scope = "sentence"  # The rule applies to each sentence.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each warning that gives no consequence."""
        for sentence in document.sentences:  # Walk each sentence.
            lead = sentence.text.lstrip().lower()  # The start of the sentence in lower case.
            if lead.startswith("warning") or lead.startswith("caution"):  # A safety lead word.
                if not _CONSEQUENCE_CUE.search(sentence.text):  # No consequence cue is present.
                    yield self._violation(
                        document.path,  # The file path.
                        sentence.line,  # The source line.
                        "Warning does not state a consequence.",  # The problem description.
                        "State what happens if the reader does not obey, for example 'can cause damage'.",
                    )  # Report the incomplete warning.
