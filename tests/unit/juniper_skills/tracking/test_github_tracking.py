"""Tests for the Juniper skill factory GitHub journal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.juniper_skills.tracking.comment_codec import StageCommentCodec
from src.juniper_skills.tracking.github_cli import GitHubCommandResult, GitHubRateLimitManager
from src.juniper_skills.tracking.github_tracker import SkillIssueTracker
from src.juniper_skills.tracking.measurements import StageMeasurementInputs, StageMeasurementReader
from src.juniper_skills.tracking.models import DocumentRecord, StageEvent, StageName


class FakeGitHubRunner:
    """Mock `gh` calls without network access."""

    def __init__(self, low_limit_first: bool = False) -> None:
        self.commands: list[list[str]] = []  # Keep every command so tests can assert idempotency.
        self.comments: list[str] = []  # Store issue comments for recovery tests.
        self.input_bodies: list[str] = []  # Store API JSON bodies for label replacement assertions.
        self.issue_counter = 9990  # Allocate deterministic fake issue numbers.
        self.search_titles: dict[str, int] = {}  # Mirror GitHub title searches for duplicate prevention.
        self.low_limit_first = low_limit_first  # Let one test force a nonblocking rate-limit pause.
        self.rate_reads = 0  # Count rate-limit checks so the fake can recover.

    def run(self, command: list[str]) -> GitHubCommandResult:
        """Return deterministic output for known `gh` commands."""
        self.commands.append(command)  # Record the command before returning a fake response.
        if command[:3] == ["gh", "api", "rate_limit"]:  # Simulate the core API bucket.
            return self._rate_limit()  # Return low or healthy limits for queue tests.
        if command[:4] == ["gh", "issue", "list", "--repo"]:  # Simulate exact title search.
            return self._issue_list(command)  # Return a match only after the fake creates one.
        if command[:3] == ["gh", "label", "create"]:  # Simulate successful label creation.
            return GitHubCommandResult(0, "", "")  # GitHub returns no useful body for created labels.
        if command[:3] == ["gh", "issue", "create"]:  # Simulate a created issue URL.
            return self._issue_create(command)  # Allocate one fake issue number.
        if command[:3] == ["gh", "issue", "view"]:  # Return issue metadata or comments.
            return self._issue_view(command)  # Mirror the GitHub JSON shape that recovery reads.
        if command[:3] == ["gh", "issue", "comment"]:  # Store the stage comment body.
            self.comments.append(command[-1])  # The `--body` value is the final argument.
            return GitHubCommandResult(0, "https://example.invalid/comment\n", "")  # Simulate success.
        if command[:3] == ["gh", "issue", "close"]:  # Simulate closing completed work.
            return GitHubCommandResult(0, "closed\n", "")  # Return a small success body.
        if command[:2] == ["gh", "api"] and "/issues/" in command[2]:  # Simulate REST issue metadata.
            return GitHubCommandResult(0, json.dumps({"id": 123456}), "")  # Return the sub-issue ID.
        return GitHubCommandResult(1, "", "unexpected command")  # Fail unknown commands.

    def run_with_input(self, command: list[str], input_text: str) -> GitHubCommandResult:
        """Return deterministic output for `gh api --input -` commands."""
        self.commands.append(command)  # Record label replacement calls.
        self.input_bodies.append(input_text)  # Keep the exact JSON body for assertions.
        return GitHubCommandResult(0, "{}", "")  # Simulate a successful labels patch.

    def _rate_limit(self) -> GitHubCommandResult:
        """Return a low first limit or a healthy limit."""
        self.rate_reads += 1  # Track calls so only the first read can fail.
        remaining = 1 if self.low_limit_first and self.rate_reads == 1 else 5000  # Force one pause when requested.
        payload = {"rate": {"remaining": remaining, "reset": 2000}}  # Match the GitHub rate-limit shape.
        return GitHubCommandResult(0, json.dumps(payload), "")  # Return CLI output text.

    def _issue_list(self, command: list[str]) -> GitHubCommandResult:
        """Return a fake search result for an issue title."""
        title = self._quoted_title(command)  # Extract the exact title from the search query.
        number = self.search_titles.get(title)  # Look for a previously created fake issue.
        rows = [] if number is None else [{"number": number, "title": title}]  # Mirror `gh issue list` JSON.
        return GitHubCommandResult(0, json.dumps(rows), "")  # Return the fake search output.

    def _issue_create(self, command: list[str]) -> GitHubCommandResult:
        """Allocate one fake issue number."""
        self.issue_counter += 1  # Move to the next deterministic issue number.
        title = command[command.index("--title") + 1]  # Read the issue title for future searches.
        self.search_titles[title] = self.issue_counter  # Make a later search find the same issue.
        url = f"https://github.com/jmorrison-juniper/MistHelper/issues/{self.issue_counter}\n"  # Match `gh`.
        return GitHubCommandResult(0, url, "")  # Return the created issue URL.

    def _issue_view(self, command: list[str]) -> GitHubCommandResult:
        """Return fake issue metadata or fake comments."""
        if "comments" in command[-1]:  # Detect the recovery comment read request.
            payload = {"comments": [{"body": comment} for comment in self.comments]}  # Mirror GitHub JSON shape.
            return GitHubCommandResult(0, json.dumps(payload), "")  # Return comments for recovery.
        return GitHubCommandResult(0, json.dumps({"id": 123456}), "")  # Return the REST ID for sub-issues.

    def _quoted_title(self, command: list[str]) -> str:
        """Extract the title from the `gh issue list --search` query."""
        query = command[command.index("--search") + 1]  # Read the search query argument.
        return query.split('"', 2)[1]  # Return the text inside the first quoted title.


class TestStageCommentCodec:
    """Verify the comment format that crash recovery reads."""

    def test_comment_round_trip_returns_exact_resume_point(self) -> None:
        document_key = "guides-junos-beginners-guide-md"  # Use the stable key shape the tracker creates.
        details = self._metrics()  # Include measured fields that every audit comment needs.
        event = StageEvent(document_key, StageName.REWRITTEN, "continue", 99, "2026-09-18T03:00:00Z", details)  # Seed.
        codec = StageCommentCodec()  # Use the production parser and renderer.
        comment = codec.build_comment(event)  # Render the GitHub issue comment.
        resume_point = codec.parse_comments([comment], document_key)  # Parse the comment into recovery state.
        assert resume_point.completed_stage == StageName.REWRITTEN  # Confirm the exact completed stage.
        assert resume_point.next_action == "run the guarded stage"  # Confirm the next action follows stage order.
        assert resume_point.issue_number == 99  # Confirm the issue link survives the round trip.
        assert "Topic count: 3." in comment  # Confirm the human audit line includes measured work.
        assert json.loads(comment.split("```json", 1)[1].split("```", 1)[0])["details"] == details  # Confirm JSON.

    def _metrics(self) -> dict[str, object]:
        """Return measured audit fields for one stage."""
        return {  # Keep test evidence compact and deterministic.
            "topic_count": 3,
            "card_count": 7,
            "retention_percentage": 98.2,
            "guard_result": "passed",
            "longest_guard_run": "retention 1.2s",
            "ste_score": 91,
        }


class TestSkillIssueTracker:
    """Verify local mirroring, idempotency, and crash recovery."""

    def test_rate_limit_exhaustion_resumes_without_duplicate_issue(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "rate_limit")  # Use an isolated database for the test.
        runner = FakeGitHubRunner(low_limit_first=True)  # Force the first GitHub check to defer.
        tracker = SkillIssueTracker(database_path, runner=runner)  # Build the tracker with the fake network.
        tracker.record_stage(self._document(1), StageName.QUEUED)  # Write SQLite first with no network requirement.
        first_sync = tracker.sync_pending(limit=10)  # Try to sync while the fake rate limit is exhausted.
        second_sync = tracker.sync_pending(limit=10)  # Retry after the fake rate limit recovers.
        assert first_sync == 0  # Confirm the pipeline did not force a blocking wait.
        assert second_sync == 2  # Confirm the issue and the queued comment synced on retry.
        assert len(self._create_calls(runner)) == 1  # Confirm the retry did not create a duplicate issue.

    def test_tracker_prevents_duplicate_issues_at_scale(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "idempotent_scale")  # Use an isolated database for the test.
        runner = FakeGitHubRunner()  # Use a fake runner to count issue creation calls.
        tracker = SkillIssueTracker(database_path, runner=runner)  # Build the tracker.
        documents = [self._document(index) for index in range(50)]  # Use enough records to prove scale behavior.
        for document in documents:  # Queue each document twice to simulate a restart.
            tracker.ensure_issue(document, sync=False)  # Store the first local row without network.
            tracker.ensure_issue(document, sync=False)  # Re-run the same document before reconciliation.
        tracker.sync_pending(limit=200)  # Reconcile the queued documents in one batch.
        tracker.sync_pending(limit=200)  # Re-run reconciliation to prove idempotency.
        assert len(self._create_calls(runner)) == len(documents)  # Confirm one GitHub issue per document.

    def test_index_generation_groups_documents_by_domain_and_stage(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "index")  # Use an isolated database for the test.
        tracker = SkillIssueTracker(database_path, runner=FakeGitHubRunner())  # Build the tracker.
        tracker.record_stage(self._document(1, "routing"), StageName.SEGMENTED)  # Add a routing document row.
        tracker.record_stage(self._document(2, "switching"), StageName.VERIFIED)  # Add a switching document row.
        index = tracker.build_index_markdown()  # Render the generated parent index.
        assert "## routing" in index  # Confirm the first domain has a section.
        assert "## switching" in index  # Confirm the second domain has a section.
        assert "`complete` `verified`" in index  # Confirm terminal stage status is visible.
        assert "`in-progress` `segmented`" in index  # Confirm unfinished stage status is visible.

    def test_tracker_records_and_recovers_exact_resume_point(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "roundtrip")  # Use an isolated database for the test.
        runner = FakeGitHubRunner()  # Use a fake runner so no network call occurs.
        tracker = SkillIssueTracker(database_path, runner=runner)  # Build the real tracker.
        document = self._document(1)  # Build one source document for the journal.
        tracker.record_stage(document, StageName.QUEUED, self._metrics(), sync=True)  # Record queued.
        tracker.record_stage(document, StageName.PARTS_JOINED, self._metrics(), sync=True)  # Record parts joined.
        tracker.record_stage(document, StageName.SEGMENTED, self._metrics(), sync=True)  # Simulate a crash here.
        resume_point = tracker.resume_point(document)  # Recover from GitHub comments and the local mirror.
        assert resume_point.completed_stage == StageName.SEGMENTED  # Confirm no uncompleted stage advanced.
        assert resume_point.next_action == "run the extracted stage"  # Confirm the resumed action is exact.
        assert len(runner.comments) == 1  # Confirm GitHub received only the immediate start comment.

    def test_tracker_posts_start_summary_and_close_for_completed_document(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "summary_calls")  # Use an isolated database for the test.
        runner = FakeGitHubRunner()  # Use a fake runner to count GitHub operations.
        tracker = SkillIssueTracker(database_path, runner=runner)  # Build the tracker.
        document = self._document(1)  # Build one source document for the queue.
        for stage in (StageName.QUEUED, StageName.SEGMENTED, StageName.VERIFIED):  # Queue a completed document.
            tracker.record_stage(document, stage, self._metrics())  # Save each transition locally first.
        tracker.sync_pending(limit=10)  # Create the issue and post the start comment.
        tracker.sync_pending(limit=10)  # Post the consolidated completion comment and close the issue.
        assert len(self._create_calls(runner)) == 1  # Confirm only one create call occurs.
        assert len(self._comment_calls(runner)) == 2  # Confirm only start and completion comments post.
        assert len(self._close_calls(runner)) == 1  # Confirm completed work closes the issue.
        assert "Skill factory document completion summary." in runner.comments[-1]  # Confirm summary form.

    def test_missing_upstream_measurements_are_not_measured(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "missing_measurements")  # Use no source database.
        reader = StageMeasurementReader(database_path)  # Read from an isolated absent database path.
        inputs = StageMeasurementInputs("missing-document")  # Provide no package or upstream result.
        details = reader.details(inputs)  # Build audit details from absent evidence.
        assert set(details.values()) == {"not measured"}  # Confirm no synthetic number can enter a comment.

    def test_package_measurements_read_values_from_disk(self, tmp_path: Path) -> None:
        database_path = self._database_path(tmp_path, "package_measurements")  # Use an isolated source database.
        package_dir = tmp_path / "package"  # Keep generated package files out of the live factory data.
        self._write_source_size(database_path, "disk-document", 1000)  # Provide real source size for retention.
        self._write_topic(package_dir / "alpha.md", "- MUST: One fact. [A p.1]\n")  # Create one real card.
        self._write_topic(package_dir / "beta.md", "- **INFO**: Two fact. [A p.2]\n")  # Create one alternate card.
        reader = StageMeasurementReader(database_path)  # Build the real measurement reader.
        details = reader.details(StageMeasurementInputs("disk-document", package_dir))  # Read package metrics.
        assert details["topic_count"] == 2  # Confirm topic count came from Markdown files.
        assert details["card_count"] == 2  # Confirm both supported card syntaxes count.
        assert details["retention_percentage"] != "not measured"  # Confirm retention used disk and database sizes.

    def _document(self, index: int, domain: str = "junos") -> DocumentRecord:
        """Return one document record for tests."""
        return DocumentRecord(  # Keep the test document small and deterministic.
            Path("guides") / f"junos-beginners-guide-{index}.pdf",
            f"Junos Beginners Guide {index}",
            "guides",
            42,
            domain,
            f"JUNOS-{index:04d}",
            2,
            f"juniper-{domain}",
            "high",
            "current",
        )

    def _metrics(self) -> dict[str, object]:
        """Return measured audit fields for one stage."""
        return {  # Keep test evidence compact and deterministic.
            "topic_count": 3,
            "card_count": 7,
            "retention_percentage": 98.2,
            "guard_result": "passed",
            "longest_guard_run": "retention 1.2s",
            "ste_score": 91,
        }

    def _create_calls(self, runner: FakeGitHubRunner) -> list[list[str]]:
        """Return fake issue creation calls."""
        return [command for command in runner.commands if command[:3] == ["gh", "issue", "create"]]  # Count creates.

    def _comment_calls(self, runner: FakeGitHubRunner) -> list[list[str]]:
        """Return fake issue comment calls."""
        return [command for command in runner.commands if command[:3] == ["gh", "issue", "comment"]]  # Count comments.

    def _close_calls(self, runner: FakeGitHubRunner) -> list[list[str]]:
        """Return fake issue close calls."""
        return [command for command in runner.commands if command[:3] == ["gh", "issue", "close"]]  # Count closes.

    def _database_path(self, tmp_path: Path, name: str) -> Path:
        """Return an isolated SQLite path for one test."""
        return tmp_path / f"test_tracking_{name}.db"  # Let pytest clean the database after open handles close.

    def _write_source_size(self, database_path: Path, document_key: str, text_chars: int) -> None:
        """Create a minimal source document table for measurement tests."""
        import sqlite3  # Keep SQLite local to this helper because production code owns database access.

        database_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the isolated database directory exists.
        connection = sqlite3.connect(database_path)  # Open a compact database fixture.
        try:
            connection.execute(
                "CREATE TABLE source_document (document_key TEXT PRIMARY KEY, text_chars INTEGER)"
            )  # Schema.
            connection.execute("INSERT INTO source_document VALUES (?, ?)", (document_key, text_chars))  # Add source.
            connection.commit()  # Persist the fixture before the measurement reader opens it.
        finally:
            connection.close()  # Release the file handle for Windows cleanup.

    def _write_topic(self, path: Path, text: str) -> None:
        """Write one generated topic file for measurement tests."""
        path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the package directory exists.
        path.write_text(text, encoding="utf-8")  # Write a small Markdown topic for the reader.


class TestGitHubRateLimitManager:
    """Verify rate-limit backoff behavior."""

    def test_rate_limit_backoff_waits_until_reset(self, monkeypatch: Any) -> None:
        waits: list[int] = []  # Capture sleep calls without delaying the test.
        runner = LowLimitRunner()  # Return a low rate limit to trigger backoff.
        manager = GitHubRateLimitManager(runner, minimum_remaining=10)  # Require more calls than remain.
        monkeypatch.setattr("src.juniper_skills.tracking.github_cli.time.time", lambda: 1000)  # Freeze current time.
        monkeypatch.setattr("src.juniper_skills.tracking.github_cli.time.sleep", waits.append)  # Capture sleep seconds.
        manager.wait_if_needed()  # Exercise the backoff path.
        assert waits == [6]  # Confirm reset minus current time plus one guard second.


class LowLimitRunner:
    """Mock a rate-limit response that requires backoff."""

    def run(self, command: list[str]) -> GitHubCommandResult:
        """Return one low-limit response."""
        payload = {"rate": {"remaining": 1, "reset": 1005}}  # Force a small wait from the manager.
        return GitHubCommandResult(0, json.dumps(payload), "")  # Match the GitHub CLI result shape.
