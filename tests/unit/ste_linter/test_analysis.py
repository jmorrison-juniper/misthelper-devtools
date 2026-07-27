"""Tests for the analysis package."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from tools.ste_linter.analysis import GrammarAnalyzer, get_backend  # The analysis parts under test.
from tools.ste_linter.analysis.backend import AUX  # The tag used to check the backend.


def test_factory_returns_heuristic() -> None:
    """The factory returns the heuristic backend when spaCy is not wanted."""
    assert get_backend(prefer_spacy=False).name == "heuristic"  # The heuristic backend is selected.


def test_heuristic_tags_auxiliary() -> None:
    """The heuristic backend tags a form of 'be' as an auxiliary."""
    tokens = get_backend(prefer_spacy=False).analyze("The file is ready")  # Analyze a short sentence.
    assert any(token.pos == AUX for token in tokens)  # The word "is" is an auxiliary.


def test_grammar_detects_passive() -> None:
    """The grammar analyzer detects the passive voice."""
    tokens = get_backend(prefer_spacy=False).analyze("The file is created by the parser")  # Passive.
    assert GrammarAnalyzer().has_passive(tokens)  # The sentence is passive.


def test_grammar_detects_perfect() -> None:
    """The grammar analyzer detects a perfect tense."""
    tokens = get_backend(prefer_spacy=False).analyze("The system has removed the file")  # Perfect.
    assert GrammarAnalyzer().has_perfect(tokens)  # The sentence uses a perfect tense.


def test_grammar_detects_progressive() -> None:
    """The grammar analyzer detects a progressive tense."""
    tokens = get_backend(prefer_spacy=False).analyze("The parser is reading the file")  # Progressive.
    assert GrammarAnalyzer().has_progressive(tokens)  # The sentence uses a progressive tense.


def test_grammar_finds_noun_cluster() -> None:
    """The grammar analyzer finds a long noun cluster."""
    tokens = get_backend(prefer_spacy=False).analyze("runway light connection resistance calibration")  # Five.
    clusters = GrammarAnalyzer().noun_clusters(tokens, limit=3)  # Find clusters longer than three.
    assert clusters  # The five-word cluster was found.
