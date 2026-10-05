# `<repository name>` agent instructions

This file holds the rules that apply to `<repository name>` only. The rules that apply to each
repository of this owner are in `AGENTS.md` at the repository root. Read `AGENTS.md` first. This
file adds to it, and it does not hold a copy of a rule from it. Where the two files disagree, obey
`AGENTS.md` for a writing rule, a safety rule, or a security rule.

<!--
How to use this skeleton. Replace each `<placeholder>`. Remove a section that does not apply, such
as the container section in a repository with no containers. Keep the paragraph above as it is.

Each sentence here must earn its tokens. Put a fact here only when an agent needs it for this
repository and `AGENTS.md` does not hold it. Write in Simplified Technical English, so the file
scores 80 or above with `ste-linter`. Remove this comment block when you finish the file.
-->

## What this repository is

One paragraph: the purpose of the product, the language, the audience, and the operating systems.
Does not belong here: the role of the agent or the autonomy rules, because `AGENTS.md` holds them.

## Language and environment

The language version, the package manager, and the commands that build a work environment in a new
worktree, for example `<bootstrap command>`. Does not belong here: the rule that each worktree gets
its own environment.

## Local gates

A table with one row for each gate: the gate, the command, and the expected result. Include the
compile check, the linter, the formatter, the type check, the tests, and the STE gate. Does not
belong here: the rule to run the gates before a commit, or the rule to fix a finding and not
suppress it.

| Gate | Command | Expected result |
| - | - | - |
| `<gate>` | `<command>` | `<result>` |

## Architecture and conventions

The primary packages or classes, the data flow, and the naming conventions of this repository.
Also the hot files that only one agent changes at a time, and the file where Spec Kit writes its
context when the repository holds `.specify/`. Does not belong here: the five-item rule, the
inline comment rule, or the action logging rule.

## Safety in this repository

The destructive operations of this product, the confirmation words, the data directories, the
file permission rules, and the backup rules for a data rewrite. Does not belong here: the generic
rule that a destructive operation needs a typed confirmation.

## Containers and ports

The compose group, the name pattern for an ephemeral container, and the cleanup commands. Also the
ports of the local stack and the free port range for a test. Remove this section when the repository has
no containers. Does not belong here: the four container rules, because `AGENTS.md` holds them.

| Port | Owner |
| - | - |
| `<port>` | `<service>` |

## Git and GitHub in this repository

The type labels and the scope labels, the changelog fragment folder and its name rule, and the pull
request template. Also the workflows, the minutes that each one costs, and the version format. Say
if the repository has a CodeQL workflow and an `auto-merge` label. Does not belong here: the
branch model, the commit format, or the merge rules.

## Known pitfalls

One bullet for each measured failure of this repository, with the issue number and the repair.
Does not belong here: a general lesson that applies to each repository, which goes to `AGENTS.md`.

## Key files

| File | Purpose |
| - | - |
| `<path>` | `<one line>` |

## External resources

The documentation, the SDK, and the example implementations that this repository uses.
