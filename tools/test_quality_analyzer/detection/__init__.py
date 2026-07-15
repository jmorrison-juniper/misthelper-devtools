"""Detection sub-package for the test quality analyzer.

Hosts per-rule detector classes plus shared enums, dataclasses, and the
Detector protocol/registry (T008, T009, T019). The package's public surface
re-exports the type layer so callers can `from ...detection import Finding`
without knowing the internal file layout.
"""

from __future__ import annotations  # Postponed annotations for protocol forward refs.

import ast  # Used only for the Detector protocol's tree parameter type.
from pathlib import Path  # Path used in the Detector protocol signature.
from typing import Protocol, runtime_checkable  # Structural typing for Detector.

# Re-exports of the type layer from types.py (T008 + T009).
from tools.test_quality_analyzer.detection.types import (
    SEVERITY_RANK,  # Numeric ranks used by the sort key.
    Baseline,  # Baseline payload dataclass.
    BaselineDiff,  # Baseline vs current diff dataclass.
    Category,  # Finding category enum.
    ConfigSnapshot,  # Immutable config view dataclass.
    Finding,  # Atomic detection record dataclass.
    MistApiPredicate,  # Two-part Mist-API predicate parameters.
    ParseError,  # AST parse-failure record dataclass.
    Report,  # Report envelope dataclass.
    Severity,  # Finding severity enum.
    SkippedFile,  # Excluded-file record dataclass.
    _sort_key,  # Canonical finding sort key.
)


@runtime_checkable
class Detector(Protocol):
    """Structural type every rule detector implements (T019)."""

    def detect(
        self,
        test_path: Path,  # Repo-relative POSIX path of the analyzed file.
        tree: ast.Module,  # Pre-parsed AST of that file.
        source: str,  # Original source text (for line-context helpers).
    ) -> list[Finding]:
        """Return zero or more Findings raised against `test_path`."""
        ...  # Protocol: bodies are supplied by concrete detector classes.


# Mutable registry populated at import time by each detector module (T019).
# Kept as a list so registration order is deterministic and inspectable.
DetectorRegistry: list[Detector] = []  # Detector modules append themselves on import.


__all__ = [  # Explicit export list keeps the package surface deliberate.
    "Baseline",
    "BaselineDiff",
    "Category",
    "ConfigSnapshot",
    "Detector",
    "DetectorRegistry",
    "Finding",
    "MistApiPredicate",
    "ParseError",
    "Report",
    "SEVERITY_RANK",
    "Severity",
    "SkippedFile",
    "_sort_key",
]
