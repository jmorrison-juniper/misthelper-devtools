# Data Model: Immediate Post-Merge Gate Dispatch

## Pull-request state

The live response has `state`, `mergedAt`, and `autoMergeRequest`.
Validate the fields with the existing state parser.

| State | Valid evidence | Result |
| --- | --- | --- |
| Merged | `MERGED` and valid `mergedAt` | Success and `merged=true` |
| Enabled | `OPEN`, null `mergedAt`, valid request with the selected method | Success without a true merge output |
| Pending | `OPEN`, null `mergedAt`, null request | Retry within the existing bound |
| Closed | `CLOSED`, null `mergedAt` | Fail without dispatch |
| Invalid | Missing, malformed, or inconsistent fields | Fail without dispatch |

Only a successful merge command or the specific merge-in-progress response
enters this verifier. A different command failure remains a failure.

## Dispatch snapshot

The snapshot holds the configured default branch, its current tip SHA, and the
configured gate names. The coverage query includes both branch and
exact SHA. Any run count greater than zero includes queued runs and is coverage.

The initial tip lookup failure stops the job before per-gate checks. For each
gate, the result is `covered`, `uncovered`, `dispatched`, or `dispatch-failed`.
Preserve the existing bounded retries and dispatch fallback for failed or
nonnumeric per-gate counts. The job reports failed dispatches after it attempts
other gates already found uncovered.

The job dispatches on the configured default-branch ref.
If the branch advances after the snapshot, dispatch can target a newer commit.
The check and dispatch are not atomic. Visible exact-tip coverage prevents a
repeat dispatch, but stale or unavailable run data can cause a repeat.

## Event eligibility

An eligible event is a manual request, a schedule, a merged closed pull request
on the configured default branch, or a confirmed immediate merge on that
branch. The configured gate list must be nonempty. The confirmed-merge output
is valid only when the enable job succeeded.
