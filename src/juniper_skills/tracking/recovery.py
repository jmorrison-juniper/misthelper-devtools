"""Crash recovery for the Juniper skill factory journal."""

from __future__ import annotations

import json
import logging

from src.juniper_skills.tracking.comment_codec import StageCommentCodec
from src.juniper_skills.tracking.github_cli import GitHubCliError, GitHubCommandRunner, GitHubRateLimitManager
from src.juniper_skills.tracking.models import ResumePoint
from src.juniper_skills.tracking.store import FactoryJournalStore

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SkillIssueRecoveryReader:
    """Reconstruct a resume point from GitHub comments and the local mirror."""

    def __init__(
        self,
        store: FactoryJournalStore,
        runner: GitHubCommandRunner,
        rate_limit: GitHubRateLimitManager,
        repo: str = "jmorrison-juniper/MistHelper",
    ) -> None:
        """Initialize the SkillIssueRecoveryReader instance."""
        self.store = store  # Keep the local mirror available when GitHub is down.
        self.runner = runner  # Use the contract-required `gh` CLI through a testable runner.
        self.rate_limit = rate_limit  # Measure rate limits before network reads.
        self.repo = repo  # Target the repository that owns issue #2925.
        self.codec = StageCommentCodec()  # Share one parser for GitHub and local comments.

    def resume_point(self, document_key: str, issue_number: int | None) -> ResumePoint:
        """Return the exact next action for one document."""
        logger.info("Recovering the skill factory resume point")  # Record the recovery action.
        comments = self._github_comments(issue_number) if issue_number else []  # Read GitHub when an issue exists.
        comments.extend(self.store.local_comment_bodies(document_key))  # Add local comments not yet synced.
        resume_point = self.codec.parse_comments(comments, document_key)  # Convert comments into one resume point.
        logger.debug("Recovered resume point from %s", resume_point.source)  # Record the recovery source.
        return resume_point

    def _github_comments(self, issue_number: int) -> list[str]:
        """Read comment bodies from one GitHub issue."""
        logger.info("Reading GitHub issue comments for issue %d", issue_number)  # Record the network read.
        try:
            self.rate_limit.wait_if_needed()  # Avoid known rate-limit failures before the read.
            result = self.runner.run(  # Ask `gh` for the comments as JSON.
                ["gh", "issue", "view", str(issue_number), "--repo", self.repo, "--json", "comments"]
            )
            payload = json.loads(result.stdout)  # Decode the issue response for recovery.
            comments = [comment["body"] for comment in payload.get("comments", [])]  # Keep only comment text.
            logger.debug("Read %d GitHub issue comments", len(comments))  # Record the safe count.
            return comments
        except (GitHubCliError, json.JSONDecodeError, KeyError, TypeError) as error:
            logger.debug("Using local comments after a GitHub read error: %s", error)  # Preserve offline recovery.
            return []
