"""Deterministic scoring model.

Turns the violations and the document unit counts into a score from 0 to 100. The
model is a weighted average of per-section penalties. Each rule contributes a
density, capped at 1, so no single rule can push the score past its share. See
``specs/1026-ste-linter/research.md`` Decision 4 for the formula.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from collections import Counter, defaultdict  # Counts violations and groups rules by section.
from typing import TYPE_CHECKING  # Types the rule list without an import cycle.

from .models import Document, Score, SectionScore, Violation  # The document and score types.

if TYPE_CHECKING:  # Import these types for annotations only.
    from .config import LinterConfig  # The active configuration.
    from .rules import Rule  # The rule base type.


class ScoringModel:
    """Computes the compliance score from violations and unit counts."""

    def score(
        self,
        document: Document,
        violations: list[Violation],
        rules: list[Rule],
        dictionary_used: bool,
        config: LinterConfig,
    ) -> Score:
        """Return the ``Score`` for one document."""
        units = self._unit_counts(document)  # The count of each unit kind.
        counts = Counter(violation.rule_id for violation in violations)  # Violations per rule.
        sections = self._group_by_section(rules)  # Rules grouped by their section.
        section_scores: list[SectionScore] = []  # Holds the per-section results.
        weighted_penalty = 0.0  # The running weighted penalty across sections.
        section_weight_total = 0.0  # The running total of section weights.
        for section in sorted(sections):  # Walk the sections in a stable order.
            penalty = self._section_penalty(sections[section], counts, units, config)  # Section penalty.
            violation_count = sum(counts.get(rule.rule_id, 0) for rule in sections[section])  # Count.
            section_scores.append(
                SectionScore(
                    section=section,  # The section name.
                    penalty=penalty,  # The section penalty.
                    score=round(100 * (1 - penalty)),  # The section score.
                    violation_count=violation_count,  # The number of violations.
                )
            )  # Record the section result.
            section_weight = config.section_weight_for(section)  # The display weight for the section.
            weighted_penalty += section_weight * penalty  # Add the weighted section penalty.
            section_weight_total += section_weight  # Add the section weight.
        overall = weighted_penalty / section_weight_total if section_weight_total else 0.0  # The mean.
        return Score(
            path=document.path,  # The file path.
            score=round(100 * (1 - overall)),  # The overall score.
            sections=section_scores,  # The per-section breakdown.
            violations=sorted(violations, key=lambda item: (item.line, item.rule_id)),  # Sorted list.
            dictionary_used=dictionary_used,  # Whether the dictionary checks ran.
            word_count=document.word_count,  # The graded word count.
        )  # Return the finished score.

    def _unit_counts(self, document: Document) -> dict[str, int]:
        """Return the number of sentences, words, paragraphs, and documents."""
        return {
            "sentence": len(document.sentences),  # The sentence count.
            "word": document.word_count,  # The word count.
            "paragraph": len(document.paragraphs),  # The paragraph count.
            "document": 1,  # There is always one document.
        }  # Return the unit counts.

    def _group_by_section(self, rules: list[Rule]) -> dict[str, list[Rule]]:
        """Return the rules grouped by their writing-guide section."""
        groups: dict[str, list[Rule]] = defaultdict(list)  # Holds the rule groups.
        for rule in rules:  # Walk each active rule.
            groups[rule.section].append(rule)  # Add the rule to its section group.
        return groups  # Return the grouped rules.

    def _section_penalty(
        self,
        rules: list[Rule],
        counts: Counter[str],
        units: dict[str, int],
        config: LinterConfig,
    ) -> float:
        """Return the penalty for one section as a weighted average of densities."""
        numerator = 0.0  # The sum of weight times density.
        denominator = 0.0  # The sum of weights.
        for rule in rules:  # Walk each rule in the section.
            weight = config.weight_for(rule.rule_id, float(rule.severity.value))  # The rule weight.
            unit = max(1, units.get(rule.scope, 1))  # The eligible unit count, at least one.
            density = min(1.0, counts.get(rule.rule_id, 0) / unit)  # The capped violation density.
            numerator += weight * density  # Add the weighted density.
            denominator += weight  # Add the weight.
        return numerator / denominator if denominator else 0.0  # Return the weighted average.
