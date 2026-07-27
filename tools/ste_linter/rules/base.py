"""Rule base class and rule context.

Defines the shared ``Rule`` base and the ``RuleContext`` that carries the backend,
the grammar analyzer, the configuration, and the optional dictionary. Each rule
reads a ``Document`` and yields ``Violation`` objects.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from collections.abc import Iterator  # Types the check method and the context fields.
from dataclasses import dataclass, field  # Declares the rule context value type.
from typing import TYPE_CHECKING

from ..models import Severity, Violation  # The severity enum and the violation type.

if TYPE_CHECKING:  # These imports are for type checking only, to avoid import cycles.
    from ..analysis import Backend, GrammarAnalyzer, Token  # The analysis types.
    from ..config import LinterConfig  # The configuration type.
    from ..dictionary.loader import Dictionary  # The optional dictionary type.
    from ..models import Document  # The parsed document type.


@dataclass
class RuleContext:
    """Carries the shared services a rule needs to run."""

    backend: Backend  # The part-of-speech backend.
    grammar: GrammarAnalyzer  # The voice, tense, and cluster helper.
    config: LinterConfig  # The active configuration.
    dictionary: Dictionary | None  # The dictionary, or None when it is absent.
    _token_cache: dict[str, list[Token]] = field(default_factory=dict, repr=False)  # Caches analyses.

    def tokens(self, sentence: str) -> list[Token]:
        """Return the analyzed tokens for a sentence, from the cache when possible."""
        cached = self._token_cache.get(sentence)  # Look for a cached analysis.
        if cached is None:  # The sentence was not analyzed yet.
            cached = self.backend.analyze(sentence)  # Analyze the sentence once.
            self._token_cache[sentence] = cached  # Store the analysis for reuse.
        return cached  # Return the analyzed tokens.


class Rule:
    """The base class for every STE rule.

    A subclass sets the class attributes and overrides ``check``. The scope tells
    the scoring model how many units the rule can apply to.
    """

    rule_id: str = "STE-BASE"  # The stable rule identifier.
    section: str = "0-none"  # The writing-guide section the rule belongs to.
    severity: Severity = Severity.WARNING  # The default severity for the finding.
    scope: str = "sentence"  # The unit the rule applies to for scoring.

    def check(self, document: Document, context: RuleContext) -> Iterator[Violation]:
        """Yield a violation for each place the rule fails.

        The base class yields nothing. Every concrete rule overrides this method.
        """
        raise NotImplementedError  # A subclass must supply the check logic.

    def _violation(self, path: str, line: int, message: str, suggestion: str) -> Violation:
        """Build a ``Violation`` from the rule fields and the given location."""
        return Violation(
            rule_id=self.rule_id,  # The rule identifier.
            section=self.section,  # The writing-guide section.
            severity=self.severity,  # The rule severity.
            path=path,  # The file path.
            line=line,  # The source line.
            message=message,  # The problem description.
            suggestion=suggestion,  # The suggested fix.
        )  # Return the finished violation.
