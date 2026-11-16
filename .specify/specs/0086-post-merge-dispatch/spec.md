# Feature Specification: Immediate Post-Merge Gate Dispatch

**Feature Branch**: `0086-post-merge-dispatch`

**Created**: 2026-11-15

**Status**: Draft

**Input**: Upstream issue jmorrison-juniper/misthelper-devtools#86 and user requirements: immediately dispatch default-branch gates after a completed GITHUB_TOKEN merge, without treating an accepted delayed auto-merge request as a completed merge.

## User Scenarios & Testing

### User Story 1 - Start gates after an immediate merge (Priority: P1)

When an auto-merge request completes a merge into the configured default branch, maintainers need the configured default-branch gates to start promptly, even though GitHub suppresses workflow events caused by GITHUB_TOKEN.

**Why this priority**: Without these gates, the merged commit can remain unvalidated until a later recovery run.

**Independent Test**: Simulate an immediate successful merge and inspect the resulting workflow activity for the exact default-branch commit.

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
2. **Given** a delayed auto-merge later completes but no merge-triggered run is available, **When** a scheduled or manual recovery run executes, **Then** it checks and starts only gates that lack coverage for the exact current default-branch commit.
3. **Given** the merge-in-progress response occurs, **When** the live pull-request state is checked, **Then** the existing bounded state verification continues to distinguish merged, pending/enabled, and closed-without-merge outcomes.

### User Story 3 - Recover safely and idempotently (Priority: P2)

Maintainers need manual and scheduled recovery to remain available, while repeated or concurrent triggers avoid duplicate work and never target a pull-request branch.

**Why this priority**: Recovery covers missed events and API delays without weakening branch safety or creating a workflow storm.

**Independent Test**: Run recovery repeatedly and concurrently against the same default-branch commit, including pre-existing gate runs and a non-default pull-request branch.

**Acceptance Scenarios**:

1. **Given** a manual or scheduled recovery trigger, **When** configured gates are checked, **Then** missing gates are dispatched against the configured default branch and covered gates are left alone.
2. **Given** concurrent or repeated triggers for the same current commit, **When** dispatch processing completes, **Then** no gate receives duplicate dispatches solely because of those triggers.
3. **Given** the target is not the configured default branch or the pull request is closed without a merge, **When** dispatch eligibility is evaluated, **Then** no post-merge gate dispatch targets that branch or pull request head.
4. **Given** a dispatch lookup or individual gate dispatch fails, **When** processing completes, **Then** the failure is visible and other missing gates are still attempted.

### Edge Cases

- The default-branch tip advances while coverage is being checked; dispatch decisions must use the exact current commit being evaluated, not a branch's most recent run alone.
- GitHub returns a temporary or malformed pull-request state; the run must fail visibly without inferring that the pull request merged.
- GitHub returns a failed or nonnumeric workflow-run count; the existing bounded retry and dispatch fallback must remain in effect, and the run must show each unavailable count.
- A gate run is queued but not yet started; it still counts as coverage for its exact commit and must not provoke a duplicate dispatch.
- A merged pull request targets a branch other than the configured default branch; it must not start default-branch gates.
- A dispatch failure for one gate must not prevent attempts for other uncovered gates.

## Requirements

### Functional Requirements

- **FR-001**: After a GITHUB_TOKEN merge into the configured default branch is confirmed complete, the system MUST promptly start each configured gate that lacks coverage for the exact current default-branch commit.
- **FR-002**: The system MUST treat an accepted auto-merge request on an open, unmerged pull request as pending, not as a completed merge, and MUST NOT dispatch post-merge gates on that basis.
- **FR-003**: The system MUST preserve manual and scheduled recovery and make them evaluate the exact current default-branch commit.
- **FR-004**: The system MUST regard an existing queued or completed gate run for the exact commit as coverage and MUST avoid duplicate dispatches for that commit.
- **FR-005**: The system MUST preserve the existing merge-in-progress behavior, including bounded live-state verification and distinct outcomes for merged, pending/enabled, and closed-without-merge pull requests.
- **FR-006**: The system MUST dispatch only for the configured default branch; it MUST NOT bypass or weaken branch protection.
- **FR-007**: The system MUST report pull-request state and dispatch failures, preserve the bounded retry and fallback behavior for failed or nonnumeric gate-run counts, attempt other gates after a dispatch failure, and fail the run if any required dispatch fails.
- **FR-008**: The system MUST bound retries and serialize or otherwise control overlapping dispatch work so repeated and concurrent events do not create an unbounded polling loop or a workflow storm.
- **FR-009**: The system MUST preserve the existing supported merge methods and merge eligibility behavior.

### Key Entities

- **Default-branch commit**: The exact commit at the configured default branch tip for which gate coverage is checked.
- **Configured gate**: A default-branch workflow that must cover the commit by an existing run or a dispatch attempt.
- **Auto-merge request**: A request that may be accepted while a pull request remains open, distinct from a completed merge.
- **Gate coverage**: Evidence that a configured gate has a queued or completed run for the exact commit.

## Success Criteria

### Measurable Outcomes

- **SC-001**: For an immediately completed GITHUB_TOKEN merge, the same workflow run checks exact-commit coverage and attempts each uncovered gate without waiting for scheduled or manual recovery.
- **SC-002**: In all tested pending-auto-merge cases, no post-merge dispatch occurs before the pull request is actually merged.
- **SC-003**: Across repeated and concurrent triggers for the same commit, each configured gate is dispatched at most once when no failure occurs and all relevant coverage data is available.
- **SC-004**: Every tested pull-request state or dispatch failure is identifiable in the originating run, and a failed dispatch does not prevent attempts for other missing gates. Failed or nonnumeric gate-run counts follow the existing bounded retry and fallback behavior.
- **SC-005**: Manual and scheduled recovery each identify uncovered gates for the exact current default-branch commit while leaving already-covered gates undispatched.

## Assumptions

- The configured gate workflows are eligible for manual dispatch and accept the default-branch reference, as required by the existing reusable-workflow contract.
- Existing scheduled and manual recovery remain the fallback for delayed merges whose completion event does not start a workflow.
- “Immediate” means the workflow starts dispatch processing in the same run after it confirms the merge. GitHub queue and API latency prevent a strict elapsed-time guarantee.
- Existing branch protection rules and required checks remain authoritative; this feature starts gates and does not change merge policy.
- Scope is limited to `.github/workflows/reusable-auto-merge.yml`, directly related focused contracts/helpers if necessary, directly related documentation, and Spec Kit records. Consumer repositories and unrelated files are out of scope.
