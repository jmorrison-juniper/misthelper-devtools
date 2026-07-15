"""Unit tests for ReportBuilder + MarkdownRenderer determinism (T018).

Covers:
    - SC-005: identical inputs -> byte-identical JSON.
    - SC-007: emitted JSON validates against report.schema.json using the
      inline hand-rolled validator (T020).
    - MarkdownRenderer determinism from the same inputs.
    - Validator rejects payloads that violate required/enum/type constraints.

No third-party JSON-schema library is used; validation goes through
`ReportBuilder.validate()` per plan.md Primary Dependencies (stdlib only).
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import hashlib  # Used to hash JSON strings for determinism assertions.
import json  # Load report.schema.json from disk.
from pathlib import Path  # Compose the schema path.
from types import MappingProxyType  # Freeze mappings before ConfigSnapshot construction.

import pytest  # pytest.raises for validator failure cases.

from tools.test_quality_analyzer.detection import (  # SUT collaborators (types).
    Category,
    ConfigSnapshot,
    Finding,
    MistApiPredicate,
    ParseError,
    Severity,
    SkippedFile,
)
from tools.test_quality_analyzer.reporting import (  # SUTs.
    MarkdownRenderer,
    ReportBuilder,
)

# Anchor path to the schema file so tests are robust to cwd shifts.
_SCHEMA_PATH = (  # Repo-relative POSIX path resolved from this test file location.
    Path(__file__).resolve().parents[3]  # tests/tools/test_quality_analyzer -> repo root.
    / "tools"
    / "test_quality_analyzer"
    / "report.schema.json"
)


def _fixed_config() -> ConfigSnapshot:
    """Return a deterministic ConfigSnapshot for repeatable test runs."""
    # Only three rules to keep the payload small but non-trivial.
    return ConfigSnapshot(
        rules_enabled=MappingProxyType(
            {"weak_bare_truthy": True, "weak_no_assertions": False, "missing_timeout": True},
        ),
        severity_overrides=MappingProxyType(
            {"weak_bare_truthy": Severity.MEDIUM, "missing_timeout": Severity.HIGH},
        ),
        exclusion_globs=("tests/legacy/**",),  # One glob to exercise list serialization.
        mist_api_predicate=MistApiPredicate(
            banned_imports=("mistapi",),  # Default banned import list.
            excluded_src_prefixes=("src/api/",),  # Default excluded prefix list.
        ),
    )


def _fixed_findings() -> tuple[Finding, ...]:
    """Return three findings across two severities to exercise the sort key."""
    # Ordering here is intentionally scrambled so the builder must re-sort them.
    return (
        Finding(
            category=Category.WEAK_ASSERTION,  # Second by category alphabetical order.
            rule_id="weak_bare_truthy",  # Rule id used by sort tiebreak.
            severity=Severity.MEDIUM,  # Lower rank than HIGH -> should sort after.
            file_path="tests/x/test_x.py",  # POSIX path -- required by contract.
            line_number=42,  # Line number tiebreak.
            explanation="Assertion checks bare truthiness.",  # Short human explanation.
            remediation="Assert an explicit value.",  # One-sentence remediation.
            heuristic=False,  # Not a heuristic detection.
            related_source=None,  # No related source path to attach.
        ),
        Finding(
            category=Category.MISSING_FAILURE_MODE,  # First alphabetically among HIGH bucket.
            rule_id="missing_timeout",  # Rule id used by sort tiebreak.
            severity=Severity.HIGH,  # Highest severity in this fixture -> sorts first.
            file_path="tests/a/test_a.py",  # Different file path for sort demo.
            line_number=7,  # Lower line number.
            explanation="No timeout assertion for the outbound call.",  # Short explanation.
            remediation="Add a timeout parameter assertion.",  # One-sentence remediation.
            heuristic=False,  # Not a heuristic detection.
            related_source="src/foo.py",  # Non-null related_source path.
        ),
        Finding(
            category=Category.WEAK_ASSERTION,  # Same category as first finding.
            rule_id="weak_no_assertions",  # Different rule id.
            severity=Severity.HIGH,  # HIGH -> sorts with missing_timeout by severity.
            file_path="tests/z/test_z.py",  # Trailing file path in sort order.
            line_number=1,  # First line in file.
            explanation="Test body contains no assertions.",  # Short explanation.
            remediation="Add an assertion checking the SUT return.",  # Remediation.
            heuristic=False,  # Not a heuristic detection.
            related_source=None,  # No related source.
        ),
    )


def _build(builder: ReportBuilder):
    """Build a Report twice with identical inputs and return (report, json_text)."""
    # Fixed timestamp per test acceptance -- no wall-clock reads.
    return builder.build(
        findings=list(_fixed_findings()),  # Intentional list (not tuple) to test coercion.
        skipped=[
            SkippedFile(
                file_path="tests/api/test_mist_client.py",  # Sample POSIX path.
                reason="mist_api_excluded",  # Reason per FR-002 taxonomy.
                matched_rule="mist_api_predicate",  # R4: matched_rule name.
            ),
        ],
        parse_errors=[
            ParseError(
                file_path="tests/broken/test_broken.py",  # Path of unparseable file.
                line_number=3,  # Locatable syntax error line.
                message="unexpected EOF",  # ASCII SyntaxError message.
            ),
        ],
        stale_baseline_entries=["tests/removed/test_gone.py"],  # One advisory entry.
        config_snapshot=_fixed_config(),  # Deterministic config snapshot.
        engine_version="0.1.0",  # Pinned engine version for reproducibility.
        generated_at="2026-07-14T12:00:00+00:00",  # Fixed ISO timestamp with UTC offset.
        scanned_roots=["tests/tools/test_quality_analyzer/fixtures"],  # One root path.
    )


def test_json_is_byte_identical_on_repeat_build() -> None:
    """SC-005: two builds with identical inputs produce byte-identical JSON output."""
    # Build the report twice via two separate ReportBuilder instances.
    builder = ReportBuilder()  # Fresh builder -- no cached state expected.
    report_a = _build(builder)  # First run.
    report_b = _build(builder)  # Second run -- same inputs, same order.
    # Convert both to canonical JSON.
    json_a = builder.to_json(report_a)  # First JSON string.
    json_b = builder.to_json(report_b)  # Second JSON string.
    # Hash both strings for a compact, unambiguous comparison signal.
    assert (
        hashlib.sha256(json_a.encode("utf-8")).hexdigest()
        == hashlib.sha256(
            json_b.encode("utf-8"),
        ).hexdigest()
    )  # Byte-identical -> hash-identical.
    # Byte equality check as well so a failure trace shows the diff, not just the hash.
    assert json_a == json_b  # Strict equality of the raw JSON text.


def test_json_ends_with_newline_and_is_ascii() -> None:
    """FR-011 + Constitution V: JSON text is ASCII and terminates with a newline."""
    # Build once and inspect the emitted string.
    builder = ReportBuilder()  # Fresh builder.
    text = builder.to_json(_build(builder))  # Emit canonical JSON.
    # Trailing newline invariant.
    assert text.endswith("\n")  # POSIX-friendly line termination.
    # ASCII-only invariant: encoding to ASCII must not raise.
    text.encode("ascii")  # Raises UnicodeEncodeError if any non-ASCII byte is present.


def test_findings_sorted_deterministically() -> None:
    """Findings must be sorted by _sort_key: descending severity, then category/path/line."""
    # Build once and inspect the tuple ordering.
    builder = ReportBuilder()  # Fresh builder.
    report = _build(builder)  # Build a fresh report.
    # HIGH severity findings must come before MEDIUM.
    severities = [f.severity for f in report.findings]  # Extract severity sequence.
    assert severities == [Severity.HIGH, Severity.HIGH, Severity.MEDIUM]  # Expected order.
    # Within the HIGH bucket, alphabetical category places missing_failure_mode ahead of weak_assertion.
    assert report.findings[0].category == Category.MISSING_FAILURE_MODE  # Sort tiebreak passes.
    assert report.findings[1].category == Category.WEAK_ASSERTION  # Confirms secondary key.


def test_json_validates_against_schema() -> None:
    """SC-007: emitted JSON payload validates against report.schema.json."""
    # Load the schema from disk each test run so drift is caught.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))  # Parse schema JSON.
    # Build and serialize a canonical report.
    builder = ReportBuilder()  # Fresh builder.
    text = builder.to_json(_build(builder))  # Emit canonical JSON.
    payload = json.loads(text)  # Re-parse so we validate the on-disk shape.
    # Validator raises on failure; no return value on success.
    builder.validate(payload, schema)  # Should not raise.


def test_validator_rejects_missing_required_field() -> None:
    """T020 acceptance: missing engine_version raises ValueError naming the field."""
    # Build a valid payload, then delete a required top-level field.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))  # Parse schema.
    builder = ReportBuilder()  # Fresh builder.
    payload = json.loads(builder.to_json(_build(builder)))  # Round-trip through JSON.
    del payload["engine_version"]  # Break the schema contract deliberately.
    # Validator must raise and mention the missing field for diagnostics.
    with pytest.raises(ValueError, match="engine_version"):
        builder.validate(payload, schema)  # Should raise a ValueError.


def test_validator_rejects_bad_severity_enum() -> None:
    """T020 acceptance: severity value outside the taxonomy is rejected."""
    # Load schema and craft a payload with an invalid severity.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))  # Parse schema.
    builder = ReportBuilder()  # Fresh builder.
    payload = json.loads(builder.to_json(_build(builder)))  # Baseline valid payload.
    payload["findings"][0]["severity"] = "catastrophic"  # Break the enum contract.
    # Validator must raise mentioning enum failure.
    with pytest.raises(ValueError, match="enum"):
        builder.validate(payload, schema)  # Should raise a ValueError.


def test_validator_rejects_bad_line_number_type() -> None:
    """T020 acceptance: line_number must be integer >= 1; strings are rejected."""
    # Load schema and craft a payload with a bogus line_number type.
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))  # Parse schema.
    builder = ReportBuilder()  # Fresh builder.
    payload = json.loads(builder.to_json(_build(builder)))  # Baseline valid payload.
    payload["findings"][0]["line_number"] = "not-an-int"  # Break the integer contract.
    # Validator must raise mentioning integer requirement.
    with pytest.raises(ValueError, match="integer"):
        builder.validate(payload, schema)  # Should raise a ValueError.


def test_markdown_render_is_deterministic() -> None:
    """MarkdownRenderer must produce byte-identical text on repeated identical builds."""
    # Build the same report twice and render both.
    builder = ReportBuilder()  # Fresh builder.
    renderer = MarkdownRenderer()  # Fresh renderer.
    md_a = renderer.render(_build(builder))  # First render.
    md_b = renderer.render(_build(builder))  # Second render -- same inputs.
    assert md_a == md_b  # Byte equality required for reproducible reports.
    assert md_a.endswith("\n")  # Trailing newline invariant for POSIX cleanliness.
    md_a.encode("ascii")  # ASCII-only text; encoding must not raise.


def test_markdown_groups_findings_by_severity_then_category() -> None:
    """Markdown output groups findings by severity DESC and category ASC."""
    # Build the fixture report and render.
    builder = ReportBuilder()  # Fresh builder.
    renderer = MarkdownRenderer()  # Fresh renderer.
    md = renderer.render(_build(builder))  # Emit Markdown.
    # HIGH section must appear before MEDIUM section in the output text.
    idx_high = md.find("### Severity: high")  # Locate HIGH header index.
    idx_medium = md.find("### Severity: medium")  # Locate MEDIUM header index.
    assert idx_high != -1 and idx_medium != -1  # Both severity sections must exist.
    assert idx_high < idx_medium  # HIGH must precede MEDIUM per sort contract.


def test_skipped_files_use_matched_rule_field() -> None:
    """R4: SkippedFile.matched_rule must round-trip through the JSON payload."""
    # Build a report and inspect the emitted skipped_files list.
    builder = ReportBuilder()  # Fresh builder.
    text = builder.to_json(_build(builder))  # Emit canonical JSON.
    payload = json.loads(text)  # Re-parse the JSON text.
    # Exactly one skipped file was supplied; its matched_rule must survive round-trip.
    assert payload["skipped_files"][0]["matched_rule"] == "mist_api_predicate"  # R4 anchor.
