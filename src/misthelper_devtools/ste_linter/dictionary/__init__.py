"""Dictionary package for the STE linter.

Exposes the dictionary loader and the entry type. The extraction tool lives in
``extract`` and is run on its own to build the git-ignored dictionary file.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from .loader import Dictionary, DictionaryEntry  # The dictionary and entry types.

__all__ = ["Dictionary", "DictionaryEntry"]  # The public names.
