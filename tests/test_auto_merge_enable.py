"""Exercise the shared auto-merge step with controlled GitHub responses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-auto-merge.yml"
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_DIR/calls"
if [ "$1" = pr ] && [ "$2" = merge ]; then
  if [ "$MERGE_STATUS" != 0 ]; then
    echo "${FAKE_MERGE_ERROR:-GraphQL: Merge already in progress (mergePullRequest)}" >&2
  fi
  exit "$MERGE_STATUS"
fi
answer="$(head -n 1 "$FAKE_DIR/answers")"
tail -n +2 "$FAKE_DIR/answers" > "$FAKE_DIR/next"
mv "$FAKE_DIR/next" "$FAKE_DIR/answers"
if [ "$answer" = LOOKUP_FAILURE ]; then
  echo "HTTP 403: Forbidden" >&2
  exit 1
fi
printf '%s\\n' "$answer"
"""

MERGED = {"state": "MERGED", "mergedAt": "2026-10-30T16:25:02Z", "autoMergeRequest": None}
OPEN = {"state": "OPEN", "mergedAt": None, "autoMergeRequest": None}
ENABLED = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": {"enabledAt": "2026-10-30T16:25:02Z", "mergeMethod": "SQUASH"},
}
CLOSED = {"state": "CLOSED", "mergedAt": None, "autoMergeRequest": None}


@pytest.fixture
def merge_step() -> dict[str, Any]:
    """Read the actual step so the tests execute the workflow implementation."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(step for step in document["jobs"]["enable-auto-merge"]["steps"] if step["name"] == "Enable auto-merge")


def run_step(
    tmp_path: Path, step: dict[str, Any], answers: list[object], merge_status: int = 1, pr_number: str = "4534"
) -> subprocess.CompletedProcess[str]:
    """Run the workflow step with a fake gh and no retry delay."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("The merge step tests need bash.")
    if shutil.which("jq") is None:
        pytest.skip("The merge step tests need jq.")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8", newline="\n")
    sleep.chmod(0o755)
    (tmp_path / "answers").write_text(
        "".join(f"{answer if isinstance(answer, str) else json.dumps(answer)}\n" for answer in answers),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_DIR": str(tmp_path),
        "MERGE_STATUS": str(merge_status),
        "MERGE_METHOD": "squash",
        "PR_NUMBER": pr_number,
        "GH_REPO": "octo/demo",
    }
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("pr_number", "answers", "expected", "lookups"),
    [
        ("4534", [MERGED], "merged", 1),
        ("4554", [OPEN, OPEN, MERGED], "merged", 3),
        ("4534", [ENABLED], "enabled", 1),
    ],
)
def test_failed_request_requires_verified_success(
    tmp_path: Path, merge_step: dict[str, Any], pr_number: str, answers: list[object], expected: str, lookups: int
) -> None:
    """The failed request can succeed only after a live state proves the outcome."""
    result = run_step(tmp_path, merge_step, answers, pr_number=pr_number)
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert result.returncode == 0, result.stderr
    assert f"Verified pull request {pr_number}: {expected}." in result.stdout
    assert "Merge already in progress" in result.stderr
    assert calls[0] == f"pr merge {pr_number} --squash --auto"
    assert len(calls) == lookups + 1


@pytest.mark.parametrize(
    ("answers", "message", "lookups"),
    [
        ([OPEN] * 5, "did not reach a verified", 5),
        ([CLOSED], "closed without a merge", 1),
        (["LOOKUP_FAILURE"], "state lookup failed", 1),
        (["not-json"], "state response is invalid", 1),
        ([{}], "state response is invalid", 1),
        ([{"state": "MERGED", "mergedAt": None, "autoMergeRequest": None}], "state response is invalid", 1),
        ([{"state": "MERGED", "mergedAt": "bad", "autoMergeRequest": None}], "state response is invalid", 1),
        ([{"state": "OPEN", "mergedAt": None, "autoMergeRequest": {}}], "state response is invalid", 1),
        (
            [
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": {"enabledAt": "2026-10-30T16:25:02Z", "mergeMethod": "MERGE"},
                }
            ],
            "state response is invalid",
            1,
        ),
    ],
)
def test_failed_request_does_not_hide_unverified_states(
    tmp_path: Path, merge_step: dict[str, Any], answers: list[object], message: str, lookups: int
) -> None:
    """Unknown and unsuccessful states must not turn a failed request into success."""
    result = run_step(tmp_path, merge_step, answers)
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert result.returncode != 0
    assert message in result.stdout
    assert "Merge already in progress" in result.stderr
    assert len(calls) == lookups + 1


def test_successful_request_needs_no_recovery(tmp_path: Path, merge_step: dict[str, Any]) -> None:
    """An accepted merge request must not make an unnecessary lookup."""
    result = run_step(tmp_path, merge_step, [], merge_status=0)
    assert result.returncode == 0
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == ["pr merge 4534 --squash --auto"]


def test_authorization_failure_does_not_use_merge_recovery(
    tmp_path: Path, merge_step: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorization error must fail even when a later lookup could show a merge."""
    monkeypatch.setenv("FAKE_MERGE_ERROR", "HTTP 403: Forbidden")
    result = run_step(tmp_path, merge_step, [MERGED])
    assert result.returncode != 0
    assert "HTTP 403: Forbidden" in result.stderr
    assert "without a merge-in-progress response" in result.stdout
    assert (tmp_path / "calls").read_text(encoding="utf-8").splitlines() == ["pr merge 4534 --squash --auto"]


def test_merge_job_keeps_the_workflow_guard_and_permissions(merge_step: dict[str, Any]) -> None:
    """Recovery must not bypass the guard or change the job permissions."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"]["enable-auto-merge"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert merge_step["if"] == "steps.workflow_guard.outputs.blocked == 'false'"
    assert "--admin" not in merge_step["run"]
