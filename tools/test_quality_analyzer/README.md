# Test Quality Analyzer

Static-analysis auditor for the MistHelper test suite. Flags weak, tautological, and missing tests without executing any test code.

## Quick usage

```bash
# Full audit run against tests/
test-quality-analyzer --roots tests --report tools/test_quality_analyzer/output/report.json

# Gate mode (CI): exit 1 if any new finding vs the committed baseline
test-quality-analyzer --gate

# Overwrite the baseline after intentionally accepting new findings
test-quality-analyzer --write-baseline

# Drop baseline entries whose file left the scan set, and keep every other entry
test-quality-analyzer --prune-baseline
```

## Stale baseline entries

The analyzer scans a file only when the name starts with `test_` or ends with
`_test.py`. A `conftest.py` file and a helper module fail both rules, so the
analyzer skips them. A baseline entry for such a file can never match a finding
again. The run reports the count under `stale_baseline_entries`.

Run `test-quality-analyzer --prune-baseline` to drop those entries. The command
keeps every entry that the scan can still reach, writes the baseline in the same
canonical form, and exits 0. Issue #1769 recorded 6 stale entries that this
command removed. Run the command again after a test file moves or leaves the
scan set, so the count stays at 0.

`--prune-baseline`, `--gate`, and `--write-baseline` select a mode. Pass one
mode only. Two modes together exit 2.

The console script is registered in `pyproject.toml` under `[project.scripts]` and resolves to `tools.test_quality_analyzer.__main__:main`. The module also runs directly via `python -m tools.test_quality_analyzer`.

## Documentation map

For canonical usage, walkthroughs, and end-to-end scenarios, see the SpecKit artefacts under `specs/1019-test-quality-analyzer/`:

- `quickstart.md` — Scenarios A–F: full audit, gate mode, baseline write, rule disable, Mist-API include, config overrides.
- `spec.md` — functional requirements (FR-001 … FR-024) and success criteria (SC-001 … SC-006).
- `plan.md` — implementation phases and architectural boundaries.
- `data-model.md` — dataclass shapes (`Finding`, `Report`, `Baseline`, `BaselineDiff`, `ConfigSnapshot`, `SkippedFile`, `ParseError`).
- `contracts/cli.md` — authoritative CLI flag surface, exit-code semantics, and stdout summary contract.
- `contracts/report.schema.json` — canonical JSON report schema.
- `contracts/config.schema.md` — TOML config surface and merge semantics.

## Detectors

Five module-level detectors register themselves with `DetectorRegistry` on import (see `__main__.py`):

| Module | Category | Purpose |
|---|---|---|
| `detection/untested.py` | `untested` | Cross-file: production symbols with no covering `test_*`. |
| `detection/weak_assertion.py` | `weak_assertion` | Bare asserts, `is not None`, echo mocks, `pytest.raises(Exception)`. |
| `detection/tautological.py` | `tautological` | Assertions that can never fail (e.g. `assert 1 == 1`). |
| `detection/missing_failure_mode.py` | `missing_failure_mode` | Happy-path-only tests missing error/exception coverage. |
| `detection/missing_edge_case.py` | `missing_edge_case` | Heuristic — no zero, empty, or negative test inputs. |

## Configuration

`config.toml` holds the effective config. Overrides via CLI flags:

- `--disable-rule RULE_ID` — repeatable; skips one rule at runtime.
- `--include-mist-api` — bypass the `src/api/` + `mistapi` exclusion predicate.
- `--roots PATH …` — one or more test root directories (default: `tests`).
- `--baseline ""` — disable baseline comparison for one run.
- `--prune-baseline` — drop stale baseline entries and exit 0 (issue #1769).

## Outputs

- `output/report.json` — machine-readable envelope validated against `report.schema.json`.
- `output/summary.md` — human-readable Markdown summary.
- One-line stdout summary: `test_quality_analyzer: N findings (C/H/M/L), K skipped, P parse errors`.

`output/` is gitignored; the committed `baseline.json` records the accepted findings.

## Running the analyzer's own tests

```bash
python -m pytest tests/tools/test_quality_analyzer -q
```

Golden fixtures under `fixtures/good/` and `fixtures/bad/` are per-file-ignored in `pyproject.toml` — do not "fix" them; they are the material under test.
