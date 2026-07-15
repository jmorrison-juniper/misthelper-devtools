"""Unit tests for the data model (T010).

Covers:
- Frozen-ness of every dataclass (FrozenInstanceError on mutation).
- Sort-key ordering (critical sorts ahead of low).
- POSIX-path invariant hygiene.
"""

from __future__ import annotations  # Postponed annotations for consistency.

import dataclasses  # For FrozenInstanceError.
from pathlib import PurePosixPath  # POSIX-path invariant test.

import pytest  # pytest.raises for frozen-ness assertions.

from tools.test_quality_analyzer.detection import (  # Import from public surface.
    Category,
    Finding,
    Severity,
    _sort_key,
)


def _mk_finding(severity: Severity, line: int = 1) -> Finding:
    """Small factory to keep tests short (constitution: 5-block/25-line rule)."""
    # Build a minimally valid Finding varying only severity and line number.
    return Finding(
        category=Category.WEAK_ASSERTION,  # Same category so severity dominates ordering.
        rule_id="weak_bare_truthy",  # Any valid rule id will do.
        severity=severity,  # The variable under test.
        file_path="tests/x/test_x.py",  # POSIX path shape.
        line_number=line,  # Vary to test line-ordering tie-break.
        explanation="e",  # Non-empty explanation.
        remediation="r",  # Non-empty remediation.
    )


def test_finding_is_frozen() -> None:
    """Finding must reject attribute mutation."""
    f = _mk_finding(Severity.LOW)  # Construct a Finding.
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.line_number = 99  # type: ignore[misc]  # Mutation is banned.


def test_sort_key_puts_critical_before_low() -> None:
    """Sort key ordering: critical (rank 4) sorts before low (rank 1)."""
    hi = _mk_finding(Severity.CRITICAL)  # High-priority finding.
    lo = _mk_finding(Severity.LOW)  # Low-priority finding.
    assert _sort_key(hi) < _sort_key(lo)  # Negated rank -> critical is a smaller tuple.


def test_sort_key_line_number_tie_breaker() -> None:
    """When severity/category/path match, line_number ascends as tie-breaker."""
    early = _mk_finding(Severity.HIGH, line=5)  # Earlier line.
    later = _mk_finding(Severity.HIGH, line=99)  # Later line.
    assert _sort_key(early) < _sort_key(later)  # Ascending line number.


def test_file_path_is_posix() -> None:
    """POSIX-path invariant: Finding.file_path must contain no backslashes."""
    f = _mk_finding(Severity.MEDIUM)  # Any severity works.
    assert "\\" not in f.file_path  # Backslashes never appear in stored paths.
    # PurePosixPath must round-trip cleanly on POSIX-style strings.
    assert str(PurePosixPath(f.file_path)) == f.file_path
