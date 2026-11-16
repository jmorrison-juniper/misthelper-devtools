# Research: Immediate Post-Merge Gate Dispatch

## Merge confirmation

**Decision**: Use one bounded live-state verifier after an accepted merge
command and after the existing merge-in-progress error. Publish a true merge
output only for a valid merged state.

**Rationale**: An accepted `gh pr merge --auto` can leave a pull request open.
The existing error recovery already distinguishes merged, enabled, pending,
and closed states.

**Alternatives considered**: Treat command success as a merge, which can
dispatch before the merge. Start gates from a new polling workflow, which adds
scope and duplicates the recovery path.

## Job scheduling

**Decision**: Add a job output and a dependency. Use `always()` with explicit
event conditions so a skipped enable job does not block recovery.

**Rationale**: The merge and dispatch jobs currently have no ordering. A plain
dependency skips dispatch after a skipped enable job.

**Alternatives considered**: Dispatch from the merge step, which duplicates
permissions, concurrency, and coverage logic. Treat the job result as proof,
which incorrectly includes enabled auto-merge.

## Coverage and failure handling

**Decision**: Preserve the exact-SHA lookup, five checks with 20-second
intervals, and the current behavior for failed or malformed coverage counts.
Keep dispatch failures visible and continue attempts for other gates.

**Rationale**: The requested repair concerns merge confirmation and dispatch
eligibility. Changing coverage failure handling or adding a second tip check
would expand behavior beyond the root cause. The existing dispatch job already
uses the exact tip SHA, bounded retries, and serialized execution.

**Alternatives considered**: Treat a failed lookup as a new failure mode or
recheck the branch tip before every dispatch. Both changes are outside the
minimum repair and can be evaluated in a separate issue.

## Timing and concurrency limits

**Decision**: Preserve job-level serialization and the retry settings. Treat
the two-minute value as a target under normal operation, not a hard guarantee.

**Rationale**: Four waits consume 80 seconds. GitHub controls queueing and API
latency. Its concurrency group limits active jobs but can replace a pending
job. A later recovery remains necessary if no eligible job completes.

**Alternatives considered**: Shorten or remove retries, which weakens the
existing stale-response protection. Add a durable queue, which exceeds scope.
Exact-SHA run visibility can also lag after dispatch, so serialization alone
cannot prove absolute deduplication under all API delays.

## Project policy

**Decision**: Apply the repository instructions and mark the optional
constitution gate unverified because its file is absent.

**Rationale**: `.specify/memory/constitution.md` is absent. Do not invent a
governance file or rely on an unavailable setup script. The feature path is
explicit in `.specify/feature.json`.

**Alternatives considered**: Invent a constitution or claim a passed gate.
Neither choice has repository support.
