# Contract: Auto-Merge Workflow

## Caller interface

Keep the existing `workflow_call` inputs, defaults, and job permissions.
`main-workflows` lists workflow files that accept `workflow_dispatch`.
`default-branch` supplies the only allowed dispatch ref. Do not add a caller
output, secret, credential, or event.

## Internal merge result

The enable job exposes an internal `merged` output from its merge step.
`merged=true` requires a validated `MERGED` response and valid `mergedAt`.
An accepted enabled request succeeds without `merged=true`.
An invalid or unavailable state fails the enable job.

## Dispatch eligibility

The dispatch job uses `needs: enable-auto-merge` and a `!cancelled()` condition.
A canceled workflow does not dispatch. The guard still permits recovery when
the enable job is skipped. It runs only when `main-workflows` is nonempty and
one condition is true:

1. The event is `schedule` or `workflow_dispatch`.
2. The event is a merged `pull_request.closed` for `default-branch`.
3. The event is a non-closed pull request for `default-branch`, the enable job
   succeeded, and its `merged` output is `true`.

The enable job retains its label and workflow-file guards. A skipped or failed
enable job cannot satisfy condition 3. Pending auto-merge does not dispatch.
Schedule, manual, and merged-closed recovery do not require a successful
enable job.

## Coverage and failures

The dispatch job retains its repository-and-branch concurrency group and
`cancel-in-progress: false`. It queries each gate for the current branch tip
SHA and keeps five checks 20 seconds apart. Queued and completed runs both
count as coverage.

A failed initial tip lookup or a tip value that is not a 40-character
lowercase hexadecimal SHA is visible and stops the job before per-gate checks.
A failed or malformed per-gate count follows the existing bounded retry and
fallback behavior. A failed dispatch remains visible. Continue attempts for
other gates already found uncovered, then fail the job when a dispatch fails.
`gh workflow run` targets `default-branch`, never the pull-request head.

Coverage uses a snapshot of the exact tip SHA. Dispatch uses the mutable branch
ref. If the branch advances after the snapshot, the run can target a newer
commit. The workflow cannot atomically bind the checked SHA to a dispatched
run. Visible exact-commit coverage prevents a repeat dispatch, but stale or
unavailable run-list data can cause the existing fallback to dispatch again.
Manual and scheduled recovery remain the fallback.
