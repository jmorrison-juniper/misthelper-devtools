# Feature Specification: Immediate Post-Merge Gate Dispatch

**Feature Branch**: `0086-post-merge-dispatch`

**Created**: 2026-11-15

**Status**: Draft

**Input**: Upstream issue jmorrison-juniper/misthelper-devtools#86 and user requirements: immediately dispatch default-branch gates after a completed GITHUB_TOKEN merge, without treating an accepted delayed auto-merge request as a completed merge.

## User Scenarios & Testing

### User Story 1 - Start gates after an immediate merge (Priority: P1)

When an auto-merge request completes a merge into the configured default branch, maintainers need the configured default-branch gates to start promptly, even though GitHub suppresses workflow events caused by GITHUB_TOKEN.

**Why this priority**: Without these gates, the merged commit can remain unvalidated until a later recovery run.

**Independent Test**: Simulate an immediate successful merge and inspect the resulting workflow activity for the exact tip SHA observed at dispatch start.

**Acceptance Scenarios**:

1. **Given** a pull request merges immediately into the configured default branch using GITHUB_TOKEN, **When** the merge is confirmed complete, **Then** the same workflow run checks coverage and attempts each uncovered configured gate without waiting for scheduled recovery.
2. **Given** a gate already has a run for that exact commit, **When** post-merge dispatch checks coverage, **Then** that gate is not dispatched again.
3. **Given** a dispatch attempt fails, **When** the dispatch processing finishes, **Then** the originating run visibly reports the failed gate and has a failing result.

### User Story 2 - Distinguish pending auto-merge from completed merge (Priority: P1)

When GitHub accepts an auto-merge request that will complete only after required checks pass, maintainers need the request to remain pending rather than trigger gates as if a merge had occurred.

**Why this priority**: Dispatching before the merge validates the wrong commit and can create unnecessary runs.

**Independent Test**: Simulate an accepted but still-open auto-merge request and verify that no post-merge dispatch occurs; then verify the completed default-branch commit through the supported recovery path.

**Acceptance Scenarios**:

1. **Given** an auto-merge request is accepted but the pull request remains open and unmerged, **When** its state is checked, **Then** the request is not considered a completed merge and does not trigger post-merge dispatch.
2. **Given** a delayed auto-merge later completes but no merge-triggered run is available, **When** a scheduled or manual recovery run executes, **Then** it checks each gate against the tip SHA observed at recovery start and starts gates that lack visible coverage. Dispatch still uses the mutable default-branch ref.
3. **Given** the merge-in-progress response occurs, **When** the live pull-request state is checked, **Then** the existing bounded state verification continues to distinguish merged, pending/enabled, and closed-without-merge outcomes.

### User Story 3 - Recover safely and idempotently (Priority: P2)

Maintainers need manual and scheduled recovery to remain available, while repeated or concurrent triggers avoid duplicate work when exact-commit coverage is visible and never target a pull-request branch.

**Why this priority**: Recovery covers missed events and API delays without weakening branch safety or claiming a duplicate-free result when GitHub returns stale or unavailable run data.

**Independent Test**: Run recovery repeatedly and concurrently against the same default-branch tip, including visible gate runs, unavailable run counts, a failed tip lookup, and a non-default pull-request branch.

**Acceptance Scenarios**:

1. **Given** a manual or scheduled recovery trigger, **When** configured gates are checked, **Then** missing gates are dispatched against the configured default branch and covered gates are left alone.
2. **Given** concurrent or repeated triggers for the same tip and visible run data, **When** dispatch processing completes, **Then** a gate with exact-commit coverage is not dispatched again. Stale or unavailable coverage data can still cause the existing fallback to dispatch.
3. **Given** the target is not the configured default branch or the pull request is closed without a merge, **When** dispatch eligibility is evaluated, **Then** no post-merge gate dispatch targets that branch or pull request head.
4. **Given** the initial tip lookup fails, **When** processing starts, **Then** the job reports the failure and stops before per-gate checks. Given an individual gate count or dispatch fails, **When** processing completes, **Then** the existing per-gate fallback and visible dispatch failure behavior apply.
5. **Given** the workflow is canceled, **When** dispatch eligibility is evaluated, **Then** no gate starts. Scheduled, manual, and merged-closed recovery still run when the enable job is skipped.

### Edge Cases

- The default-branch tip advances after the coverage snapshot; the workflow dispatches by branch ref, so the run can target a newer commit. The workflow does not provide an atomic check-and-dispatch operation.
- The initial tip lookup fails or does not return a 40-character lowercase hexadecimal SHA; the job fails before per-gate checks and dispatches.
- GitHub returns a temporary or malformed pull-request state; the run must fail visibly without inferring that the pull request merged.
- GitHub returns a failed or nonnumeric per-gate workflow-run count; the existing bounded retry and dispatch fallback must remain in effect, and the run must show each unavailable count. A failed initial tip lookup stops the job before per-gate checks.
- A gate run is queued but not yet started; it still counts as coverage for its exact commit and must not provoke a duplicate dispatch.
- A merged pull request targets a branch other than the configured default branch; it must not start default-branch gates.
- A dispatch failure for one gate must not prevent attempts for other uncovered gates.

## Requirements

### Functional Requirements

- **FR-001**: After a GITHUB_TOKEN merge into the configured default branch is confirmed complete, the system MUST check each configured gate against the exact tip SHA observed by the dispatcher and attempt uncovered gates on the configured default-branch ref in the same workflow run.
- **FR-002**: The system MUST treat an accepted auto-merge request on an open, unmerged pull request as pending, not as a completed merge, and MUST NOT dispatch post-merge gates on that basis.
- **FR-003**: The system MUST preserve manual and scheduled recovery and check coverage against the exact tip SHA observed at the start of dispatch processing.
- **FR-004**: The system MUST regard an existing queued or completed gate run for the exact observed tip SHA as coverage and MUST avoid dispatch when that coverage is visible. The system MUST preserve its current bounded retry and fallback behavior when coverage data is stale, failed, or malformed.
- **FR-005**: The system MUST preserve the existing merge-in-progress behavior, including bounded live-state verification and distinct outcomes for merged, pending/enabled, and closed-without-merge pull requests.
- **FR-006**: The system MUST dispatch only for the configured default branch; it MUST NOT bypass or weaken branch protection.
- **FR-007**: The system MUST report pull-request state and dispatch failures. A failed initial tip lookup or a tip value that is not a 40-character lowercase hexadecimal SHA MUST fail before per-gate checks. Failed or nonnumeric per-gate counts MUST preserve the bounded retry and fallback behavior. The system MUST attempt other gates after a dispatch failure and fail the run if any required dispatch fails.
- **FR-008**: The system MUST bound retries and serialize or otherwise control overlapping dispatch work. It MUST avoid duplicate dispatches when exact-commit coverage is visible, but MUST NOT promise this result when GitHub returns stale or unavailable coverage data.
- **FR-009**: The system MUST preserve the existing supported merge methods and merge eligibility behavior.
- **FR-010**: The system MUST NOT dispatch gates after workflow cancellation. It MUST preserve scheduled, manual, and merged-closed recovery when the enable job is skipped.

### Key Entities

- **Default-branch commit**: The exact commit at the configured default branch tip for which gate coverage is checked.
- **Configured gate**: A default-branch workflow that must cover the commit by an existing run or a dispatch attempt.
- **Auto-merge request**: A request that may be accepted while a pull request remains open, distinct from a completed merge.
- **Gate coverage**: Evidence that a configured gate has a queued or completed run for the exact commit.

## Success Criteria

### Measurable Outcomes

- **SC-001**: For an immediately completed GITHUB_TOKEN merge, the same workflow run checks coverage against the tip SHA observed at dispatch start and attempts each uncovered gate without waiting for scheduled or manual recovery.
- **SC-002**: In all tested pending-auto-merge cases, no post-merge dispatch occurs before the pull request is actually merged.
- **SC-003**: Across repeated and concurrent triggers, a gate with visible exact-tip coverage is not dispatched again. A stale or unavailable run list can cause the existing fallback to dispatch it.
- **SC-004**: Every tested pull-request state or dispatch failure is identifiable in the originating run. A failed initial tip lookup or a tip value that is not a 40-character lowercase hexadecimal SHA stops the job. A failed dispatch does not prevent attempts for other gates already found uncovered.
- **SC-005**: Manual and scheduled recovery check each gate against the exact tip SHA observed at dispatch start and leave visibly covered gates undispatched. Dispatch uses the mutable default-branch ref, so a later branch update can change the run's target commit.

## Assumptions

- The configured gate workflows are eligible for manual dispatch and accept the default-branch reference, as required by the existing reusable-workflow contract.
- Existing scheduled and manual recovery remain the fallback for delayed merges whose completion event does not start a workflow.
- “Immediate” means the workflow starts dispatch processing in the same run after it confirms the merge. GitHub queue and API latency prevent a strict elapsed-time guarantee.
- The dispatcher snapshots the default-branch tip for coverage queries, then dispatches by branch ref. GitHub does not provide an atomic operation that binds the coverage snapshot to the workflow run.
- Existing branch protection rules and required checks remain authoritative; this feature starts gates and does not change merge policy.
- Scope is limited to `.github/workflows/reusable-auto-merge.yml`, directly related focused contracts/helpers if necessary, directly related documentation, and Spec Kit records. Consumer repositories and unrelated files are out of scope.
