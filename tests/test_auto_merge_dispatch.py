"""Run the dispatch step of the shared auto-merge workflow with a fake gh.

The dispatch job starts each main workflow that has no run on the tip of the
default branch. The run list of GitHub can answer from old data, and an old
answer made the job start duplicate runs (#32). These tests run the step script
in bash, as GitHub runs it, and a fake gh gives each answer. Thus the tests
prove what the job does with an old answer, with no run, and with a failed call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "reusable-auto-merge.yml"
JOB = "dispatch-main-workflows"
REPO = "octo/demo"
TIP = "0123456789abcdef0123456789abcdef01234567"

# The fake gh records each call. A query for the runs of a workflow reads the
# next line of the answer file of that workflow, and the last line repeats. The
# answer `fail` makes the call fail, as a failed API call does.
FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_GH_DIR}/calls.log"
if [ "$1" = "api" ]; then
  case "$2" in
    */commits/*)
      echo "${FAKE_TIP}"
      exit 0
      ;;
    */actions/workflows/*)
      workflow="${2#*/actions/workflows/}"
      workflow="${workflow%%/*}"
      answers="${FAKE_GH_DIR}/answers-${workflow}"
      answer="$(head -n 1 "${answers}")"
      if [ "$(wc -l < "${answers}")" -gt 1 ]; then
        tail -n +2 "${answers}" > "${answers}.next"
        mv "${answers}.next" "${answers}"
      fi
      if [ "${answer}" = "fail" ]; then
        echo "HTTP 502: Bad Gateway" >&2
        exit 1
      fi
      echo "${answer}"
      exit 0
      ;;
  esac
fi
if [ "$1" = "workflow" ] && [ "$2" = "run" ]; then
  if [ -f "${FAKE_GH_DIR}/dispatch-fails" ] && grep -qxF "$3" "${FAKE_GH_DIR}/dispatch-fails"; then
    exit 1
  fi
  echo "$3" >> "${FAKE_GH_DIR}/dispatched.log"
  exit 0
fi
echo "The fake gh has no answer for: $*" >&2
exit 2
"""


def find_bash() -> str | None:
    """Find a bash that reads the paths of this platform.

    Returns:
        The path of bash, or None when no such bash exists.
    """
    if sys.platform == "win32":
        # The bash on the Windows PATH can be the WSL launcher, which cannot
        # read a Windows path. Git for Windows puts its bash next to git.
        git = shutil.which("git")
        if git is None:
            return None
        bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        return str(bash) if bash.is_file() else None
    return shutil.which("bash")


def write_lines(path: Path, lines: list[str] | tuple[str, ...]) -> None:
    """Write one item on each line, with a Unix line end that bash reads."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


@dataclass
class Result:
    """The effect of one run of the dispatch step."""

    returncode: int
    output: str
    calls: list[str]
    dispatched: list[str]
    summary: str

    def queries(self, workflow: str) -> list[str]:
        """Return each query for the runs of one workflow."""
        return [call for call in self.calls if f"/actions/workflows/{workflow}/runs?" in call]


@pytest.fixture(scope="module")
def dispatch_job() -> dict[str, Any]:
    """Parse the dispatch job of the shared auto-merge workflow."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"][JOB]
    assert isinstance(job, dict)
    return job


@pytest.fixture(scope="module")
def bash() -> str:
    """Return a usable bash, or skip the tests."""
    found = find_bash()
    if found is None:
        pytest.skip("These tests need bash.")
    return found


@pytest.fixture
def run_step(tmp_path: Path, bash: str, dispatch_job: dict[str, Any]) -> Any:
    """Return a function that runs the dispatch step with the given answers."""
    script = "\n".join(str(step["run"]) for step in dispatch_job["steps"] if "run" in step)
    checks = dispatch_job["env"]["COVERAGE_CHECKS"]

    def run(answers: dict[str, list[str]], failing_dispatches: tuple[str, ...] = ()) -> Result:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
        fake_gh.chmod(0o755)
        for workflow, lines in answers.items():
            write_lines(tmp_path / f"answers-{workflow}", lines)
        if failing_dispatches:
            write_lines(tmp_path / "dispatch-fails", failing_dispatches)
        step = tmp_path / "step.sh"
        step.write_text(script, encoding="utf-8", newline="\n")
        summary = tmp_path / "summary.md"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_GH_DIR": tmp_path.as_posix(),
            "FAKE_TIP": TIP,
            "GH_REPO": REPO,
            "MAIN_WORKFLOWS": " ".join(answers),
            "DEFAULT_BRANCH": "main",
            "COVERAGE_CHECKS": checks,
            # The tests do not wait between the checks.
            "COVERAGE_CHECK_INTERVAL": "0",
            "GITHUB_STEP_SUMMARY": summary.as_posix(),
        }
        # GitHub runs a bash step with these options.
        completed = subprocess.run(
            [bash, "--noprofile", "--norc", "-eo", "pipefail", step.as_posix()],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=120,
        )

        def lines_of(name: str) -> list[str]:
            path = tmp_path / name
            return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

        return Result(
            returncode=completed.returncode,
            output=completed.stdout + completed.stderr,
            calls=lines_of("calls.log"),
            dispatched=lines_of("dispatched.log"),
            summary=summary.read_text(encoding="utf-8") if summary.is_file() else "",
        )

    return run


class TestDispatchCoverage:
    """The job starts a workflow only when no check finds a run on the tip."""

    def test_a_run_on_the_tip_starts_no_workflow(self, run_step: Any) -> None:
        """A person merged, and the push started each workflow."""
        result = run_step({"ci.yml": ["1"], "codeql.yml": ["2"]})
        assert result.returncode == 0, result.output
        assert result.dispatched == []
        assert len(result.queries("ci.yml")) == 1
        assert len(result.queries("codeql.yml")) == 1
        assert "**Started:** none" in result.summary

    def test_an_old_answer_then_a_run_starts_no_workflow(self, run_step: Any) -> None:
        """The first answer came from old data, and the next check found the push run."""
        result = run_step({"ci.yml": ["0", "1"], "codeql.yml": ["1"]})
        assert result.returncode == 0, result.output
        assert result.dispatched == []
        assert len(result.queries("ci.yml")) == 2
        # A workflow that covers the tip needs no second check.
        assert len(result.queries("codeql.yml")) == 1

    def test_a_workflow_with_no_run_starts_after_the_last_check(
        self, run_step: Any, dispatch_job: dict[str, Any]
    ) -> None:
        """An auto-merge starts no push run, so the job must start the workflow."""
        result = run_step({"ci.yml": ["0"], "container-build.yml": ["0"]})
        assert result.returncode == 0, result.output
        assert result.dispatched == ["ci.yml", "container-build.yml"]
        checks = int(dispatch_job["env"]["COVERAGE_CHECKS"])
        assert len(result.queries("ci.yml")) == checks
        assert len(result.queries("container-build.yml")) == checks
        assert "**Started:** ci.yml container-build.yml" in result.summary

    def test_the_query_asks_for_the_tip_on_the_default_branch(self, run_step: Any) -> None:
        """The newest run on the branch is not a safe test, so the query names the tip commit."""
        result = run_step({"ci.yml": ["1"]})
        assert result.queries("ci.yml") == [
            f"api repos/{REPO}/actions/workflows/ci.yml/runs?branch=main&head_sha={TIP}&per_page=1 --jq .total_count"
        ]

    def test_a_failed_query_counts_as_no_run(self, run_step: Any) -> None:
        """A failed query must not stop a workflow that missed the tip."""
        result = run_step({"ci.yml": ["fail"], "codeql.yml": ["1"]})
        assert result.returncode == 0, result.output
        assert result.dispatched == ["ci.yml"]

    def test_a_failed_dispatch_fails_the_job_after_the_other_dispatches(self, run_step: Any) -> None:
        """One failed dispatch must not stop the other dispatches."""
        result = run_step({"ci.yml": ["0"], "codeql.yml": ["0"]}, failing_dispatches=("ci.yml",))
        assert result.returncode == 1
        assert result.dispatched == ["codeql.yml"]
        assert "**Failed:** ci.yml" in result.summary
