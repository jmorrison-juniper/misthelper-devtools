"""Depth extraction engine for Juniper skill source documents."""

from .coverage import CoverageAnalyzer  # Export the manifest analyzer for writer coverage checks.
from .engine import FactExtractionEngine  # Export the main engine for factory callers.
from .extractors import (  # Export each extractor so tests can verify every fact class.
    CommandFactExtractor,
    ConfigurationFactExtractor,
    ConstraintFactExtractor,
    DefinitionFactExtractor,
    NumericFactExtractor,
    OutputFieldFactExtractor,
    PlatformReleaseFactExtractor,
    PrerequisiteFactExtractor,
    TableRowFactExtractor,
)
from .ground_truth import GroundTruthEvaluator  # Export independent recall and precision measurement.
from .guard import CachedSourceSimilarityGuard  # Export the cached guard runner for large measurements.
from .models import (  # Export result models for reports.
    CoverageEntry,
    CoverageManifest,
    CoverageVerificationReport,
    DepthExtractionResult,
    ExtractedFact,
    GroundTruthMeasurement,
    GroundTruthRegion,
    GroundTruthReport,
    TopicSplit,
)

__all__ = [  # Keep the public surface explicit for skill factory imports.
    "CachedSourceSimilarityGuard",
    "CoverageAnalyzer",
    "CommandFactExtractor",
    "ConfigurationFactExtractor",
    "ConstraintFactExtractor",
    "CoverageEntry",
    "CoverageManifest",
    "CoverageVerificationReport",
    "DefinitionFactExtractor",
    "DepthExtractionResult",
    "ExtractedFact",
    "FactExtractionEngine",
    "GroundTruthEvaluator",
    "GroundTruthMeasurement",
    "GroundTruthRegion",
    "GroundTruthReport",
    "NumericFactExtractor",
    "OutputFieldFactExtractor",
    "PlatformReleaseFactExtractor",
    "PrerequisiteFactExtractor",
    "TableRowFactExtractor",
    "TopicSplit",
]
