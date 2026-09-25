# MistHelper development tooling

This repository holds the in-house testing tooling and the development tooling
for the [MistHelper](https://github.com/jmorrison-juniper/MistHelper) project.

A customer never runs the code in this repository. The tooling reads the
MistHelper source, it measures the quality of that source, and it writes
reports for an engineer. The shipped MistHelper wheel and the shipped
MistHelper container carry none of it.

## What is here

| Path | Purpose |
| - | - |
| `tools/ste_linter/` | The Simplified Technical English linter. It scores prose and reports each rule violation. |
| `tools/test_quality_analyzer/` | The test quality analyzer. It finds a tautological test, a weak assertion, and a missing failure mode. |
| `tools/compliance_analyzer/` | The compliance analyzer. It measures the inline comment rule and the action logging rule. |
| `tools/refactor_analyzer/` | The refactor analyzer. It builds the module graph and it reports a decomposition candidate. |
| `tools/symbol_diff/` | The symbol comparison tool. It reports a module-level name that a sweep lost. |
| `tools/*.py` | Single-file tools. They audit citations, guard proofs, prompts, dependencies, and performance. |
| `src/juniper_skills/` | The skill factory. It reads a Juniper document set and it writes a skill package. |
| `scripts/juniper_skills/` | The command-line entry points for the skill factory. |
| `tests/` | The test suite for every tool above. |

## Install

Python 3.13 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

The STE linter reads grammar with the standard library. For a higher accuracy,
install the optional group and the English model.

```powershell
python -m pip install -e ".[dev,grammar]"
python -m spacy download en_core_web_sm
```

## Run a tool

```powershell
python -m tools.ste_linter path\to\file.md
python -m tools.test_quality_analyzer tests\
python -m tools.compliance_analyzer src\
python -m tools.symbol_diff --base main path\to\file.py
```

Two tools also install as a command.

```powershell
ste-linter path\to\file.md
test-quality-analyzer tests\
```

## Run the tests

```powershell
python -m pytest
```

The analyzer fixtures under `tools/test_quality_analyzer/fixtures/` hold the
defects that the detectors find. Pytest never collects them, and the linter
never repairs them.

## Quality gates

```powershell
python -m ruff check .
python -m black --check .
python -m mypy src --config-file pyproject.toml
python -m pytest
```

The type gate reads `src/` only. That scope matches the MistHelper repository,
which never type-checked `tools/`. The `tools/` tree holds 60 known annotation
defects. A separate change repairs them and widens the scope.

## Relationship to MistHelper

MistHelper depends on this repository for its quality gates. This repository
does not depend on MistHelper. No module here imports `MistHelper`, `mistapi`,
or any other product module.

Warning: never add a product import to this repository. The dependency runs in
one direction only. A reverse import would put development tooling back into
the shipped product.

## Writing style

Every document, comment, and message in this repository follows Simplified
Technical English. The `tools/ste_linter/` package measures that rule, and it
measures the same rule for the MistHelper repository.

## License

MIT. See [LICENSE](LICENSE).
