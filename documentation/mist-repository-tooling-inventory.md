# Mist repository tooling inventory

This document records the development tooling of each Mist repository. It
gives the part that moved to this repository and the part that stays in each
repository. It also gives the candidates for a later wave and the tasks for the
owner.

A repository calls a shared workflow at a pinned commit. See
[Shared workflows](../docs/tooling-guide.md#shared-workflows) for the pin rule.

## Scope

The scan read each repository of the `jmorrison-juniper` account that holds
Mist code. It did not read a repository of another owner, for example
`tmunzer/mistmcp`, and it did not read a repository that holds no Mist code.

| Repository | Tooling found | Wave 1 result | Wave 2 result | Wave 3 result | Wave 4 result | Wave 5 result |
| - | - | - | - | - | - | - |
| MistHelper | The source of most shared workflows and tools. | Not changed here. | Calls the shared Copilot, linked issue, and container workflows. Runs `complexity-gate`. Keeps its own test quality baseline. | Pins `v0.4.0`. Calls the shared auto-merge, gate issue, stranded branch, and STE lint workflows. Keeps its own analyzer settings and benchmarks. | Pins `v0.5.2`. Calls the shared CodeQL workflow, the Mermaid action, the new commands, and the two hooks. Deletes its copies of the tools. | Pins `v0.6.0`. |
| MistCircuitStats | Quality gates with an inline radon script, gate issues, auto-merge, linked issue close, container build | Calls the shared workflows. Uses `complexity-gate`. | Pins `v0.3.0`. | Pins `v0.4.0`. Dependabot keeps Python 3.13 and the CodeQL `v4` tag. | Calls the shared Python gate and CodeQL workflows. Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch and STE lint workflows. Gets the orphaned-push report. |
| MistHelper-Go | Quality gates, gate issues, auto-merge, linked issue close, container build | Calls the shared workflows. | Go 1.27.1, so the govulncheck gate passes. The release image uses the shared container workflow. Pins `v0.3.0`. | Pins `v0.4.0`. Dependabot keeps the CodeQL `v4` tag. | Calls the shared CodeQL workflow. Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch and STE lint workflows. Gets the orphaned-push report. |
| MistSiteDashboard | Two container build workflows | Calls the shared container workflow. | One build workflow. Dependabot updates the actions. Pins `v0.3.0`. | Python 3.13. Pins `v0.4.0`. | Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch, STE lint, and Python gate workflows. |
| MistGuestAuthorizations | Container build, release | Calls the shared container workflow. | The release image uses the shared container workflow. Dependabot updates the actions. Pins `v0.3.0`. | Python 3.13. Pins `v0.4.0`. | Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch, STE lint, and Python gate workflows. |
| MistOrgLicensingComparison | Container build | Calls the shared container workflow. | Dependabot updates the actions. Pins `v0.3.0`. | Pins `v0.4.0`. | Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch, STE lint, and Python gate workflows. |
| MistCircuitStats-Redis | Container build | Calls the shared container workflow. | Dependabot updates the actions. Pins `v0.3.0`. | Pins `v0.4.0`. | Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch, STE lint, and Python gate workflows. |
| MistWANPerformance | `check_ports.py`, a debug script | The script moved here as the `wan-port-report` command. | The agent notes install `v0.3.0`. | Python 3.13. The agent notes install `v0.4.0`. | The first CI workflow calls the shared Python gates. Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch and STE lint workflows. Turns on the black, Ruff, vulture, and mypy gates. |
| MistCiscoConfigConverter | No workflow. The image copies the whole repository. | The image ignore file drops the development files and the local data files. | The ignore file is now `.dockerignore`, so Docker also reads it. | No change. | The README links to the published OpenAPI specification. | The first workflows call the shared stranded branch and STE lint workflows. Dependabot updates the actions. |
| MistDSW | Spec Kit files and product scripts only | No change. | No change. | No change. | No change. | The first workflows call the shared stranded branch and STE lint workflows. Dependabot updates the actions. |
| starlink-dashboard | A CI workflow with one Ruff and pytest job, and one job that makes the protocol modules | No change. | No change. | No change. | Calls the shared Python gates for Ruff and pytest. Pins `v0.5.2`. | Pins `v0.6.0`. Calls the shared stranded branch and STE lint workflows. Dependabot updates the actions. |

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

Note: release `v0.2.0` of the shared Copilot assignment workflow sent the
assignment with `GITHUB_TOKEN` and the login `copilot`. GitHub ignored that login and
returned no error, so the job added the in-progress label to an issue that had
no assignee. The old MistHelper copy has the same fault: issue
jmorrison-juniper/MistHelper#2295 has the `copilot` and `in-progress` labels
and no assignee. Release `v0.3.0` sends the login `copilot-swe-agent[bot]`
with the `assign-token` secret, reads the assignees in the response, and
writes the cause on the issue. Release `v0.6.2` removes the workflow. Read
[Copilot assignment](#copilot-assignment).

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
| misthelper-devtools | #6 | `e0bcb68` | The shared Copilot assignment workflow sends a user token and reads the assignees in the response. `self-test.yml` runs each result with dry-run. Release `v0.3.0` is this commit. |
| misthelper-devtools | #7 | `52f9152` | Dependabot updates the actions that the shared workflows use. |
| misthelper-devtools | #8 | `68ad430` | The first Dependabot update of those actions. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#46 | `2c21d66` | Go 1.27.1 and `golang.org/x/crypto` v0.57.0. govulncheck found three reachable advisories in v0.53.0, and it finds none now. Issue jmorrison-juniper/MistHelper-Go#38 closed. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#43 | `712fbe2` | A Dependabot update of `modernc.org/sqlite`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#47 | `885a165` | `mistapi-go` v0.4.108. The inventory call gives no value for the new `disconnectedBefore` filter. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#25 | `e3004b3` | A Dependabot update of `actions/setup-go`. After these merges, Dependabot closed five other update pull requests. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#48 | `6312dca` | Pins `v0.3.0`. The Copilot callers give a user token secret, and the release image uses the shared container workflow. Dependabot also updates the base image. On a test issue, the assignment wrote one comment with the cause and added no label. |
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
cooldown period. So Wave 2 changed the pins by hand. Wave 3 and Wave 4 also
changed the pins by hand, on the day of each release.

Dependabot does not change a pip requirement that points to a Git commit.
Six repositories pin this package that way in `requirements-dev.txt`:
MistHelper, MistCircuitStats, MistCircuitStats-Redis, MistGuestAuthorizations,
MistOrgLicensingComparison, and MistSiteDashboard. Change that pin by hand, in
the same pull request as the workflow pins.

Since release `v0.6.0`, the shared jobs find a requirement pin that you forgot
to change. The radon job and the ste-lint job run `devtools-pin-check`. The
command compares the pin with the commit of the workflow. When the two differ,
it writes a warning with the new text of the line. The warning does not stop
the job. Issue #36 compares this check with the other choices.

For MistHelper-Go, Dependabot also updates the base image. Dependabot reads a
file with the name `Containerfile`.

## Wave 3 pull requests

Wave 3 moved each consumer to release `v0.4.0`, commit
`bf1ba8a29ee9bab27b721db6e613fd5cd83d82fa`. It also moved the last MistHelper
workflows to the shared copies, and it kept each Python project on Python 3.13.

| Repository | Pull request | Merge commit | Change and check |
| - | - | - | - |
| misthelper-devtools | #10 | `938f747` | The package installs one top-level name, `misthelper_devtools`. See [Package layout](#package-layout). |
| misthelper-devtools | #11 | `bc03b89` | The package holds no test quality baseline. The analyzer reads the baseline of the repository that runs it. |
| misthelper-devtools | #12 | `0300169` | The MistHelper tests of the tools run here. |
| misthelper-devtools | #13 | `9fc1de2` | Adds the `stranded-branch-report` command and `reusable-stranded-branch-report.yml`. |
| misthelper-devtools | #14 | `6690fbd` | The auto-merge workflow can report a push that arrives after the merge. The linked issue workflow reads the issue links that GitHub keeps for each pull request. |
| misthelper-devtools | #15 | `f0d3e74` | Adds `reusable-ste-lint.yml`. The CI workflow of this repository grades its documents with it. |
| misthelper-devtools | #16 | `c705bf3` | Adds tests for the rules that the shared workflows took from MistHelper. |
| misthelper-devtools | #17 | `c5ec756` | mypy checks the full package. |
| misthelper-devtools | #18 | `bf1ba8a` | Release `v0.4.0` is this commit. |
| MistHelper | jmorrison-juniper/MistHelper#3468 | `052d563` | The four modules that import product code move to `scripts/`. MistHelper keeps its own analyzer settings in `.github/test-quality-config.toml`. |
| MistHelper | jmorrison-juniper/MistHelper#3470 | `fc6a93a` | The container check job uses Python 3.13. Dependabot keeps the CodeQL `v4` tag. |
| MistHelper | jmorrison-juniper/MistHelper#3489 | `df6027b` | Pins `v0.4.0`. Calls the shared auto-merge, gate issue, stranded branch, and STE lint workflows. Removes the old copies of the tool tests. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#42 | `2384867` | A Dependabot update of `radon`. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#43 | `05351bc` | Dependabot keeps Python 3.13 and the CodeQL `v4` tag. This change replaced jmorrison-juniper/MistCircuitStats#37 and jmorrison-juniper/MistCircuitStats#39, and both closed with no merge. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#44 | `ee7c6ef` | Pins `v0.4.0` in the workflows and in `requirements-dev.txt`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#50 | `0c2db87` | Pins `v0.4.0`. Dependabot keeps the CodeQL `v4` tag. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#10 | `af67394` | Python 3.13 in the image and in the check job. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#11 | `a4ae13a` | Pins `v0.4.0`. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#7 | `764a879` | Python 3.13 in the image. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#8 | `689f11f` | Pins `v0.4.0`. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#8 | `45b12bc` | Pins `v0.4.0`. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#9 | `cb51655` | Pins `v0.4.0`. The workflow runs only for a push to `main` or for a tag, so the check ran after the merge. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#7 | `fb0db89` | Python 3.13 in the image and in the project settings. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#8 | `3c8ebd2` | The agent notes install `v0.4.0`. |

After each merge, the main runs passed, and each container workflow pushed a
new image to GHCR. In MistHelper, the job for gate issues ran on `main`. The
orphaned-push report ran for each push to a branch with an open pull request,
and it wrote no notice. A manual run of the stranded branch report opened
jmorrison-juniper/MistHelper#3490 with two branches. The old MistHelper script
opened no issue. See [Defects that the shared workflows
repair](#defects-that-the-shared-workflows-repair).

A later run of the report opened jmorrison-juniper/MistHelper#3529 with three
branches. A Copilot session then closed #3490 as a duplicate of #3529.

## Wave 4 pull requests

Wave 4 moved the last MistHelper tools, tool tests, and CI scripts to this
repository. It also moved each consumer to release `v0.5.2`, commit
`0b969be7f60599f9a19ebc3069161f9353a1d830`. Releases `v0.5.0` and `v0.5.1`
came earlier in the same wave. Each Python project stays on Python 3.13.

| Repository | Pull request | Merge commit | Change and check |
| - | - | - | - |
| misthelper-devtools | #20 | `f64f9c2` | The last MistHelper tests of the shared tools run here. |
| misthelper-devtools | #21 | `c7a3c9c` | `test-quality-analyzer --changed-from` scans only the test files that changed. A change to a `--full-gate-path` file makes it scan each root. |
| misthelper-devtools | #22 | `fcf2d92` | Adds `reusable-python-quality-gates.yml`. The caller turns on each gate with an input. |
| misthelper-devtools | #23 | `2e19c25` | Adds `reusable-codeql.yml`. The CodeQL workflow of this repository calls it. |
| misthelper-devtools | #24 | `efd236b` | Publishes the `ste-linter` pre-commit hook. |
| misthelper-devtools | #25 | `bf2c1c6` | Tests prove that a change to the baseline or the settings file makes the analyzer scan each root. |
| misthelper-devtools | #26 | `a4b8dbd` | Adds the `codeql-verdict-register`, `bandit-exclude-check`, `diagram-refs`, and `exclusion-drift` commands. |
| misthelper-devtools | #27 | `7c24165` | Adds the `mermaid-lint` action. `setup-node` cannot hash a lock file outside the caller workspace, so the action uses no npm cache. |
| misthelper-devtools | #28 | `dabd9e2` | Adds the `markdown-link-check`, `pytest-chunks`, and `worktree-cleanup` commands, and the `markdown-link-check` hook. `worktree-cleanup merged` also finds a branch after a squash merge. |
| misthelper-devtools | #29 | `0936f17` | Release `v0.5.0` is this commit. |
| misthelper-devtools | #30 | `cd7e9a6` | `markdown-link-check` reads a link that starts with `/` from the repository root. See the notes after this table. |
| misthelper-devtools | #31 | `3f498b1` | Release `v0.5.1` is this commit. |
| misthelper-devtools | #33 | `0400b40` | The auto-merge dispatch job looks for a run on the tip commit. See the notes after this table. |
| misthelper-devtools | #34 | `0b969be` | Release `v0.5.2` is this commit. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#45 | `0f53ff8` | Calls the shared Python gate and CodeQL workflows. `requirements-dev.txt` pins each gate tool. Pins `v0.5.1`. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#46 | `7f938c6` | Pins `v0.5.2`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#51 | `c707c12` | Calls the shared CodeQL workflow. Pins `v0.5.1`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#52 | `1f2a1ba` | Pins `v0.5.2`. |
| starlink-dashboard | jmorrison-juniper/starlink-dashboard#1 | `e5e49d1` | Calls the shared Python gates for the Ruff lint, the Ruff format check, and pytest. The protocol job stays. Pins `v0.5.1`. |
| starlink-dashboard | jmorrison-juniper/starlink-dashboard#2 | `cf0a169` | Pins `v0.5.2`. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#9 | `884ebc6` | The first CI workflow. It calls the shared Python gates for pytest, Bandit, and pip-audit. Pins `v0.5.1`. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#10 | `1432f2c` | Pins `v0.5.2`. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#12 | `7a82a67` | Pins `v0.5.2`. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#9 | `2802668` | Pins `v0.5.2`. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#9 | `5a6458c` | Pins `v0.5.2`. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#10 | `5d6d367` | Pins `v0.5.2`. The workflow runs only for a push to `main` or for a tag, so the check ran after the merge. |
| MistCiscoConfigConverter | jmorrison-juniper/MistCiscoConfigConverter#7 | `48ed47a` | The README links to the published OpenAPI specification. `.gitignore` excludes the two local copies, so the old links failed on GitHub. |
| MistHelper | jmorrison-juniper/MistHelper#3527 | `ba6431c` | Pins `v0.5.2`. Calls the shared CodeQL workflow, the Mermaid action, the new commands, and the two hooks. Deletes its copies of the tools and their tests. Closes jmorrison-juniper/MistHelper#3515. |
| MistHelper | jmorrison-juniper/MistHelper#3531 | `8af5728` | Deletes the Dependabot rule for `github/codeql-action`. No MistHelper workflow names that action now. Closes jmorrison-juniper/MistHelper#3530. |

After each merge, the main runs passed, and each container workflow pushed a
new image to GHCR. In MistHelper, MistCircuitStats, and MistHelper-Go, the key
of each code scanning analysis did not change. It stays
`.github/workflows/codeql.yml:analyze`, so each open alert stays open.

Note: MistCircuitStats has a branch protection rule on `main`. Before
jmorrison-juniper/MistCircuitStats#45 merged, the rule changed to the new check
names. It requires the six gate checks, for example `gates / Ruff (lint)`, and
`codeql / Analyze (python)`.

Note: in MistWANPerformance, four gates do not run: Ruff, black, mypy, and
vulture. Each one fails on the current code. black reformats 43 files, and mypy
reports 129 errors. Ruff and vulture report unused names. A comment in `ci.yml`
tells when to set the input of each gate to `true`.

Note: release `v0.5.0` of `markdown-link-check` read a link that starts with
`/` from the file system root. So it reported each such link as a missing
file. GitHub reads that link from the repository root, and release `v0.5.1`
does the same.

Note: release `v0.5.2` repairs a fault of the auto-merge dispatch job, issue
#32. See [Defects that the shared workflows
repair](#defects-that-the-shared-workflows-repair). The job now asks for the
runs of each workflow on the tip commit. It asks up to five times, 20 seconds
apart, before it starts a run. One dispatch job at a time runs for each branch.

After each `v0.5.2` merge, GitHub showed one run of each workflow for the tip of
`main`. The job started a run only for a workflow that the push did not start.
The MistHelper-Go container workflow has no push trigger. The MistHelper
container workflow has a path filter, and the change of
jmorrison-juniper/MistHelper#3531 did not match it.

### Python and CodeQL versions

Python 3.13 is the version in the image, the workflows, and the project
settings of each project that holds Python code. The owner keeps Python 3.13
for now.

Dependabot updates a base image only in MistCircuitStats and MistHelper-Go. In
MistCircuitStats, an ignore rule stops each update of the `python` image to
version 3.14 or later. The MistHelper-Go image holds no Python. To move to a
newer Python, remove the rule. Then change each image, workflow, and project
file in the same wave.

The CodeQL workflows of MistHelper, MistCircuitStats, and MistHelper-Go call
`reusable-codeql.yml`, and that workflow uses the floating `v4` tag. GitHub
moves that tag to each new CodeQL release, so each scan gets the fixes with no
pull request. In this repository, an ignore rule stops the minor and patch
updates of `github/codeql-action`. Dependabot still proposes a new major
version. The three consumers name no CodeQL action now, so they hold no rule.

### Copilot assignment

Release `v0.6.2` removes the shared Copilot assignment workflow. The owner
cannot use the Copilot cloud agent, so the workflow served no consumer. A
consumer removes its caller workflows, for example `copilot-auto-assign.yml`
and `copilot-label-checkbox.yml`, and the token secret of the caller. The
issues jmorrison-juniper/MistHelper#3900 and jmorrison-juniper/MistHelper-Go#69
record that change.

### Package layout

Release 0.3.0 installed the `src` package and the `tools` package at the top
level. For each package name, Python loads the first package that it finds, so
a consumer with its own `src` or `tools` package could import the wrong code.
The MistHelper CI ran `python -m tools.check_citations` that way.

Since release `v0.4.0`, one name installs at the top level,
`misthelper_devtools`. The skill factory is
`misthelper_devtools.juniper_skills`. Release `v0.4.0` installed twelve
commands. Release `v0.5.0` adds seven, for a total of nineteen commands.
Release `v0.6.0` adds `devtools-pin-check`, for a total of twenty. The README
gives the old and the new name of each import and each command.

Four modules imported modules of the MistHelper product:
`performance_memory.py`, `bench_performance_overhead.py`,
`bench_e2e_hook_overhead.py`, and `e2e_store_reset.py`. They ran only in a
MistHelper checkout. MistHelper keeps its own copies under `scripts/`
(jmorrison-juniper/MistHelper#3466), and the package no longer holds them.

The package holds no baseline for the test quality analyzer. By default,
`test-quality-analyzer` reads the `.github/test-quality-baseline.json` file of
the repository that runs it.

## Wave 5 pull requests

Wave 5 moved each consumer to release `v0.6.0`, commit
`b140350ebc40e61b57a3a65731c0df520f143661`. It also added the shared stranded
branch report and the STE lint workflow to each other repository.
MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison, and
MistCircuitStats-Redis turned on the shared Ruff, black, mypy, and vulture
gates. MistWANPerformance turned on its black, Ruff, vulture, and mypy gates.
Each Python project stays on Python 3.13.

| Repository | Pull request | Merge commit | Change and check |
| - | - | - | - |
| misthelper-devtools | #38 | `06d7c30` | Adds the `devtools-pin-check` command. The radon job and the ste-lint job run it. |
| misthelper-devtools | #39 | `b140350` | Release `v0.6.0` is this commit. |
| MistHelper | jmorrison-juniper/MistHelper#3676 | `af8dd66` | Pins `v0.6.0` in ten workflows, the Mermaid action, the pre-commit hook, and `requirements-dev.txt`. Closes jmorrison-juniper/MistHelper#3672. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#48 | `8be781f` | Pins `v0.6.0`. See the note about the pin check after this table. |
| MistCircuitStats | jmorrison-juniper/MistCircuitStats#50 | `8cc5476` | Calls the shared stranded branch and STE lint workflows. The STE job grades four files. The auto-merge caller sets `report-orphaned-push: true`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#59 | `38ea387` | Pins `v0.6.0`. |
| MistHelper-Go | jmorrison-juniper/MistHelper-Go#61 | `e07e5c4` | Calls the shared stranded branch and STE lint workflows. The STE job grades seven files. The auto-merge caller sets `report-orphaned-push: true`. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#17 | `e9f2f95` | Pins `v0.6.0`. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#19 | `c3da518` | Calls the shared stranded branch and STE lint workflows. |
| MistSiteDashboard | jmorrison-juniper/MistSiteDashboard#22 | `0ba3675` | Turns on the Ruff, black, mypy, and vulture gates. The pytest, Bandit, and pip-audit gates ran before this change. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#11 | `40d23c1` | Pins `v0.6.0`. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#13 | `663f480` | Calls the shared stranded branch and STE lint workflows. |
| MistGuestAuthorizations | jmorrison-juniper/MistGuestAuthorizations#16 | `61f6a48` | Calls the shared Python gates for Ruff, black, mypy, Bandit, pip-audit, and vulture. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#11 | `7877378` | Pins `v0.6.0`. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#13 | `283c7bd` | Calls the shared stranded branch and STE lint workflows. |
| MistOrgLicensingComparison | jmorrison-juniper/MistOrgLicensingComparison#15 | `8eea164` | Calls the shared Python gates for Ruff, black, mypy, Bandit, pip-audit, vulture, and radon with limit 15. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#14 | `a457281` | Pins `v0.6.0`. The workflow runs only for a push to `main` or for a tag, so the check ran after the merge. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#16 | `d295e72` | Calls the shared stranded branch and STE lint workflows. |
| MistCircuitStats-Redis | jmorrison-juniper/MistCircuitStats-Redis#19 | `3021091` | Calls the shared Python gates for Ruff, black, mypy, Bandit, pip-audit, and vulture. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#18 | `132494b` | The agent notes install `v0.6.0`, and the workflows pin it. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#20 | `6e8140a` | Calls the shared stranded branch and STE lint workflows. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#22 | `a63e5da` | black formats each file, and the black gate is on. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#24 | `716ad90` | Turns on the Ruff gate. The fixes keep each `except Exception` handler of `main`, and the Ruff settings stop the BLE001 check for 19 files. Closes jmorrison-juniper/MistWANPerformance#23. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#26 | `30cefa1` | Turns on the vulture gate. Each unused parameter of a callback or an exit method gets the prefix `_`. Closes jmorrison-juniper/MistWANPerformance#25. |
| MistWANPerformance | jmorrison-juniper/MistWANPerformance#29 | `07bfba6` | Turns on the mypy gate. See the note about the mypy gate after this table. Closes jmorrison-juniper/MistWANPerformance#27. |
| starlink-dashboard | jmorrison-juniper/starlink-dashboard#4 | `70029d7` | Pins `v0.6.0`. |
| starlink-dashboard | jmorrison-juniper/starlink-dashboard#6 | `933db2f` | Calls the shared stranded branch and STE lint workflows. Dependabot updates the actions. |
| MistCiscoConfigConverter | jmorrison-juniper/MistCiscoConfigConverter#11 | `5b6248b` | The first workflows. They call the shared stranded branch and STE lint workflows. The STE job grades three files. Dependabot updates the actions. |
| MistDSW | jmorrison-juniper/MistDSW#6 | `47fb381` | The first workflows. They call the shared stranded branch and STE lint workflows. Dependabot updates the actions. |

After each merge, the main runs passed. Each STE caller grades its files with
the limit 80. A manual run of each new stranded branch report found no branch,
so it opened no issue. In MistCircuitStats and MistHelper-Go, a push to the
branch of each pull request started the orphaned-push report, and the report
job passed.

Note: jmorrison-juniper/MistCircuitStats#48 shows the pin check. Its first
commit changed only the workflow pins. The STE job then wrote a warning on line
15 of `requirements-dev.txt`. The warning named the old commit and the comment
`# v0.5.2`, and it gave the new text of the line. The second commit changed the
line, and the job wrote: "requirements-dev.txt line 15 installs
jmorrison-juniper/misthelper-devtools at
b140350ebc40e61b57a3a65731c0df520f143661, as this workflow does."

Note: release 0.16 of Ruff adds checks to the default set of rules. Each Ruff
gate in this wave uses that release. One of the new checks is BLE001, which
finds each `except Exception`. In these repositories, each route, cache call,
and worker loop catches each exception, writes a log line, and continues. A
narrow list of exception types lets the other types stop the request or the
worker. So each repository keeps `except Exception`, and its Ruff settings
stop the BLE001 check for each such file, with the reason.

Note: three repositories keep the radon gate off. Some of their functions are
above the limit 15. You must add tests for each of these functions before you
change it. These issues track the work:
jmorrison-juniper/MistSiteDashboard#21,
jmorrison-juniper/MistGuestAuthorizations#15, and
jmorrison-juniper/MistCircuitStats-Redis#18.

Note: the mypy gate of MistWANPerformance found 157 errors. The fixes add
annotations and `cast()` calls, and they do not change what the code does. A
`type: ignore` comment stays only where no annotation can describe the code,
and each comment gives its reason. One error shows a possible bug: a caller
that gives an `AsyncMistAPIClient` and sets `use_async_api` to false gets an
`AttributeError`. The issue jmorrison-juniper/MistWANPerformance#28 records it.

## What moved

### Shared workflows

| Shared workflow | Replaces | Consumers |
| - | - | - |
| `reusable-container-image.yml` | The build and push job of each container workflow, and the image job of each release workflow | MistHelper, MistCircuitStats, MistCircuitStats-Redis, MistHelper-Go, MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison |
| `reusable-quality-gate-issues.yml` | The `create_failure_issues` and `close_resolved_issues` jobs | MistHelper, MistCircuitStats, MistHelper-Go, MistWANPerformance, MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison, MistCircuitStats-Redis |
| `reusable-auto-merge.yml` | The jobs of the MistHelper `auto-merge.yml`, with its orphaned-push report | MistHelper, MistCircuitStats, MistHelper-Go |
| `reusable-close-linked-issues.yml` | The old copy of the MistHelper `close-linked-issues.yml` | MistHelper, MistCircuitStats, MistHelper-Go |
| `reusable-stranded-branch-report.yml` | The job of the MistHelper `stranded-branch-report.yml` | Each repository in the [Scope](#scope) table |
| `reusable-ste-lint.yml` | The job of the MistHelper `ste-lint.yml` | Each repository in the [Scope](#scope) table, and misthelper-devtools |
| `reusable-python-quality-gates.yml` | The gate jobs of each Python quality gate workflow, and the MistHelper template `.github/quality-gates-portable.yml` | MistCircuitStats, MistWANPerformance, starlink-dashboard, MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison, MistCircuitStats-Redis |
| `reusable-codeql.yml` | The `analyze` job of each CodeQL workflow | MistHelper, MistCircuitStats, MistHelper-Go, misthelper-devtools |

Each consumer keeps its triggers, its tag rules, its test jobs, and the job that
makes each release. MistHelper, MistCircuitStats, and MistHelper-Go set
`report-orphaned-push`, so each of them gets the orphaned-push report.

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

- The old jobs for gate issues used a free text search, so they could match
  any open issue that holds the same words. The shared workflow compares the
  exact title.

- The old MistHelper stranded branch job opened no tracking issue. Its `grep`
  pattern held `` \` ``, and GNU grep reads that pair as a buffer anchor. One
  failed compare also stopped the full report, for example MistHelper run
  34849396592. The shared command lists that branch with `unknown` values and
  continues. It also reads each page of branches, not only the first 100.

- In the old MistHelper copy, the orphaned-push report wrote a notice when a
  compare failed. It also wrote a notice for the head of a squash merge. The
  shared job fails when a call fails, and it writes no notice for a commit that
  the merged pull request holds.

- Release `v0.3.0` of the linked issue workflow read only the keywords in the
  text of the pull request. So a link from the Development panel closed no
  issue. Release `v0.4.0` reads the issue links that GitHub keeps for each pull
  request, as the old MistHelper copy did.

- The dispatch job of the old MistHelper copy compared the tip with the newest
  run in the run list. That list can answer from old data, so the job could
  start a second run for the same tip. Releases up to `v0.5.1` of the shared
  job did the same. Release `v0.5.2` asks for the runs on the tip commit. Issue
  #32 records the fault.

### Tools

| Tool | Replaces | Consumer |
| - | - | - |
| `complexity-gate` | The inline radon script of the quality gate workflow | MistCircuitStats (limit 15), MistOrgLicensingComparison (limit 15), MistHelper (limit 10) |
| `wan-port-report` | `check_ports.py` (MistWANPerformance commit ddbf849) | MistWANPerformance |
| `stranded-branch-report` | The MistHelper `scripts/report_stranded_branches.py` | Each repository in the [Scope](#scope) table, through `reusable-stranded-branch-report.yml` |
| `codeql-verdict-register` | The MistHelper `scripts/codeql_verdict_register.py` | MistHelper |
| `bandit-exclude-check` | The inline Bandit exclude check of the MistHelper CI workflow | MistHelper |
| `diagram-refs` | The MistHelper `scripts/lint_diagram_refs.py` | MistHelper |
| `exclusion-drift` | The MistHelper `scripts/check_exclusion_drift.py` | MistHelper |
| `markdown-link-check` | The link checker of the MistHelper Markdown guardrail test | MistHelper, through the guardrail test and the pre-commit hook |
| `pytest-chunks` | The MistHelper `scripts/run_local_test_shard.py` | MistHelper developers |
| `worktree-cleanup` | The MistHelper `scripts/cleanup_merged_worktrees.py` and `scripts/cleanup_stale_worktree_admin_dirs.py` | MistHelper developers |
| `test-quality-analyzer --changed-from` | The inline scope script of the MistHelper test quality gate | MistHelper |
| `devtools-pin-check` | No earlier tool. The owner compared each requirement pin with the workflow pins by hand. | Each caller of the radon gate or the STE lint workflow |

The other MistHelper tools, for example `test-quality-analyzer` and
`ste-linter`, moved here in an earlier migration, issue
jmorrison-juniper/MistHelper#3404. The MistHelper file
`documentation/development-tooling-migration.md` describes that migration.

### Actions and hooks

| Item | Replaces | Consumer |
| - | - | - |
| The `mermaid-lint` action | The MistHelper `scripts/mermaid/lint_mermaid.mjs` and its npm files | MistHelper |
| The `ste-linter` pre-commit hook | The local STE hook of the MistHelper `.pre-commit-config.yaml` | MistHelper |
| The `markdown-link-check` pre-commit hook | No earlier hook. The MistHelper guardrail test did the same check in CI. | MistHelper |

## What stays in each repository

- The Spec Kit files (`.specify/`, `.github/agents/`, `.github/prompts/`).
  The prompts use paths that are relative to the repository.

- The product scripts, the test suites, and the Dockerfiles.

- The Dependabot configuration of each repository, and the caller file of each
  CodeQL workflow. The caller file keeps its name, so the key of each code
  scanning analysis stays the same.

- The list of quality gates. A caller of `reusable-python-quality-gates.yml`
  turns on each gate with an input. MistHelper and MistHelper-Go keep their
  own gate jobs.

- The job that makes each release, with its tag form and its notes. See
  [Release workflows](#release-workflows).

MistHelper also keeps these files and jobs:

- The test quality baseline, `.github/test-quality-baseline.json`, and the
  analyzer settings, `.github/test-quality-config.toml`. MistHelper can change
  a rule or a baseline entry without a devtools release.

- The data files that the shared commands read:
  `.github/diagram-refs-allowlist.txt`, `quality_gate_exclusions.json`, and
  `documentation/security/codeql-verdict-register.md`.

- The product benchmarks under `scripts/benchmarks/`, and
  `scripts/e2e_store_reset.py`. They import the MistHelper product.

- The guardrail tests that read its workflow callers. They check that each
  caller pins a full commit of the shared workflow. They also check that each
  caller keeps the triggers and the permissions that the shared jobs need.

## Candidates for a later wave

- The radon gate of MistSiteDashboard, MistGuestAuthorizations, and
  MistCircuitStats-Redis. Some functions in each repository are above the limit
  15. The issues jmorrison-juniper/MistSiteDashboard#21,
  jmorrison-juniper/MistGuestAuthorizations#15, and
  jmorrison-juniper/MistCircuitStats-Redis#18 list them.

- The possible bug of MistWANPerformance that its mypy gate found. The issue
  jmorrison-juniper/MistWANPerformance#28 gives the input that fails.

- The BLE001 ignore of each file that catches `Exception`. A handler can name
  the exception types that it expects, but each change needs a test for each
  failure path first. Remove a file from the ignore list in the same pull
  request.

## Tasks for the owner

These tasks need the account owner. A workflow cannot do them.

1. At each devtools release, change the `requirements-dev.txt` pin by hand in
   the six repositories that the [Dependabot](#dependabot) section names. Also
   change the install line in the MistWANPerformance `agents.md`. Dependabot
   changes only the workflow pins. Since `v0.6.0`, the radon job and the
   ste-lint job write a warning when you forget a `requirements-dev.txt` pin.
   The warning gives the new text of the line. Issue #36 added this check.

2. Examine the branches in jmorrison-juniper/MistHelper#3529:
   `preservation/2746-pr3011-turn0`, `refactor/2926-portal-handlers-c`, and
   `fix/2746-fm-http-status`. Each branch holds work that no pull request
   holds. Open a pull request for each branch that you keep, and delete each
   other branch. The weekly report closes the issue when no branch stays on
   the list. The earlier report, jmorrison-juniper/MistHelper#3490, named the
   first two branches. A Copilot session closed it as a duplicate of #3529.
