# MistHelper development tooling

This repository holds the in-house testing tooling and the development tooling
for the [MistHelper](https://github.com/jmorrison-juniper/MistHelper) project
and for the related Mist repositories. It also holds the shared GitHub Actions
workflows that those repositories call.

A customer never runs the code in this repository. The tooling reads the
source of a Mist repository, it measures the quality of that source, and it
writes reports for an engineer. No shipped wheel and no shipped container
carries it.

## What is here

The installable package is `misthelper_devtools`. Each tool is a module or a
sub-package of it, so a tool never shares a top-level name with the code of a
consumer repository.

| Path | Purpose |
| - | - |
| `src/misthelper_devtools/ste_linter/` | The Simplified Technical English linter. It scores prose and reports each rule violation. |
| `src/misthelper_devtools/test_quality_analyzer/` | The test quality analyzer. It finds a tautological test, a weak assertion, and a missing failure mode. |
| `src/misthelper_devtools/compliance_analyzer/` | The compliance analyzer. It measures the inline comment rule and the action logging rule. |
| `src/misthelper_devtools/refactor_analyzer/` | The refactor analyzer. It builds the module graph and it reports a decomposition candidate. |
| `src/misthelper_devtools/symbol_diff/` | The symbol comparison tool. It reports a module-level name that a sweep lost. |
| `src/misthelper_devtools/complexity_gate.py` | The complexity gate. It reads a `radon cc -j` report and fails on a block above the limit. |
| `src/misthelper_devtools/wan_port_report.py` | The WAN port report. It reads a saved Mist port list and prints the gateway WAN port state. |
| `src/misthelper_devtools/*.py` | Single-file tools. They audit citations, guard proofs, prompts, dependencies, and virtual environments. |
| `.github/workflows/reusable-*.yml` | The shared workflows that the Mist repositories call. See [Shared workflows](#shared-workflows). |
| `.pre-commit-hooks.yaml` | The pre-commit hooks that the Mist repositories use. See [Pre-commit hooks](#pre-commit-hooks). |
| `src/misthelper_devtools/juniper_skills/` | The skill factory. It reads a Juniper document set and it writes a skill package. |
| `scripts/juniper_skills/` | The command-line entry points for the skill factory. |
| `tests/` | The test suite for every tool above. |
| `documentation/mist-repository-tooling-inventory.md` | The tooling of each Mist repository, and the part that moved here. |

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
python -m misthelper_devtools.ste_linter path\to\file.md
python -m misthelper_devtools.test_quality_analyzer --roots tests
python -m misthelper_devtools.compliance_analyzer src\
python -m misthelper_devtools.symbol_diff --base main path\to\file.py
```

Nineteen tools also install as a command.

| Command | Module |
| - | - |
| `bandit-exclude-check` | `misthelper_devtools.bandit_exclude_check` |
| `check-citations` | `misthelper_devtools.check_citations` |
| `codeql-verdict-register` | `misthelper_devtools.codeql_verdict_register` |
| `complexity-gate` | `misthelper_devtools.complexity_gate` |
| `compliance-analyzer` | `misthelper_devtools.compliance_analyzer` |
| `diagram-refs` | `misthelper_devtools.diagram_refs` |
| `exclusion-drift` | `misthelper_devtools.exclusion_drift` |
| `guard-proof-audit` | `misthelper_devtools.guard_proof_audit` |
| `markdown-link-check` | `misthelper_devtools.markdown_link_check` |
| `pytest-chunks` | `misthelper_devtools.pytest_chunks` |
| `refactor-analyzer` | `misthelper_devtools.refactor_analyzer` |
| `speckit-task-audit` | `misthelper_devtools.speckit_task_audit` |
| `ste-linter` | `misthelper_devtools.ste_linter` |
| `stranded-branch-report` | `misthelper_devtools.stranded_branch_report` |
| `symbol-diff` | `misthelper_devtools.symbol_diff` |
| `test-quality-analyzer` | `misthelper_devtools.test_quality_analyzer` |
| `venv-health` | `misthelper_devtools.venv_health` |
| `wan-port-report` | `misthelper_devtools.wan_port_report` |
| `worktree-cleanup` | `misthelper_devtools.worktree_cleanup` |

```powershell
ste-linter path\to\file.md
test-quality-analyzer --roots tests
radon cc . -j | complexity-gate --max 15
check-citations src tests
```

### CI check command notes

`codeql-verdict-register` reads the repository name from `--repository`, then
from `GITHUB_REPOSITORY`, then from `gh repo view`. It writes no MistHelper
default. MistHelper can keep today's register check with:

```powershell
codeql-verdict-register check
```

`diagram-refs` has generic defaults. It scans `documentation/diagrams/`,
`README.md`, and `src/`. MistHelper must name its top-level file and its
product words:

```powershell
diagram-refs --source-files MistHelper.py src/ --allow MistHelper --allow InfrastructureCore --allow ConfigObjects --allow APIFetching --allow DataProcessing --allow OrgExporters --allow SiteExporters --allow GatewayExporters --allow WebSocketNet --allow UITUI --allow SystemRegistry --allow OrgExporter --allow SiteExporter --allow GatewayExporter --allow MigrationManager
```

`exclusion-drift` reads `quality_gate_exclusions.json` from the repository root
by default. Use `--root` for a different checkout. Use `--manifest` for a
different manifest. MistHelper can keep the drift job with:

```powershell
exclusion-drift --format github --output exclusion-drift.json
```

`bandit-exclude-check` checks that `[tool.bandit].exclude_dirs` has each
spelling for path separators. Each `--include-sample` path must stay in the
Bandit scan scope. The command has no default sample, and it imports Bandit
only for a sample check. MistHelper keeps its Bandit guard with:

```powershell
bandit-exclude-check --include-sample ./src/utils/zen_city_metadata.py --include-sample .\src\utils\zen_city_metadata.py
```

### Clean git worktrees

`worktree-cleanup` removes only safe targets. It uses dry-run mode by default.
Add `--apply` only after you review the plan.

```powershell
worktree-cleanup merged --base main
worktree-cleanup --apply merged --base main --delete-branch
worktree-cleanup stale-admin
worktree-cleanup --apply stale-admin
```

The `merged` mode keeps the main worktree, a dirty worktree, and a worktree
whose branch is not merged into the base branch. Use repeatable `--path` values
to limit the list of candidates. The `stale-admin` mode removes stale admin
directories in `.git\worktrees`, then runs `git worktree prune`.

A squash merge counts as a merge. For a branch that git does not show as
merged, the tool writes a probe commit. The probe holds the tree of the branch
on top of the merge base. Then `git cherry` looks for a base commit with the
same patch. The tool keeps a branch that is only partly on the base.

### Run pytest in chunks

`pytest-chunks` runs caller-named pytest paths in bounded chunks. The first
chunk runs the named paths without the `--split` folders. Each `--split` folder
then runs as one chunk for each child folder, plus batches of eight for its
top-level test files.

The command runs every chunk by default. Add `-x` to stop after the first
failed chunk. The command gives `--timeout` to pytest only when the environment
has the `pytest-timeout` plugin. If a chunk runs for more than the
`--chunk-timeout` value in seconds, the command stops it with status 124.

```powershell
pytest-chunks -x tests\unit --split tests\unit\upgrade_portal
pytest-chunks -x tests\contract tests\guardrails tests\integration --split tests\contract\upgrade_portal --split tests\integration\upgrade_portal
```

### Check Markdown links

`markdown-link-check` reads tracked Markdown files with git. It reports
repository-local links that point to a missing file, folder, or Markdown anchor.
The command checks every tracked Markdown file by default. Use repeatable
`--exclude` globs to skip a tree, such as a wiki mirror with links that only
the wiki can resolve.

```powershell
markdown-link-check
markdown-link-check --exclude 'documentation/wiki/**'
markdown-link-check documentation
```

## Upgrade from release 0.3.0 to release 0.4.0

Release 0.3.0 installed two top-level packages with generic names, `tools` and
`src`. A consumer repository with its own `src` package could import the wrong
code. Every module is now inside `misthelper_devtools`. Change each import and
each `python -m` command of a consumer.

| Release 0.3.0 | Release 0.4.0 |
| - | - |
| `from tools.ste_linter.cli import LinterCLI` | `from misthelper_devtools.ste_linter.cli import LinterCLI` |
| `python -m tools.check_citations src tests` | `check-citations src tests` |
| `python -m tools.speckit_task_audit` | `speckit-task-audit` |
| `python -m tools.venv_health` | `venv-health` |
| `from src.juniper_skills.install import SkillInstaller` | `from misthelper_devtools.juniper_skills.install import SkillInstaller` |

The names of the console scripts `ste-linter`, `test-quality-analyzer`,
`complexity-gate`, and `wan-port-report` did not change.

The package no longer holds a test quality baseline. By default,
`test-quality-analyzer` reads `.github/test-quality-baseline.json` in the
current directory. Before a consumer runs `test-quality-analyzer --gate`, it must
commit its own baseline file. The
[analyzer README](src/misthelper_devtools/test_quality_analyzer/README.md#baseline)
gives the procedure.

## Use a tool from another repository

A Mist repository installs this package from GitHub. Pin a full commit SHA, so
that a change here cannot change the gate result of the consumer by surprise.

```text
# requirements-dev.txt
misthelper-devtools @ git+https://github.com/jmorrison-juniper/misthelper-devtools@<commit-sha>
```

The package needs Python 3.13 or newer.

## Pre-commit hooks

This repository supplies hooks for the [pre-commit](https://pre-commit.com)
framework. The `ste-linter` hook runs the STE linter on each Markdown file and
each Python file that a commit changes. The `markdown-link-check` hook checks
links in changed Markdown files. The links must point to files and anchors in
the repository. To use these hooks, add this repository to
`.pre-commit-config.yaml` at a release tag:

```yaml
repos:
  - repo: https://github.com/jmorrison-juniper/misthelper-devtools
    rev: v0.5.0
    hooks:
      - id: ste-linter
        args: [--config, .ste-linter.toml, --min-score, "80"]
        exclude: ^tests/fixtures/
      - id: markdown-link-check
```

pre-commit installs this package at the tag in a separate environment. Use the
release that `requirements-dev.txt` pins. Then the hook and the CI gate give
the same result.

## Shared workflows

Each shared workflow starts with `on: workflow_call`. A Mist repository keeps
its own triggers, and it calls the shared job with `uses:`. The header of each
file holds a full caller example and the permissions that the caller must give.

| Workflow | Purpose |
| - | - |
| `reusable-container-image.yml` | Builds a multi-arch image with Buildx and can push it to GHCR. The caller gives the tag rules. |
| `reusable-codeql.yml` | Runs one CodeQL analysis for each language of the caller and uploads the alerts to code scanning. |
| `reusable-quality-gate-issues.yml` | Opens one issue for each failed gate and closes it after the gate passes. |
| `reusable-auto-merge.yml` | Enables auto-merge for a labeled pull request. After the merge, it starts the main workflows that the merge did not start. With `report-orphaned-push`, it writes a notice on a merged pull request when a later push adds a commit to its branch. |
| `reusable-close-linked-issues.yml` | Closes each issue that a merged pull request links. A sweep finds the auto-merged pull requests. |
| `reusable-copilot-assign.yml` | Assigns the Copilot cloud agent to an issue with a user token. It adds the in-progress label only when the agent is an assignee, and otherwise writes the cause on the issue. |
| `reusable-stranded-branch-report.yml` | Runs `stranded-branch-report` and keeps one issue with each branch that holds work with no pull request. It closes the issue after a pull request holds every branch. |
| `reusable-ste-lint.yml` | Runs `ste-linter` on the documentation files of the caller. It fails when a file scores below the threshold, and it writes the report to the job summary. |
| `reusable-python-quality-gates.yml` | Runs each Python gate that the caller turns on: ruff, ruff format, black, mypy, pytest, bandit, pip-audit, pylint, radon with `complexity-gate`, vulture, pydocstyle, interrogate, and pydoclint. Its `results` output goes to `reusable-quality-gate-issues.yml`. |

A caller pins the full commit SHA of a release and writes the release tag in a
comment:

```yaml
jobs:
  build-and-push:
    permissions:
      contents: read
      packages: write
    uses: jmorrison-juniper/misthelper-devtools/.github/workflows/reusable-container-image.yml@<commit-sha> # v0.4.0
```

To upgrade a consumer, read the release notes, then change the SHA and the
comment together. The `self-test.yml` workflow runs each shared workflow in
this repository before a release.

Dependabot can make the upgrade. A consumer with a `github-actions` entry in
`.github/dependabot.yml` gets a pull request that changes the SHA and the
comment together. Dependabot waits 3 days after a release before it opens that
pull request. Dependabot does not change the pin in `requirements-dev.txt`, so
change that pin by hand.

Dependabot also updates the actions that the shared workflows use. The
Dependabot of a consumer cannot see these actions, so the configuration in
this repository updates them. A consumer gets such an update after the next
release.

Warning: a missing permission on the caller job can cause GitHub to stop the
run before it starts. Give the caller job each permission that the shared job
asks for.

Warning: an auto-merge of a pull request that edits `.github/workflows/` can
fail with no reason on the pull request. Merge that pull request by hand. The
cause is that `GITHUB_TOKEN` cannot write a workflow file. The auto-merge
workflow writes a notice on that pull request.

Note: a dispatch call does not use the `paths` filter of the workflow that it
starts. The auto-merge workflow runs after each merge and on a schedule. Each
run starts each workflow in `main-workflows` whose newest run is not on the tip
of the default branch. Thus a merge that changes only the documentation can
start a container build that its `paths` filter skipped.

Note: `report-orphaned-push` needs a `push` trigger in the caller, for example
`branches-ignore: [main]`. The job writes no notice when an open pull request
has the branch, or when the merged pull request or the default branch already
holds the pushed commit.

Note: `reusable-close-linked-issues.yml` reads GitHub's own list of linked
issues. The list holds the closing keywords of a pull request into the default
branch, for example `Fixes #12`, and each link from the Development panel. The
job skips a merge into any other branch and an issue in another repository.

Note: code scanning keeps the alerts of each analysis configuration. The key
of a configuration holds the path of the caller workflow, the job ID
`analyze`, and the category `/language:<language>`. A repository that moves its
own `analyze` job to `reusable-codeql.yml`, and keeps the file name of its
CodeQL workflow, keeps the same configuration. So the open alerts stay open,
and a pull request does not report a missing configuration. The check name
changes, for example from `Analyze (python)` to `codeql / Analyze (python)`.
The `CodeQL` check of code scanning keeps its name.

Note: GitHub assigns the Copilot cloud agent only for a user token, for
example a fine-grained personal access token. For `GITHUB_TOKEN`, GitHub
ignores the agent login and returns no error. Put the token in a repository
secret, and pass that secret to `reusable-copilot-assign.yml` as
`assign-token`. Without the secret, the job writes one comment on the issue
that tells how to set it up. The header of the workflow gives the token
permissions.

Note: `reusable-stranded-branch-report.yml`, `reusable-ste-lint.yml`, and the
radon gate of `reusable-python-quality-gates.yml` check out this repository at
the commit that the caller pins, and they install the command from that commit.
Thus the workflow and the command always come from the same release. An open
pull request protects its head branch in the stranded branch report. A closed
or merged pull request protects a branch only when the branch holds no commit
above the last head of that pull request. The report lists a branch that it
cannot compare, for example a branch with no shared history, with `unknown`
values.

Note: `reusable-python-quality-gates.yml` installs no tool of its own, except
`complexity-gate`. The install command of the caller installs each tool, so
pin each tool in that command, for example in `requirements-dev.txt`. Then the
Dependabot of the caller can update the tools. A gate fails with a clear error
when the install command does not install its tool. Each check name holds the
caller job and the gate, for example `gates / Ruff (lint)`. The `Gate results`
check fails when a gate fails, so branch protection can require that one
check.

## Run the tests

```powershell
python -m pytest
```

The analyzer fixtures under `src/misthelper_devtools/test_quality_analyzer/fixtures/` hold the
defects that the detectors find. Pytest never collects them, and the linter
never repairs them.

## Quality gates

```powershell
python -m ruff check .
python -m black --check .
python -m mypy -p misthelper_devtools --config-file pyproject.toml
python -m pytest
ste-linter --min-score 80 README.md documentation/ASD-STE100_writing-guide.md documentation/mist-repository-tooling-inventory.md src/misthelper_devtools/ste_linter/README.md src/misthelper_devtools/test_quality_analyzer/README.md
actionlint
```

The type gate reads the full `misthelper_devtools` package. At release 0.3.0,
the gate read the `juniper_skills` sub-package only, because the other modules
held 51 known annotation defects. Pull request #17 repaired them and widened
the scope.

The STE command reads the same files as the CI workflow.

In the CI workflow, [actionlint](https://github.com/rhysd/actionlint) and
shellcheck read each workflow file, because a defect in a shared workflow
reaches every consumer.

## Relationship to the Mist repositories

MistHelper and the related Mist repositories depend on this repository for
their quality gates and their shared workflows. This repository depends on no
Mist repository. No module here imports `MistHelper` or `mistapi`, except as
sample text in the analyzer fixtures.

Four older modules imported modules of the MistHelper product, so they could
run only in a MistHelper checkout. They were `performance_memory.py`,
`bench_performance_overhead.py`, `bench_e2e_hook_overhead.py`, and
`e2e_store_reset.py`. This repository no longer holds them. MistHelper keeps
its own copies under `scripts/` (jmorrison-juniper/MistHelper#3466).

Warning: do not add a product import here, because the module can then fail
outside the product repository. The dependency runs in one direction only. A
reverse import would put development tooling back into the shipped product.

The [inventory](documentation/mist-repository-tooling-inventory.md) gives the
consumers of each shared workflow and tool.

## Writing style

Every document, comment, and message in this repository follows Simplified
Technical English. The `src/misthelper_devtools/ste_linter/` package measures that rule, and it
measures the same rule for the MistHelper repository.

## License

MIT. See [LICENSE](LICENSE).
