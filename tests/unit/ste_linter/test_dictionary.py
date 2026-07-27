"""Tests for the dictionary loader."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Writes a temporary dictionary file.
import pathlib  # Builds the temporary path.

from tools.ste_linter.dictionary.loader import Dictionary  # The loader under test.


def test_load_missing_returns_none() -> None:
    """A missing dictionary file loads as None."""
    assert Dictionary.load("no-such-file.json") is None  # The loader returns None.


def test_load_valid_dictionary(tmp_path: pathlib.Path) -> None:
    """A valid dictionary file loads and looks up a word."""
    records = {"entries": [{"keyword": "accuracy", "approved": False, "alternatives": ["precision"]}]}
    path = tmp_path / "dict.json"  # The temporary file path.
    path.write_text(json.dumps(records), encoding="utf-8")  # Write the dictionary file.
    dictionary = Dictionary.load(str(path))  # Load the dictionary.
    assert dictionary is not None  # The dictionary loaded.
    assert dictionary.lookup("accuracy")  # The word is present.


def test_load_malformed_returns_none(tmp_path: pathlib.Path) -> None:
    """A malformed dictionary file loads as None."""
    path = tmp_path / "bad.json"  # The temporary file path.
    path.write_text("{not valid json", encoding="utf-8")  # Write broken content.
    assert Dictionary.load(str(path)) is None  # The loader returns None.
