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

Twelve tools also install as a command.

| Command | Module |
| - | - |
| `check-citations` | `misthelper_devtools.check_citations` |
| `complexity-gate` | `misthelper_devtools.complexity_gate` |
| `compliance-analyzer` | `misthelper_devtools.compliance_analyzer` |
| `guard-proof-audit` | `misthelper_devtools.guard_proof_audit` |
| `refactor-analyzer` | `misthelper_devtools.refactor_analyzer` |
| `speckit-task-audit` | `misthelper_devtools.speckit_task_audit` |
| `ste-linter` | `misthelper_devtools.ste_linter` |
| `stranded-branch-report` | `misthelper_devtools.stranded_branch_report` |
| `symbol-diff` | `misthelper_devtools.symbol_diff` |
| `test-quality-analyzer` | `misthelper_devtools.test_quality_analyzer` |
| `venv-health` | `misthelper_devtools.venv_health` |
| `wan-port-report` | `misthelper_devtools.wan_port_report` |

```powershell
ste-linter path\to\file.md
test-quality-analyzer --roots tests
radon cc . -j | complexity-gate --max 15
check-citations src tests
```

## Upgrade from release 0.3.0

Release 0.3.0 installed two top-level packages with generic names, `tools` and
`src`. A consumer repository with its own `src` package could import the wrong
code. Every module is now inside `misthelper_devtools`. Change each import and
each `python -m` command of a consumer.

| Release 0.3.0 | Now |
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

## Shared workflows

Each shared workflow starts with `on: workflow_call`. A Mist repository keeps
its own triggers, and it calls the shared job with `uses:`. The header of each
file holds a full caller example and the permissions that the caller must give.

| Workflow | Purpose |
| - | - |
| `reusable-container-image.yml` | Builds a multi-arch image with Buildx and can push it to GHCR. The caller gives the tag rules. |
| `reusable-quality-gate-issues.yml` | Opens one issue for each failed gate and closes it after the gate passes. |
| `reusable-auto-merge.yml` | Enables auto-merge for a labeled pull request. After the merge, it starts the main workflows that the merge did not start. |
| `reusable-close-linked-issues.yml` | Closes each issue that a merged pull request names with a closing keyword. A sweep finds the auto-merged pull requests. |
| `reusable-copilot-assign.yml` | Assigns the Copilot cloud agent to an issue with a user token. It adds the in-progress label only when the agent is an assignee, and otherwise writes the cause on the issue. |
| `reusable-stranded-branch-report.yml` | Runs `stranded-branch-report` and keeps one issue with each branch that holds work with no pull request. It closes the issue after a pull request holds every branch. |

A caller pins the full commit SHA of a release and writes the release tag in a
comment:

```yaml
jobs:
  build-and-push:
    permissions:
      contents: read
      packages: write
    uses: jmorrison-juniper/misthelper-devtools/.github/workflows/reusable-container-image.yml@<commit-sha> # v0.3.0
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

Note: GitHub assigns the Copilot cloud agent only for a user token, for
example a fine-grained personal access token. For `GITHUB_TOKEN`, GitHub
ignores the agent login and returns no error. Put the token in a repository
secret, and pass that secret to `reusable-copilot-assign.yml` as
`assign-token`. Without the secret, the job writes one comment on the issue
that tells how to set it up. The header of the workflow gives the token
permissions.

Note: `reusable-stranded-branch-report.yml` checks out this repository at the
commit that the caller pins, and it installs the command from that commit.
Thus the workflow and the command always come from the same release. An open
pull request protects its head branch. A closed or merged pull request protects
a branch only when the branch holds no commit above the last head of that pull
request. The report lists a branch that it cannot compare, for example a branch
with no shared history, with `unknown` values.

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
python -m mypy -p misthelper_devtools.juniper_skills --config-file pyproject.toml
python -m pytest
actionlint
```

The type gate reads the `juniper_skills` sub-package only. That scope matches
the MistHelper repository, which type-checked its `src/` tree only. The other
modules of the package hold 51 known annotation defects. A separate change
repairs them and widens the scope.

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
