"""GitHub issue tracker for the Juniper skill factory."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.juniper_skills.tracking.comment_codec import StageCommentCodec
from src.juniper_skills.tracking.github_cli import (
    GitHubCliError,
    GitHubCliRunner,
    GitHubRateLimitExhausted,
    GitHubRateLimitManager,
)
from src.juniper_skills.tracking.models import DocumentRecord, ResumePoint, StageEvent, StageName
from src.juniper_skills.tracking.recovery import SkillIssueRecoveryReader
from src.juniper_skills.tracking.rest_client import GitHubRestRunner
from src.juniper_skills.tracking.store import FactoryJournalStore

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SkillIssueTracker:
    """Open and update one GitHub issue for each source document."""

    def __init__(
        self,
        database_path: Path,
        repo: str = "jmorrison-juniper/MistHelper",
        parent_issue: int = 2925,
        issue_shape: str = "document",
        runner: GitHubCliRunner | GitHubRestRunner | None = None,
    ) -> None:
        """Initialize the SkillIssueTracker instance."""
        self.repo = repo  # Store the repository owner and name for each `gh` command.
        self.parent_issue = parent_issue  # Link created journals back to the factory issue.
        self.issue_shape = issue_shape  # Keep compatibility while document issues are now the default.
        self.runner = runner or GitHubRestRunner()  # Use persistent REST by default and let tests inject fakes.
        self.rate_limit = GitHubRateLimitManager(self.runner)  # Measure real limits before GitHub calls.
        self.store = FactoryJournalStore(database_path)  # Persist all work before any network call.
        self.codec = StageCommentCodec()  # Encode journal comments in one stable format.
        self.ensured_labels: set[str] = set()  # Avoid repeated label create calls during large reconciliations.

    def ensure_issue(self, document: DocumentRecord, sync: bool = True) -> int | None:
        """Queue one document issue and optionally reconcile it to GitHub."""
        logger.info("Ensuring a document GitHub journal issue is queued")  # Record the durable local action.
        self.store.save_document(document, self.issue_shape)  # Mirror document metadata before a network action.
        issue_number = self.store.get_issue_number(document.document_key)  # Reuse a local issue link if it exists.
        if issue_number is not None:  # Avoid duplicate issue creation on retries.
            logger.debug("Using local journal issue %d", issue_number)  # Record the idempotent result.
            return issue_number
        synced_count = self.sync_pending(limit=1) if sync else 0  # Let callers defer GitHub work.
        logger.debug("Document issue queue sync attempted %d item", synced_count)  # Record nonblocking behavior.
        return self.store.get_issue_number(document.document_key)  # Return None when the background queue must retry.

    def record_stage(
        self,
        document: DocumentRecord,
        stage: StageName,
        details: dict[str, object] | None = None,
        sync: bool = False,
    ) -> int:
        """Store a stage event and optionally sync the background queue."""
        logger.info("Recording a skill factory stage transition")  # Record the durable journal action.
        self.store.save_document(document, self.issue_shape)  # Save the document before the comment event.
        issue_number = self.store.get_issue_number(document.document_key)  # Attach the issue number if it exists.
        event = self._stage_event(document, stage, issue_number, details or {})  # Build the exact recovery state.
        comment_body = self.codec.build_comment(event)  # Create the GitHub-ready comment text.
        event_id = self.store.enqueue_stage_event(event, comment_body)  # Save the local mirror before the network call.
        self.store.update_document_stage(
            document.document_key, stage.value, self._status_for_stage(stage)
        )  # Update board.
        self.sync_pending() if sync else logger.debug("Deferred GitHub sync for stage event %d", event_id)
        return event_id

    def sync_pending(self, limit: int = 25) -> int:
        """Reconcile queued issues and comments to GitHub."""
        logger.info("Reconciling local journal work to GitHub")  # Record the sync action.
        synced_count = self._sync_document_issues(limit)  # Create document issues before comments that need them.
        remaining_limit = max(0, limit - synced_count)  # Keep the caller limit across issue and comment writes.
        synced_count += self._sync_stage_events(remaining_limit)  # Send comments only for documents with issues.
        logger.debug("Synced %d queued GitHub journal items", synced_count)  # Record the sync result.
        return synced_count

    def resume_point(self, document: DocumentRecord) -> ResumePoint:
        """Return the next action for a document after a crash."""
        logger.info("Reading the crash recovery point for a document")  # Record the recovery action.
        issue_number = self.store.get_issue_number(document.document_key)  # Use the local issue link if present.
        reader = SkillIssueRecoveryReader(self.store, self.runner, self.rate_limit, self.repo)  # Build the reader.
        return reader.resume_point(document.document_key, issue_number)  # Reconstruct the exact resume point.

    def build_index_markdown(self) -> str:
        """Return a generated progress index grouped by domain."""
        logger.info("Building the skill factory issue index")  # Record the index action.
        rows = self.store.documents_for_index()  # Read progress rows from the durable local mirror.
        index = SkillFactoryIssueIndex(self.repo).render(rows)  # Render the Markdown index for issue #2925 or a file.
        logger.debug("Built the skill factory issue index with %d characters", len(index))  # Record output size.
        return index

    def sync_parent_index_comment(self) -> int:
        """Add a generated index comment to the parent issue."""
        logger.info("Writing the generated index comment to the parent issue")  # Record the index sync.
        body = self.build_index_markdown()  # Build the current local progress board.
        self.rate_limit.defer_if_needed()  # Defer this optional write when the API bucket is low.
        self.runner.run(
            ["gh", "issue", "comment", str(self.parent_issue), "--repo", self.repo, "--body", body]
        )  # Write.
        logger.debug("Wrote the generated index comment to issue %d", self.parent_issue)  # Record the target issue.
        return 1

    def _sync_document_issues(self, limit: int) -> int:
        """Create queued document issues."""
        synced_count = 0  # Count successful creates for throughput reporting.
        for row in self.store.unsynced_documents(limit):  # Process a bounded queue slice.
            synced_count += self._sync_one_document(row)  # Create one issue and preserve the rest on failure.
        return synced_count

    def _sync_stage_events(self, limit: int) -> int:
        """Send queued stage comments."""
        synced_count = 0  # Count successful comments for caller evidence.
        for document_key in self.store.unsynced_document_keys(limit):  # Send one GitHub update per document state.
            synced_count += self._sync_document_events(document_key)  # Consolidate stage details where possible.
        return synced_count

    def _sync_document_events(self, document_key: str) -> int:
        """Send the required GitHub audit comment for one document."""
        events = self.store.stage_events_for_document(document_key)  # Read all local events for this document.
        if self._needs_start_comment(events):  # Post the start comment as soon as the document enters the pipeline.
            return self._sync_one_event(events[0])  # Mark only the start event as synced.
        if self._latest_stage(events) == StageName.FAILED.value:  # Failures must appear on GitHub immediately.
            return self._sync_event_summary(events)  # Post full context for human triage.
        if self._latest_stage(events) in {StageName.VERIFIED.value, StageName.RELEASED.value}:  # Healthy terminal.
            return self._sync_event_summary(events)  # Post one completion table with all measured values.
        logger.debug("Deferred nonterminal stage events for document key %s", document_key)  # Wait for completion.
        return 0

    def _needs_start_comment(self, events: list[dict[str, Any]]) -> bool:
        """Return whether the queued stage still needs its start comment."""
        has_queued_start = bool(events) and events[0]["stage"] == StageName.QUEUED.value  # Confirm the first stage.
        return has_queued_start and not events[0]["github_synced"]  # Post the start comment only once.

    def _latest_stage(self, events: list[dict[str, Any]]) -> str:
        """Return the last recorded stage for one document."""
        if not events:  # A corrupt queue should not try a GitHub write.
            return ""  # Return no terminal stage.
        return str(events[-1]["stage"])  # The store returns events in creation order.

    def _sync_event_summary(self, events: list[dict[str, Any]]) -> int:
        """Write one consolidated stage summary and mark all events synced."""
        try:
            self.rate_limit.defer_if_needed()  # Never block the pipeline when the API bucket is empty.
            issue_number = self._document_issue_number(str(events[-1]["document_key"]))  # Read the current issue.
            stage_events = tuple(self._stage_event_from_row(event, issue_number) for event in events)  # Rebuild events.
            self._comment_summary(stage_events)  # Write one complete audit table to GitHub.
            self._set_issue_labels(issue_number, str(events[-1]["stage"]))  # Keep filters precise.
            self._close_if_complete(issue_number, str(events[-1]["stage"]))  # Close finished document work.
            self.store.mark_stage_events_synced(tuple(int(event["id"]) for event in events))  # Mark after success.
            return 1
        except (GitHubCliError, GitHubRateLimitExhausted) as error:
            logger.debug("Deferred GitHub summary sync after error: %s", error)  # Leave events queued for retry.
            return 0

    def _sync_one_document(self, row: dict[str, Any]) -> int:
        """Create or find one document issue."""
        document = self._document_from_row(row)  # Rebuild the document identity from SQLite.
        try:
            self.rate_limit.defer_if_needed()  # Never block the pipeline when the API bucket is empty.
            issue_number = self._find_or_create_issue(document)  # Search GitHub before creating a lazy issue.
            self.store.mark_document_issue_synced(document.document_key, issue_number)  # Persist the remote link.
            return 1
        except (GitHubCliError, GitHubRateLimitExhausted) as error:
            logger.debug("Deferred document issue sync after error: %s", error)  # Leave the row queued for retry.
            return 0

    def _sync_one_event(self, event: dict[str, object]) -> int:
        """Send one queued stage event to GitHub."""
        try:
            self.rate_limit.defer_if_needed()  # Measure the API bucket before the comment call.
            issue_number = self._document_issue_number(str(event["document_key"]))  # Read the current issue link.
            self._comment_issue(event, issue_number)  # Write the machine-readable audit comment.
            event_id = int(str(event["id"]))  # Convert persisted JSON values through text for strict typing.
            self.store.mark_stage_event_synced(event_id)  # Mark the row only after GitHub accepts the comment.
            return 1
        except (GitHubCliError, GitHubRateLimitExhausted) as error:
            logger.debug("Deferred GitHub journal sync after error: %s", error)  # Leave the row queued for retry.
            return 0

    def _comment_issue(self, event: dict[str, object], issue_number: int) -> None:
        """Write one stage comment to a document issue."""
        logger.info("Writing a stage audit comment to GitHub issue %s", issue_number)  # Record the write.
        self.runner.run(  # Write the crash-recovery journal comment to GitHub.
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                self.repo,
                "--body",
                str(event["comment_body"]),
            ]
        )
        logger.debug("Wrote a stage audit comment for event %s", event["id"])  # Record the queue row.

    def _comment_summary(self, events: tuple[StageEvent, ...]) -> None:
        """Write one consolidated stage summary comment."""
        issue_number = events[-1].issue_number  # Use the latest event's issue number.
        logger.info("Writing a consolidated audit comment to GitHub issue %s", issue_number)  # Record the write.
        body = self.codec.build_summary_comment(events)  # Build a full stage table with JSON recovery data.
        self.runner.run(["gh", "issue", "comment", str(issue_number), "--repo", self.repo, "--body", body])  # Write.
        logger.debug("Wrote a consolidated audit comment to issue %s", issue_number)  # Record success.

    def _document_issue_number(self, document_key: str) -> int:
        """Return the GitHub issue number for one document key."""
        row = self.store.document_row(document_key)  # Read the local document row after issue creation.
        if row is None or row["issue_number"] is None:  # A summary cannot post without an issue number.
            raise GitHubCliError("document issue number is missing")
        return int(row["issue_number"])  # Return the current issue number.

    def _stage_event_from_row(self, row: dict[str, Any], issue_number: int) -> StageEvent:
        """Return a stage event from a local queue row."""
        return StageEvent(  # Rebuild the typed event for the comment codec.
            str(row["document_key"]),
            StageName(str(row["stage"])),
            str(row["next_action"]),
            issue_number,
            str(row["created_at"]),
            dict(row["details"]),
        )

    def _find_or_create_issue(self, document: DocumentRecord) -> int:
        """Search for a journal issue and create one only when absent."""
        title = self._issue_title(document)  # Build the idempotency key that GitHub search can find.
        found_issue = self._search_issue(title)  # Search before create to avoid duplicate issues.
        if found_issue is not None:  # Reuse the existing issue when a prior run created it.
            return found_issue
        return self._create_issue(document, title)  # Create the document issue only after no match exists.

    def _search_issue(self, title: str) -> int | None:
        """Search GitHub for an exact journal title."""
        logger.info("Searching GitHub for an existing document issue")  # Record the idempotency search.
        query = f'repo:{self.repo} in:title "{title}"'  # Scope search to this repository and exact title text.
        result = self.runner.run(  # Ask GitHub for a small JSON result set.
            ["gh", "issue", "list", "--repo", self.repo, "--state", "all", "--search", query, "--json", "number,title"]
        )
        matches = json.loads(result.stdout)  # Decode the result set for exact title comparison.
        issue_number = next((int(item["number"]) for item in matches if item["title"] == title), None)  # Match title.
        logger.debug("GitHub document issue search found %s", issue_number)  # Record the idempotency result.
        return issue_number

    def _create_issue(self, document: DocumentRecord, title: str) -> int:
        """Create a GitHub journal issue."""
        logger.info("Creating a document GitHub journal issue")  # Record the network create action.
        self._ensure_labels(document.domain, StageName.QUEUED.value, "queued")  # Ensure initial filters exist first.
        result = self.runner.run(  # Create one issue for this source document.
            [
                "gh",
                "issue",
                "create",
                "--repo",
                self.repo,
                "--title",
                title,
                "--body",
                self._issue_body(document),
                "--label",
                self._label_csv(document.domain, StageName.QUEUED.value, "queued"),
            ]
        )
        issue_number = self._issue_number_from_url(result.stdout)  # Read the created number from the CLI URL.
        issue_id = self._issue_id(issue_number)  # Read the REST ID that the sub-issue API needs.
        self._link_parent_issue(issue_number, issue_id)  # Try to connect the issue to #2925.
        logger.debug("Created GitHub document journal issue %d", issue_number)  # Record the created issue number.
        return issue_number

    def _set_issue_labels(self, issue_number: int, stage: str) -> None:
        """Replace labels so the issue list shows the current stage."""
        status = self._status_for_stage(StageName(stage))  # Derive the status label from the completed stage.
        row = self._row_for_issue(issue_number)  # Read the domain that the label set needs.
        labels = self._label_list(str(row["domain"]), stage, status)  # Build the complete label set for this issue.
        self._ensure_labels(str(row["domain"]), stage, status)  # Create labels before the issue references them.
        body = json.dumps({"labels": labels}, sort_keys=True)  # Replace the label set in one GitHub API call.
        self.runner.run_with_input(
            ["gh", "api", f"repos/{self.repo}/issues/{issue_number}", "--method", "PATCH", "--input", "-"], body
        )  # Apply exact labels.
        logger.debug("Set labels for issue %d to %s", issue_number, ",".join(labels))  # Record safe label names.

    def _close_if_complete(self, issue_number: int, stage: str) -> None:
        """Close the issue when the document reaches a terminal stage."""
        if stage not in {StageName.VERIFIED.value, StageName.RELEASED.value}:  # Keep open work visible until done.
            return
        logger.info("Closing completed document issue %d", issue_number)  # Record the state change.
        self.runner.run(
            ["gh", "issue", "close", str(issue_number), "--repo", self.repo, "--reason", "completed"]
        )  # Close.
        logger.debug("Closed completed document issue %d", issue_number)  # Record the completed issue.

    def _row_for_issue(self, issue_number: int) -> dict[str, Any]:
        """Return the local document row for a GitHub issue."""
        rows = self.store.documents_for_index()  # Read the small local progress table.
        return next(row for row in rows if row["issue_number"] == issue_number)  # Match by the stored issue number.

    def _stage_event(
        self,
        document: DocumentRecord,
        stage: StageName,
        issue_number: int | None,
        details: dict[str, object],
    ) -> StageEvent:
        """Create a stage event with a deterministic next action."""
        created_at = datetime.now(UTC).isoformat()  # Use UTC so comments sort the same on every machine.
        next_action = self._next_action_for_stage(stage)  # Store the exact action the recovery reader reports.
        return StageEvent(document.document_key, stage, next_action, issue_number, created_at, details)  # Return state.

    def _next_action_for_stage(self, stage: StageName) -> str:
        """Return the stored next action for one completed stage."""
        if stage == StageName.FAILED:  # A failed stage requires repair before more pipeline work.
            return "repair the failed stage"
        if stage in {StageName.VERIFIED, StageName.RELEASED}:  # A terminal document needs no more pipeline work.
            return "no action"
        return "continue the document pipeline"  # The codec expands this from the exact completed stage.

    def _status_for_stage(self, stage: StageName) -> str:
        """Return the progress status for one stage."""
        if stage == StageName.FAILED:  # A failed document needs operator repair.
            return "failed"
        if stage in {StageName.VERIFIED, StageName.RELEASED}:  # A verified or released document is complete.
            return "complete"
        return "in-progress"  # Any other stage means the document still has work.

    def _issue_title(self, document: DocumentRecord) -> str:
        """Return the document issue title."""
        safe_title = document.title[:90]  # Bound the title so GitHub search remains readable.
        return f"[skill-factory] {document.audit_citation_key}: {safe_title}"  # Make the citation the idempotency key.

    def _issue_body(self, document: DocumentRecord) -> str:
        """Return the GitHub issue body for the journal."""
        lines = self._identity_lines(document)  # Build the self-contained document identity section.
        checklist = [f"- [ ] {stage.value}" for stage in StageName if stage != StageName.FAILED]  # List normal stages.
        return "\n".join(
            lines + ["", "Stage checklist:", *checklist, "", f"Parent factory issue: #{self.parent_issue}"]
        )

    def _identity_lines(self, document: DocumentRecord) -> list[str]:
        """Return the self-contained issue identity lines."""
        return [  # Keep each field visible without requiring another index.
            "This issue tracks one source document for the Juniper skill factory.",
            "",
            f"Document title: {document.title}",
            f"Citation key: `{document.audit_citation_key}`",
            f"Source PDF path: `{document.source_path}`",
            f"Page count: {document.page_count}",
            f"Part count: {document.part_count}",
            f"Domain: `{document.domain}`",
            f"Assigned skill name: `{document.audit_skill_name}`",
            f"Priority: {document.priority}",
            f"Version status: {document.version_status}",
        ]

    def _document_from_row(self, row: dict[str, Any]) -> DocumentRecord:
        """Rebuild a document record from a local database row."""
        return DocumentRecord(  # Keep GitHub reconciliation independent of in-memory pipeline objects.
            Path(str(row["source_path"])),
            str(row["title"]),
            str(row["category"]),
            int(row["page_count"]),
            str(row["domain"]),
            str(row["citation_key"]),
            int(row["part_count"]),
            str(row["skill_name"]),
            str(row["priority"]),
            str(row["version_status"]),
        )

    def _issue_number_from_url(self, output: str) -> int:
        """Read the issue number from the `gh issue create` output URL."""
        issue_number = int(output.rstrip().split("/")[-1])  # The CLI returns the issue URL on success.
        logger.debug("Parsed created issue number %d", issue_number)  # Record the parsed issue number.
        return issue_number

    def _issue_id(self, issue_number: int) -> int:
        """Read the database ID that the GitHub sub-issue API uses."""
        logger.info("Reading the GitHub issue REST ID")  # Record the metadata read.
        result = self.runner.run(["gh", "api", f"repos/{self.repo}/issues/{issue_number}"])  # Ask `gh` for metadata.
        payload = json.loads(result.stdout)  # Decode the issue metadata response.
        logger.debug("Read GitHub issue REST ID for issue %d", issue_number)  # Record safe issue context.
        return int(payload["id"])

    def _ensure_labels(self, domain: str, stage: str, status: str) -> None:
        """Create labels that make document issues easy to filter."""
        for name, color in self._label_colors(domain, stage, status).items():  # Create all labels used by this issue.
            self._ensure_one_label(name, color)  # Keep one label create small and recoverable.

    def _ensure_one_label(self, name: str, color: str) -> None:
        """Create one GitHub label if it does not exist."""
        if name in self.ensured_labels:  # Skip labels that this process already checked.
            logger.debug("GitHub label %s already checked in this process", name)  # Record the local cache hit.
            return
        try:
            self.runner.run(["gh", "label", "create", name, "--repo", self.repo, "--color", color])  # Create label.
            logger.debug("Created GitHub label %s", name)  # Record the created label.
        except GitHubCliError as error:
            logger.debug("GitHub label %s already exists or cannot be created: %s", name, error)  # Keep idempotency.
        self.ensured_labels.add(name)  # Cache the result because an existing label is sufficient for later writes.

    def _label_colors(self, domain: str, stage: str, status: str) -> dict[str, str]:
        """Return label colors for one document state."""
        return {  # Use predictable colors for generated filters.
            "skill-factory": "0969da",
            f"domain:{self._label_value(domain)}": "d4c5f9",
            f"stage:{self._label_value(stage)}": "bfdadc",
            f"status:{self._label_value(status)}": "c2e0c6",
        }

    def _label_csv(self, domain: str, stage: str, status: str) -> str:
        """Return a comma-separated label list for `gh issue create`."""
        return ",".join(self._label_list(domain, stage, status))  # Match GitHub CLI label input format.

    def _label_list(self, domain: str, stage: str, status: str) -> list[str]:
        """Return the exact label list for one issue state."""
        return [  # Keep generated issue labels precise and navigable.
            "skill-factory",
            f"domain:{self._label_value(domain)}",
            f"stage:{self._label_value(stage)}",
            f"status:{self._label_value(status)}",
        ]

    def _label_value(self, value: str) -> str:
        """Return a safe GitHub label suffix."""
        safe_chars = [char.lower() if char.isalnum() else "-" for char in value]  # Normalize spaces and punctuation.
        return "-".join(part for part in "".join(safe_chars).split("-") if part)  # Collapse duplicate separators.

    def _link_parent_issue(self, issue_number: int, issue_id: int) -> None:
        """Attach the journal issue to the parent issue when the API permits it."""
        logger.info("Linking the journal issue to the parent issue")  # Record the parent-link action.
        try:
            self.runner.run(  # Use the GitHub sub-issue API when it is available for this repository.
                [
                    "gh",
                    "api",
                    f"repos/{self.repo}/issues/{self.parent_issue}/sub_issues",
                    "-f",
                    f"sub_issue_id={issue_id}",
                ]
            )
            logger.debug("Linked issue %d as a sub-issue of %d", issue_number, self.parent_issue)  # Record success.
        except GitHubCliError as error:
            logger.debug("Sub-issue link failed for issue %d: %s", issue_number, error)  # Preserve core tracking.


class SkillFactoryIssueIndex:
    """Render a generated index for per-document audit issues."""

    def __init__(self, repo: str) -> None:
        """Initialize the SkillFactoryIssueIndex instance."""
        self.repo = repo  # Store the repository name for GitHub issue links.

    def render(self, rows: list[dict[str, Any]]) -> str:
        """Return a Markdown index grouped by domain."""
        logger.info("Rendering the skill factory issue index")  # Record the render action.
        lines = ["Skill factory document issue index.", "", "<!-- skill-factory-index -->"]  # Mark generated content.
        for domain, domain_rows in self._group_rows(rows).items():  # Render one section per domain.
            lines.extend(self._domain_lines(domain, domain_rows))  # Add the rows for one domain.
        logger.debug("Rendered index for %d documents", len(rows))  # Record the document count.
        return "\n".join(lines)

    def _group_rows(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Group rows by domain while preserving sorted input order."""
        grouped: dict[str, list[dict[str, Any]]] = {}  # Collect rows for each domain.
        for row in rows:  # Process the store output in its stable order.
            grouped.setdefault(str(row["domain"]), []).append(row)  # Append the row to its domain section.
        return grouped

    def _domain_lines(self, domain: str, rows: list[dict[str, Any]]) -> list[str]:
        """Return Markdown lines for one domain."""
        lines = ["", f"## {domain}"]  # Start a clear domain section.
        for row in rows:  # Render each document issue as one bullet.
            lines.append(self._row_line(row))  # Add one compact progress row.
        return lines

    def _row_line(self, row: dict[str, Any]) -> str:
        """Return one Markdown row for a document."""
        issue = self._issue_link(row.get("issue_number"))  # Link when GitHub already has an issue number.
        stage = row.get("current_stage", "queued")  # Show the current stage from the local mirror.
        status = row.get("status", "queued")  # Show whether the issue should remain open.
        return f"- {issue} `{status}` `{stage}` {row['title']}"  # Keep the row readable in a large issue.

    def _issue_link(self, issue_number: object) -> str:
        """Return a Markdown issue link or a queued marker."""
        if issue_number is None:  # A local-only document has no GitHub issue yet.
            return "`local-only`"  # Make deferred network work visible in the index.
        return f"[#{issue_number}](https://github.com/{self.repo}/issues/{issue_number})"  # Link the document issue.
