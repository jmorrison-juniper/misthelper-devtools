"""Tests for the rules."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from tools.ste_linter.dictionary import Dictionary, DictionaryEntry  # Types for the dictionary rule test.
from tools.ste_linter.rules.dictionary import UnapprovedWordRule  # The dictionary rule under test.
from tools.ste_linter.rules.sentences import (  # The sentence rules under test.
    ComplexTenseRule,
    ContractionRule,
    PassiveVoiceRule,
    SentenceLengthRule,
)
from tools.ste_linter.rules.structure import (  # The structure rules under test.
    NounClusterRule,
    ParagraphLengthRule,
    SemicolonRule,
    WarningSignalRule,
)
from tools.ste_linter.rules.words import (  # The word rules under test.
    GenderedPronounRule,
    LatinAbbreviationRule,
    PhrasalVerbRule,
)


def _run(rule, document, context):
    """Return the list of violations a rule reports for a document."""
    return list(rule.check(document, context))  # Collect the rule findings.


def test_sentence_length_flags_long(build_doc, make_context) -> None:
    """A sentence over the descriptive limit is flagged."""
    text = "The system " + "very " * 30 + "slow."  # A long descriptive sentence.
    violations = _run(SentenceLengthRule(), build_doc(text), make_context())  # Run the rule.
    assert violations and violations[0].rule_id == "STE-S4-LEN"  # The long sentence was flagged.


def test_passive_voice_flagged(build_doc, make_context) -> None:
    """A passive sentence is flagged."""
    violations = _run(PassiveVoiceRule(), build_doc("The file is created by the parser."), make_context())
    assert violations  # The passive sentence was flagged.


def test_complex_tense_flagged(build_doc, make_context) -> None:
    """A perfect tense is flagged."""
    violations = _run(ComplexTenseRule(), build_doc("The system has removed the file."), make_context())
    assert violations  # The complex tense was flagged.


def test_contraction_flagged(build_doc, make_context) -> None:
    """A contraction is flagged with the full form."""
    violations = _run(ContractionRule(), build_doc("It isn't ready."), make_context())  # Run the rule.
    assert violations and "is not" in violations[0].suggestion  # The full form is suggested.


def test_latin_abbreviation_flagged(build_doc, make_context) -> None:
    """A Latin abbreviation is flagged."""
    violations = _run(LatinAbbreviationRule(), build_doc("Use a tool, e.g. a wrench."), make_context())
    assert violations  # The Latin abbreviation was flagged.


def test_phrasal_verb_flagged(build_doc, make_context) -> None:
    """A phrasal verb is flagged with a single verb."""
    violations = _run(PhrasalVerbRule(), build_doc("Put out the fire now."), make_context())  # Run the rule.
    assert violations and "extinguish" in violations[0].suggestion  # The single verb is suggested.


def test_gendered_pronoun_flagged(build_doc, make_context) -> None:
    """A gendered pronoun is flagged."""
    violations = _run(GenderedPronounRule(), build_doc("He fixed the cable."), make_context())  # Run.
    assert violations  # The gendered pronoun was flagged.


def test_semicolon_flagged(build_doc, make_context) -> None:
    """A semicolon is flagged."""
    violations = _run(SemicolonRule(), build_doc("Do this; do that."), make_context())  # Run the rule.
    assert violations  # The semicolon was flagged.


def test_noun_cluster_flagged(build_doc, make_context) -> None:
    """A long noun cluster is flagged."""
    text = "The runway light connection resistance calibration failed."  # A five-word cluster.
    violations = _run(NounClusterRule(), build_doc(text), make_context())  # Run the rule.
    assert violations  # The long cluster was flagged.


def test_paragraph_length_flagged(build_doc, make_context) -> None:
    """A paragraph with more than six sentences is flagged."""
    text = "A runs. B runs. C runs. D runs. E runs. F runs. G runs."  # Seven short sentences.
    violations = _run(ParagraphLengthRule(), build_doc(text), make_context())  # Run the rule.
    assert violations  # The long paragraph was flagged.


def test_warning_without_consequence_flagged(build_doc, make_context) -> None:
    """A warning with no consequence is flagged."""
    violations = _run(WarningSignalRule(), build_doc("Warning: be careful here."), make_context())  # Run.
    assert violations  # The incomplete warning was flagged.


def test_warning_with_consequence_passes(build_doc, make_context) -> None:
    """A warning that states a consequence is not flagged."""
    violations = _run(WarningSignalRule(), build_doc("Warning: this can cause a burn."), make_context())
    assert not violations  # The complete warning passed.


def test_unapproved_word_flagged(build_doc, make_context) -> None:
    """An unapproved dictionary word is flagged with an alternative."""
    entry = DictionaryEntry(keyword="accuracy", part_of_speech="n", approved=False, alternatives=["precision"])
    dictionary = Dictionary({"accuracy": [entry]})  # A small test dictionary.
    violations = _run(UnapprovedWordRule(), build_doc("The accuracy is high."), make_context(dictionary))
    assert violations and "precision" in violations[0].suggestion  # The alternative is suggested.


def test_dictionary_rule_skips_without_dictionary(build_doc, make_context) -> None:
    """The dictionary rule does nothing when no dictionary is loaded."""
    violations = _run(UnapprovedWordRule(), build_doc("The accuracy is high."), make_context(None))  # No dict.
    assert not violations  # The rule was skipped.
