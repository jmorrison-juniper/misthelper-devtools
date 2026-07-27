"""Dictionary loading.

Loads the STE dictionary from a local JSON file and gives a word lookup. The file
is generated from the licensed PDF by the extraction tool and is ignored by git.
The loader returns None when the file is missing or malformed, so the linter runs
the structural rules only.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Reads the dictionary file.
import logging  # Records the load result.
import os  # Tests whether the file exists.
from dataclasses import dataclass, field  # Declares the entry value type.

# The logger for the dictionary stage. The CLI configures the handlers.
_LOG = logging.getLogger("ste_linter.dictionary")


@dataclass(frozen=True)
class DictionaryEntry:
    """One dictionary record for a word."""

    keyword: str  # The word, in lower case.
    part_of_speech: str = ""  # The approved part of speech, or empty.
    approved: bool = True  # True when the word is approved in STE.
    alternatives: list[str] = field(default_factory=list)  # Approved words to use instead.
    approved_meaning: str = ""  # The approved meaning, or empty.


class Dictionary:
    """Holds the dictionary entries and gives a word lookup."""

    def __init__(self, entries: dict[str, list[DictionaryEntry]]) -> None:
        """Store the entry map keyed by the lower-case word."""
        self._entries = entries  # The map from a lower-case word to its entries.

    @property
    def size(self) -> int:
        """Return the number of words in the dictionary."""
        return len(self._entries)  # Each key is one word.

    def lookup(self, word: str) -> list[DictionaryEntry]:
        """Return the entries for a word, or an empty list when it is absent."""
        return self._entries.get(word.lower(), [])  # Look up the lower-case word.

    @classmethod
    def load(cls, path: str) -> Dictionary | None:
        """Return a dictionary from ``path``, or None when it cannot be loaded."""
        if not os.path.isfile(path):  # The dictionary file is not present.
            _LOG.debug("No dictionary file at %s", path)  # Record the absence.
            return None  # Signal that the dictionary checks should be skipped.
        _LOG.info("Loading dictionary from %s", path)  # Log before the read.
        try:  # The file may be missing or malformed.
            with open(path, encoding="utf-8") as handle:  # Open the dictionary file.
                data = json.load(handle)  # Parse the JSON content.
            entries = cls._build_entries(data)  # Turn the records into entry objects.
        except (OSError, ValueError, KeyError) as error:  # A read or parse problem.
            _LOG.warning("Could not load dictionary: %s", error)  # Record the problem.
            return None  # Signal that the dictionary checks should be skipped.
        _LOG.debug("Loaded %d dictionary words", len(entries))  # Log the result.
        return cls(entries)  # Return the built dictionary.

    @staticmethod
    def _build_entries(data: dict) -> dict[str, list[DictionaryEntry]]:
        """Turn the raw JSON records into an entry map keyed by the word."""
        entries: dict[str, list[DictionaryEntry]] = {}  # Holds the built entries.
        for record in data.get("entries", []):  # Walk each record in the file.
            entry = DictionaryEntry(
                keyword=str(record["keyword"]).lower(),  # The word in lower case.
                part_of_speech=str(record.get("part_of_speech", "")),  # The part of speech.
                approved=bool(record.get("approved", True)),  # The approved flag.
                alternatives=[str(item) for item in record.get("alternatives", [])],  # The alternatives.
                approved_meaning=str(record.get("approved_meaning", "")),  # The approved meaning.
            )  # Build the entry.
            entries.setdefault(entry.keyword, []).append(entry)  # Group entries by word.
        return entries  # Return the entry map.
