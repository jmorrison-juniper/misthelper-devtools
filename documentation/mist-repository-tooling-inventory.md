# Mist repository tooling inventory

This document records the development tooling of each Mist repository. It
gives the part that moved to this repository and the part that stays in each
repository. It also gives the candidates for a later wave and the tasks for the
owner.

A repository calls a shared workflow at a pinned commit. See
[Shared workflows](../README.md#shared-workflows) for the pin rule.

## Scope

The scan read each repository of the `jmorrison-juniper` account that holds
Mist code. It did not read a repository of another owner, for example
`tmunzer/mistmcp`, and it did not read a repository that holds no Mist code.

| Repository | Tooling found | Wave 1 result | Wave 2 result |
| - | - | - | - |
| MistHelper | The source of most shared workflows and tools. | Not changed here. | Calls the shared Copilot, linked issue, and container workflows. Runs `complexity-gate`. Keeps its own test quality baseline. |
| MistCircuitStats | Quality gates with an inline radon script, gate issues, auto-merge, linked issue close, container build | Calls the shared workflows. Uses `complexity-gate`. | Pins `v0.3.0`. |
| MistHelper-Go | Quality gates, gate issues, auto-merge, linked issue close, Copilot assign, container build | Calls the shared workflows. | Go 1.27.1, so the govulncheck gate passes. The release image uses the shared container workflow. Pins `v0.3.0`. |
| MistSiteDashboard | Two container build workflows | Calls the shared container workflow. | One build workflow. Dependabot updates the actions. Pins `v0.3.0`. |
| MistGuestAuthorizations | Container build, release | Calls the shared container workflow. | The release image uses the shared container workflow. Dependabot updates the actions. Pins `v0.3.0`. |
| MistOrgLicensingComparison | Container build | Calls the shared container workflow. | Dependabot updates the actions. Pins `v0.3.0`. |
| MistCircuitStats-Redis | Container build | Calls the shared container workflow. | Dependabot updates the actions. Pins `v0.3.0`. |
| MistWANPerformance | `check_ports.py`, a debug script | The script moved here as `tools/wan_port_report.py`. | The agent notes install `v0.3.0`. |
| MistCiscoConfigConverter | No workflow. The image copies the whole repository. | The image ignore file drops the development files and the local data files. | The ignore file is now `.dockerignore`, so Docker also reads it. |
| MistDSW | Spec Kit files and product scripts only | No change. | No change. |
| starlink-dashboard | Local ruff and pytest settings only | No change. | No change. |

## Wave 1 pull requests

Each consumer pins release `v0.2.0`, commit
`ca3659400847c35f5dac438c37fc5055c020910e`. The last column gives the check
that followed each merge.

| Repository | Pull request | Merge commit | Check after the merge |
| - | - | - | - |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#40 | `fd0de1c` | The main run pushed the `main`, version, `latest`, and `sha-` tags, and made release `v26.09.26.06.38`. The radon job ran `complexity-gate`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#45 | `97d2808` | The auto-merge job started the container run, and that run pushed the version and `latest` tags. The govulncheck gate fails. See the note after this table. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#5 | `9021dec` | The pull request build pushed no image. The main run pushed the short SHA, `main`, and `latest` tags. A manual start of `container-build.yml` pushed the version and `latest` tags. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#5 | `f89442e` | The main run pushed the short SHA, `main`, and `latest` tags. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#5 | `7809a51` | The main run pushed the short SHA, `main`, and `latest` tags. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#6 | `0f19139` | The main run pushed the version, `sha-`, `main`, and `latest` tags. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#5 | `a180736` | For one sample port list, `wan-port-report` printed the same text as `check_ports.py`. |
| MistCiscoConfigConverter | jmorrison-juniper/MistCiscoConfigConverter#5 | `4359dfa` | A Podman build put only the runtime files in `/app`, and `/health` returned 200. |

Each image that this wave pushed to GHCR holds a linux/amd64 image and a
linux/arm64 image. The tag form of each image did not change.

Note: in MistHelper-Go, the govulncheck gate failed before this wave.
`govulncheck@latest` needs Go 1.26, and the workflow sets Go 1.25. Issue
jmorrison-juniper/MistHelper-Go#38 records the fault. The shared job for gate
issues found that issue and opened no second issue. In Wave 2,
jmorrison-juniper/MistHelper-Go#46 moved the workflow to Go 1.27.1 and
`golang.org/x/crypto` to v0.57.0. The gate now passes.

Note: release `v0.2.0` of `reusable-copilot-assign.yml` sent the assignment
with `GITHUB_TOKEN` and the login `copilot`. GitHub ignored that login and
returned no error, so the job added the in-progress label to an issue that had
no assignee. The old MistHelper copy has the same fault: issue
jmorrison-juniper/MistHelper#2295 has the `copilot` and `in-progress` labels
and no assignee. Release `v0.3.0` sends the login `copilot-swe-agent[bot]`
with the `assign-token` secret, reads the assignees in the response, and
writes the cause on the issue. `self-test.yml` runs it with dry-run.

Note: in MistCircuitStats and MistHelper-Go, the dispatch job of
`reusable-auto-merge.yml` ran after each merge. The merge job runs only for a
pull request with the auto-merge label, and no pull request of this wave had
that label.

## Wave 2 pull requests

Wave 2 repaired the faults that Wave 1 found, and it moved each consumer to
release `v0.3.0`, commit `e0bcb680817721a41d3add1dfb7498afeadb6503`. It also
moved the MistHelper workflows to the shared copies.

| Repository | Pull request | Merge commit | Change and check |
| - | - | - | - |
| misthelper-devtools | #6 | `e0bcb68` | `reusable-copilot-assign.yml` sends a user token and reads the assignees in the response. `self-test.yml` runs each result with dry-run. Release `v0.3.0` is this commit. |
| misthelper-devtools | #7 | `52f9152` | Dependabot updates the actions that the shared workflows use. |
| misthelper-devtools | #8 | `68ad430` | The first Dependabot update of those actions. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#46 | `2c21d66` | Go 1.27.1 and `golang.org/x/crypto` v0.57.0. govulncheck found three reachable advisories in v0.53.0, and it finds none now. Issue jmorrison-juniper/MistHelper-Go#38 closed. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#43 | `712fbe2` | A Dependabot update of `modernc.org/sqlite`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#47 | `885a165` | `mistapi-go` v0.4.108. The inventory call gives no value for the new `disconnectedBefore` filter. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#25 | `e3004b3` | A Dependabot update of `actions/setup-go`. After these merges, Dependabot closed five other update pull requests. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#48 | `6312dca` | Pins `v0.3.0`. The Copilot callers give the `COPILOT_ASSIGN_TOKEN` secret, and the release image uses the shared container workflow. Dependabot also updates the base image. On a test issue, the assignment wrote one comment with the cause and added no label. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#6 | `2ee1073` | One build workflow instead of two. The main run pushed one image with the short SHA, version, `main`, and `latest` tags. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#7 | `366850f` | Adds `dependabot.yml` for the actions. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#8 | `1a8a4e0` | The first Dependabot update of the actions. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#9 | `a15158c` | Pins `v0.3.0`. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#6 | `f7262ef` | The release image uses the shared container workflow, so the `sed` step for the version label is gone. Pins `v0.3.0` and adds `dependabot.yml`. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#7 | `8dc2215` | Adds `dependabot.yml` for the actions. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#8 | `924068b` | Pins `v0.3.0`. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#6 | `82a7ef9` | Adds `dependabot.yml` for the actions. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#7 | `5354794` | Pins `v0.3.0`. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#41 | `71c03a1` | Pins `v0.3.0` in the workflows and in `requirements-dev.txt`. The radon job runs `complexity-gate`. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#38 | `3d84994` | A Dependabot update of `actions/setup-python`. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#6 | `c827d27` | The agent notes install `v0.3.0`. |
| MistCiscoConfigConverter | jmorrison-juniper/MistCiscoConfigConverter#6 | `c686605` | Renames `.containerignore` to `.dockerignore`. Before the change, a Docker build copied `.env`, `data/`, and `input/` into the image. After the change, it copies none of them. |
| MistHelper | jmorrison-juniper/MistHelper#3451 | `64455cd` | The Copilot, linked issue, container, and release image workflows call the shared copies. |
| MistHelper | jmorrison-juniper/MistHelper#3455 | `ea28f78` | MistHelper keeps its own test quality baseline, with 762 entries. The full gate found no new finding. |
| MistHelper | jmorrison-juniper/MistHelper#3459 | `abcf000` | The radon job runs `complexity-gate --max 10`. The job printed the report of the shared command. |

After jmorrison-juniper/MistHelper#3451 merged, each main run pushed a version
tag and the `latest` tag. The `latest` image holds a linux/amd64 image and a
linux/arm64 image. The linked issue workflow ran for each later merge, and a
manual sweep skipped each closed issue. A manual assignment run on
jmorrison-juniper/MistHelper#3450 wrote one comment with the cause, and it
added no label.

### Release workflows

Wave 2 adds no shared release workflow. The three release workflows use
different tag forms, and they make different files.

- MistGuestAuthorizations reads a `YY.MM.DD.HH.MM` tag or a version input, and
  it writes the notes from the Git log.
- MistHelper-Go reads a `v*.*.*` tag, and GitHub writes the notes.
- MistHelper builds a wheel, a standalone zip file, and an image.

The image job of each release workflow calls `reusable-container-image.yml`,
and each repository keeps the job that makes the release. A BuildKit test showed
that the version label of the shared workflow replaces the label in the
Dockerfile.

### Dependabot

Dependabot updates the actions of each consumer that has a workflow.
Dependabot reads the `# vX.Y.Z` comment after each SHA pin, and it proposes the
pin of the next devtools release. It waits 3 days after a release. On the day
of release `v0.3.0`, the Dependabot log reported that all versions were in the
cooldown period. So Wave 2 changed the pins by hand.

Dependabot does not change a pip requirement that points to a Git commit.
MistCircuitStats and MistHelper pin this package that way in
`requirements-dev.txt`. Change that pin by hand, in the same pull request as
the workflow pins.

For MistHelper-Go, Dependabot also updates the base image. Dependabot reads a
file with the name `Containerfile`.

### Copilot assignment

GitHub assigns the Copilot cloud agent only for a user token. Release `v0.3.0`
sends the token from the `assign-token` secret, and it reads the assignees in
the response. Without the secret, the workflow adds no label, and it writes the
cause in one comment on the issue.

The agent is not enabled for the account now. For MistHelper, MistHelper-Go,
and this repository, the list of actors that can take an issue holds only the
owner. So the agent cannot take an issue, even with a token.

### Package layout

The wheel installs the `src` package and the `tools` package at the top level.
The `src` package of this repository holds only `src.juniper_skills`. The four
commands import only `tools`, so they work next to the `src` package of a
consumer. The MistHelper CI runs `complexity-gate` and `test-quality-analyzer`
that way.

Three modules in `tools/` import modules of the MistHelper product.
`performance_memory.py` and `bench_performance_overhead.py` import
`src.utils.performance`, and `e2e_store_reset.py` imports `src.upgrade_portal`.
These modules run only in a MistHelper checkout. There, the `src` package of
MistHelper comes first on the import path. The MistHelper test
`tests/test_performance_memory.py` imports `tools.performance_memory` from this
package.

For each package name, Python loads the first package that it finds. If a
consumer adds its own `tools` package, a `python -m tools.<name>` command in
that consumer finds the wrong package. The MistHelper CI runs
`tools.check_citations` and `tools.speckit_task_audit` that way.

## What moved

### Shared workflows

| Shared workflow | Replaces | Consumers |
| - | - | - |
| `reusable-container-image.yml` | The build and push job of each container workflow, and the image job of each release workflow | MistHelper, MistCircuitStats, MistCircuitStats-Redis, MistHelper-Go, MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison |
| `reusable-quality-gate-issues.yml` | The `create_failure_issues` and `close_resolved_issues` jobs | MistCircuitStats, MistHelper-Go |
| `reusable-auto-merge.yml` | The old copy of the MistHelper `auto-merge.yml` | MistCircuitStats, MistHelper-Go |
| `reusable-close-linked-issues.yml` | The old copy of the MistHelper `close-linked-issues.yml` | MistHelper, MistCircuitStats, MistHelper-Go |
| `reusable-copilot-assign.yml` | `copilot-auto-assign.yml` and `copilot-label-checkbox.yml` | MistHelper, MistHelper-Go |

Each consumer keeps its triggers, its tag rules, its test jobs, and the job that
makes each release.

### Defects that the shared workflows repair

- An auto-merge with `GITHUB_TOKEN` starts no push run on `main`. In
  MistCircuitStats, jmorrison-juniper/MistCircuitStats#33 and
  jmorrison-juniper/MistCircuitStats#34 merged that way, and neither
  merge started the gates, the code scan, or the container build. The shared
  auto-merge workflow starts each main workflow with a dispatch call. The
  MistHelper fix is jmorrison-juniper/MistHelper#1851.

- An auto-merged pull request starts no closed event, so the old linked issue
  workflow did not close its issues. The shared workflow adds a sweep. The
  MistHelper fix is jmorrison-juniper/MistHelper#1742.

- `GITHUB_TOKEN` cannot merge a pull request that edits a workflow file. The
  merge failed with no reason on the pull request. The shared workflow writes a
  notice. The MistHelper fix is jmorrison-juniper/MistHelper#1954.

- In MistHelper-Go, the checkbox workflow adds the `copilot` label with
  `GITHUB_TOKEN`. GitHub starts no workflow from that label event, so the
  assign workflow cannot run for a checkbox issue. The shared workflow assigns
  the agent in the same run.

- The old jobs for gate issues used a free text search, so they could match
  any open issue that holds the same words. The shared workflow compares the
  exact title.

### Tools

| Tool | Replaces | Consumer |
| - | - | - |
| `complexity-gate` | The inline radon script of the quality gate workflow | MistCircuitStats (limit 15), MistHelper (limit 10) |
| `wan-port-report` | `check_ports.py` (MistWANPerformance commit ddbf849) | MistWANPerformance |

The other MistHelper tools, for example `test-quality-analyzer` and
`ste-linter`, moved here in an earlier migration, issue
jmorrison-juniper/MistHelper#3404. The MistHelper file
`documentation/development-tooling-migration.md` describes that migration.

## What stays in each repository

- The Spec Kit files (`.specify/`, `.github/agents/`, `.github/prompts/`).
  The prompts use paths that are relative to the repository.

- The product scripts, the test suites, and the Dockerfiles.

- The CodeQL workflow and the Dependabot configuration of each repository.

- The quality gate jobs. Each repository keeps its own list of gates.

- The job that makes each release, with its tag form and its notes. See
  [Release workflows](#release-workflows).

MistHelper also keeps these files and jobs:

- `auto-merge.yml`. Its orphaned-push report and its close job exist only in
  MistHelper, and a guardrail test reads the job text.

- The `create_failure_issues` and `close_resolved_issues` jobs in `ci.yml`.
  Two guardrail tests read the job text.

- `.github/quality-gates-portable.yml`. This template must run without this
  package, so it keeps its inline radon script.

- `ste-lint.yml`. MistHelper is the only repository with an STE gate, so a
  shared workflow would have one caller.

- The test quality baseline, `.github/test-quality-baseline.json`.

## Candidates for a later wave

- The MistHelper `auto-merge.yml` and gate issue jobs. Before the move, the
  shared workflow for auto-merge needs the orphaned-push report and the close
  job of the MistHelper copy. Then the guardrail tests must read the shared
  workflows.

- A shared STE lint workflow, when a second repository adopts the STE rules.
  At this time, MistHelper grades the text of one file with `ste-lint.yml`.

- Move the three modules that import modules of the MistHelper product to
  MistHelper, for example to `scripts/`. See [Package layout](#package-layout).
  A `tools` package in MistHelper would hide the `tools` package of this
  repository.

- A unique name for the two top-level packages, for example
  `misthelper_devtools`. The change touches 255 import lines in this
  repository, and it changes each `python -m tools.<name>` command of a
  consumer.

- Remove `baseline.json` from the package. MistHelper keeps its own baseline
  now, and no other consumer runs `test-quality-analyzer`. The default of the
  `--baseline` option must then change.

- A MistHelper copy of the analyzer settings. The analyzer reads the
  `config.toml` file of the package when a run gives no `--config` option.
  With its own copy, MistHelper can change a rule without a devtools release.

## Tasks for the owner

These tasks need the account owner. A workflow cannot do them.

1. Enable the Copilot cloud agent for the account. Then save a user token as
   the `COPILOT_ASSIGN_TOKEN` secret in MistHelper and in MistHelper-Go. Until
   then, each assignment run writes one comment with the cause.

2. Decide on jmorrison-juniper/MistCircuitStats#39. Dependabot changes the
   CodeQL pin from `v4` to the exact version `v4.37.3`.

3. Decide on jmorrison-juniper/MistCircuitStats#37. It moves the base image
   from Python 3.13 to Python 3.14, and that changes the runtime of the
   application.

4. At each devtools release, change the `requirements-dev.txt` pin in
   MistCircuitStats and MistHelper by hand. Dependabot changes only the
   workflow pins.
