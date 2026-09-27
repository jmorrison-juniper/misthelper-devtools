"""Guard the rules that the shared workflows took over from MistHelper.

MistHelper kept these rules in its own workflow files, and a guardrail test
read each file. The rules now live in the reusable workflows of this
repository, so the guards move here with them. A change that drops a rule
fails here, before a consumer pins the change.

The rules and the issues that record them:

* jmorrison-juniper/MistHelper#1926: a closing keyword never closed its issue.
  The close job must read the links, sweep on a schedule, keep an issue that
  a person reopened, and report a failed close.
* jmorrison-juniper/MistHelper#2623: a run on main closed the gate issue of an
  open pull request. Only the run of that pull request may close that issue.
* jmorrison-juniper/MistHelper#1960: a push after the merge lost its commit.
  The orphan job must write one notice for each such commit.

The self-test workflow runs each shared workflow. These tests read the text,
so they also hold a rule that a dry run cannot reach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# PyYAML reads the bare key `on` as the boolean True.
TRIGGERS = True


def load_workflow(name: str) -> dict[str, Any]:
    """Parse one workflow file of this repository.

    Args:
        name: The file name below .github/workflows.

    Returns:
        The parsed workflow.
    """
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{name} must parse to a mapping."
    return document


def job_script(document: dict[str, Any], job: str) -> str:
    """Join the run scripts of one job.

    Args:
        document: The parsed workflow.
        job: The job ID.

    Returns:
        Each run script of the job, one after the other.
    """
    steps = document["jobs"][job]["steps"]
    return "\n".join(str(step.get("run", "")) for step in steps)


def input_default(document: dict[str, Any], name: str) -> Any:
    """Return the default value of one workflow_call input.

    Args:
        document: The parsed workflow.
        name: The input name.

    Returns:
        The default value of the input.
    """
    return document[TRIGGERS]["workflow_call"]["inputs"][name]["default"]


@pytest.fixture(scope="module")
def close_workflow() -> dict[str, Any]:
    """Parse the shared workflow that closes the linked issues."""
    return load_workflow("reusable-close-linked-issues.yml")


@pytest.fixture(scope="module")
def gate_workflow() -> dict[str, Any]:
    """Parse the shared workflow that syncs the quality gate issues."""
    return load_workflow("reusable-quality-gate-issues.yml")


@pytest.fixture(scope="module")
def merge_workflow() -> dict[str, Any]:
    """Parse the shared auto-merge workflow."""
    return load_workflow("reusable-auto-merge.yml")


class TestCloseLinkedIssues:
    """The close job closes each linked issue once and keeps a human reopen."""

    JOB = "close-linked-issues"

    def test_the_job_reads_the_links_that_github_records(self, close_workflow: dict[str, Any]) -> None:
        """GitHub records each closing link, so the job must read that list."""
        script = job_script(close_workflow, self.JOB)
        assert "closingIssuesReferences" in script
        # A pull request with no link is normal, so the list can be empty.
        assert ".closingIssuesReferences[]?" in script

    def test_the_sweep_reads_the_merged_pull_requests(self, close_workflow: dict[str, Any]) -> None:
        """An auto-merge starts no closed event, so the sweep must find the merge."""
        assert "gh pr list --state merged" in job_script(close_workflow, self.JOB)

    def test_the_job_keeps_an_issue_that_a_person_reopened(self, close_workflow: dict[str, Any]) -> None:
        """A reopen after the merge is a human decision, and the sweep keeps it."""
        script = job_script(close_workflow, self.JOB)
        assert "timeline?per_page=100" in script
        # A busy issue can put a recent reopen on a later page.
        assert "--paginate" in script
        assert '.event == "reopened"' in script
        assert '.actor.type == "User"' in script
        # A reopen before the merge must not stop the close.
        assert ".created_at >" in script

    def test_the_job_closes_an_open_issue_only(self, close_workflow: dict[str, Any]) -> None:
        """A second close call on a closed issue fails, so the job reads the state first."""
        script = job_script(close_workflow, self.JOB)
        assert "--json state" in script
        assert '!= "OPEN"' in script

    def test_the_job_closes_for_a_merge_into_the_default_branch_only(self, close_workflow: dict[str, Any]) -> None:
        """GitHub records a closing link only for a pull request into the default branch."""
        assert ".baseRefName == $base" in job_script(close_workflow, self.JOB)

    def test_a_failed_close_fails_the_job(self, close_workflow: dict[str, Any]) -> None:
        """Issue #1926 stayed hidden because nothing reported the missed close."""
        script = job_script(close_workflow, self.JOB)
        assert "set -euo pipefail" in script
        assert "::error::" in script
        assert 'exit "${failed}"' in script

    def test_the_job_holds_the_least_permission(self, close_workflow: dict[str, Any]) -> None:
        """The job closes issues and reads pull requests, and it needs nothing more."""
        permissions = close_workflow["jobs"][self.JOB]["permissions"]
        assert permissions == {"issues": "write", "pull-requests": "read"}


class TestQualityGateIssues:
    """An open pull request keeps its own gate issue."""

    JOB = "sync-issues"

    def test_a_main_run_reads_the_state_of_the_named_pull_request(self, gate_workflow: dict[str, Any]) -> None:
        """A sweep that never reads the state cannot keep an open pull request."""
        script = job_script(gate_workflow, self.JOB)
        assert "gh pr view" in script
        assert "--json state" in script

    def test_a_main_run_keeps_the_issue_of_an_open_pull_request(self, gate_workflow: dict[str, Any]) -> None:
        """The skip is the repair of issue #2623."""
        script = job_script(gate_workflow, self.JOB)
        # The title suffix is the one marker that names the owner of an issue.
        assert "(PR #" in script
        assert '[ "${pr_state}" = "OPEN" ]' in script
        assert "continue" in script

    def test_a_pull_request_run_closes_its_own_title_only(self, gate_workflow: dict[str, Any]) -> None:
        """A pull request run matches the exact title, and a main run matches any title."""
        script = job_script(gate_workflow, self.JOB)
        assert 'mode="exact"' in script
        assert 'mode="any"' in script

    def test_a_passing_gate_still_closes_an_issue(self, gate_workflow: dict[str, Any]) -> None:
        """A guard that closes nothing leaves each repaired gate issue open."""
        assert "gh issue close" in job_script(gate_workflow, self.JOB)

    def test_the_default_scope_is_main(self, gate_workflow: dict[str, Any]) -> None:
        """A pull request run changes no issue unless the caller asks for that."""
        assert input_default(gate_workflow, "scope") == "main"

    def test_an_issue_fault_keeps_a_green_run_green(self, gate_workflow: dict[str, Any]) -> None:
        """An issue API fault must not fail the gate run."""
        job = gate_workflow["jobs"][self.JOB]
        assert job["continue-on-error"] == "${{ !inputs.fail-on-error }}"
        assert input_default(gate_workflow, "fail-on-error") is False

    def test_the_job_holds_the_least_permission(self, gate_workflow: dict[str, Any]) -> None:
        """The job writes issues and reads pull requests, and it needs nothing more."""
        permissions = gate_workflow["jobs"][self.JOB]["permissions"]
        assert permissions == {"issues": "write", "pull-requests": "read"}


class TestAutoMerge:
    """The auto-merge jobs react to the correct events only."""

    def test_a_closed_pull_request_skips_the_merge_job(self, merge_workflow: dict[str, Any]) -> None:
        """A closed pull request cannot accept auto-merge, so the job would fail each time."""
        condition = merge_workflow["jobs"]["enable-auto-merge"]["if"]
        assert "github.event.action != 'closed'" in condition

    def test_the_dispatch_job_runs_after_a_merge_and_on_the_schedule(self, merge_workflow: dict[str, Any]) -> None:
        """A merge repairs the default branch at once, and the schedule is the backstop."""
        condition = merge_workflow["jobs"]["dispatch-main-workflows"]["if"]
        assert "github.event.pull_request.merged == true" in condition
        assert "github.event_name == 'schedule'" in condition

    def test_the_orphan_notice_is_opt_in(self, merge_workflow: dict[str, Any]) -> None:
        """The notice needs a push trigger, so a caller must ask for it."""
        assert input_default(merge_workflow, "report-orphaned-push") is False
        condition = merge_workflow["jobs"]["report-orphaned-push"]["if"]
        assert "inputs.report-orphaned-push" in condition
        assert "github.event_name == 'push'" in condition
        # A push that deletes a branch adds no commit.
        assert "github.event.deleted != true" in condition

    def test_the_orphan_job_writes_one_notice_for_each_commit(self, merge_workflow: dict[str, Any]) -> None:
        """A rerun must add no second copy of the notice."""
        script = job_script(merge_workflow, "report-orphaned-push")
        assert "<!-- auto-merge:orphaned-push:${PUSHED_SHA} -->" in script
        assert 'grep -qF "${marker}"' in script

    def test_the_orphan_job_skips_a_branch_with_an_open_pull_request(self, merge_workflow: dict[str, Any]) -> None:
        """An open pull request takes the push to a review, so no notice is necessary."""
        assert "--state open" in job_script(merge_workflow, "report-orphaned-push")

    def test_the_orphan_notice_names_the_recovery(self, merge_workflow: dict[str, Any]) -> None:
        """The notice gives the one command that recovers the commits."""
        script = job_script(merge_workflow, "report-orphaned-push")
        assert "git cherry-pick ${merged_head}..${PUSHED_SHA}" in script
