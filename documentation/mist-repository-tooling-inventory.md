# Mist repository tooling inventory

This document records the development tooling of each Mist repository. It
gives the part that moved to this repository, the part that stays in the
repository, and the candidates for a later wave.

A repository calls a shared workflow at a pinned commit. See
[Shared workflows](../README.md#shared-workflows) for the pin rule.

## Scope

The scan read each repository of the `jmorrison-juniper` account that holds
Mist code. It did not read a repository of another owner, for example
`tmunzer/mistmcp`, and it did not read a repository that holds no Mist code.

| Repository | Tooling found | Wave 1 result |
| - | - | - |
| MistHelper | The source of most shared workflows. A separate migration moves its tooling. | Not changed here. |
| MistCircuitStats | Quality gates with an inline radon script, gate issues, auto-merge, linked issue close, container build | Calls the shared workflows. Uses `complexity-gate`. |
| MistHelper-Go | Quality gates, gate issues, auto-merge, linked issue close, Copilot assign, container build | Calls the shared workflows. |
| MistSiteDashboard | Two container build workflows | Calls the shared container workflow. |
| MistGuestAuthorizations | Container build, release | Calls the shared container workflow. |
| MistOrgLicensingComparison | Container build | Calls the shared container workflow. |
| MistCircuitStats-Redis | Container build | Calls the shared container workflow. |
| MistWANPerformance | `check_ports.py`, a debug script | The script moved here as `tools/wan_port_report.py`. |
| MistCiscoConfigConverter | No workflow. The image copies the whole repository. | The image ignore file drops the development files. |
| MistDSW | Spec Kit files and product scripts only | No change. |
| starlink-dashboard | Local ruff and pytest settings only | No change. |

## What moved

### Shared workflows

| Shared workflow | Replaces | Consumers |
| - | - | - |
| `reusable-container-image.yml` | The build and push job of each container workflow | MistCircuitStats, MistCircuitStats-Redis, MistHelper-Go, MistSiteDashboard, MistGuestAuthorizations, MistOrgLicensingComparison |
| `reusable-quality-gate-issues.yml` | The `create_failure_issues` and `close_resolved_issues` jobs | MistCircuitStats, MistHelper-Go |
| `reusable-auto-merge.yml` | The old copy of the MistHelper `auto-merge.yml` | MistCircuitStats, MistHelper-Go |
| `reusable-close-linked-issues.yml` | The old copy of the MistHelper `close-linked-issues.yml` | MistCircuitStats, MistHelper-Go |
| `reusable-copilot-assign.yml` | `copilot-auto-assign.yml` and `copilot-label-checkbox.yml` | MistHelper-Go |

Each consumer keeps its triggers, its tag rules, its test jobs, and its release
jobs.

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
| `complexity-gate` | The inline radon script of the quality gate workflow | MistCircuitStats (limit 15) |
| `wan-port-report` | `check_ports.py` (MistWANPerformance commit ddbf849) | MistWANPerformance |

## What stays in each repository

- The Spec Kit files (`.specify/`, `.github/agents/`, `.github/prompts/`).
  The prompts use paths that are relative to the repository.

- The product scripts, the test suites, and the Dockerfiles.

- The CodeQL workflow and the Dependabot configuration of each repository.

- The quality gate jobs. Each repository keeps its own list of gates.

## Candidates for a later wave

- MistHelper uses its own copies of the auto-merge, linked issue, gate issue,
  and container workflows. The MistHelper migration can move those copies to
  the shared workflows.

- MistHelper runs the same inline script for radon, with a limit of 10. It
  can use `complexity-gate --max 10`.

- The release workflows of MistGuestAuthorizations and MistHelper-Go use
  different tag forms and different release actions.

- MistSiteDashboard builds the same image in two workflows on each push to
  `main`.

- An STE lint gate for the other repositories, with the `ste-linter` command.
