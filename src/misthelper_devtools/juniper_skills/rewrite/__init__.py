"""Rewrite tools for copyright-safe Juniper documentation skills."""

from .backends import PromptTemplateBuilder, RewriteBackend, RuleBasedBackend  # Export the rewrite seam.
from .cards import CardExtractor  # Export the card extractor.
from .guard import VerbatimSimilarityGuard  # Export the copyright similarity guard.
from .models import (  # Export stable value models for the orchestrator.
    CardClassMark,
    KnowledgeCard,
    RewriteResult,
    RewriteWorkPacket,
    SimilarityCheckInput,
    SimilarityFileResult,
    SimilarityGuardReport,
    SteFileReport,
    SteValidationReport,
)
from .ste import SteValidator  # Export the STE validator.

__all__ = [  # Keep public imports stable for other factory agents.
    "CardClassMark",
    "CardExtractor",
    "KnowledgeCard",
    "PromptTemplateBuilder",
    "RewriteBackend",
    "RewriteResult",
    "RewriteWorkPacket",
    "RuleBasedBackend",
    "SimilarityCheckInput",
    "SimilarityFileResult",
    "SimilarityGuardReport",
    "SteFileReport",
    "SteValidationReport",
    "SteValidator",
    "VerbatimSimilarityGuard",
]
