"""Deterministic report building + rendering (T016, T017, T020).

Two public classes per plan.md Five-Item Rule for this module:

    ReportBuilder    -- assembles a canonical Report + JSON payload, and hosts
                        the private inline JSON-Schema validator (T020).
    MarkdownRenderer -- produces the ASCII-only summary.md text.

All JSON output uses:
    json.dumps(payload, ensure_ascii=True, indent=2,
               sort_keys=True, separators=(",", ": "))
with a trailing newline, per plan.md §Constraints and FR-011.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # Stdlib JSON emitter -- no third-party JSON library.
import logging  # info-before / debug-after logging pattern.
import re  # Used by the tiny inline schema validator for `pattern` keyword.
from collections.abc import Iterable  # Any for schema/payload; Iterable for inputs.
from typing import Any

from tools.test_quality_analyzer.detection import (  # Types from detection package.
    ConfigSnapshot,
    Finding,
    ParseError,
    Report,
    SkippedFile,
    _sort_key,
)

_LOGGER = logging.getLogger(__name__)  # Module-scoped logger.


class ReportBuilder:
    """Assemble a canonical `Report` and serialize it to deterministic JSON."""

    def build(
        self,
        findings: Iterable[Finding],  # Detector output, any order.
        skipped: Iterable[SkippedFile],  # Excluded files (FR-002).
        parse_errors: Iterable[ParseError],  # Non-fatal AST parse failures (FR-018).
        stale_baseline_entries: Iterable[str],  # File paths per FR-019.
        config_snapshot: ConfigSnapshot,  # Effective config after CLI merge.
        engine_version: str,  # Value of __version__ at run time.
        generated_at: str,  # ISO-8601 UTC timestamp with seconds precision.
        scanned_roots: Iterable[str],  # CLI-supplied test roots (POSIX strings).
    ) -> Report:
        """Return an immutable Report with deterministically sorted collections."""
        # Log before build so operators can trace which run produced which report.
        _LOGGER.info("Building report at %s", generated_at)
        # Sort findings by the canonical _sort_key so JSON output is stable.
        sorted_findings = tuple(sorted(findings, key=_sort_key))
        # Sort skipped/parse-error records by file_path for deterministic order.
        sorted_skipped = tuple(sorted(skipped, key=lambda s: s.file_path))
        sorted_parse = tuple(sorted(parse_errors, key=lambda p: p.file_path))
        # Sort stale-baseline entries alphabetically for the same reason.
        sorted_stale = tuple(sorted(stale_baseline_entries))
        # Freeze scanned_roots into a tuple with insertion order preserved.
        roots_tuple = tuple(scanned_roots)
        # Build and return the frozen dataclass.
        report = Report(
            engine_version=engine_version,
            generated_at=generated_at,
            scanned_roots=roots_tuple,
            config_snapshot=config_snapshot,
            findings=sorted_findings,
            skipped_files=sorted_skipped,
            parse_errors=sorted_parse,
            stale_baseline_entries=sorted_stale,
        )
        # Debug-after with finding count for quick log skimming.
        _LOGGER.debug("Report built with %s findings", len(sorted_findings))
        return report

    def to_json(self, report: Report) -> str:
        """Serialize `report` to canonical JSON text with trailing newline."""
        # Convert the frozen dataclass tree to a plain dict/list structure.
        payload = self._to_payload(report)
        # Emit with the deterministic json.dumps configuration mandated by plan.md.
        text = json.dumps(
            payload,
            ensure_ascii=True,  # ASCII-only output (Constitution Principle V).
            indent=2,  # Two-space indentation.
            sort_keys=True,  # Deterministic key ordering.
            separators=(",", ": "),  # Match reference formatting exactly.
        )
        # Trailing newline so POSIX tools see the file as line-terminated.
        return text + "\n"

    def _to_payload(self, report: Report) -> dict[str, Any]:
        """Convert the frozen Report into a JSON-safe dict of primitives."""
        # Config snapshot has to be flattened before it will round-trip through JSON.
        cs = report.config_snapshot
        # rules_enabled and severity_overrides are MappingProxy views; dict() unfreezes.
        payload: dict[str, Any] = {
            "engine_version": report.engine_version,
            "generated_at": report.generated_at,
            "scanned_roots": list(report.scanned_roots),
            "config_snapshot": {
                "rules_enabled": dict(cs.rules_enabled),
                "severity_overrides": {key: value.value for key, value in cs.severity_overrides.items()},
                "exclusion_globs": list(cs.exclusion_globs),
                "mist_api_predicate": {
                    "banned_imports": list(cs.mist_api_predicate.banned_imports),
                    "excluded_src_prefixes": list(
                        cs.mist_api_predicate.excluded_src_prefixes,
                    ),
                },
            },
            "findings": [self._finding_dict(f) for f in report.findings],
            "skipped_files": [
                {
                    "file_path": s.file_path,
                    "reason": s.reason,
                    "matched_rule": s.matched_rule,
                }
                for s in report.skipped_files
            ],
            "parse_errors": [
                {
                    "file_path": p.file_path,
                    "line_number": p.line_number,
                    "message": p.message,
                }
                for p in report.parse_errors
            ],
            "stale_baseline_entries": list(report.stale_baseline_entries),
        }
        return payload

    def _finding_dict(self, finding: Finding) -> dict[str, Any]:
        """Convert a single Finding into the schema-conformant dict shape."""
        # Emit every field including optional related_source (null when unset).
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

    # ---- T020: inline JSON Schema validator (private, tuned to report.schema.json) ----

    def validate(self, payload: dict[str, Any], schema: dict[str, Any]) -> None:
        """Validate `payload` against `schema`; raise ValueError on any failure.

        Supports the subset of JSON Schema draft-2020-12 used by our own schema:
        type, required, properties, additionalProperties, items, enum, pattern,
        minimum, minLength, minItems, anyOf, $defs / $ref.
        """
        # Delegate to the recursive walker; empty path prefix identifies the root node.
        self._validate_node(payload, schema, schema, "")

    def _validate_node(
        self,
        node: Any,  # Current value under inspection.
        subschema: dict[str, Any],  # Schema fragment that applies here.
        root_schema: dict[str, Any],  # Full schema for $ref resolution.
        path: str,  # Dotted path for diagnostic messages.
    ) -> None:
        """Recursive validator dispatch. Handles $ref, anyOf, and type-branch."""
        # $ref shortcut: dereference and recurse under the same node.
        if "$ref" in subschema:
            resolved = self._resolve_ref(subschema["$ref"], root_schema)
            self._validate_node(node, resolved, root_schema, path)
            return
        # anyOf: at least one branch must succeed; collect errors for diagnostics.
        if "anyOf" in subschema:
            errors: list[str] = []
            for branch in subschema["anyOf"]:
                try:
                    self._validate_node(node, branch, root_schema, path)
                    return  # First success wins.
                except ValueError as exc:
                    errors.append(str(exc))
            raise ValueError(
                "%s failed all anyOf branches: %s" % (path or "<root>", "; ".join(errors)),
            )
        # Otherwise dispatch on the declared JSON type.
        json_type = subschema.get("type")
        if json_type == "object":
            self._check_object(node, subschema, root_schema, path)
        elif json_type == "array":
            self._check_array(node, subschema, root_schema, path)
        elif json_type == "string":
            self._check_string(node, subschema, path)
        elif json_type == "integer":
            self._check_integer(node, subschema, path)
        elif json_type == "boolean":
            if not isinstance(node, bool):
                raise ValueError("%s must be boolean" % (path or "<root>"))
        elif json_type == "null":
            if node is not None:
                raise ValueError("%s must be null" % (path or "<root>"))
        # Missing type is legal (e.g. inside $defs indirection); no-op.

    def _check_object(
        self,
        node: Any,
        subschema: dict[str, Any],
        root_schema: dict[str, Any],
        path: str,
    ) -> None:
        """Validate a JSON object node."""
        # Type gate: must be a dict.
        if not isinstance(node, dict):
            raise ValueError("%s must be object" % (path or "<root>"))
        # Required properties must exist.
        for req in subschema.get("required", []):
            if req not in node:
                raise ValueError(
                    "%s missing required property '%s'" % (path or "<root>", req),
                )
        # additionalProperties: false forbids unknown keys.
        props = subschema.get("properties", {})
        if subschema.get("additionalProperties") is False:
            for key in node:
                if key not in props:
                    raise ValueError(
                        "%s has unexpected property '%s'" % (path or "<root>", key),
                    )
        # Recurse into declared properties.
        for key, sub in props.items():
            if key in node:
                child_path = "%s.%s" % (path, key) if path else key
                self._validate_node(node[key], sub, root_schema, child_path)
        # additionalProperties as a schema: validate every non-declared key.
        addl = subschema.get("additionalProperties")
        if isinstance(addl, dict):
            for key, value in node.items():
                if key in props:
                    continue
                child_path = "%s.%s" % (path, key) if path else key
                self._validate_node(value, addl, root_schema, child_path)

    def _check_array(
        self,
        node: Any,
        subschema: dict[str, Any],
        root_schema: dict[str, Any],
        path: str,
    ) -> None:
        """Validate a JSON array node."""
        # Type gate: must be a list.
        if not isinstance(node, list):
            raise ValueError("%s must be array" % (path or "<root>"))
        # Minimum-length gate.
        if "minItems" in subschema and len(node) < subschema["minItems"]:
            raise ValueError(
                "%s must have at least %s items" % (path or "<root>", subschema["minItems"]),
            )
        # Recurse into each item using the shared items schema.
        items_schema = subschema.get("items")
        if items_schema is not None:
            for idx, item in enumerate(node):
                child_path = "%s[%d]" % (path or "<root>", idx)
                self._validate_node(item, items_schema, root_schema, child_path)

    def _check_string(self, node: Any, subschema: dict[str, Any], path: str) -> None:
        """Validate a JSON string node."""
        # Type gate.
        if not isinstance(node, str):
            raise ValueError("%s must be string" % (path or "<root>"))
        # Minimum-length gate.
        if "minLength" in subschema and len(node) < subschema["minLength"]:
            raise ValueError(
                "%s must have minLength %s" % (path or "<root>", subschema["minLength"]),
            )
        # Enum membership.
        if "enum" in subschema and node not in subschema["enum"]:
            raise ValueError(
                "%s value '%s' not in enum %s" % (path or "<root>", node, subschema["enum"]),
            )
        # Regex pattern match.
        if "pattern" in subschema and not re.search(subschema["pattern"], node):
            raise ValueError(
                "%s value '%s' does not match pattern %r" % (path or "<root>", node, subschema["pattern"]),
            )

    def _check_integer(self, node: Any, subschema: dict[str, Any], path: str) -> None:
        """Validate a JSON integer node."""
        # Type gate: reject booleans (bool is a subclass of int in Python).
        if isinstance(node, bool) or not isinstance(node, int):
            raise ValueError("%s must be integer" % (path or "<root>"))
        # Lower-bound gate.
        if "minimum" in subschema and node < subschema["minimum"]:
            raise ValueError(
                "%s must be >= %s" % (path or "<root>", subschema["minimum"]),
            )

    def _resolve_ref(self, ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
        """Resolve a local `#/$defs/name` reference to a subschema fragment."""
        # Only local refs are supported; anything else is an error in our subset.
        if not ref.startswith("#/"):
            raise ValueError("Unsupported non-local $ref: %s" % ref)
        # Walk the JSON pointer segments from the schema root.
        node: Any = root_schema
        for segment in ref.lstrip("#/").split("/"):
            if segment not in node:
                raise ValueError("Unresolvable $ref segment: %s" % segment)
            node = node[segment]
        return node


class MarkdownRenderer:
    """Render a human summary of a Report to ASCII-only Markdown."""

    def render(self, report: Report) -> str:
        """Return a deterministic ASCII Markdown summary for `report`."""
        # Header block: engine version + generated timestamp + roots.
        lines: list[str] = [
            "# Test Quality Analyzer Report",
            "",
            "- Engine version: %s" % report.engine_version,
            "- Generated at: %s" % report.generated_at,
            "- Scanned roots: %s" % ", ".join(report.scanned_roots),
            "",
            "## Summary",
            "",
            "- Findings: %s" % len(report.findings),
            "- Skipped files: %s" % len(report.skipped_files),
            "- Parse errors: %s" % len(report.parse_errors),
            "- Stale baseline entries: %s" % len(report.stale_baseline_entries),
            "",
        ]
        # Group findings by severity (descending) then by category, then file:line ascending.
        lines.append("## Findings")
        lines.append("")
        # Order severities descending using the same rank the sort key uses.
        for severity in ("critical", "high", "medium", "low"):
            bucket = [f for f in report.findings if f.severity.value == severity]
            if not bucket:
                continue  # Skip empty severity sections for readability.
            lines.append("### Severity: %s" % severity)
            lines.append("")
            # Group by category deterministically (alphabetical to remove noise).
            for category in sorted({f.category.value for f in bucket}):
                lines.append("#### Category: %s" % category)
                lines.append("")
                subset = [f for f in bucket if f.category.value == category]
                # Sort within a category by file_path then line_number ascending.
                subset.sort(key=lambda f: (f.file_path, f.line_number))
                for finding in subset:
                    lines.append(
                        "- %s:%d [%s] %s"
                        % (
                            finding.file_path,
                            finding.line_number,
                            finding.rule_id,
                            finding.explanation,
                        ),
                    )
                lines.append("")  # Blank line separates categories.
        # Skipped files block (sorted by file_path already in ReportBuilder).
        if report.skipped_files:
            lines.append("## Skipped Files")
            lines.append("")
            for skipped in report.skipped_files:
                lines.append("- %s (%s)" % (skipped.file_path, skipped.reason))
            lines.append("")
        # Parse errors block.
        if report.parse_errors:
            lines.append("## Parse Errors")
            lines.append("")
            for pe in report.parse_errors:
                line_str = str(pe.line_number) if pe.line_number is not None else "?"
                lines.append("- %s:%s %s" % (pe.file_path, line_str, pe.message))
            lines.append("")
        # Stale baseline advisory block (FR-019).
        if report.stale_baseline_entries:
            lines.append("## Stale Baseline Entries")
            lines.append("")
            for entry in report.stale_baseline_entries:
                lines.append("- %s" % entry)
            lines.append("")
        # Join with newlines; ensure trailing newline for POSIX cleanliness.
        return "\n".join(lines) + "\n"
