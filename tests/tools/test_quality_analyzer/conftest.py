"""Pytest fixtures for tools.test_quality_analyzer tests.

Provides:
    repo_root: absolute Path of the repository top-level directory.
    run_engine: placeholder callable; wired to TestQualityCLI once T041 lands.
"""

from __future__ import annotations  # Defer annotation evaluation for cleaner typing.

from pathlib import Path  # Path used for repo-root resolution.

import pytest  # pytest fixtures.


@pytest.fixture
def repo_root() -> Path:
    """Return the absolute Path of the repository top-level."""
    # conftest.py lives at tests/tools/test_quality_analyzer/conftest.py -> parents[3] is repo root.
    return Path(__file__).resolve().parents[3]  # Absolute repo root.


@pytest.fixture
def run_engine():
    """Return a placeholder engine runner; replaced with real CLI in T041 wiring."""

    # T006 acceptance only requires collection to succeed -- real callable wires later.
    def _not_yet_implemented(*_args, **_kwargs):
        # Explicit signal so any test using this before T041 is easy to spot.
        raise NotImplementedError("run_engine wired in T041; not yet available")

    return _not_yet_implemented
