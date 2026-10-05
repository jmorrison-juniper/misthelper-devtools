# `misthelper-devtools` agent instructions

This file holds the rules that apply to `misthelper-devtools` only. The rules that apply to each
repository of this owner are in `AGENTS.md` at the repository root. Read `AGENTS.md` first. This
file adds to it, and it does not hold a copy of a rule from it. Where the two files disagree, obey
`AGENTS.md` for a writing rule, a safety rule, or a security rule.

## What this repository is

`misthelper-devtools` is a Python package of command-line tools and a set of shared GitHub Actions
workflows for MistHelper and the related Mist repositories. The tools read the source, the tests,
the prose, the diagrams, and the links of a consumer repository, and they write reports for an
engineer. A customer never runs this code, and no shipped product carries it. The audience is the
engineers who work on the Mist repositories. The code runs on Windows, macOS, and Linux, and CI
runs on Ubuntu. This repository also holds the canonical agent instruction templates that each
repository copies.

## Language and environment

Python 3.13 or newer. Hatchling builds the wheel from `src/misthelper_devtools/`. Four runtime
dependencies exist: `libcst`, `packaging`, `PyYAML`, and `requests`. Each other import comes from
the standard library. A new worktree has no environment, so make one first:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,grammar]"
python -m spacy download en_core_web_sm
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. The `grammar` extra installs spaCy, which
raises the accuracy of the STE linter. The licensed ASD-STE100 dictionary lives at the user level,
for example `~/.local/share/ste-linter/ste_dictionary.json`. Without it, the linter runs the
structural rules only.

## Local gates

Run each gate from the repository root, in the active environment. CI runs the same commands.

| Gate | Command | Expected result |
| - | - | - |
| Lint | `python -m ruff check .` | `All checks passed!` |
| Format | `python -m black --check .` | `N files would be left unchanged` |
| Types | `python -m mypy -p misthelper_devtools --config-file pyproject.toml` | `Success: no issues found` |
| Tests | `python -m pytest` | Each test passes. CI adds `--cov=misthelper_devtools`. |
| Prose | `ste-linter --config .ste-linter.toml --min-score 80 README.md AGENTS.md .github/copilot-instructions.md docs/*.md documentation/*.md src/misthelper_devtools/*/README.md` | Each file reports `PASS`. |
| Workflows | `actionlint` (CI uses 1.7.12) | No output. |
| Links | `markdown-link-check` | `0 broken link(s)` |
| Hooks | `pre-commit try-repo . --files README.md` | Each hook reports `Passed`. |

CI also runs the Mermaid lint action on `tests/fixtures/mermaid/` and CodeQL for Python. Branch
protection on `main` requires 11 checks: `lint`, `format`, `types`, `tests`, `actionlint`,
`ste-lint / STE compliance`, `pre-commit hooks`, `mermaid-lint-action`, `ci-result`,
`codeql / Analyze (python)`, and `CodeQL`. The protection is strict, so rebase onto `main` before
the merge. A full CI run takes about 90 seconds of runner time, and the repository is public.

## Architecture and conventions

`src/misthelper_devtools/` holds one module or one sub-package for each tool. The sub-packages are
`ste_linter`, `test_quality_analyzer`, `compliance_analyzer`, `refactor_analyzer`, `symbol_diff`,
and `juniper_skills`. Each other tool is one file at the package root. A tool that installs as a
command has a line in `[project.scripts]` of `pyproject.toml` and a row in the command table of
`docs/tooling-guide.md`. A tool reads the repository that the operator names: resolve the root with
`resolve_repository_root` from `repository_root.py`, and never read the install directory.

The folder `scripts/juniper_skills/` holds the thin entry points of the skill factory, and Ruff
excludes it. The folder `tests/unit/` holds the unit tests by package, `tests/tools/` holds the
coverage tests of the analyzers, and `tests/fixtures/` holds sample inputs. The root of `tests/`
holds the guard tests that read the repository files: the workflows, the hook manifest, and the
templates.

The files `.github/workflows/reusable-*.yml` are the 10 shared workflows. The header comment of
each one holds the caller example and the permissions. The folder `.github/actions/mermaid-lint/`
holds the composite action, and the file `.pre-commit-hooks.yaml` holds the hook manifest. A consumer pins a full commit SHA
with a `# vX.Y.Z` comment, so a merged change reaches a consumer only at its next pin.

Hot files that only one agent changes at a time: `pyproject.toml`, `docs/tooling-guide.md`,
`.github/workflows/ci.yml`, and each `.github/workflows/reusable-*.yml`.

Conventions of the code:

- The line length is 120. Ruff selects `E`, `F`, `W`, `I`, `UP`, `B`, `G`, `PLW1514`, and
  `RUF100`. `PLW1514` means that each text `open()` names `encoding="utf-8"`, because Windows
  opens a text file as cp1252.
- Mypy runs in strict mode over the full package with `explicit_package_bases`.
- A module starts with a docstring that gives the purpose and a `Usage::` block. A console script
  exposes `main(argv: list[str] | None = None) -> int`.

- The fixtures under `src/misthelper_devtools/test_quality_analyzer/fixtures/` hold deliberate
  defects. Pytest skips them through `norecursedirs`, and Ruff, Black, mypy, and CodeQL exclude
  them. Do not repair a fixture.
- The `norecursedirs` list in `pyproject.toml` replaces the pytest default list, so it restates
  `*.egg`, `.*`, `build`, and `dist`. Keep the four entries.

Documentation rules of this repository:

- The file `README.md` keeps six sections: What, How, Where, When, Why, and Who. It links to
  `docs/tooling-guide.md` for each detail.
- The guide `docs/tooling-guide.md` holds the command table, the shared workflow table, and one
  "Upgrade from release X to release Y" section for each release.
- The inventory `documentation/mist-repository-tooling-inventory.md` lists the consumers of each
  tool, and the STE gate grades the `README.md` of each tool package.

## Safety in this repository

- Do not import product code. No module imports `MistHelper` or `mistapi`, except as sample text
  in the analyzer fixtures. The dependency runs from a consumer to this package only, so a reverse
  import puts development tooling into a shipped product. Issue jmorrison-juniper/MistHelper#3466
  records the four modules that this rule removed.

- The ASD-STE100 dictionary has a copyright. Do not commit `data/ste_dictionary.json` or an extract
  of it. The `data/` folder is in `.gitignore`.

- A change to a reusable workflow or to the Mermaid action reaches each consumer. Change one shared
  workflow in one pull request, pass `self-test.yml`, and write the upgrade section in the release.
- The command `worktree-cleanup` runs in dry-run mode by default. Add `--apply` only after you read
  the plan.

- The file `templates/agent-instructions/AGENTS.md` is the canonical generic file for each
  repository. The root `AGENTS.md` is a byte-identical copy, and a test asserts that. Do not edit
  the root copy. Change the template in its own pull request, then re-sync each repository.

## Git and GitHub in this repository

- Type labels: `bug`, `enhancement`, and `documentation`. Status label: `in-progress`. Dependabot
  uses `dependencies` and `github_actions`. This repository has no scope labels, so a pull request
  carries one type label and, while the work is open, `in-progress`.
- Commit scope: the tool name, the workflow name, or the folder, for example `feat(ste-linter)` or
  `fix(auto-merge)`. A documentation change uses `docs:` with no scope.

- No changelog file and no fragments exist. A release pull request, titled
  `chore(release): version X.Y.Z`, bumps the version in `pyproject.toml`, adds the upgrade section
  to `docs/tooling-guide.md`, points the README upgrade link at it, and changes each `vX.Y.Z`
  comment. The owner tags `vX.Y.Z` on `main` after the merge. A feature pull request writes no
  upgrade section. It describes the change that a user sees in its body for the release.

- No pull request template exists. The body holds a Summary and a Validation section with the
  commands that you ran and their results. A shared workflow change links its `self-test.yml`
  run.
- The repository has no `auto-merge` label and no auto-merge workflow. After the 11 required
  checks pass, merge with `gh pr merge <number> --squash --delete-branch`. The squash commit takes
  the pull request title.

- Dependabot runs weekly for the GitHub Actions of the workflows and of the Mermaid action, and
  for the npm packages of the Mermaid action. It groups the updates and uses the `ci` and `build`
  prefixes.
- The workflow `codeql.yml` calls `reusable-codeql.yml` for Python with
  `.github/codeql/codeql-config.yml`, which ignores the fixtures. The workflow `self-test.yml`
  runs each shared workflow when a `reusable-*.yml` file, a fixture, or one of its two commands
  changes.

## Known pitfalls

- Release 0.3.0 installed the top-level packages `tools` and `src`, and a consumer with its own
  `src` package imported the wrong code. Pull request #10 moved each module into
  `misthelper_devtools`. Do not add a second top-level package to the wheel.
- The command `markdown-link-check` reported each link that starts with `/` as a missing file.
  GitHub starts such a link at the repository root, and pull request #30 made the checker do the
  same.

- The auto-merge dispatch read a stale run list and started duplicate runs. Pull request #33 made
  it look for a run on the tip commit, 5 times, 20 seconds apart.

- The auto-merge dispatch failed on Bash 3, the shell of macOS. Pull request #45 repaired it. Test
  a shared shell script with Bash 3 and with Bash 5.
- The type gate once read the `juniper_skills` package only, because the other modules held 51
  annotation defects. Pull request #17 repaired them and widened the gate to the full package.
  Keep the full scope.

- A `GITHUB_TOKEN` cannot write a workflow file, so auto-merge of a pull request that edits
  `.github/workflows/` fails in a consumer with no reason on the pull request. The consumer merges
  that pull request by hand.
- Two checkouts of this repository can hold different `AGENTS.md` copies. Run
  `agent-instructions-check --canonical templates/agent-instructions/AGENTS.md` before you commit.

## Key files

| File | Purpose |
| - | - |
| `pyproject.toml` | The version, the console scripts, and the Ruff, Black, mypy, pytest, and coverage settings. |
| `.ste-linter.toml` | The STE linter settings of this repository, from the template. |
| `README.md` | The six-section overview. |
| `docs/tooling-guide.md` | The detailed guide, the upgrade notes, and the agent instructions procedure. |
| `documentation/ASD-STE100_writing-guide.md` | The STE writing guide that the linter measures. |
| `documentation/mist-repository-tooling-inventory.md` | The tools and shared workflows of each consumer. |
| `.github/workflows/ci.yml` | The gates and the `ci-result` aggregator. |
| `.github/workflows/self-test.yml` | The dry run of each shared workflow. |
| `.github/workflows/reusable-*.yml` | The 10 shared workflows that the consumers call. |
| `.github/actions/mermaid-lint/` | The shared Mermaid syntax lint action. |
| `.pre-commit-hooks.yaml` | The hook manifest for the consumers. |
| `templates/agent-instructions/` | The canonical agent instruction files. |
| `src/misthelper_devtools/repository_root.py` | The root resolver that each tool uses. |
| `tests/test_shared_workflow_guards.py` | The contract tests of the shared workflows. |

## External resources

- MistHelper, the primary consumer: https://github.com/jmorrison-juniper/MistHelper
- actionlint: https://github.com/rhysd/actionlint
- pre-commit: https://pre-commit.com
- ASD-STE100 Issue 9. The licensed PDF is not in the repository.
