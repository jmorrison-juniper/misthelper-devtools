"""Shared fixtures for the STE linter unit tests.

Gives each test a heuristic backend, a grammar analyzer, a default configuration,
a document builder, a rule context factory, and a grade helper. The heuristic
backend is forced so the tests do not depend on spaCy.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import pathlib  # Builds paths to the shared fixture files.
from collections.abc import Callable  # Types the factory fixtures.

import pytest  # The test framework.

from tools.ste_linter.analysis import Backend, GrammarAnalyzer, get_backend  # The analysis parts.
from tools.ste_linter.config import LinterConfig  # The configuration.
from tools.ste_linter.dictionary import Dictionary  # The optional dictionary.
from tools.ste_linter.models import Document, Score  # The document and score types.
from tools.ste_linter.parsing import DocumentBuilder  # The document builder.
from tools.ste_linter.rules import RuleContext, load_rules  # The rule context and registry.
from tools.ste_linter.scoring import ScoringModel  # The scoring model.

# The folder that holds the shared fixture files.
_FIXTURES = pathlib.Path(__file__).parents[2] / "fixtures" / "ste_linter"


@pytest.fixture
def backend() -> Backend:
    """Return the heuristic backend for deterministic tests."""
    return get_backend(prefer_spacy=False)  # Force the standard-library backend.


@pytest.fixture
def grammar() -> GrammarAnalyzer:
    """Return a grammar analyzer."""
    return GrammarAnalyzer()  # A fresh grammar analyzer.


@pytest.fixture
def config() -> LinterConfig:
    """Return a default configuration."""
    return LinterConfig()  # The built-in defaults.


@pytest.fixture
def build_doc() -> Callable[[str, str], Document]:
    """Return a helper that builds a document from text."""
    builder = DocumentBuilder()  # One builder for the whole test.

    def _build(text: str, path: str = "test.md") -> Document:
        """Build a document from the text and an optional path."""
        return builder.build(path, text)  # Parse the text into a document.

    return _build  # Return the helper.


@pytest.fixture
def make_context(
    backend: Backend, grammar: GrammarAnalyzer, config: LinterConfig
) -> Callable[[Dictionary | None], RuleContext]:
    """Return a helper that builds a rule context with an optional dictionary."""

    def _make(dictionary: Dictionary | None = None) -> RuleContext:
        """Build a rule context, with the dictionary when one is given."""
        return RuleContext(backend=backend, grammar=grammar, config=config, dictionary=dictionary)

    return _make  # Return the factory.


@pytest.fixture
def grade(backend: Backend, grammar: GrammarAnalyzer, config: LinterConfig) -> Callable[[str], Score]:
    """Return a helper that grades a file path and returns its score."""
    builder = DocumentBuilder()  # The document builder.
    scorer = ScoringModel()  # The scoring model.
    rules = load_rules(config)  # The active rules for the default configuration.

    def _grade(path: str) -> Score:
        """Grade one file and return its score."""
        text = pathlib.Path(path).read_text(encoding="utf-8")  # Read the file text.
        document = builder.build(path, text)  # Parse the file.
        context = RuleContext(backend=backend, grammar=grammar, config=config, dictionary=None)  # Context.
        violations = [item for rule in rules for item in rule.check(document, context)]  # Run the rules.
        return scorer.score(document, violations, rules, False, config)  # Score the file.

    return _grade  # Return the grade helper.


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the path to the shared fixture folder."""
    return _FIXTURES  # The fixtures live next to the unit tests.
