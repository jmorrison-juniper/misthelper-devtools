"""Persistent REST transport for Juniper skill factory GitHub audit writes."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess  # nosec B404 - This module starts a fixed GitHub CLI token command without a shell.
import time
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from src.juniper_skills.tracking.github_cli import GitHubCliError, GitHubCommandResult

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class GitHubRestRunner:
    """Run the tracker GitHub operations through one persistent HTTPS session."""

    def __init__(self, timeout_seconds: int = 30) -> None:
        """Initialize the GitHubRestRunner instance."""
        self.timeout_seconds = timeout_seconds  # Bound each REST call so reconciliation can retry later.
        self.api_call_count = 0  # Count HTTP calls for throughput measurements.
        self.request_counts: dict[str, int] = {}  # Count calls by method and path family for bottleneck reports.
        self.latencies: list[float] = []  # Store per-call latency so proof runs can report a measured average.
        self.session = self._session()  # Reuse TLS and auth state across thousands of writes.

    def run(self, command: list[str]) -> GitHubCommandResult:
        """Run one supported GitHub operation."""
        logger.info("Running a GitHub REST operation: %s", self._safe_command(command))  # Log without secrets.
        result = self._dispatch(command)  # Convert the tracker command into one REST operation.
        logger.debug("GitHub REST operation returned code %d", result.returncode)  # Record safe status.
        return result  # Return the same result shape as the legacy CLI runner.

    def run_with_input(self, command: list[str], input_text: str) -> GitHubCommandResult:
        """Run one supported GitHub operation with a JSON body."""
        logger.info("Running a GitHub REST operation with input: %s", self._safe_command(command))  # Log safely.
        payload = json.loads(input_text)  # Parse the JSON body that tracker generated.
        result = self._request("PATCH", command[2], payload)  # Apply the issue update through REST.
        logger.debug("GitHub REST operation with input returned code %d", result.returncode)  # Record status.
        return result  # Return the shared command result shape.

    def _dispatch(self, command: list[str]) -> GitHubCommandResult:
        """Route one supported operation to REST."""
        if command[:3] == ["gh", "api", "rate_limit"]:  # Read the GitHub rate-limit endpoint.
            return self._request("GET", "rate_limit")  # Return the rate-limit JSON.
        if command[:4] == ["gh", "issue", "list", "--repo"]:  # Search issues by title.
            return self._issue_list(command)  # Return the compact CLI-compatible JSON shape.
        if command[:3] == ["gh", "label", "create"]:  # Create a repository label.
            return self._label_create(command)  # Treat an existing label as success.
        if command[:3] == ["gh", "issue", "create"]:  # Create a document issue.
            return self._issue_create(command)  # Return the issue URL as the CLI does.
        if command[:3] == ["gh", "issue", "view"]:  # Read issue comments.
            return self._issue_view(command)  # Return the comments shape that recovery expects.
        if command[:3] == ["gh", "issue", "comment"]:  # Create an issue comment.
            return self._issue_comment(command)  # Return the created comment URL.
        if command[:3] == ["gh", "issue", "close"]:  # Close a completed issue.
            return self._issue_close(command)  # Return a small CLI-compatible response.
        if command[:2] == ["gh", "api"]:  # Run a direct API call used by sub-issue linking.
            return self._direct_api(command)  # Preserve tracker compatibility.
        raise GitHubCliError("unsupported GitHub REST operation")  # Fail fast for an unimplemented operation.

    def _session(self) -> requests.Session:
        """Return an authenticated session with connection reuse."""
        token = self._read_token()  # Read the token once through the existing authenticated CLI.
        session = requests.Session()  # Keep TCP, TLS, and auth reusable across calls.
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=2)  # Retry transient transport errors.
        session.mount("https://", adapter)  # Apply the adapter to all GitHub API calls.
        session.headers.update(self._headers(token))  # Set headers once without writing the token to disk.
        logger.debug("Created the GitHub REST session with a connection pool")  # Record session creation.
        return session  # Return the configured session.

    def _read_token(self) -> str:
        """Read the GitHub token from `gh` without logging it."""
        logger.info("Reading the GitHub token from the authenticated CLI")  # Record the auth source.
        gh_path = shutil.which("gh")  # Resolve the executable path before the token handoff.
        if gh_path is None:  # Stop when the authenticated GitHub CLI is absent.
            raise GitHubCliError("GitHub CLI was not found on PATH")  # Give the operator a clear setup error.
        completed = subprocess.run(  # nosec B603 - The command is a fixed token read without shell expansion.
            [gh_path, "auth", "token"],
            capture_output=True,
            check=False,
            text=True,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:  # Stop when the local CLI has no usable credential.
            raise GitHubCliError(completed.stderr.strip() or "GitHub token read failed")
        logger.debug("Read a GitHub token from the authenticated CLI")  # Do not record the token value.
        return completed.stdout.strip()  # Return the token only in memory.

    def _headers(self, token: str) -> dict[str, str]:
        """Return GitHub REST headers."""
        return {  # Use the supported GitHub REST media type and bearer auth.
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, payload: Any | None = None) -> GitHubCommandResult:
        """Send one REST request and return a command-compatible result."""
        url = f"https://api.github.com/{path.lstrip('/')}"  # Build the API URL without logging credentials.
        self.api_call_count += 1  # Count the real HTTP call for throughput reporting.
        started = time.perf_counter()  # Measure REST latency instead of subprocess startup time.
        response = self.session.request(method, url, json=payload, timeout=self.timeout_seconds)  # Send the request.
        self.latencies.append(time.perf_counter() - started)  # Store the measured HTTP latency.
        self._count_request(method, path)  # Record the endpoint family for call-count reports.
        if response.status_code >= 400:  # Convert failed HTTP responses to the existing typed error.
            raise GitHubCliError(response.text.strip() or response.reason)
        return GitHubCommandResult(response.status_code, response.text, "")  # Return text for existing parsers.

    def _count_request(self, method: str, path: str) -> None:
        """Count one request by method and endpoint family."""
        key = f"{method} {path.split('?', 1)[0]}"  # Remove query text so search calls group together.
        self.request_counts[key] = self.request_counts.get(key, 0) + 1  # Increment the measured call family.

    def _issue_list(self, command: list[str]) -> GitHubCommandResult:
        """Search issues and return number and title fields."""
        query = command[command.index("--search") + 1]  # Read the exact query that the tracker built.
        payload = json.loads(self._request("GET", f"search/issues?q={quote(query)}").stdout)  # Search through REST.
        rows = [{"number": item["number"], "title": item["title"]} for item in payload.get("items", [])]  # Compact.
        return GitHubCommandResult(200, json.dumps(rows), "")  # Match `gh issue list --json` output.

    def _label_create(self, command: list[str]) -> GitHubCommandResult:
        """Create one label and treat an existing label as success."""
        repo = command[command.index("--repo") + 1]  # Read the target repository.
        payload = {"name": command[3], "color": command[command.index("--color") + 1]}  # Build label JSON.
        try:
            return self._request("POST", f"repos/{repo}/labels", payload)  # Create the label when absent.
        except GitHubCliError as error:
            if "already_exists" in str(error) or "already exists" in str(error):  # Existing labels are idempotent.
                return GitHubCommandResult(200, "{}", "")  # Report success for repeat reconciliation.
            raise  # Re-raise unexpected label failures.

    def _issue_create(self, command: list[str]) -> GitHubCommandResult:
        """Create one issue and return its HTML URL."""
        repo = command[command.index("--repo") + 1]  # Read the target repository.
        payload = self._issue_create_payload(command)  # Convert CLI-style arguments to REST JSON.
        response = json.loads(self._request("POST", f"repos/{repo}/issues", payload).stdout)  # Create the issue.
        return GitHubCommandResult(201, f"{response['html_url']}\n", "")  # Match CLI create output.

    def _issue_create_payload(self, command: list[str]) -> dict[str, Any]:
        """Return the REST payload for one issue create operation."""
        labels = command[command.index("--label") + 1].split(",")  # Convert comma labels to JSON labels.
        return {  # Keep the created issue body self-contained.
            "title": command[command.index("--title") + 1],
            "body": command[command.index("--body") + 1],
            "labels": labels,
        }

    def _issue_view(self, command: list[str]) -> GitHubCommandResult:
        """Read issue comments and return the recovery shape."""
        repo = command[command.index("--repo") + 1]  # Read the target repository.
        issue_number = command[3]  # Read the issue number from the CLI-compatible command.
        comments = json.loads(self._request("GET", f"repos/{repo}/issues/{issue_number}/comments?per_page=100").stdout)
        payload = {"comments": [{"body": comment["body"]} for comment in comments]}  # Keep only comment bodies.
        return GitHubCommandResult(200, json.dumps(payload), "")  # Match the legacy recovery parser.

    def _issue_comment(self, command: list[str]) -> GitHubCommandResult:
        """Create one issue comment."""
        repo = command[command.index("--repo") + 1]  # Read the target repository.
        payload = {"body": command[command.index("--body") + 1]}  # Send the exact audit body.
        response = json.loads(self._request("POST", f"repos/{repo}/issues/{command[3]}/comments", payload).stdout)
        return GitHubCommandResult(201, f"{response['html_url']}\n", "")  # Match CLI comment output.

    def _issue_close(self, command: list[str]) -> GitHubCommandResult:
        """Close one completed issue."""
        repo = command[command.index("--repo") + 1]  # Read the target repository.
        payload = {"state": "closed", "state_reason": command[command.index("--reason") + 1]}  # Preserve reason.
        self._request("PATCH", f"repos/{repo}/issues/{command[3]}", payload)  # Close the issue through REST.
        return GitHubCommandResult(200, "closed\n", "")  # Return a CLI-compatible body.

    def _direct_api(self, command: list[str]) -> GitHubCommandResult:
        """Run the direct API calls that the tracker still uses."""
        if "-f" in command:  # The sub-issue call sends one form field.
            return self._request("POST", command[2], self._form_payload(command))  # Link the parent issue.
        return self._request("GET", command[2])  # Read issue metadata by default.

    def _form_payload(self, command: list[str]) -> dict[str, Any]:
        """Return a JSON payload from `gh api -f` arguments."""
        fields = [part for part in command if "=" in part]  # Keep only key=value field arguments.
        return {key: int(value) for key, value in (field.split("=", 1) for field in fields)}  # Parse numeric IDs.

    def _safe_command(self, command: list[str]) -> str:
        """Return a log-safe command string."""
        safe_parts = ["***" if "token" in part.lower() else part for part in command]  # Redact token-like args.
        return " ".join(safe_parts)  # Store a readable command for logs.
