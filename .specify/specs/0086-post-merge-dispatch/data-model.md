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

For each gate, the result is `covered`, `uncovered`, `dispatched`, or
`dispatch-failed`. Preserve the existing bounded retries for failed or
nonnumeric coverage counts. The job reports failed dispatches after it
attempts other uncovered gates.

The job dispatches on the configured default-branch ref.
The existing serialized job and exact-tip checks reduce duplicate dispatches.

## Event eligibility

An eligible event is a manual request, a schedule, a merged closed pull request
on the configured default branch, or a confirmed immediate merge on that
branch. The configured gate list must be nonempty. The confirmed-merge output
is valid only when the enable job succeeded.
