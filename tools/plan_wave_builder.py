"""Build per-spec metadata + prompt files for the next plan-agent wave."""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(os.path.expandvars(r"%USERPROFILE%\.copilot\session-state\270b3bdf-8f07-470a-9757-690a1d246ec2\session.db"))
REPO = Path(__file__).resolve().parents[1]
FILES = Path(os.path.expandvars(r"%USERPROFILE%\.copilot\session-state\270b3bdf-8f07-470a-9757-690a1d246ec2\files"))

PROMPT_TEMPLATE = """Task: Generate SpecKit Phase 0 + Phase 1 artifacts for a single Mist API GET endpoint.

You are completing the SpecKit /speckit.plan workflow for ONE feature spec only.

## Inputs (all absolute paths on Windows)

- **Spec dir**: `{spec_path}`
- **Spec file**: `{spec_path}\\spec.md` -- already exists, READ IT FIRST
- **Repo root**: `{repo}`
- **Constitution**: `{repo}\\.specify\\memory\\constitution.md`
- **Plan template**: `{repo}\\.specify\\templates\\plan-template.md`
- **MistHelper source**: `{repo}\\MistHelper.py` -- the ~28K-line monolith you are extending
- **Enriched per-endpoint docs**: `{repo}\\documentation\\api\\` -- has METHOD_path.md files with full parameter, response, and SDK information. For this endpoint look for a file matching the path tokens (drop `/api/v1/`, replace `/` and `{{}}` with `_`, e.g. `/api/v1/orgs/{{org_id}}/sites` -> `GET_orgs_org_id_sites.md` under `documentation\\api\\orgs\\`).
- **Reference plan**: `{repo}\\specs\\014-device-utility-commands\\plan.md` -- the quality bar
- **Reference plan (Phase 1 quality)**: `{repo}\\specs\\500-mist-get-org-license-async-claim-status\\plan.md` -- pre-validated example of this exact workflow

## Endpoint context (from spec.md)

- **operationId**: `{op}`
- **OpenAPI path**: `{path}`
- **HTTP method**: GET
- **Tag**: `{tag}`
- **mistapi SDK module**: `{module}`

## Your deliverables (all under the Spec dir above)

Create these files. Use absolute paths in every create/edit call.

1. **`plan.md`** -- Implementation plan following `.specify/templates/plan-template.md`. Must contain ALL sections matching the reference plan's bar:
   - Header (Branch / Date / Spec link)
   - Summary (2-4 sentences describing the new menu item and approach)
   - Technical Context (Language/Version, Primary Dependencies, Storage, Testing, Target Platform, Project Type, Performance Goals, Constraints, Scale/Scope) -- Python 3.13+, mistapi 0.59+, podman, Windows 11, ~28K-line monolith. NO "NEEDS CLARIFICATION" entries.
   - Constitution Check with explicit PASS / EXCEPTION verdict on EACH of the 7 principles (I-V plus VI Inline Comments and VII Action Logging, all NON-NEGOTIABLE).
   - Pre-Phase 0 Gate verdict (PASS / FAIL with justification)
   - Project Structure documentation (this feature's file tree)
   - Project Structure source code (where the new menu method lives in MistHelper.py -- name the class to extend or justify a new one)
   - Post-Phase 1 Re-Check
   - Post-Phase 1 Gate verdict
   - Complexity Tracking table -- only fill if a Constitution exception was needed.

2. **`research.md`** -- Phase 0 research output. Sections (each using Decision / Rationale / Alternatives Considered format):
   - Research Task 1: SDK function signature & behavior (read documentation/api/ for this path)
   - Research Task 2: Primary Key Strategy (natural_pk / composite_pk / auto_increment_with_unique)
   - Research Task 3: Output filename and SQLite table
   - Research Task 4: Menu category placement and next available menu number
   - Research Task 5: Required user prompts (which IDs from the user, which from .env)

3. **`data-model.md`** -- Phase 1 entity & state model:
   - Every entity returned by the endpoint (from response schema)
   - For each entity: fields, types, primary key(s), foreign keys
   - State transitions or "N/A -- read-only endpoint"
   - SQLite DDL snippet (CREATE TABLE)
   - ENDPOINT_PRIMARY_KEY_STRATEGIES dict entry

4. **`quickstart.md`** -- Phase 1 dev quickstart:
   - How to run this menu item locally
   - Required .env variables
   - Expected data/ output filename
   - Example invocation with prompts
   - Quality gates: py_compile, ruff check, black --check, python MistHelper.py --test

5. **`contracts/{contract_filename}.md`** -- Phase 1 endpoint contract:
   - Full HTTP contract: METHOD, URL template, required path params, query params, headers
   - Full response schema (200 success) from documentation/api/
   - Error responses (401, 403, 404, 429) and MistHelper handling
   - Exact mistapi Python call signature

## Hard requirements (per the Constitution)

- No NEEDS CLARIFICATION markers in plan.md -- make decisions.
- All seven principles must have explicit PASS/EXCEPTION verdicts.
- ASCII-only output (no emoji).
- Reference real files by absolute or repo-relative path.
- Cite the spec.md in the plan header.
- The plan must mention safe_input(), DataExporter.write_with_format_selection, ENDPOINT_PRIMARY_KEY_STRATEGIES, and an explicit menu number proposal.

## Out of scope (do NOT do these)

- Do NOT generate tasks.md.
- Do NOT modify spec.md.
- Do NOT modify MistHelper.py.
- Do NOT modify any other spec dir.
- Do NOT update .github/copilot-instructions.md SpecKit markers.
- Do NOT commit, push, or run git operations.

## Process

1. View the spec.md to confirm endpoint details.
2. Glob `documentation/api/` for files matching this endpoint's path. Use the enriched doc to ground Research Task 1.
3. View the reference plan briefly to match the structural depth (do NOT copy text verbatim).
4. View the constitution sections you cite.
5. Write all five artifacts. Each must be substantive -- no placeholder text, no NEEDS CLARIFICATION, no TODOs.
6. Return a short summary listing the files you created with their byte sizes.

## Quality gate

Before you finish, verify each file exists and is non-empty by listing the spec dir contents."""


def op_to_snake(op: str) -> str:
    """Convert operationId to snake_case for contract filename."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower()
    return re.sub(r"[^a-z0-9_]+", "_", s).strip("_")


def main() -> int:
    """Generate per-spec prompt files for the next wave of plan agents."""
    wave_size = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT spec_num, spec_dir, operation_id FROM plan_runs " "WHERE status='pending' ORDER BY spec_num LIMIT ?",
        (wave_size,),
    ).fetchall()
    manifest = []
    for spec_num, spec_dir, op in rows:
        spec_path = REPO / "specs" / spec_dir
        spec_md_path = spec_path / "spec.md"
        text = spec_md_path.read_text(encoding="utf-8")
        path_m = re.search(r"\*\*Path\*\*: `(.+?)`", text)
        tag_m = re.search(r"\*\*Tag\*\*: `(.+?)`", text)
        mod_m = re.search(r"\*\*mistapi SDK module\*\*: `(.+?)`", text)
        prompt = PROMPT_TEMPLATE.format(
            spec_path=str(spec_path),
            repo=str(REPO),
            op=op,
            path=path_m.group(1) if path_m else "",
            tag=tag_m.group(1) if tag_m else "",
            module=mod_m.group(1) if mod_m else "",
            contract_filename=op_to_snake(op),
        )
        prompt_file = FILES / f"prompt_{spec_num}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        manifest.append(
            {
                "spec_num": spec_num,
                "spec_dir": spec_dir,
                "operation_id": op,
                "agent_id": f"plan-{spec_num}-{op_to_snake(op)[:30]}",
                "prompt_file": str(prompt_file),
                "prompt_chars": len(prompt),
            }
        )
    (FILES / "wave_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest)} prompt files + wave_manifest.json")
    for entry in manifest:
        print(f"  {entry['spec_num']} {entry['operation_id']} -> {entry['agent_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
