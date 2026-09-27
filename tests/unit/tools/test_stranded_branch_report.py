"""Test the stranded branch report that MistHelper issues #1980 and #2251 asked for.

A stranded branch holds commits above the base branch and has no open pull
request. Only `refs/pull/<n>/head` survives a branch deletion, so a branch with
no pull request has one copy. These tests lock the three protection tests and
the report that names the result.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from misthelper_devtools import stranded_branch_report
from misthelper_devtools.stranded_branch_report import (
    BranchRecord,
    GitHubReader,
    StrandedBranchReporter,
    _as_rows,
    _default_repository,
    _head_date,
    _head_ref,
    main,
)

logger = logging.getLogger(__name__)  # WHY: keep test log records on the module logger.

# The instant that every age test measures against. A fixed value keeps the
# tests hermetic, because a real clock changes between two runs.
_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)


def _record(name: str, ahead_by: int = 3, age_days: int = 30) -> BranchRecord:
    """Build one branch record at a chosen age and ahead count."""
    logger.info("Building the record for the branch %s", name)  # Report the build before the work.
    record = BranchRecord(
        name=name,  # The short branch name the tests assert on.
        sha="a" * 40,  # A fixed fork point, because no test reads the value.
        ahead_by=ahead_by,  # The count that the first protection test reads.
        last_commit_at=_NOW - timedelta(days=age_days),  # The date that the age test reads.
    )
    logger.debug("The record for %s is %d days old", name, age_days)  # Record the age.
    return record


def _reporter(records: list[BranchRecord], open_heads: frozenset[str], min_age_days: int = 7) -> StrandedBranchReporter:
    """Build a reporter over a fixed set of records and open pull requests."""
    logger.info("Building a reporter over %d records", len(records))  # Report before the build.
    by_name = {record.name: record for record in records}  # Index the records for the read callback.
    return StrandedBranchReporter(
        lambda: list(by_name),  # Answer every branch name in the fixed set.
        by_name.get,  # Answer one record, or None when the name is unknown.
        lambda: open_heads,  # Answer the head name of every open pull request.
        min_age_days,  # Apply the quiet period the caller chose.
    )


# ---------------------------------------------------------------------------
# The three protection tests
# ---------------------------------------------------------------------------


def test_a_branch_with_work_and_no_pull_request_is_stranded() -> None:
    """A branch above the base with no open pull request MUST be reported."""
    logger.info("Checking that unprotected work is reported")  # Report the plan.
    record = _record("fix/1234-a-defect")  # Three commits above the base, thirty days old.

    assert record.is_stranded(frozenset(), 7, _NOW), "unprotected work must be reported"


def test_an_open_pull_request_protects_the_branch() -> None:
    """A branch that an open pull request names MUST NOT be reported."""
    logger.info("Checking that an open pull request protects the head")  # Report the plan.
    record = _record("fix/1234-a-defect")  # The same unprotected shape as the previous test.

    # WHY: refs/pull/<n>/head survives a deletion, so the head is already permanent.
    assert not record.is_stranded(frozenset({"fix/1234-a-defect"}), 7, _NOW)


def test_a_branch_at_the_base_holds_nothing_to_lose() -> None:
    """A branch with no commit above the base MUST NOT be reported."""
    logger.info("Checking that a branch at the base is quiet")  # Report the plan.
    record = _record("chore/no-work", ahead_by=0)  # No commit above the base branch.

    assert not record.is_stranded(frozenset(), 7, _NOW), "a branch at the base loses nothing"


def test_recent_work_stays_below_the_quiet_period() -> None:
    """A branch younger than the threshold MUST NOT be reported."""
    logger.info("Checking that recent work stays quiet")  # Report the plan.
    record = _record("feat/today", age_days=2)  # Pushed two days ago, so the work is active.

    assert not record.is_stranded(frozenset(), 7, _NOW), "active work is not stranded"


@pytest.mark.parametrize(
    "name",
    ["dependabot/npm_and_yarn/ops-portal/x", "gh-readonly-queue/main/pr-1", "revert-1234-a-branch"],
)
def test_a_bot_branch_is_never_reported(name: str) -> None:
    """A bot branch follows its own lifecycle, so the report MUST skip it."""
    logger.info("Checking that the bot branch %s stays quiet", name)  # Report the plan.
    record = _record(name)  # The same unprotected shape as a real feature branch.

    assert not record.is_stranded(frozenset(), 7, _NOW), f"{name} must not be reported"


def test_a_clock_skew_never_reports_a_negative_age() -> None:
    """A head dated in the future MUST report an age of zero, not a negative."""
    logger.info("Checking the age floor against a clock skew")  # Report the plan.
    record = _record("feat/future", age_days=-5)  # A head dated five days ahead of the clock.

    assert record.age_days(_NOW) == 0, "an age must never fall below zero"


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_report_names_every_stranded_branch() -> None:
    """The report MUST name each stranded branch and skip each protected one."""
    logger.info("Checking the report content")  # Report the plan before the work.
    records = [_record("fix/a-defect"), _record("feat/protected"), _record("chore/fresh", age_days=1)]
    reporter = _reporter(records, frozenset({"feat/protected"}))  # One branch has a pull request.

    text = reporter.render(reporter.find(now=_NOW), _NOW)  # Find, then render, against one instant.
    logger.debug("The report is %r", text)  # Record the report for a failure read.

    assert "`fix/a-defect`" in text, "the stranded branch must appear"
    assert "feat/protected" not in text, "a branch with a pull request must not appear"
    assert "chore/fresh" not in text, "a branch inside the quiet period must not appear"


def test_the_report_orders_the_oldest_head_first() -> None:
    """The report MUST list the oldest head first, because it is the most at risk."""
    logger.info("Checking the report order")  # Report the plan before the work.
    records = [_record("feat/newer", age_days=10), _record("feat/older", age_days=200)]
    reporter = _reporter(records, frozenset())  # Neither branch has a pull request.

    names = [record.name for record in reporter.find(now=_NOW)]  # Read the order the reporter chose.
    logger.debug("The report order is %r", names)  # Record the order for a failure read.

    assert names == ["feat/older", "feat/newer"], "the oldest head must come first"


def test_the_reference_time_controls_the_age_classification() -> None:
    """The instant the caller passes MUST decide the age, not the real clock."""
    logger.info("Checking that the caller instant decides the age")  # Report the plan.
    reporter = _reporter([_record("chore/fresh", age_days=1)], frozenset())  # One young branch.

    inside = [record.name for record in reporter.find(now=_NOW)]  # One day old at the fixed instant.
    outside = [record.name for record in reporter.find(now=_NOW + timedelta(days=30))]  # Older later.
    logger.debug("The result is %r at the fixed instant and %r later", inside, outside)  # Record both.

    assert inside == [], "a branch inside the quiet period must not appear"
    assert outside == ["chore/fresh"], "the later instant must move the branch past the threshold"


def test_the_report_age_column_reads_the_reference_time() -> None:
    """The age column MUST measure against the instant the caller passes."""
    logger.info("Checking the age column against a fixed instant")  # Report the plan.
    reporter = _reporter([_record("fix/a-defect", age_days=30)], frozenset())  # One stranded branch.

    text = reporter.render(reporter.find(now=_NOW), _NOW)  # Render against the same fixed instant.
    logger.debug("The report is %r", text)  # Record the report for a failure read.

    assert "| `fix/a-defect` | 3 | 30 |" in text, "the age column must report the fixture age"


def test_the_default_reference_time_reads_the_real_clock() -> None:
    """A call with no instant MUST measure against the real UTC clock."""
    logger.info("Checking the default reference time")  # Report the plan before the work.
    real_now = datetime.now(UTC)  # Read the real clock that the default path must also read.
    records = [
        BranchRecord("feat/today", "b" * 40, 3, real_now),  # Pushed now, so the branch is active.
        BranchRecord("fix/old", "c" * 40, 3, real_now - timedelta(days=30)),  # Older than the period.
    ]
    reporter = _reporter(records, frozenset())  # Neither branch has a pull request.

    names = [record.name for record in reporter.find()]  # Call with no instant, so the clock decides.
    logger.debug("The default path reported %r", names)  # Record the result for a failure read.

    assert names == ["fix/old"], "the default path must measure against the real clock"


def test_a_clean_repository_answers_a_clear_sentence() -> None:
    """An empty result MUST answer a sentence, not an empty table."""
    logger.info("Checking the clean report")  # Report the plan before the work.
    reporter = _reporter([], frozenset())  # No branch at all, so nothing can be stranded.

    text = reporter.render(reporter.find())  # Render the empty result.

    assert "Every branch" in text, "a clean repository needs a clear answer"
    assert "|" not in text, "a clean report must hold no table"


def test_the_base_branch_is_never_reported() -> None:
    """The base branch MUST NOT appear in its own report."""
    logger.info("Checking that the base branch is skipped")  # Report the plan.
    reporter = _reporter([_record("main")], frozenset())  # Only the base branch exists.

    assert reporter.find("main") == [], "the base branch cannot be stranded against itself"


def test_an_unreadable_branch_does_not_stop_the_report() -> None:
    """A branch the reader cannot answer MUST NOT stop the report, and it MUST be listed."""
    logger.info("Checking that an unreadable branch does not stop the report")  # Report the plan.
    reporter = StrandedBranchReporter(
        lambda: ["feat/unreadable", "fix/a-defect"],  # The API lists two branches.
        {"fix/a-defect": _record("fix/a-defect")}.get,  # The compare answers only one of them.
        frozenset,  # No open pull request protects either branch.
    )

    names = [record.name for record in reporter.find(now=_NOW)]  # Read the branches the reporter kept.

    assert names == ["fix/a-defect"], "an unreadable branch must not stop the report"
    assert reporter.unreadable == ["feat/unreadable"], "an unreadable branch can hold the only copy of its work"


def test_an_unreadable_branch_renders_an_unknown_row() -> None:
    """An unreadable branch MUST appear with unknown values, even with no stranded branch."""
    logger.info("Checking the unknown row")  # Report the plan.
    reporter = StrandedBranchReporter(lambda: ["preservation/no-shared-history"], lambda name: None, frozenset)

    text = reporter.render(reporter.find(now=_NOW), _NOW)  # Find, then render, against one instant.

    assert "| `preservation/no-shared-history` | unknown | unknown |" in text, "list the unreadable branch"
    assert "no shared history" in text, "tell the reader what an unknown row means"
    assert not text.startswith("Every branch"), "an unreadable branch is not a clean result"


@pytest.mark.parametrize(
    ("open_heads", "held", "name"),
    [
        (frozenset({"fix/open"}), False, "fix/open"),  # An open pull request protects the branch.
        (frozenset(), True, "fix/merged"),  # A closed pull request holds the branch head.
        (frozenset(), False, "dependabot/pip/x-1.0"),  # A bot branch follows its own lifecycle.
    ],
    ids=["open-pull-request", "closed-pull-request", "bot-prefix"],
)
def test_a_protected_unreadable_branch_is_not_listed(open_heads: frozenset[str], held: bool, name: str) -> None:
    """A pull request or a bot prefix MUST protect an unreadable branch too."""
    logger.info("Checking the protection of the unreadable branch %s", name)  # Report the plan.
    reporter = StrandedBranchReporter(lambda: [name], lambda branch: None, lambda: open_heads, 7, lambda branch: held)

    reporter.find(now=_NOW)  # Apply every test to the one branch.

    assert reporter.unreadable == [], "a protected branch needs no report"


def test_the_fail_flag_counts_an_unreadable_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable branch MUST fail the run under --fail-on-find."""
    logger.info("Checking the exit status for an unreadable branch")  # Report the plan.
    _patch_reader(monkeypatch, [])  # Name a repository and patch every reader.
    monkeypatch.setattr(GitHubReader, "list_branches", lambda self: ["preservation/no-shared-history"])

    assert main(["--fail-on-find"]) == 1, "an unreadable branch must fail the run under the flag"


def test_a_failed_compare_answers_no_record(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A gh error on one compare MUST answer None and log the gh error text."""
    logger.info("Checking a failed compare")  # Report the plan.

    def fail(self: GitHubReader, path: str) -> object:
        raise subprocess.CalledProcessError(1, ["gh", "api", path], stderr="gh: No common ancestor (HTTP 404)")

    monkeypatch.setattr(GitHubReader, "_api", fail)

    with caplog.at_level(logging.WARNING, logger=stranded_branch_report.__name__):
        record = GitHubReader("owner/name", "main").read_branch("preservation/no-shared-history")

    assert record is None, "a failed compare cannot build a record"
    assert "No common ancestor" in caplog.text, "the warning must carry the gh error text"


def test_a_failed_closed_pull_request_read_holds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gh error in the closed pull request read MUST count as no protection."""
    logger.info("Checking a failed closed pull request read")  # Report the plan.

    def fail(self: GitHubReader, path: str) -> object:
        raise subprocess.TimeoutExpired(["gh", "api", path], 120)

    monkeypatch.setattr(GitHubReader, "_api", fail)

    assert not GitHubReader("owner/name", "main").is_held_by_closed_pull_request("fix/a-defect")


def test_the_compare_path_encodes_a_special_branch_character(monkeypatch: pytest.MonkeyPatch) -> None:
    """A branch name with a URL character MUST NOT break the compare path."""
    logger.info("Checking the compare path encoding")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    monkeypatch.setattr(GitHubReader, "_api", _routed_api({}, paths))

    GitHubReader("owner/name", "main").read_branch("fix/a#b%c")

    assert paths == ["compare/main...fix/a%23b%25c"], "encode the branch name in the path"


def test_a_compare_body_builds_the_branch_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed compare body MUST fill every field of the record."""
    logger.info("Checking the record that a compare body builds")  # Report the plan.
    body = {
        "ahead_by": 3,  # The branch holds three commits above the base.
        "merge_base_commit": {"sha": "d" * 40},  # The fork point.
        "commits": [{"commit": {"committer": {"date": "2026-08-01T10:00:00Z"}}}],  # The head commit.
    }
    monkeypatch.setattr(GitHubReader, "_api", _routed_api({"compare/": body}, []))

    record = GitHubReader("owner/name", "main").read_branch("fix/a-defect")

    assert record == BranchRecord("fix/a-defect", "d" * 40, 3, datetime(2026, 8, 1, 10, tzinfo=UTC))


def test_the_api_call_runs_gh_with_the_repository_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """One API read MUST run `gh api` on the repository path and decode the JSON body."""
    logger.info("Checking the gh api call")  # Report the plan.
    calls: list[tuple[list[str], dict[str, object]]] = []  # The commands and options of each call.

    def answer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))  # Record the call, so the test can check it.
        return subprocess.CompletedProcess(command, 0, stdout='[{"name": "main"}]', stderr="")

    monkeypatch.setattr(subprocess, "run", answer)

    body = GitHubReader("owner/name", "main")._api("branches?per_page=100&page=1")

    assert body == [{"name": "main"}], "the reader must decode the JSON body"
    assert calls[0][0] == ["gh", "api", "repos/owner/name/branches?per_page=100&page=1"], "read the repository path"
    assert calls[0][1]["check"] is True, "an error status must raise, so the caller can handle it"
    assert calls[0][1]["encoding"] == "utf-8", "gh writes UTF-8 on every platform"
    assert calls[0][1]["timeout"] == 120, "a stalled read must stop at the bound"


# ---------------------------------------------------------------------------
# The response readers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{"message": "Not Found"}, None, "text", 7])
def test_a_non_list_body_answers_no_rows(body: object) -> None:
    """An error body or a scalar MUST answer no rows, not raise."""
    logger.info("Checking the row reader against %r", body)  # Report the plan.

    assert list(_as_rows(body)) == [], "only a JSON array holds rows"


def test_the_row_reader_drops_a_non_object_element() -> None:
    """A list that mixes objects and scalars MUST answer the objects only."""
    logger.info("Checking the row reader against a mixed list")  # Report the plan.

    rows = list(_as_rows([{"name": "a"}, "b", None, {"name": "c"}]))  # Two objects, two scalars.

    assert rows == [{"name": "a"}, {"name": "c"}], "a scalar element must be dropped"


def test_the_head_reference_reads_a_well_formed_row() -> None:
    """A pull request row MUST answer the short head branch name."""
    logger.info("Checking the head reference reader")  # Report the plan before the work.

    name = _head_ref({"head": {"ref": "fix/1234-a-defect", "sha": "b" * 40}})  # A normal API row.

    assert name == "fix/1234-a-defect", "the reader must answer the short branch name"


@pytest.mark.parametrize("row", [{}, {"head": None}, {"head": "main"}, {"head": {}}, {"head": {"ref": 7}}])
def test_a_malformed_row_answers_an_empty_head(row: dict[str, object]) -> None:
    """A row with no readable head MUST answer an empty name, not raise."""
    logger.info("Checking the head reference reader against %r", row)  # Report the plan.

    # WHY: an empty name never matches a branch, so a malformed row protects nothing.
    assert _head_ref(row) == "", "a malformed row must answer an empty name"


def test_an_unreadable_pull_request_row_protects_no_branch() -> None:
    """An empty head name MUST NOT protect a branch whose name is also empty."""
    logger.info("Checking that an empty head name protects nothing")  # Report the plan.
    record = _record("")  # A branch with an empty name cannot exist, so it must not match.

    assert record.is_stranded(frozenset(), 7, _NOW), "an empty name must not act as protection"


def test_the_head_date_reads_the_last_commit() -> None:
    """The head date MUST come from the last commit, because compare orders oldest first."""
    logger.info("Checking the head date reader")  # Report the plan before the work.
    commits = [
        {"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}},  # The oldest commit.
        {"commit": {"committer": {"date": "2026-06-15T09:30:00Z"}}},  # The head commit.
    ]

    stamp = _head_date(commits)  # Read the date that the age test uses.
    logger.debug("The head date is %s", stamp)  # Record the value for a failure read.

    assert stamp == datetime(2026, 6, 15, 9, 30, tzinfo=UTC), "the last commit is the head"


@pytest.mark.parametrize("commits", [[], [{"commit": {}}], [{"commit": {"committer": {}}}], "not-a-list"])
def test_a_missing_head_date_reads_as_now(commits: object) -> None:
    """A missing date MUST read as now, so the branch stays below every threshold."""
    logger.info("Checking the head date fallback against %r", commits)  # Report the plan.

    stamp = _head_date(commits)  # Read the fallback value.

    # WHY: a missing date cannot support a deletion, so the report must stay quiet.
    assert (datetime.now(UTC) - stamp).total_seconds() < 60, "a missing date must read as now"


# ---------------------------------------------------------------------------
# The closed pull request protection
# ---------------------------------------------------------------------------


def test_a_closed_pull_request_that_holds_the_head_protects_the_branch() -> None:
    """A merged branch that still exists MUST NOT appear, because its pull request holds the head."""
    logger.info("Checking the closed pull request protection")  # Report the plan.
    records = {name: _record(name) for name in ("fix/merged", "fix/a-defect")}  # Two old branches.
    reporter = StrandedBranchReporter(
        lambda: list(records),  # The API lists both branches.
        records.get,  # The compare answers both branches.
        frozenset,  # No open pull request protects either branch.
        7,  # The default quiet period.
        lambda name: name == "fix/merged",  # A closed pull request holds only the merged branch.
    )

    names = [record.name for record in reporter.find(now=_NOW)]  # Read the branches the reporter kept.

    assert names == ["fix/a-defect"], "only the branch that no pull request holds is stranded"


def test_the_closed_pull_request_check_runs_only_for_a_candidate() -> None:
    """The extra API read MUST run only for a branch that fails the three tests."""
    logger.info("Checking that the closed pull request read stays narrow")  # Report the plan.
    records = [_record("feat/protected"), _record("chore/fresh", age_days=1), _record("fix/a-defect")]
    checked: list[str] = []  # The names that the closed pull request check reads.

    def held(name: str) -> bool:
        checked.append(name)  # Record the read, so the test can count it.
        return False

    by_name = {record.name: record for record in records}  # Index the records for the read callback.
    reporter = StrandedBranchReporter(
        lambda: list(by_name), by_name.get, lambda: frozenset({"feat/protected"}), 7, held
    )

    reporter.find(now=_NOW)  # Apply every test to every branch.

    assert checked == ["fix/a-defect"], "a protected or recent branch needs no extra read"


def _routed_api(routes: dict[str, object], paths: list[str]) -> object:
    """Build an `_api` stand-in that answers by the longest matching path prefix."""
    logger.info("Building a routed API over %d routes", len(routes))  # Report the plan.

    def answer(self: GitHubReader, path: str) -> object:
        paths.append(path)  # Record the path, so a test can check the query.
        matches = [prefix for prefix in routes if path.startswith(prefix)]
        return routes[max(matches, key=len)] if matches else []

    return answer


def test_a_closed_pull_request_at_the_branch_head_holds_the_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed pull request whose head holds every branch commit MUST protect the branch."""
    logger.info("Checking the reader against a merged branch")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    routes: dict[str, object] = {
        "pulls?state=closed": [{"head": {"sha": "c" * 40}}],  # One closed pull request.
        f"compare/{'c' * 40}...fix/merged": {"ahead_by": 0},  # The branch adds nothing above its head.
    }
    monkeypatch.setattr(GitHubReader, "_api", _routed_api(routes, paths))

    held = GitHubReader("owner/name", "main").is_held_by_closed_pull_request("fix/merged")

    assert held, "the closed pull request holds every commit of the branch"
    assert paths[0] == "pulls?state=closed&head=owner:fix/merged&per_page=100&page=1", "filter by the branch head"


def test_a_branch_that_moved_past_the_closed_head_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit above the closed pull request head MUST leave the branch unprotected."""
    logger.info("Checking the reader against a branch with new work")  # Report the plan.
    routes: dict[str, object] = {
        "pulls?state=closed": [{"head": {"sha": "c" * 40}}],  # One closed pull request.
        "compare/": {"ahead_by": 2},  # The branch gained two commits after the pull request closed.
    }
    monkeypatch.setattr(GitHubReader, "_api", _routed_api(routes, []))

    held = GitHubReader("owner/name", "main").is_held_by_closed_pull_request("fix/more-work")

    assert not held, "new work above the held head has one copy"


@pytest.mark.parametrize("rows", [[], [{"head": None}], [{"head": {"sha": ""}}], [{"head": {"sha": 7}}]])
def test_a_branch_with_no_readable_closed_head_is_not_held(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]
) -> None:
    """No closed row with a head commit MUST leave the branch unprotected, with no compare read."""
    logger.info("Checking the reader against the closed rows %r", rows)  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    monkeypatch.setattr(GitHubReader, "_api", _routed_api({"pulls?state=closed": rows}, paths))

    held = GitHubReader("owner/name", "main").is_held_by_closed_pull_request("fix/a-defect")

    assert not held, "a row with no head commit holds nothing"
    assert not any(path.startswith("compare/") for path in paths), "no head commit means no compare read"


def test_the_head_filter_encodes_a_special_branch_character(monkeypatch: pytest.MonkeyPatch) -> None:
    """A branch name with a query character MUST NOT break the head filter."""
    logger.info("Checking the head filter encoding")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    monkeypatch.setattr(GitHubReader, "_api", _routed_api({}, paths))

    GitHubReader("owner/name", "main").is_held_by_closed_pull_request("fix/a&b#c")

    assert paths == ["pulls?state=closed&head=owner:fix/a%26b%23c&per_page=100&page=1"], "encode the query value"


# ---------------------------------------------------------------------------
# The paged list reads
# ---------------------------------------------------------------------------


def _paged_api(pages: list[list[dict[str, object]]], paths: list[str]) -> object:
    """Build an `_api` stand-in that answers one fixed page for each call."""
    logger.info("Building a paged API over %d pages", len(pages))  # Report the plan.

    def answer(self: GitHubReader, path: str) -> object:
        paths.append(path)  # Record the path, so a test can check the page keys.
        index = len(paths) - 1  # The call count names the page that the reader asked for.
        return pages[index] if index < len(pages) else []

    return answer


def _branch_rows(start: int, count: int) -> list[dict[str, object]]:
    """Build `count` branch rows with distinct names."""
    return [{"name": f"feat/{number}"} for number in range(start, start + count)]


def test_the_branch_list_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository with more than one page of branches MUST report every branch."""
    logger.info("Checking the branch list across two pages")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    pages = [_branch_rows(0, 100), _branch_rows(100, 5)]  # One full page and one short page.
    monkeypatch.setattr(GitHubReader, "_api", _paged_api(pages, paths))

    names = GitHubReader("owner/name", "main").list_branches()  # Read every page.

    assert len(names) == 105, "the reader must keep the rows of the second page"
    assert names[-1] == "feat/104", "the reader must keep the API order"
    assert paths == ["branches?per_page=100&page=1", "branches?per_page=100&page=2"], "a short page ends the read"


def test_the_open_pull_request_list_keeps_the_state_filter_on_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every page of the pull request read MUST keep the open state filter."""
    logger.info("Checking the pull request list across two pages")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    full_page = [{"head": {"ref": f"fix/{number}"}} for number in range(100)]  # One full page.
    pages = [full_page, [{"head": {"ref": "fix/last"}}]]  # The second page holds one more row.
    monkeypatch.setattr(GitHubReader, "_api", _paged_api(pages, paths))

    heads = GitHubReader("owner/name", "main").list_open_pull_request_heads()  # Read every page.

    assert "fix/last" in heads, "the reader must keep the head names of the second page"
    assert len(heads) == 101, "every open pull request must protect its head"
    assert all(path.startswith("pulls?state=open&per_page=100&page=") for path in paths), "keep the state filter"


def test_the_list_read_stops_at_the_page_bound(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An API that keeps answering full pages MUST stop at the bound and log a warning."""
    logger.info("Checking the page bound")  # Report the plan.
    paths: list[str] = []  # The paths that the reader requests, in order.
    monkeypatch.setattr(stranded_branch_report, "_MAX_PAGES", 3)  # A small bound keeps the test fast.
    pages = [_branch_rows(100 * page, 100) for page in range(5)]  # More full pages than the bound.
    monkeypatch.setattr(GitHubReader, "_api", _paged_api(pages, paths))

    with caplog.at_level(logging.WARNING, logger=stranded_branch_report.__name__):
        names = GitHubReader("owner/name", "main").list_branches()  # Read up to the bound.

    assert len(paths) == 3, "the reader must not request a page above the bound"
    assert len(names) == 300, "the reader must keep every row it read"
    assert "can be incomplete" in caplog.text, "a stopped read must say that the list can be incomplete"


# ---------------------------------------------------------------------------
# The repository default
# ---------------------------------------------------------------------------


def _refuse_gh(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Fail the test when the code under test makes a call that the test forbids."""
    raise AssertionError("the code under test must not make this call")


def test_the_workflow_repository_wins_over_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A workflow run MUST read GITHUB_REPOSITORY and start no process."""
    logger.info("Checking the workflow repository default")  # Report the plan.
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/from-workflow")
    monkeypatch.setattr(subprocess, "run", _refuse_gh)

    assert _default_repository() == "owner/from-workflow", "a workflow run names its repository"


def test_a_local_run_asks_gh_for_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local run MUST ask gh for the repository of the current checkout."""
    logger.info("Checking the gh repository default")  # Report the plan.
    commands: list[list[str]] = []  # The commands that the code under test starts.

    def answer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)  # Record the command, so the test can check it.
        return subprocess.CompletedProcess(command, 0, stdout="owner/from-gh\n", stderr="")

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(subprocess, "run", answer)

    assert _default_repository() == "owner/from-gh", "gh names the repository of the checkout"
    assert commands == [["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]]


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("gh"), subprocess.TimeoutExpired(["gh"], 120)],
    ids=["gh-missing", "gh-stalled"],
)
def test_a_gh_that_cannot_start_names_no_repository(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """A missing or stalled gh MUST answer an empty name, not raise."""
    logger.info("Checking the gh failure %r", failure)  # Report the plan.

    def fail(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise failure

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(subprocess, "run", fail)

    assert _default_repository() == "", "a gh failure must name no repository"


def test_a_directory_outside_a_checkout_names_no_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gh error status MUST answer an empty name."""
    logger.info("Checking the gh error status")  # Report the plan.

    def answer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="not a git repository")

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(subprocess, "run", answer)

    assert _default_repository() == "", "a gh error must name no repository"


def test_no_repository_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run with no repository MUST stop with status 2 before any API read."""
    logger.info("Checking the usage error")  # Report the plan.
    monkeypatch.setattr(stranded_branch_report, "_default_repository", lambda: "")
    monkeypatch.setattr(GitHubReader, "_api", _refuse_gh)

    with pytest.raises(SystemExit) as stop:
        main([])

    assert stop.value.code == 2, "argparse answers status 2 for a usage error"


def test_the_repository_option_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --repository MUST skip the default lookup and bind the reader."""
    logger.info("Checking the explicit repository")  # Report the plan.
    bound: list[str] = []  # The repositories that the reader binds to.
    original_init = GitHubReader.__init__

    def record_init(self: GitHubReader, repository: str, base_branch: str) -> None:
        bound.append(repository)  # Record the binding, so the test can check it.
        original_init(self, repository, base_branch)

    monkeypatch.setattr(stranded_branch_report, "_default_repository", _refuse_gh)
    monkeypatch.setattr(GitHubReader, "__init__", record_init)
    _patch_reader(monkeypatch, [])  # No branch at all, so the report stays clean.

    assert main(["--repository", "owner/explicit"]) == 0, "a clean repository must pass"
    assert bound == ["owner/explicit"], "the reader must read the repository that the caller named"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_command_line_reports_success_without_the_fail_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A find MUST answer status 0 unless the caller passes --fail-on-find."""
    logger.info("Checking the default exit status")  # Report the plan before the work.
    _patch_reader(monkeypatch, [_record("fix/a-defect")])  # One stranded branch exists.

    assert main([]) == 0, "a report alone must not fail a run"


def test_the_fail_flag_turns_a_find_into_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A find with --fail-on-find MUST answer status 1, so a gate can block."""
    logger.info("Checking the exit status under --fail-on-find")  # Report the plan.
    _patch_reader(monkeypatch, [_record("fix/a-defect")])  # One stranded branch exists.

    assert main(["--fail-on-find"]) == 1, "a find must fail the run under the flag"


def test_a_clean_repository_answers_zero_under_the_fail_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """No find MUST answer status 0, even with --fail-on-find."""
    logger.info("Checking the clean exit status under --fail-on-find")  # Report the plan.
    _patch_reader(monkeypatch, [])  # No branch at all, so nothing can be stranded.

    assert main(["--fail-on-find"]) == 0, "a clean repository must pass the gate"


def test_none_arguments_read_sys_argv_and_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A None argument list MUST use sys.argv like module execution."""
    logger.info("Checking the None argument path")  # Report the plan before the work.
    monkeypatch.setattr(sys, "argv", ["stranded-branch-report"])  # Keep pytest flags out of argparse.
    _patch_reader(monkeypatch, [])  # No branch at all, so the report stays clean.

    assert main(None) == 0, "module execution with no findings must pass"  # Prove the None path behavior.


def _patch_reader(monkeypatch: pytest.MonkeyPatch, records: list[BranchRecord]) -> None:
    """Replace the GitHub reader with a fixed set of records."""
    logger.info("Patching the GitHub reader with %d records", len(records))  # Report the plan.
    by_name = {record.name: record for record in records}  # Index the records for the read callback.
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/fixture")  # Name a repository, so no gh process starts.
    monkeypatch.setattr(
        "misthelper_devtools.stranded_branch_report.GitHubReader.is_held_by_closed_pull_request",
        lambda self, name: False,
    )
    monkeypatch.setattr(
        "misthelper_devtools.stranded_branch_report.GitHubReader.list_branches", lambda self: list(by_name)
    )
    monkeypatch.setattr(
        "misthelper_devtools.stranded_branch_report.GitHubReader.read_branch", lambda self, name: by_name.get(name)
    )
    monkeypatch.setattr(
        "misthelper_devtools.stranded_branch_report.GitHubReader.list_open_pull_request_heads", lambda self: frozenset()
    )
    logger.debug("The GitHub reader now answers a fixed set")  # Record the patch after the work.
