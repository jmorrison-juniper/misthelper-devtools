"""Analysis package for the STE linter.

Exposes the backend factory, the token and backend types, and the grammar
analyzer. The factory returns the spaCy backend when spaCy and a model are
present, and the heuristic backend otherwise.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import logging  # Records which backend the factory selected.

from .backend import Backend, Token  # The public backend interface and token type.
from .grammar import GrammarAnalyzer  # The voice, tense, and cluster helper.
from .heuristic import HeuristicBackend  # The standard-library backend.

# The logger for the analysis stage. The CLI configures the handlers.
_LOG = logging.getLogger("ste_linter.analysis")

# The default spaCy model the factory tries to load.
_DEFAULT_MODEL = "en_core_web_sm"

__all__ = ["Backend", "Token", "GrammarAnalyzer", "HeuristicBackend", "get_backend"]  # The public names.


def get_backend(prefer_spacy: bool = True) -> Backend:
    """Return the best available backend.

    Tries the spaCy backend first when ``prefer_spacy`` is true. Falls back to the
    heuristic backend when spaCy or its model is not present.
    """
    if prefer_spacy:  # The caller allows the spaCy backend.
        spacy_backend = _try_spacy()  # Try to build the spaCy backend.
        if spacy_backend is not None:  # The spaCy backend is available.
            _LOG.debug("Using the spaCy backend")  # Record the choice.
            return spacy_backend  # Return the spaCy backend.
    _LOG.debug("Using the heuristic backend")  # Record the fallback choice.
    return HeuristicBackend()  # Return the standard-library backend.


def _try_spacy() -> Backend | None:
    """Return a spaCy backend, or None when spaCy or its model is missing."""
    try:  # The import fails when the optional package is not installed.
        import importlib  # Loads the optional package by name without a static import.

        spacy = importlib.import_module("spacy")  # The optional natural-language library.

        from .spacy_backend import SpacyBackend  # The wrapper for a spaCy pipeline.

        pipeline = spacy.load(_DEFAULT_MODEL)  # Load the small English model, may raise.
        return SpacyBackend(pipeline)  # Return the wrapped backend.
    except Exception as error:  # Any import or load problem means no spaCy backend.
        _LOG.debug("spaCy backend not available: %s", error)  # Record the reason.
        return None  # Signal that the heuristic backend should be used.
