"""Catalog Mist OpenAPI GET endpoints, diff against MistHelper usage, and generate SpecKit feature stubs.

Outputs (relative to repo root):
- documentation/MIST_API_GET_ENDPOINTS.md  -- full catalog of GET endpoints
- documentation/MIST_API_MISSING_ENDPOINTS.md -- diff against current MistHelper usage
- specs/5NN-mist-<operationid-kebab>/spec.md -- one SpecKit spec per missing GET endpoint

Driven by:
- documentation/mist-api-openapi31json.json (OpenAPI 3.1 source)
- specs/010-endpoint-usage-audit/catalog_misthelper.json (existing call-site catalog)
- MistHelper.py (fallback grep sweep so we never miss a mistapi.* reference)

This tool follows the project's NON-NEGOTIABLE conventions:
* Inline comments on every executable line.
* logging.info() before each action, logging.debug() after with a count.
* ASCII-only logging.
* 5-Item Rule: small focused helpers, max 5 params, <= 25 lines per function.
"""

from __future__ import annotations  # Postpone annotation evaluation for forward refs

import argparse  # CLI flag parsing for generator invocation
import json  # Read OpenAPI 3.1 JSON document
import logging  # Project-mandated action logging
import re  # Tokenize OpenAPI paths and scan MistHelper.py for mistapi calls
import sys  # Exit codes when invariants fail
from dataclasses import dataclass, field  # Dataclasses for endpoint records
from datetime import UTC, datetime  # Timestamp generated artifacts in UTC
from pathlib import Path  # Cross-platform path manipulation

LOG = logging.getLogger("openapi_endpoint_catalog")  # Module-scoped logger

REPO_ROOT = Path(__file__).resolve().parents[1]  # repo root sits one level above tools/
OPENAPI_PATH = REPO_ROOT / "documentation" / "mist-api-openapi31json.json"  # Source spec
MISTHELPER_PATH = REPO_ROOT / "MistHelper.py"  # Primary script to grep for mistapi.* calls
EXISTING_CATALOG = REPO_ROOT / "specs" / "010-endpoint-usage-audit" / "catalog_misthelper.json"  # Prior audit output
CATALOG_OUT = REPO_ROOT / "documentation" / "MIST_API_GET_ENDPOINTS.md"  # Catalog destination
DIFF_OUT = REPO_ROOT / "documentation" / "MIST_API_MISSING_ENDPOINTS.md"  # Diff destination
SPECS_DIR = REPO_ROOT / "specs"  # Where SpecKit feature dirs live
SPEC_TEMPLATE = REPO_ROOT / ".specify" / "templates" / "spec-template.md"  # Reference template

# Spec numbering window for this batch -- avoids known existing numbers (168-208, 429-440).
SPEC_NUMBER_START = 500  # First feature number to attempt
SPEC_NUMBER_END = 1500  # Hard upper bound; should comfortably cover ~500 specs
KEBAB_RE = re.compile(r"[^a-z0-9]+")  # Used to slugify operationIds
MISTAPI_CALL_RE = re.compile(r"mistapi\.api\.v1\.[\w\.]+")  # Match mistapi SDK function references
PATH_PARAM_RE = re.compile(r"\{(\w+)\}")  # Match {param} placeholders in OpenAPI paths


@dataclass(slots=True)
class GetEndpoint:
    """In-memory record for a single OpenAPI GET endpoint."""

    operation_id: str  # OpenAPI operationId (also the mistapi function name)
    path: str  # Full API path, e.g. /api/v1/orgs/{org_id}/sites
    tag: str  # First OpenAPI tag (used for grouping)
    summary: str  # Short human-readable purpose
    description: str  # Longer description (may be empty)
    path_params: list[str] = field(default_factory=list)  # Required path parameter names
    query_params: list[tuple[str, bool]] = field(default_factory=list)  # (name, required) per query param
    mistapi_module: str = ""  # Reconstructed SDK module path (e.g. mistapi.api.v1.orgs.sites)


def configure_logging(verbose: bool) -> None:
    """Wire up ASCII-safe stderr logging for the run."""
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)  # Verbose flag elevates to DEBUG
    handler = logging.StreamHandler(stream=sys.stderr)  # All progress goes to stderr
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))  # ASCII-only formatter
    LOG.addHandler(handler)  # Attach single handler


def slugify(operation_id: str) -> str:
    """Convert an operationId like listOrgSites into list-org-sites for filesystem use."""
    LOG.debug("Slugifying operationId %s", operation_id)  # Trace input
    snake = re.sub(r"(?<!^)(?=[A-Z])", "-", operation_id).lower()  # Insert hyphens before capitals
    cleaned = KEBAB_RE.sub("-", snake).strip("-")  # Replace non-kebab chars with hyphens
    return cleaned  # Caller stamps directory names with this


def derive_mistapi_module(api_path: str) -> str:
    """Reconstruct the mistapi SDK module path from an OpenAPI URL path."""
    LOG.debug("Deriving mistapi module from path %s", api_path)  # Trace input path
    tokens = [t for t in api_path.split("/") if t and not t.startswith("{")]  # Drop empties and {params}
    if tokens[:2] == ["api", "v1"]:  # mistapi SDK is rooted at api.v1
        tokens = tokens[2:]  # Trim the leading api/v1
    parts = [re.sub(r"[^a-z0-9_]+", "_", tok.lower()).strip("_") for tok in tokens]  # Normalize each token
    parts = [p for p in parts if p]  # Drop empty segments
    return "mistapi.api.v1." + ".".join(parts) if parts else "mistapi.api.v1"  # Final module dotted path


def parse_openapi_get_endpoints(spec_path: Path) -> list[GetEndpoint]:
    """Walk the OpenAPI document and return every GET endpoint as a GetEndpoint record."""
    LOG.info("Loading OpenAPI spec from %s", spec_path)  # Announce parse start
    with spec_path.open("r", encoding="utf-8") as fh:  # Open spec file
        doc = json.load(fh)  # Parse full JSON document
    endpoints: list[GetEndpoint] = []  # Accumulator
    for path, methods in doc.get("paths", {}).items():  # Iterate every path in the spec
        op = methods.get("get") if isinstance(methods, dict) else None  # GET operation object (or None)
        if not op:  # Skip non-GET-bearing paths
            continue
        endpoints.append(_endpoint_from_op(path, op))  # Convert raw dict to dataclass
    LOG.debug("Parsed %d GET endpoints", len(endpoints))  # Confirm count
    return endpoints  # Hand off to downstream stages


def _endpoint_from_op(path: str, op: dict) -> GetEndpoint:
    """Project a single OpenAPI GET op dict into a GetEndpoint record."""
    LOG.debug("Projecting endpoint for path %s", path)  # Trace per-path projection
    operation_id = op.get("operationId") or _synth_operation_id(path)  # Fallback when operationId missing
    tags = op.get("tags") or ["Untagged"]  # Default tag when none provided
    path_params, query_params = _split_parameters(op.get("parameters", []))  # Param decomposition helper
    return GetEndpoint(
        operation_id=operation_id,  # Captured operationId
        path=path,  # Original path
        tag=str(tags[0]),  # First tag
        summary=str(op.get("summary") or "").strip(),  # Summary trimmed for readability
        description=str(op.get("description") or "").strip(),  # Description trimmed
        path_params=path_params,  # Required path params
        query_params=query_params,  # Query params with required flag
        mistapi_module=derive_mistapi_module(path),  # Derived SDK module dotted path
    )


def _synth_operation_id(path: str) -> str:
    """Synthesize an operationId when the OpenAPI spec omits one."""
    LOG.debug("Synthesizing operationId for %s", path)  # Trace synth path
    tokens = [t for t in path.split("/") if t and not t.startswith("{")]  # Drop placeholders/empties
    return "get" + "".join(t.capitalize() for t in tokens)  # CamelCase rejoin prefixed with 'get'


def _split_parameters(params: list[dict]) -> tuple[list[str], list[tuple[str, bool]]]:
    """Split OpenAPI parameter entries into (path_param_names, query_param_required_pairs)."""
    LOG.debug("Splitting %d parameters", len(params))  # Trace incoming param count
    path_params: list[str] = []  # Required path params accumulator
    query_params: list[tuple[str, bool]] = []  # Query param accumulator
    for p in params:  # Walk each parameter object
        if not isinstance(p, dict):  # Defensive against $ref or unexpected types
            continue
        name = p.get("name")  # Param name string
        loc = p.get("in")  # Param location (path/query/header)
        if not name:  # Skip malformed entries with no name
            continue
        if loc == "path":  # Path params are always required by the OpenAPI spec
            path_params.append(name)
        elif loc == "query":  # Capture query params with their required flag
            query_params.append((name, bool(p.get("required"))))
    return path_params, query_params  # Tuple keeps callers terse


def load_existing_operation_ids(catalog_path: Path, source_path: Path) -> set[str]:
    """Build the set of mistapi operationIds already used somewhere in this repo."""
    LOG.info("Building existing-usage set from %s and %s", catalog_path, source_path)  # Announce inputs
    used: set[str] = set()  # Will hold operationId strings (trailing token of mistapi.* references)
    if catalog_path.exists():  # Prior audit JSON if present
        for row in json.loads(catalog_path.read_text(encoding="utf-8")):  # Iterate cataloged rows
            fn = row.get("function") or ""  # Pull full mistapi function path
            if fn:
                used.add(fn.rsplit(".", 1)[-1])  # Keep only operationId tail
    if source_path.exists():  # Additional sweep on MistHelper.py source
        text = source_path.read_text(encoding="utf-8", errors="ignore")  # Tolerant decode
        for match in MISTAPI_CALL_RE.finditer(text):  # Each match like mistapi.api.v1.orgs.sites.listOrgSites
            used.add(match.group(0).rsplit(".", 1)[-1])  # Add operationId tail
    LOG.debug("Existing usage set size: %d", len(used))  # Confirm dedup count
    return used  # Return canonical implementation set


def write_get_catalog(endpoints: list[GetEndpoint], out_path: Path) -> None:
    """Emit the human-readable GET endpoint catalog markdown grouped by tag."""
    LOG.info("Writing GET endpoint catalog to %s (%d endpoints)", out_path, len(endpoints))  # Announce write
    by_tag: dict[str, list[GetEndpoint]] = {}  # Group endpoints by their first tag
    for ep in endpoints:  # Bucket by tag for clean presentation
        by_tag.setdefault(ep.tag, []).append(ep)
    lines: list[str] = []  # Markdown line buffer
    lines.append("# Mist API GET Endpoint Catalog")  # H1 banner
    lines.append("")  # Blank line for markdownlint
    lines.append(f"> Generated {_now_iso()} from `documentation/mist-api-openapi31json.json`.")  # Provenance
    lines.append("")  # Spacing
    lines.append(f"- **Total GET endpoints**: {len(endpoints)}")  # High-level stat
    lines.append(f"- **Tags represented**: {len(by_tag)}")  # Tag count
    lines.append("")  # Spacing
    lines.append("## Index")  # Tag index header
    lines.append("")  # Blank
    for tag in sorted(by_tag):  # Stable ordering
        anchor = re.sub(r"[^a-z0-9-]+", "-", tag.lower()).strip("-")  # Markdown-safe anchor
        lines.append(f"- [{tag}](#{anchor}) ({len(by_tag[tag])} endpoints)")  # Index entry
    lines.append("")  # Spacing before section content
    for tag in sorted(by_tag):  # Per-tag sections
        lines.extend(_format_tag_section(tag, by_tag[tag]))  # Reuse formatter
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # Persist file
    LOG.debug("Catalog written: %d lines", len(lines))  # Confirm byte/line count


def _format_tag_section(tag: str, items: list[GetEndpoint]) -> list[str]:
    """Render a single tag section of the GET catalog markdown."""
    LOG.debug("Formatting tag section %s (%d endpoints)", tag, len(items))  # Trace formatting
    lines = [f"## {tag}", "", "| operationId | Path | Summary | mistapi module |", "|---|---|---|---|"]  # Table header
    for ep in sorted(items, key=lambda e: e.operation_id):  # Stable per-tag order
        summary = ep.summary.replace("|", "\\|") or ep.operation_id  # Escape table delimiters
        lines.append(f"| `{ep.operation_id}` | `{ep.path}` | {summary} | `{ep.mistapi_module}` |")  # Row
    lines.append("")  # Blank line after table
    return lines  # Caller appends


def write_diff(
    endpoints: list[GetEndpoint],
    used_ops: set[str],
    out_path: Path,
) -> list[GetEndpoint]:
    """Emit the missing-endpoint diff markdown and return the missing endpoints list."""
    LOG.info("Computing missing-endpoint diff against %d implemented ops", len(used_ops))  # Announce
    missing = [ep for ep in endpoints if ep.operation_id not in used_ops]  # Set difference
    by_tag: dict[str, list[GetEndpoint]] = {}  # Group missing by tag
    for ep in missing:  # Bucket by tag for the diff report
        by_tag.setdefault(ep.tag, []).append(ep)
    lines = [
        "# Mist API Missing GET Endpoints",  # H1 banner
        "",
        f"> Generated {_now_iso()}. Diff between OpenAPI GET endpoints and current `MistHelper.py` usage.",
        "",
        f"- **GET endpoints in spec**: {len(endpoints)}",  # Total
        f"- **Implemented in repo**: {len(endpoints) - len(missing)}",  # Implemented count
        f"- **Missing (to be specced)**: {len(missing)}",  # Missing count
        "",
        "## Coverage by Tag",
        "",
        "| Tag | Implemented | Missing | Total |",
        "|---|---:|---:|---:|",
    ]  # Coverage header table
    all_tags = sorted({ep.tag for ep in endpoints})  # Union of tags
    for tag in all_tags:  # Per-tag coverage stats
        total = sum(1 for ep in endpoints if ep.tag == tag)  # Total per tag
        miss = sum(1 for ep in missing if ep.tag == tag)  # Missing per tag
        impl = total - miss  # Implemented per tag
        lines.append(f"| {tag} | {impl} | {miss} | {total} |")  # Row
    lines.append("")  # Spacing
    lines.append("## Missing Endpoints")  # Detail section
    lines.append("")  # Blank line
    for tag in sorted(by_tag):  # Per-tag missing sections
        lines.append(f"### {tag}")  # Subsection header
        lines.append("")  # Blank line
        lines.append("| operationId | Path | Summary | Proposed Spec Slug |")  # Table header
        lines.append("|---|---|---|---|")  # Markdown sep
        for ep in sorted(by_tag[tag], key=lambda e: e.operation_id):  # Stable ordering
            slug = slugify(ep.operation_id)  # Spec slug preview
            summary = ep.summary.replace("|", "\\|") or ep.operation_id  # Escape delimiters
            lines.append(f"| `{ep.operation_id}` | `{ep.path}` | {summary} | `mist-{slug}` |")  # Row
        lines.append("")  # Blank line between sections
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # Persist file
    LOG.debug("Diff written: %d missing endpoints", len(missing))  # Confirm size
    return missing  # Pass to spec generator


def allocate_spec_numbers(missing: list[GetEndpoint], specs_dir: Path) -> dict[str, int]:
    """Assign each missing operationId a unique spec directory number.

    Idempotent: if a directory `<num>-mist-<slug>` for an endpoint already exists, that
    number is reused (preventing duplicate spec dirs across runs). Otherwise the next free
    number in [SPEC_NUMBER_START, SPEC_NUMBER_END] is taken. Sorting by operationId keeps
    the numbering stable across runs that don't change the missing set.
    """
    LOG.info("Allocating spec numbers for %d missing endpoints", len(missing))  # Announce
    used_numbers: set[int] = set()  # Numbers occupied by any existing spec directory
    slug_to_number: dict[str, int] = {}  # Map of mist-<slug> -> assigned number for idempotency
    for child in specs_dir.iterdir() if specs_dir.exists() else []:  # Walk current specs
        if not child.is_dir() or "-" not in child.name:  # Skip non-matching
            continue
        head, _, tail = child.name.partition("-")  # Split number prefix from tail
        if not head.isdigit():  # Only digits become reserved numbers
            continue
        num = int(head)  # Parse number
        used_numbers.add(num)  # Reserve regardless of suffix shape
        if tail.startswith("mist-"):  # Track our own generated dirs by their slug
            slug_to_number[tail] = num  # Remember slug->number mapping
    LOG.debug(
        "Existing spec numbers reserved: %d (mist-* tracked: %d)",
        len(used_numbers),
        len(slug_to_number),
    )  # Confirm
    assignment: dict[str, int] = {}  # operationId -> spec number
    cursor = SPEC_NUMBER_START  # Walk forward from the configured base
    for ep in sorted(missing, key=lambda e: e.operation_id):  # Deterministic sort
        slug_key = f"mist-{slugify(ep.operation_id)}"  # Filesystem suffix for this op
        if slug_key in slug_to_number:  # Same slug already on disk -> reuse its number
            assignment[ep.operation_id] = slug_to_number[slug_key]  # Idempotent reuse
            continue
        while cursor in used_numbers and cursor <= SPEC_NUMBER_END:  # Skip occupied slots
            cursor += 1
        if cursor > SPEC_NUMBER_END:  # Hard ceiling -- abort with a useful error
            raise RuntimeError(f"Exhausted spec number window {SPEC_NUMBER_START}..{SPEC_NUMBER_END}")
        assignment[ep.operation_id] = cursor  # Record assignment
        used_numbers.add(cursor)  # Reserve it so we don't double up
        cursor += 1  # Move to next candidate slot
    return assignment  # Return final mapping


def generate_specs(missing: list[GetEndpoint], assignment: dict[str, int], specs_dir: Path) -> int:
    """Write one SpecKit spec.md per missing endpoint. Skips dirs that already exist."""
    LOG.info("Generating SpecKit feature dirs for %d endpoints", len(missing))  # Announce
    written = 0  # Counter for verification later
    for ep in missing:  # Walk missing endpoints in input order
        number = assignment[ep.operation_id]  # Look up pre-assigned number
        slug = slugify(ep.operation_id)  # Filesystem-safe slug
        feature_dir = specs_dir / f"{number:03d}-mist-{slug}"  # Feature directory path
        if feature_dir.exists():  # Re-runs should be idempotent
            LOG.debug("Skipping existing %s", feature_dir.name)  # Trace skip
            continue
        feature_dir.mkdir(parents=True, exist_ok=True)  # Create directory
        spec_md = _render_spec(ep, feature_dir.name)  # Render markdown body
        (feature_dir / "spec.md").write_text(spec_md, encoding="utf-8")  # Write spec.md
        written += 1  # Bump counter
    LOG.debug("Generated %d new spec.md files", written)  # Confirm count
    return written  # Return for verify stage


def _render_spec(ep: GetEndpoint, branch_name: str) -> str:
    """Render a complete spec.md for a single missing GET endpoint."""
    LOG.debug("Rendering spec for %s", ep.operation_id)  # Trace render
    summary = ep.summary or ep.operation_id  # Fallback summary
    description = ep.description or "No description provided by the OpenAPI spec."  # Fallback description
    path_param_block = _format_path_param_block(ep.path_params)  # Helper rendering for path params
    query_param_block = _format_query_param_block(ep.query_params)  # Helper for query params
    fr_block = _format_functional_requirements(ep)  # FR list driven by metadata
    today = datetime.now(UTC).strftime("%Y-%m-%d")  # Stamp Created date
    return _SPEC_BODY.format(
        title=summary,  # Title from summary
        branch=branch_name,  # Feature branch name
        created=today,  # Created date
        op=ep.operation_id,  # operationId
        path=ep.path,  # OpenAPI path
        tag=ep.tag,  # Tag
        module=ep.mistapi_module,  # SDK module
        description=description,  # Long description
        path_params=path_param_block,  # Path params markdown
        query_params=query_param_block,  # Query params markdown
        functional_requirements=fr_block,  # FR markdown
    )


def _format_path_param_block(params: list[str]) -> str:
    """Render the path-parameter bullet list for spec.md."""
    LOG.debug("Formatting %d path params", len(params))  # Trace
    if not params:  # No path params -> static placeholder
        return "_None._"
    return "\n".join(f"- `{name}` (required)" for name in params)  # One bullet per param


def _format_query_param_block(params: list[tuple[str, bool]]) -> str:
    """Render the query-parameter bullet list for spec.md."""
    LOG.debug("Formatting %d query params", len(params))  # Trace
    if not params:  # Static placeholder when absent
        return "_None._"
    bullets = []  # Accumulator
    for name, required in params:  # Walk each query param
        marker = "required" if required else "optional"  # Tag with required flag
        bullets.append(f"- `{name}` ({marker})")  # Bullet entry
    return "\n".join(bullets)  # Joined markdown


def _format_functional_requirements(ep: GetEndpoint) -> str:
    """Produce a stable Functional Requirements section for the endpoint."""
    LOG.debug("Formatting FRs for %s", ep.operation_id)  # Trace
    reqs = [
        (
            f"**FR-001**: Provide a new menu item that invokes "
            f"`{ep.mistapi_module}.{ep.operation_id}()` via the `mistapi` SDK."
        ),  # Calls the right SDK
        (
            "**FR-002**: Collect required inputs using `safe_input()` "
            "so the operation works in SSH and container contexts."
        ),  # Safety-first input
        (
            "**FR-003**: Apply rate limiting and retry logic consistent with adjacent "
            "menu items (delay_metrics.json + tuning_data.json)."
        ),  # Rate handling
        (
            "**FR-004**: Persist results using "
            "`DataExporter.write_with_format_selection(data, filename, api_function_name=...)` "
            "so CSV/SQLite/ArangoDB backends all work."
        ),  # Multi-backend
        (
            f"**FR-005**: Register the operationId `{ep.operation_id}` in "
            "`ENDPOINT_PRIMARY_KEY_STRATEGIES` with the correct PK strategy "
            "(natural / composite / auto-increment)."
        ),  # PK strategy
        (
            "**FR-006**: Log `INFO` before the API call and `DEBUG` with response counts "
            "after, ASCII-only, per Action Logging principle."
        ),  # Logging
        ("**FR-007**: Add inline comments on every new executable line per " "Inline Comments principle."),  # Comments
        ("**FR-008**: Update README.md menu table and CHANGELOG.md with " "the new operation number."),  # Documentation
    ]  # End requirement list
    return "\n".join(reqs)  # Caller embeds


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # Used in markdown banners


_SPEC_BODY = """# Feature Specification: Mist API Read Operation -- {title}

**Feature Branch**: `{branch}`
**Created**: {created}
**Status**: Draft
**Input**: User description:
"Catalog the missing Mist API GET endpoint `{op}` and add it as a new MistHelper menu item."

## Source Endpoint

- **operationId**: `{op}`
- **Method**: `GET`
- **Path**: `{path}`
- **Tag**: `{tag}`
- **mistapi SDK module**: `{module}`

### Description

{description}

### Path Parameters

{path_params}

### Query Parameters

{query_params}

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read-only data retrieval (Priority: P1)

A junior NOC engineer launches MistHelper, selects the new menu item, supplies the required identifiers
(org / site / device as applicable), and receives the JSON payload exposed by `{op}` -- exported to the
configured storage backend (CSV, SQLite, or ArangoDB+Redis) under `data/`.

**Why this priority**: This is a read-only Mist API call -- no destructive effect, so it can ship as P1
without elaborate guardrails. Coverage of this endpoint unlocks data the user cannot currently extract
from MistHelper without writing custom code.

**Independent Test**: Run the new menu item against a known org/site; verify the resulting file exists
under `data/`, has at least one row when the upstream API returns data, and that re-running the menu
item upserts cleanly into SQLite (no duplicate primary keys).

**Acceptance Scenarios**:

1. **Given** valid credentials and org context, **When** the user selects the new menu item, **Then**
   MistHelper invokes `{module}.{op}()` exactly once per required scope and persists results.
2. **Given** an SSH or container session, **When** the user is prompted for identifiers, **Then**
   `safe_input()` handles EOF gracefully and the operation exits 0 without a traceback.
3. **Given** repeated runs, **When** SQLite is the active backend, **Then** rows upsert by the configured
   primary key strategy (no duplicates).

### Edge Cases

- The API returns an empty list -> menu reports "no data returned" and exits cleanly.
- The user supplies an unknown org/site UUID -> 404 from Mist API surfaces as a logged warning, not a traceback.
- Rate limiting (429) triggers the adaptive delay system; no manual intervention required.
- The user runs with `--fast` -> retries cap respected, concurrency raised.
- Output backend is ArangoDB+Redis -> graph edges (per spec 188) and Redis caches are updated consistently.

## Requirements *(mandatory)*

{functional_requirements}

## Constitution & Instructions Conformance

- Inline comments on every executable line (Constitution VI -- NON-NEGOTIABLE).
- Action logging before/after every meaningful step (Constitution VII -- NON-NEGOTIABLE).
- 5-Item Rule: implementation function <=25 lines, <=5 params, <=5 nesting blocks.
- ASCII-only logging (no Unicode/emoji).
- Multi-backend output via `DataExporter`.
- `safe_input()` wraps all `input()` calls.
- Primary key strategy registered in `ENDPOINT_PRIMARY_KEY_STRATEGIES`.
- README menu table + CHANGELOG entry updated in the same PR.

## Non-Functional Requirements

- **Performance**: Single-page request <=5s; full paginated retrieval bounded by Mist API rate limits.
- **Security**: API token loaded from `.env`; never logged.
- **Compatibility**: Python 3.13+, mistapi 0.59+, runs in Podman container and on bare Windows venv.

## Out of Scope

- Write operations against the same path (POST/PUT/PATCH/DELETE) -- separate spec when needed.
- UI changes beyond the new menu item label.
- Database schema migrations beyond the new primary-key strategy entry.

## Acceptance Criteria Checklist

- [ ] Menu item added with sequential operation number.
- [ ] `ENDPOINT_PRIMARY_KEY_STRATEGIES` updated.
- [ ] Inline comments + action logging on every new line.
- [ ] `DataExporter.write_with_format_selection` used for output.
- [ ] `safe_input()` used for prompts.
- [ ] README.md and CHANGELOG.md updated.
- [ ] `python -m py_compile MistHelper.py`, `python -m ruff check`, `python -m black --check` all green.
- [ ] Test invocation via `python MistHelper.py --menu <num>` returns 0 on a known org.
"""


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: parse args, run pipeline, return exit code."""
    parser = argparse.ArgumentParser(
        description="Catalog Mist API GET endpoints and generate SpecKit specs.",
    )  # Top-level CLI
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Only emit catalog + diff (skip spec generation)",
    )  # Quick mode
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on number of specs to generate (0 = no limit)",
    )  # Safety valve
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )  # Verbose flag
    args = parser.parse_args(argv)  # Parse argv (None defaults to sys.argv)
    configure_logging(args.verbose)  # Wire up logger first
    LOG.info("Starting OpenAPI endpoint catalog pipeline")  # Pipeline banner
    endpoints = parse_openapi_get_endpoints(OPENAPI_PATH)  # Stage 1: parse spec
    used_ops = load_existing_operation_ids(EXISTING_CATALOG, MISTHELPER_PATH)  # Stage 2: existing usage
    write_get_catalog(endpoints, CATALOG_OUT)  # Stage 3: catalog markdown
    missing = write_diff(endpoints, used_ops, DIFF_OUT)  # Stage 4: diff markdown
    if args.catalog_only:  # Stop early for catalog-only runs
        LOG.info("catalog-only run complete: %d endpoints, %d missing", len(endpoints), len(missing))  # Final report
        return 0
    capped = missing if not args.limit else missing[: args.limit]  # Apply optional cap
    assignment = allocate_spec_numbers(capped, SPECS_DIR)  # Stage 5: number allocation
    written = generate_specs(capped, assignment, SPECS_DIR)  # Stage 6: spec generation
    LOG.info(
        "Pipeline complete: %d endpoints in spec, %d missing, %d new spec dirs written",
        len(endpoints),
        len(missing),
        written,
    )  # Closing report
    return 0  # Success exit


if __name__ == "__main__":  # Standard CLI dispatch
    raise SystemExit(main())  # Propagate exit code to OS
