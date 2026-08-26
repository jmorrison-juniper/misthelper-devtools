"""BaselineDiffer (T046): load, diff, and write baseline finding sets.

Baseline serialization is a canonical JSON *array* of finding objects
(no envelope), per FR-012 + data-model.md §Relationships. Diffing is a
set-difference on the canonical five-tuple key
``(category, rule_id, file_path, line_number, explanation)`` -- severity
and remediation are deliberately excluded so retuning them does not
invalidate the baseline.

Stale-baseline advisory (FR-019) is computed by cross-referencing baseline
`file_path` values against the working-tree files that were actually
scanned; entries that name absent files are returned separately.
``prune`` drops those entries, so a stale entry cannot stay in the file
forever (issue #1769).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Stdlib JSON emitter / parser -- no third-party JSON library.
import logging  # Principle VII structured logging.
from collections.abc import Iterable  # Iterable annotation for canonical inputs.
from pathlib import Path  # Filesystem primitives for load/write.

from tools.test_quality_analyzer.detection import (  # Shared type layer.
    Baseline,
    BaselineDiff,
    Category,
    Finding,
    Severity,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


def _canonical_key(finding: Finding) -> tuple[str, str, str, int, str]:
    """Return the five-tuple identity used for baseline set-difference."""
    # Category and severity round-trip via .value so the key is JSON-friendly.
    return (
        finding.category.value,  # Category name string.
        finding.rule_id,  # Rule id string.
        finding.file_path,  # POSIX file path.
        finding.line_number,  # 1-based line.
        finding.explanation,  # Explanation text (stable across severity retunes).
    )


class BaselineDiffer:
    """Load, diff, and write baseline snapshots of Finding tuples."""

    def load(self, path: Path) -> Baseline:
        """Return a Baseline parsed from `path`; empty baseline if file missing."""
        # Missing baseline -> empty baseline (caller decides whether that's an error).
        if not path.exists():
            _LOGGER.info("Baseline file %s does not exist; treating as empty", path)
            return Baseline(findings=())
        # Read once as UTF-8 text; JSON parser handles any byte-order marks below.
        text = path.read_text(encoding="utf-8")
        # Parse the JSON payload; malformed JSON surfaces as ValueError.
        payload = json.loads(text)
        # Baseline files must be a JSON array of finding objects.
        if not isinstance(payload, list):
            raise ValueError(
                "Baseline at %s must be a JSON array of findings; got %s" % (path, type(payload).__name__),
            )
        # Deserialize each object into a Finding dataclass instance.
        findings = tuple(self._finding_from_dict(obj) for obj in payload)
        _LOGGER.debug("Loaded %s findings from baseline %s", len(findings), path)
        return Baseline(findings=findings)

    def diff(
        self,
        current: Iterable[Finding],  # Current run's findings, any order.
        baseline: Baseline,  # Committed baseline snapshot.
    ) -> BaselineDiff:
        """Return a BaselineDiff of `current` vs `baseline` on the canonical key."""
        # info-before per Principle VII.
        _LOGGER.info("Diffing current findings against baseline")
        # Materialize current as tuple so it can be iterated twice.
        current_tuple = tuple(current)  # Freeze the iterable.
        # Build canonical-key sets for O(1) membership tests.
        current_keys = {_canonical_key(f): f for f in current_tuple}
        baseline_keys = {_canonical_key(f): f for f in baseline.findings}
        # Set differences produce the new / removed / unchanged partitions.
        new_key_set = current_keys.keys() - baseline_keys.keys()
        removed_key_set = baseline_keys.keys() - current_keys.keys()
        unchanged_keys = current_keys.keys() & baseline_keys.keys()
        # Map keys back to Finding objects so callers can print details.
        new_findings = tuple(
            sorted(
                (current_keys[k] for k in new_key_set),
                key=_canonical_key,
            )
        )
        removed_findings = tuple(
            sorted(
                (baseline_keys[k] for k in removed_key_set),
                key=_canonical_key,
            )
        )
        # debug-after with counts for quick log skimming.
        _LOGGER.debug(
            "Diff result: new=%s removed=%s unchanged=%s",
            len(new_findings),
            len(removed_findings),
            len(unchanged_keys),
        )
        return BaselineDiff(
            new_findings=new_findings,
            removed_findings=removed_findings,
            unchanged_count=len(unchanged_keys),
        )

    def write(self, path: Path, findings: Iterable[Finding]) -> None:
        """Serialize `findings` to `path` as a canonical JSON array (no envelope)."""
        _LOGGER.info("Writing baseline to %s", path)
        # Materialize once so we can log the count and iterate a single pass.
        findings_tuple = tuple(findings)
        # Convert each Finding into the schema-conformant dict shape.
        payload = [self._finding_to_dict(f) for f in findings_tuple]
        # Canonical dumps configuration matches ReportBuilder.to_json for parity.
        text = json.dumps(
            payload,
            ensure_ascii=True,  # ASCII-safe output (Principle IV).
            indent=2,  # Two-space indent per reporting contract.
            sort_keys=True,  # Deterministic key order inside each finding.
            separators=(",", ": "),  # Match reference formatting exactly.
        )
        # Ensure the parent directory exists so write_text does not raise.
        path.parent.mkdir(parents=True, exist_ok=True)
        # Trailing newline so POSIX tools see the file as line-terminated.
        # newline="\n" holds the LF ending on Windows, so the tracked file stays
        # byte-identical on both platforms and the diff shows the findings only.
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
        _LOGGER.debug("Wrote %s findings to baseline %s", len(findings_tuple), path)

    def stale_entries(
        self,
        baseline: Baseline,  # Baseline whose file_paths to audit.
        scanned_files: Iterable[str],  # POSIX paths of files actually scanned this run.
    ) -> tuple[str, ...]:
        """Return baseline file_paths absent from `scanned_files` (FR-019)."""
        # Snapshot the scanned set for O(1) membership tests.
        scanned_set = set(scanned_files)
        # Collect distinct baseline paths not present in the scanned set.
        stale = {f.file_path for f in baseline.findings if f.file_path not in scanned_set}
        # Sort for deterministic output ordering.
        return tuple(sorted(stale))

    def prune(
        self,
        baseline: Baseline,  # Baseline to filter.
        stale_paths: Iterable[str],  # Paths the scan can no longer reach.
    ) -> tuple[Finding, ...]:
        """Return the baseline findings that name no stale path (issue #1769)."""
        # Snapshot the stale paths for O(1) membership tests.
        stale_set = set(stale_paths)
        # info-before names the work, per Principle VII.
        _LOGGER.info("Pruning %s stale path(s) from the baseline", len(stale_set))
        # Keep each finding whose file the scan still reaches.
        retained = tuple(f for f in baseline.findings if f.file_path not in stale_set)
        # debug-after states how many findings survived the prune.
        _LOGGER.debug(
            "Prune kept %s of %s baseline finding(s)",
            len(retained),
            len(baseline.findings),
        )
        return retained

    # --- Internal helpers ---------------------------------------------------

    def _finding_to_dict(self, finding: Finding) -> dict:
        """Convert a Finding to the schema-conformant dict shape."""
        # Mirror ReportBuilder._finding_dict so a baseline element is drop-in.
        return {
            "category": finding.category.value,
            "rule_id": finding.rule_id,
            "severity": finding.severity.value,
            "file_path": finding.file_path,
            "line_number": finding.line_number,
            "explanation": finding.explanation,
            "remediation": finding.remediation,
            "heuristic": finding.heuristic,
            "related_source": finding.related_source,
        }

    def _finding_from_dict(self, obj: dict) -> Finding:
        """Rebuild a Finding from a schema-conformant dict."""
        # Fail loudly if the object is not the expected shape.
        if not isinstance(obj, dict):
            raise ValueError(
                "Baseline finding entry must be a JSON object; got %s" % type(obj).__name__,
            )
        # Enum values round-trip via their .value strings.
        return Finding(
            category=Category(obj["category"]),
            rule_id=obj["rule_id"],
            severity=Severity(obj["severity"]),
            file_path=obj["file_path"],
            line_number=int(obj["line_number"]),
            explanation=obj["explanation"],
            remediation=obj["remediation"],
            heuristic=bool(obj.get("heuristic", False)),
            related_source=obj.get("related_source"),
        )
