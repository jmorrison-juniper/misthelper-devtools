"""Classify Juniper source documents into one domain skill."""

from __future__ import annotations  # Keep annotations cheap during package import.

from .classifier import DomainClassifier  # Expose the classifier as the public write interface.
from .lookup import DomainLookup  # Expose the lookup as the public read interface.
from .models import DomainAssignment, DomainDocument, DomainReport  # Share stable result contracts with consumers.

__all__ = [  # Keep the public surface small for the other factory components.
    "DomainAssignment",  # Return one persisted classification decision.
    "DomainClassifier",  # Apply the taxonomy and persist decisions.
    "DomainDocument",  # Carry all signals for one source document.
    "DomainLookup",  # Read persisted domains without reclassification.
    "DomainReport",  # Summarize a database classification run.
]
