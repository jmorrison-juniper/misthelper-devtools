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

| Path | Purpose |
| - | - |
| `tools/ste_linter/` | The Simplified Technical English linter. It scores prose and reports each rule violation. |
| `tools/test_quality_analyzer/` | The test quality analyzer. It finds a tautological test, a weak assertion, and a missing failure mode. |
| `tools/compliance_analyzer/` | The compliance analyzer. It measures the inline comment rule and the action logging rule. |
| `tools/refactor_analyzer/` | The refactor analyzer. It builds the module graph and it reports a decomposition candidate. |
| `tools/symbol_diff/` | The symbol comparison tool. It reports a module-level name that a sweep lost. |
| `tools/complexity_gate.py` | The complexity gate. It reads a `radon cc -j` report and fails on a block above the limit. |
| `tools/wan_port_report.py` | The WAN port report. It reads a saved Mist port list and prints the gateway WAN port state. |
| `tools/*.py` | Single-file tools. They audit citations, guard proofs, prompts, dependencies, and performance. |
| `.github/workflows/reusable-*.yml` | The shared workflows that the Mist repositories call. See [Shared workflows](#shared-workflows). |
| `src/juniper_skills/` | The skill factory. It reads a Juniper document set and it writes a skill package. |
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
python -m tools.ste_linter path\to\file.md
python -m tools.test_quality_analyzer tests\
python -m tools.compliance_analyzer src\
python -m tools.symbol_diff --base main path\to\file.py
```

Four tools also install as a command.

```powershell
ste-linter path\to\file.md
test-quality-analyzer tests\
radon cc . -j | complexity-gate --max 15
wan-port-report --input ports.json
```

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
actionlint
```

The type gate reads `src/` only. That scope matches the MistHelper repository,
which never type-checked `tools/`. The `tools/` tree holds 60 known annotation
defects. A separate change repairs them and widens the scope.

In the CI workflow, [actionlint](https://github.com/rhysd/actionlint) and
shellcheck read each workflow file, because a defect in a shared workflow
reaches every consumer.

## Relationship to the Mist repositories

MistHelper and the related Mist repositories depend on this repository for
their quality gates and their shared workflows. This repository depends on no
Mist repository. No module here imports `MistHelper`, `mistapi`, or any other
product module.

Warning: never add a product import to this repository. The dependency runs in
one direction only. A reverse import would put development tooling back into
the shipped product.

The [inventory](documentation/mist-repository-tooling-inventory.md) gives the
consumers of each shared workflow and tool.

## Writing style

Every document, comment, and message in this repository follows Simplified
Technical English. The `tools/ste_linter/` package measures that rule, and it
measures the same rule for the MistHelper repository.

## License

MIT. See [LICENSE](LICENSE).
