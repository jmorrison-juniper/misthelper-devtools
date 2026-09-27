"""Report a branch that holds finished work and has no pull request.

A branch with commits above `main` and no pull request has one copy. A cleanup
deletes that copy, and git prints no warning. A repository that squash-merges
never shows a feature branch in `git branch --merged`, so a cleanup that trusts
`--merged` is unsafe.

A pull request makes a branch head permanent, because `refs/pull/<n>/head`
survives a branch deletion. An open pull request follows the branch, and a
closed or merged pull request holds the head that it had when it closed. This
tool reports every branch that has no such protection, so an operator can open
a pull request before any cleanup.

Issue jmorrison-juniper/MistHelper#1980 recorded five stranded branches. Issue
jmorrison-juniper/MistHelper#2251 recorded the deletion of all five, and one
head was unrecoverable.

Run the tool from the root of a checkout. The tool reads the repository from
`--repository`, then from `GITHUB_REPOSITORY`, then from `gh repo view`.

    stranded-branch-report
    stranded-branch-report --min-age-days 14 --fail-on-find
    stranded-branch-report --repository jmorrison-juniper/MistHelper
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

# The variable that a GitHub Actions run sets to the owner and name pair of the
# repository. The tool reads it when the caller passes no --repository value.
_REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"

# The branch that every feature branch merges into. A branch is stranded only
# when it holds commits that this branch does not hold.
DEFAULT_BASE_BRANCH = "main"

# The age below which the tool stays quiet. A branch that an engineer pushed
# today is active work, and a report on it is noise.
DEFAULT_MIN_AGE_DAYS = 7

# A branch name that the tool never reports. A protected branch and a bot branch
# both follow their own lifecycle, so neither one needs a pull request warning.
IGNORED_PREFIXES = ("dependabot/", "gh-readonly-queue/", "revert-")

# The gh call reaches the GitHub API over the network. A stalled read has no
# bound of its own, so this cap stops it from hanging the whole gate.
_GH_TIMEOUT_SECONDS = 120

# The number of branches that one API page returns. The API caps a page at 100.
_PAGE_SIZE = 100

# The last page that one list read requests. A short page ends a read, so this
# bound applies only when the API keeps answering full pages.
_MAX_PAGES = 50

# The faults that one gh call can raise: an error status, a stalled call, and a
# body that is not JSON.
_API_ERRORS = (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError)

# The value that a report row shows when the tool could not compare a branch.
_UNKNOWN = "unknown"

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class BranchRecord:
    """One branch, with the facts that decide whether it is stranded."""

    name: str
    """The short branch name, such as `fix/1234-a-defect`."""

    sha: str
    """The head commit of the branch."""

    ahead_by: int
    """The count of commits that the branch holds above the base branch."""

    last_commit_at: datetime
    """The commit date of the head, read as an aware UTC value."""

    def age_days(self, now: datetime) -> int:
        """Report the whole days between the head commit and `now`."""
        return max((now - self.last_commit_at).days, 0)  # A negative age means a clock skew.

    def is_stranded(self, open_heads: frozenset[str], min_age_days: int, now: datetime) -> bool:
        """Report whether the branch holds unprotected work.

        A branch is stranded when it holds work above the base branch, no open
        pull request names it as the head, and it is older than the threshold.
        """
        if self.ahead_by < 1:  # A branch at or behind the base holds nothing to lose.
            return False
        if self.name in open_heads:  # An open pull request already protects the head.
            return False
        if self.name.startswith(IGNORED_PREFIXES):  # A bot branch follows its own lifecycle.
            return False
        return self.age_days(now) >= min_age_days  # Recent work is active, not stranded.


class GitHubReader:
    """Read the branch facts from the GitHub API through the `gh` command."""

    def __init__(self, repository: str, base_branch: str) -> None:
        """Store the repository and the base branch that every read uses."""
        self._repository = repository  # The owner and name pair that each path needs.
        self._base_branch = base_branch  # The branch that every comparison starts from.

    def _api(self, path: str) -> object:
        """Call one GitHub API path and return the decoded body."""
        _LOG.info("Reading the GitHub API path %s", path)  # Report the read before the network call.
        command = ["gh", "api", f"repos/{self._repository}/{path}"]  # Build the read-only call.
        result = subprocess.run(  # The argument list is built here, not by a shell.
            # gh writes UTF-8. A locale decode fails on Windows when a title holds a character
            # outside the code page, so the call names the encoding.
            command,
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=_GH_TIMEOUT_SECONDS,
        )
        body = json.loads(result.stdout)  # The API answers JSON for every path this tool reads.
        _LOG.debug("The path %s answered %d bytes", path, len(result.stdout))  # Record the size.
        return body

    def _api_rows(self, path: str, query: str = "") -> list[dict[str, object]]:
        """Read every page of one list path and return the rows in API order."""
        _LOG.info("Reading every page of %s", path)  # Report the plan before the first page.
        rows: list[dict[str, object]] = []  # Collect the rows of each page in order.
        prefix = f"{query}&" if query else ""  # Keep the caller filter in front of the page keys.
        for page in range(1, _MAX_PAGES + 1):  # Read one page at a time until a short page.
            batch = list(_as_rows(self._api(f"{path}?{prefix}per_page={_PAGE_SIZE}&page={page}")))
            rows.extend(batch)  # Keep the rows of this page.
            if len(batch) < _PAGE_SIZE:  # A short page is the last page.
                _LOG.debug("The path %s answered %d rows on %d pages", path, len(rows), page)
                return rows
        _LOG.warning("Stopped at page %d of %s, so the list can be incomplete", _MAX_PAGES, path)
        return rows

    def list_open_pull_request_heads(self) -> frozenset[str]:
        """Report the head branch name of every open pull request."""
        _LOG.info("Listing the open pull requests")  # Report the plan before the read.
        rows = self._api_rows("pulls", "state=open")  # Read every page of the open rows.
        heads = {_head_ref(row) for row in rows}  # Keep the head name only.
        _LOG.debug("Found %d open pull requests", len(heads))  # Record the count after the read.
        return frozenset(heads - {""})  # Drop the empty name that an unreadable row answers.

    def list_branches(self) -> list[str]:
        """Report the short name of every branch in the repository."""
        _LOG.info("Listing the branches")  # Report the plan before the read.
        rows = self._api_rows("branches")  # Read every page of the branch rows.
        names = [str(row["name"]) for row in rows]  # Keep the branch name only.
        _LOG.debug("Found %d branches", len(names))  # Record the count after the read.
        return names

    def read_branch(self, name: str) -> BranchRecord | None:
        """Compare one branch against the base and build its record.

        The method answers None when the API cannot compare the branch. A branch
        with no shared history answers a 404 error, and a branch that a
        cleanup deleted after the list read answers the same error.
        """
        _LOG.info("Comparing the branch %s against %s", name, self._base_branch)  # Report the plan.
        path = f"compare/{quote(self._base_branch, safe='/')}...{quote(name, safe='/')}"
        try:
            body = self._api(path)  # The API reports the ahead count.
        except _API_ERRORS as error:  # One unreadable branch must not stop the report.
            _LOG.warning("Could not compare %s with %s: %s", name, self._base_branch, _error_text(error))
            return None
        if not isinstance(body, dict):  # A malformed body cannot decide a deletion, so skip it.
            return None
        commits = body.get("commits") or []  # An empty list means the branch adds nothing.
        record = BranchRecord(
            name=name,  # Keep the name the caller asked for.
            sha=str(body.get("merge_base_commit", {}).get("sha", ""))[:40],  # Record the fork point.
            ahead_by=int(body.get("ahead_by", 0)),  # The count of commits above the base branch.
            last_commit_at=_head_date(commits),  # The date that decides the age test.
        )
        _LOG.debug("The branch %s is %d commits ahead", name, record.ahead_by)  # Record the result.
        return record

    def is_held_by_closed_pull_request(self, name: str) -> bool:
        """Report whether a closed pull request holds every commit of the branch.

        A merged or closed pull request keeps `refs/pull/<n>/head` at its last
        head commit. A branch that holds no commit above that head loses
        nothing in a deletion, so a merged branch that still exists is safe.
        """
        _LOG.info("Checking the closed pull requests of the branch %s", name)  # Report the plan.
        owner = self._repository.split("/", 1)[0]  # The head filter names the owner of the branch.
        head_filter = quote(f"{owner}:{name}", safe=":/")  # Keep a special branch character out of the query.
        try:
            return self._closed_head_holds(name, head_filter)
        except _API_ERRORS as error:  # An unread protection counts as no protection.
            _LOG.warning("Could not read the closed pull requests of %s: %s", name, _error_text(error))
            return False

    def _closed_head_holds(self, name: str, head_filter: str) -> bool:
        """Report whether the head of one closed pull request holds every branch commit."""
        for row in self._api_rows("pulls", f"state=closed&head={head_filter}"):  # Read every closed row.
            head = row.get("head")  # The API nests the head commit inside one object.
            head_sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(head_sha, str) or not head_sha:  # A row with no head commit holds nothing.
                continue
            body = self._api(f"compare/{head_sha}...{quote(name, safe='/')}")  # Count the commits above it.
            if isinstance(body, dict) and body.get("ahead_by") == 0:  # The pull request holds every commit.
                _LOG.debug("A closed pull request holds the head of %s", name)  # Record the protection.
                return True
        return False


class StrandedBranchReporter:
    """Find every branch that holds work with no pull request behind it."""

    def __init__(
        self,
        list_branches: Callable[[], list[str]],
        read_branch: Callable[[str], BranchRecord | None],
        list_open_heads: Callable[[], frozenset[str]],
        min_age_days: int = DEFAULT_MIN_AGE_DAYS,
        held_by_closed_pull_request: Callable[[str], bool] | None = None,
    ) -> None:
        """Store the readers and the age threshold that the report uses."""
        self._list_branches = list_branches  # Answers every branch name in the repository.
        self._read_branch = read_branch  # Answers one branch record, or None when unreadable.
        self._list_open_heads = list_open_heads  # Answers the head name of every open pull request.
        self._min_age_days = min_age_days  # The age below which the report stays quiet.
        # Answers whether a closed pull request holds the branch head. The default holds nothing.
        self._held_by_closed_pull_request = held_by_closed_pull_request or (lambda name: False)
        self._unreadable: list[str] = []  # The unprotected branches that the last search could not read.

    @property
    def unreadable(self) -> list[str]:
        """The unprotected branches that the last search could not compare, in name order."""
        return list(self._unreadable)

    def find(self, base_branch: str = DEFAULT_BASE_BRANCH, now: datetime | None = None) -> list[BranchRecord]:
        """Report every stranded branch, ordered from the oldest head first.

        The `now` parameter names the instant that every age test measures
        against. A caller that passes no instant reads the live clock, so a
        production call keeps its behavior. A test passes a fixed instant, so
        the result stays the same on every date.

        A branch that the reader cannot compare goes to `unreadable`, unless a
        pull request or a bot prefix protects it. Its age and its ahead count
        are unknown, so no quiet period applies to it.
        """
        _LOG.info("Searching for a branch with no pull request")  # Report the plan before the work.
        open_heads = self._list_open_heads()  # Read the protection set one time, not per branch.
        now = self._reference_time(now)  # Use the caller instant, or read the clock one time.
        stranded: list[BranchRecord] = []  # Collect the branches that fail every protection test.
        unreadable: list[str] = []  # Collect the unprotected branches that the reader cannot compare.
        for name in self._list_branches():  # Test each branch in the repository.
            if name == base_branch:  # The base branch is never stranded against itself.
                continue
            record = self._read_branch(name)  # Build the record that the tests read.
            if record is None:  # An unreadable branch can still hold the only copy of its work.
                if self._is_unprotected(name, open_heads):
                    unreadable.append(name)
                continue
            if not record.is_stranded(open_heads, self._min_age_days, now):  # Apply the three tests.
                continue
            if self._held_by_closed_pull_request(name):  # A merged branch that still exists is safe.
                continue
            stranded.append(record)  # Keep the branch for the report.
        stranded.sort(key=lambda item: item.last_commit_at)  # Report the oldest head first.
        self._unreadable = sorted(unreadable)  # Keep the list for the render and the exit status.
        _LOG.debug("Found %d stranded and %d unreadable branches", len(stranded), len(unreadable))
        return stranded

    def _is_unprotected(self, name: str, open_heads: frozenset[str]) -> bool:
        """Report whether no pull request and no bot prefix protects a branch."""
        if name in open_heads or name.startswith(IGNORED_PREFIXES):
            return False
        return not self._held_by_closed_pull_request(name)

    def render(self, stranded: list[BranchRecord], now: datetime | None = None) -> str:
        """Build the Markdown report that an operator or an issue body reads.

        The `now` parameter names the instant that every age column measures
        against. It defaults to the live clock, so a production call keeps its
        behavior. The rows of the unreadable branches from the last search come
        after the stranded rows.
        """
        _LOG.info("Rendering the report for %d branches", len(stranded))  # Report before the build.
        if not stranded and not self._unreadable:  # A clean repository still needs a clear answer.
            return "Every branch with work above the base branch has a pull request that holds its head.\n"
        now = self._reference_time(now)  # Use the caller instant, so every age column agrees.
        lines = [
            "The branches below hold commits above the base branch, and no pull request",
            "holds those commits. Open a pull request for each one before any cleanup,",
            "because `refs/pull/<n>/head` is the only reference that survives a branch deletion.",
            "",
            "| Branch | Commits ahead | Age in days |",
            "| - | - | - |",
        ]  # The header states the rule, so the reader needs no other page.
        for record in stranded:  # Add one row for each stranded branch.
            lines.append(f"| `{record.name}` | {record.ahead_by} | {record.age_days(now)} |")
        for name in self._unreadable:  # Add one row for each branch that the reader could not compare.
            lines.append(f"| `{name}` | {_UNKNOWN} | {_UNKNOWN} |")
        if self._unreadable:  # Tell the reader what an unknown row means.
            lines.extend(
                [
                    "",
                    f"A row with `{_UNKNOWN}` values names a branch that the report could not compare with",
                    "the base branch, such as a branch with no shared history. Check each one before any cleanup.",
                ]
            )
        return "\n".join(lines) + "\n"  # A trailing newline keeps the Markdown well formed.

    @staticmethod
    def _reference_time(now: datetime | None) -> datetime:
        """Answer the instant the caller chose, or the live UTC clock."""
        if now is not None:  # A caller instant keeps a test hermetic on every date.
            _LOG.debug("Using the caller reference time %s", now)  # Record the chosen instant.
            return now
        _LOG.debug("Reading the live UTC clock for the reference time")  # Record the default path.
        return datetime.now(UTC)  # Read the clock one time, so every age uses the same instant.


def _head_ref(row: dict[str, object]) -> str:
    """Read the head branch name of one pull request row."""
    head = row.get("head")  # The API nests the head reference inside one object.
    if not isinstance(head, dict):  # A row with no head object names no branch.
        return ""  # An empty name never matches a real branch.
    ref = head.get("ref")  # The short branch name lives under the `ref` key.
    return ref if isinstance(ref, str) else ""  # Only a string can match a branch name.


def _as_rows(body: object) -> Iterable[dict[str, object]]:
    """Read a JSON array of objects, and answer nothing for any other shape."""
    if not isinstance(body, list):  # A single object or an error body holds no rows.
        return []
    return [row for row in body if isinstance(row, dict)]  # Drop any element that is not an object.


def _head_date(commits: object) -> datetime:
    """Read the commit date of the newest commit in a compare response."""
    rows = list(_as_rows(commits))  # The compare response orders the commits oldest first.
    if not rows:  # A branch with no commit above the base has no head date to read.
        return datetime.now(UTC)  # A current date keeps the branch below every age threshold.
    raw = rows[-1].get("commit", {})  # The last row is the head of the branch.
    stamp = raw.get("committer", {}).get("date") if isinstance(raw, dict) else None
    if not isinstance(stamp, str):  # A missing date cannot support a deletion decision.
        return datetime.now(UTC)  # Treat the branch as new, so the report stays quiet.
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))  # The API answers RFC 3339.


def _default_repository() -> str:
    """Answer the repository of the current run, or an empty string.

    A GitHub Actions run names its repository in `GITHUB_REPOSITORY`. A local
    run asks `gh` for the repository of the current checkout.
    """
    from_environment = os.environ.get(_REPOSITORY_VARIABLE, "").strip()  # A workflow run sets this value.
    if from_environment:
        _LOG.debug("Read the repository %s from %s", from_environment, _REPOSITORY_VARIABLE)
        return from_environment
    _LOG.info("Asking gh for the repository of the current checkout")  # Report the fallback read.
    command = ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    try:
        result = subprocess.run(  # The argument list is built here, not by a shell.
            command, capture_output=True, encoding="utf-8", check=False, timeout=_GH_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as error:  # A missing or stalled gh names no repository.
        _LOG.warning("gh could not name the repository: %s", error)
        return ""
    if result.returncode != 0:  # A directory outside a checkout names no repository.
        _LOG.warning("gh could not name the repository: %s", result.stderr.strip())
        return ""
    return result.stdout.strip()


def _build_parser() -> argparse.ArgumentParser:
    """Build the command line parser for the tool."""
    parser = argparse.ArgumentParser(
        prog="stranded-branch-report", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repository",
        default="",
        help="The owner and name pair to read. The default is GITHUB_REPOSITORY, then the gh checkout.",
    )
    parser.add_argument("--base-branch", default=DEFAULT_BASE_BRANCH, help="The branch every feature merges into.")
    parser.add_argument("--min-age-days", type=int, default=DEFAULT_MIN_AGE_DAYS, help="The quiet period in days.")
    parser.add_argument("--fail-on-find", action="store_true", help="Exit with status 1 when a branch is stranded.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read the repository, print the report, and answer the exit status."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")  # One line per event.
    parser = _build_parser()
    args = parser.parse_args(argv)  # Read the command line before any network call.
    repository = args.repository or _default_repository()  # An explicit value wins over the run context.
    if not repository:  # The tool cannot guess a repository, so stop with a usage error.
        parser.error("pass --repository, because GITHUB_REPOSITORY is empty and gh named no repository")
    _LOG.info("Reporting the stranded branches of %s", repository)  # Report the plan.
    reader = GitHubReader(repository, args.base_branch)  # Bind the reader to one repository.
    reporter = StrandedBranchReporter(
        reader.list_branches,
        reader.read_branch,
        reader.list_open_pull_request_heads,
        args.min_age_days,
        reader.is_held_by_closed_pull_request,
    )
    stranded = reporter.find(args.base_branch)  # Apply the three protection tests to every branch.
    sys.stdout.write(reporter.render(stranded))  # Print the Markdown report for the caller.
    found = len(stranded) + len(reporter.unreadable)  # An unreadable branch can hold the only copy.
    _LOG.debug("The report named %d branches", found)  # Record the count after the render.
    return 1 if found and args.fail_on_find else 0  # Only --fail-on-find turns a find into a failure.


def _error_text(error: Exception) -> str:
    """Answer the most useful text of one gh call fault."""
    if isinstance(error, subprocess.CalledProcessError) and isinstance(error.stderr, str) and error.stderr.strip():
        return error.stderr.strip()  # The gh error text names the HTTP status and the API message.
    return str(error)


if __name__ == "__main__":
    raise SystemExit(main())
