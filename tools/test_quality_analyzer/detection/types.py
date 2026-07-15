"""Shared enums and dataclasses for the test quality analyzer.

Per data-model.md. All dataclasses are `frozen=True, slots=True` to guarantee
value semantics and cheap hashing. Enums are `str` subclasses so their
`.value` serializes directly to JSON.

Note on Category enumeration (R3):
    `PARSE_ERROR` and `STALE_BASELINE` appear in the JSON Schema's `category`
    enum for spec completeness, but they are NOT emitted as `Finding.category`
    values. Parse-error records live in `Report.parse_errors`, stale-baseline
    advisories in `Report.stale_baseline_entries`. The category taxonomy is
    reused so JSON consumers see one enum instead of two.
"""

from __future__ import annotations  # Postponed annotations for forward refs.

from collections.abc import Mapping  # Read-only mapping annotation for ConfigSnapshot.
from dataclasses import dataclass  # Frozen dataclasses used pervasively.
from enum import Enum  # Enum base for Severity and Category.


class Severity(str, Enum):
    """Finding severity taxonomy (FR-009). Order matches sort-key ranking."""

    CRITICAL = "critical"  # Ranked 4 (highest priority) in _sort_key.
    HIGH = "high"  # Ranked 3.
    MEDIUM = "medium"  # Ranked 2.
    LOW = "low"  # Ranked 1 (lowest priority).


# Mapping used by _sort_key to build descending-severity order.
SEVERITY_RANK: Mapping[str, int] = {  # Frozen intent -- consumers must not mutate.
    "critical": 4,  # Highest severity sorts first.
    "high": 3,  # Second highest.
    "medium": 2,  # Third.
    "low": 1,  # Lowest severity sorts last.
}


class Category(str, Enum):
    """Finding category taxonomy (FR-009 + FR-018/019 advisories)."""

    UNTESTED = "untested"  # Detector 1 category.
    WEAK_ASSERTION = "weak_assertion"  # Detector 2 category.
    MISSING_FAILURE_MODE = "missing_failure_mode"  # Detector 4 category.
    MISSING_EDGE_CASE = "missing_edge_case"  # Detector 5 category (heuristic).
    TAUTOLOGICAL = "tautological"  # Detector 3 category.
    PARSE_ERROR = "parse_error"  # See R3 note above -- not a Finding.category value.
    STALE_BASELINE = "stale_baseline"  # See R3 note above -- not a Finding.category value.


@dataclass(frozen=True, slots=True)
class Finding:
    """Atomic detection record. Serializes 1:1 to the schema `finding` object."""

    category: Category  # Enum value; serialized as its .value string.
    rule_id: str  # Stable identifier for the offending rule.
    severity: Severity  # Post-override severity value.
    file_path: str  # Repo-relative POSIX path (forward slashes).
    line_number: int  # 1-based line number; invariant >= 1.
    explanation: str  # One-to-two sentence human explanation (FR-008).
    remediation: str  # One sentence suggested fix (FR-008).
    heuristic: bool = False  # True for FR-006 edge-case findings.
    related_source: str | None = None  # Optional repo-relative path to SUT.


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """Record of a test file the engine deliberately did NOT analyze (FR-002)."""

    file_path: str  # Repo-relative POSIX path of the skipped file.
    reason: str  # E.g. "mist_api_excluded" or "user_excluded".
    matched_rule: str  # R4: predicate rule id -- e.g. "mist_api_predicate".


@dataclass(frozen=True, slots=True)
class ParseError:
    """Non-fatal AST parse failure record. Aggregated in Report.parse_errors (FR-018)."""

    file_path: str  # Repo-relative POSIX path of the unparseable file.
    line_number: int | None  # None if SyntaxError does not localize a line.
    message: str  # ASCII-normalized SyntaxError.msg text.


@dataclass(frozen=True, slots=True)
class MistApiPredicate:
    """Parameters of the two-part Mist-API exclusion predicate (FR-002)."""

    banned_imports: tuple[str, ...]  # Default ("mistapi",).
    excluded_src_prefixes: tuple[str, ...]  # Default ("src/api/",).


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Immutable view of the effective config for auditability (report envelope)."""

    rules_enabled: Mapping[str, bool]  # rule_id -> enabled boolean.
    severity_overrides: Mapping[str, Severity]  # rule_id -> Severity enum.
    exclusion_globs: tuple[str, ...]  # POSIX globs from [exclusions].path_globs.
    mist_api_predicate: MistApiPredicate  # Mist-API predicate parameters.


@dataclass(frozen=True, slots=True)
class Report:
    """Run envelope emitted to output/report.json (FR-011)."""

    engine_version: str  # __version__ at run time.
    generated_at: str  # ISO-8601 UTC seconds precision.
    scanned_roots: tuple[str, ...]  # CLI-supplied test roots.
    config_snapshot: ConfigSnapshot  # Effective config after merge.
    findings: tuple[Finding, ...]  # Deterministically sorted findings.
    skipped_files: tuple[SkippedFile, ...]  # Files excluded per FR-002.
    parse_errors: tuple[ParseError, ...]  # Non-fatal parse failures (FR-018).
    stale_baseline_entries: tuple[str, ...]  # File paths per FR-019 advisory.


@dataclass(frozen=True, slots=True)
class Baseline:
    """Committed baseline payload -- tuple of Findings, no envelope (FR-012)."""

    findings: tuple[Finding, ...]  # Same shape as Report.findings; canonical order.


@dataclass(frozen=True, slots=True)
class BaselineDiff:
    """Set-difference result between the current run and the committed baseline."""

    new_findings: tuple[Finding, ...]  # Present in run but not baseline; gate trigger.
    removed_findings: tuple[Finding, ...]  # Present in baseline but not run; advisory only.
    unchanged_count: int  # Number of findings appearing in both sides.


def _sort_key(finding: Finding) -> tuple[int, str, str, int, str]:
    """Return the canonical five-tuple used to sort Report.findings deterministically."""
    # Descending severity: negate the rank so a lower tuple value sorts higher.
    return (
        -SEVERITY_RANK[finding.severity.value],  # Descending severity primary key.
        finding.category.value,  # Ascending category secondary key.
        finding.file_path,  # Ascending POSIX path.
        finding.line_number,  # Ascending line number.
        finding.rule_id,  # Ascending rule id.
    )
