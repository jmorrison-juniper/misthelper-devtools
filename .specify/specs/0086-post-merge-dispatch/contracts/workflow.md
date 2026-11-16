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

The dispatch job uses `needs: enable-auto-merge` and an `always()` condition.
It runs only when `main-workflows` is nonempty and one condition is true:

1. The event is `schedule` or `workflow_dispatch`.
2. The event is a merged `pull_request.closed` for `default-branch`.
3. The event is a non-closed pull request for `default-branch`, the enable job
   succeeded, and its `merged` output is `true`.

The enable job retains its label and workflow-file guards. A skipped or failed
enable job cannot satisfy condition 3. Pending auto-merge does not dispatch.

## Coverage and failures

The dispatch job retains its repository-and-branch concurrency group and
`cancel-in-progress: false`. It queries each gate for the current branch tip
SHA and keeps five checks 20 seconds apart. Queued and completed runs both
count as coverage.

A failed or malformed coverage lookup follows the existing bounded retry and
fallback behavior. A failed dispatch remains visible. Continue attempts for
other missing gates, then fail the job when a dispatch fails.
`gh workflow run` targets `default-branch`, never the pull-request head.

GitHub scheduling, eventual run visibility, and mutable branch refs prevent a
strict two-minute or absolute deduplication guarantee. Manual and scheduled
recovery remain the fallback.
