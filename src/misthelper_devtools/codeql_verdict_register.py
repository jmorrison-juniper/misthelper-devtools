"""Build and check the CodeQL verdict register.

The register is the audit record for every dismissed CodeQL alert. A dismissal
accepts a security risk, so the register states who accepted the risk, when they
accepted it, and why.

The tool has two modes. The `generate` mode writes the register from the live
GitHub code scanning API. The `check` mode compares the register against the same
API and reports every difference. The `check` mode is the reconciliation that
clause C-8 of the superseded contract describes.

Run the tool from the repository root. The repository default must come from
the checkout or the CI environment, because this shared tool serves more than
one product.

    codeql-verdict-register generate
    codeql-verdict-register check
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# The rule that the register governs. The register covers one rule at a time.
DEFAULT_RULE_ID = "py/clear-text-logging-sensitive-data"

# The register path. The record lives outside a feature folder, because the
# record outlives the feature that created it.
DEFAULT_REGISTER_PATH = Path("documentation") / "security" / "codeql-verdict-register.md"

# The number of days between a dismissal and the review that the register forces.
REVIEW_INTERVAL_DAYS = 180

# The gh call reaches the GitHub API over the network. A stalled read has no
# bound of its own, so this cap stops it from hanging the whole gate.
_GH_TIMEOUT_SECONDS = 120

# The map from an API dismissal reason to a register verdict. The API accepts
# three reasons, so the register holds one verdict for each reason.
API_REASON_TO_VERDICT = {
    "false positive": "false_positive",
    "won't fix": "accepted_with_rationale",
    "used in tests": "test_fixture",
}

# The text that a row carries when the dismissal recorded no reason. The cell is
# never blank, because a blank cell hides the gap instead of reporting it.
MISSING_REASON_TEXT = "Warning: the dismissal recorded no reason. A reviewer must write one."

# The trigger that a row carries when the dismissal recorded no reason.
MISSING_REASON_TRIGGER = "A reviewer writes the missing reason."

# The trigger that a row carries when the dismissal recorded a reason.
DEFAULT_TRIGGER = "A later CodeQL scan raises the same alert again."

# The eleven column names. Clause C-1 fixes the order, so a move breaks a
# plain text comparison between two revisions.
COLUMNS = (
    "Alert",
    "Issue",
    "File",
    "Line",
    "Anchor",
    "Verdict",
    "Reason",
    "Author",
    "Decided",
    "Review",
    "Trigger",
)

# The line and anchor cells are generated locations, not a security decision.
POSITIONAL_COLUMNS = frozenset({"Line", "Anchor"})  # Ignore generated location drift during reconciliation.

logger = logging.getLogger(__name__)

_REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"


@dataclass(frozen=True)
class VerdictRow:
    """One register row. The row records one accepted security risk."""

    alert: int
    issue: str
    file: str
    line: str
    anchor: str
    verdict: str
    reason: str
    author: str
    decided: str
    review: str
    trigger: str

    def to_markdown(self) -> str:
        """Return the row as one markdown table line."""
        # Collect the cells in the fixed column order that clause C-1 states.
        cells = [
            str(self.alert),
            self.issue,
            self.file,
            self.line,
            self.anchor,
            self.verdict,
            self.reason,
            self.author,
            self.decided,
            self.review,
            self.trigger,
        ]
        # Escape a pipe inside a cell, because a raw pipe ends the cell early.
        safe = [cell.replace("|", "\\|") for cell in cells]
        # Join the cells into the markdown row format.
        return "| " + " | ".join(safe) + " |"


class AlertSource:
    """Read dismissed CodeQL alerts from the GitHub code scanning API."""

    def __init__(self, repository: str, rule_id: str = DEFAULT_RULE_ID) -> None:
        # Store the repository in owner/name form for the API path.
        self.repository = repository
        # Store the rule, because the register covers one rule at a time.
        self.rule_id = rule_id

    def fetch(self) -> list[dict[str, Any]]:
        """Return every dismissed alert that matches the rule."""
        path = f"repos/{self.repository}/code-scanning/alerts?state=dismissed&per_page=100"
        logger.info("Reading dismissed CodeQL alerts for rule %s", self.rule_id)
        pages = json.loads(self._read(path))
        matched = self._decode_pages(pages)
        logger.debug("Read %d pages and matched %d dismissed alerts", len(pages), len(matched))
        return matched

    def _read(self, path: str) -> str:
        """Read all API pages without exposing error response content."""
        try:
            return subprocess.run(
                ["gh", "api", "--paginate", "--slurp", path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=_GH_TIMEOUT_SECONDS,
            ).stdout
        except subprocess.TimeoutExpired:
            logger.error("The gh api call passed the %ds bound and was stopped", _GH_TIMEOUT_SECONDS)
            msg = f"The GitHub API read passed the {_GH_TIMEOUT_SECONDS}s bound"
            raise RuntimeError(msg) from None
        except subprocess.CalledProcessError as error:
            msg = (
                f"The GitHub API read failed with exit code {error.returncode}. "
                "Confirm security-events:read access and GitHub availability."
            )
            # The response can contain private data, so report no stdout or stderr.
            raise RuntimeError(msg) from None

    def _decode_pages(self, pages: object) -> list[dict[str, Any]]:
        """Validate every page before selecting the governed alerts."""
        if not isinstance(pages, list) or not pages:
            raise ValueError("The GitHub response must contain at least one alert page.")
        matched: list[dict[str, Any]] = []
        seen: set[int] = set()
        for page in pages:
            if not isinstance(page, list):
                raise ValueError("Each GitHub alert page must be a list.")
            for item in page:
                alert = self._validate_alert(item)
                number = alert["number"]
                if number in seen:
                    raise ValueError(f"The GitHub response repeats alert {number}.")
                seen.add(number)
                if alert["rule"]["id"] == self.rule_id:
                    matched.append(alert)
        return matched

    def _validate_alert(self, item: object) -> dict[str, Any]:
        """Reject malformed identities before a rule filter can hide them."""
        if not isinstance(item, dict):
            raise ValueError("Each GitHub alert must be an object.")
        number = item.get("number")
        if type(number) is not int or number <= 0:
            raise ValueError("Each GitHub alert must have a positive integer number.")
        rule = item.get("rule")
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"]:
            raise ValueError(f"GitHub alert {number} has no valid rule ID.")
        if item.get("state") != "dismissed":
            raise ValueError(f"GitHub alert {number} does not have the dismissed state.")
        return item


class RowBuilder:
    """Turn one API alert into one register row."""

    def build(self, alert: dict[str, Any]) -> VerdictRow:
        """Return the register row for one dismissed alert."""
        self._validate(alert)
        logger.info("Building the row for alert %d", alert["number"])
        location = self._location(alert)
        file_path, line = location["path"], str(location.get("start_line") or "-")
        comment = (alert.get("dismissed_comment") or "").strip()
        decided = (alert.get("dismissed_at") or "-")[:10]
        row = VerdictRow(
            alert=alert["number"],
            issue=self._issue_for(comment),
            file=file_path,
            line=line,
            anchor=f"{file_path}::L{line}",
            verdict=API_REASON_TO_VERDICT[alert["dismissed_reason"]],
            reason=" ".join(comment.split()) if comment else MISSING_REASON_TEXT,
            author=(alert.get("dismissed_by") or {}).get("login", "unknown"),
            decided=decided,
            review=self._review_date(decided),
            trigger=DEFAULT_TRIGGER if comment else MISSING_REASON_TRIGGER,
        )
        logger.debug("Built the row for alert %d", row.alert)
        return row

    def _validate(self, alert: dict[str, Any]) -> None:
        """Reject invalid metadata instead of inventing a security decision."""
        number = alert.get("number")
        if type(number) is not int or number <= 0:
            raise ValueError("Each alert must have a positive integer number.")
        reason = alert.get("dismissed_reason")
        if not isinstance(reason, str) or reason not in API_REASON_TO_VERDICT:
            raise ValueError(f"Alert {number} has no valid dismissal reason.")
        for key in ("dismissed_comment", "dismissed_at"):
            value = alert.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Alert {number} has an invalid {key} field.")
        author = alert.get("dismissed_by")
        if author is not None and (
            not isinstance(author, dict) or not isinstance(author.get("login"), str) or not author["login"]
        ):
            raise ValueError(f"Alert {number} has no valid dismissal author.")

    def _issue_for(self, comment: str) -> str:
        """Return the issue reference that the comment names, or a dash."""
        # Look for a hash reference, because a dismissal often names its issue.
        for token in comment.replace("(", " ").replace(")", " ").split():
            # Accept a token that starts with a hash and holds digits after it.
            if token.startswith("#") and token[1:].rstrip(".,").isdigit():
                return "#" + token[1:].rstrip(".,")
        return "-"

    def _location(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Validate the reported location without substituting a false location."""
        instance = alert.get("most_recent_instance")
        if not isinstance(instance, dict) or not isinstance(instance.get("location"), dict):
            raise ValueError(f"Alert {alert['number']} has no valid location.")
        location: dict[str, Any] = instance["location"]
        if not isinstance(location.get("path"), str) or not location["path"]:
            raise ValueError(f"Alert {alert['number']} has no valid file path.")
        line = location.get("start_line")
        if line is not None and (type(line) is not int or line <= 0):
            raise ValueError(f"Alert {alert['number']} has no valid start line.")
        return location

    def _review_date(self, decided: str) -> str:
        """Return the date when a reviewer must revisit the accepted risk."""
        # Report a dash when the decision carries no date to count from.
        if decided == "-":
            return "-"
        try:
            start = datetime.strptime(decided, "%Y-%m-%d").replace(tzinfo=UTC)
            if start.strftime("%Y-%m-%d") != decided:
                raise ValueError
        except ValueError:
            raise ValueError("A dismissal date must use a valid YYYY-MM-DD value.") from None
        # Add the interval and format the result as an ISO date.
        return (start + timedelta(days=REVIEW_INTERVAL_DAYS)).strftime("%Y-%m-%d")


class RegisterWriter:
    """Write the register file."""

    def __init__(self, path: Path, rule_id: str = DEFAULT_RULE_ID) -> None:
        # Store the target path for the register file.
        self.path = path
        # Store the rule so the header states what the register governs.
        self.rule_id = rule_id

    def write(self, rows: list[VerdictRow]) -> None:
        """Write the header, the table, and the summary to the register file."""
        logger.info("Writing %d register rows to %s", len(rows), self.path)
        # Create the parent directory, because the security folder may be new.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Sort the rows by alert number so a difference stays readable.
        ordered = sorted(rows, key=lambda row: row.alert)
        # Join the document parts into the final text.
        text = "\n".join([self._header(ordered), self._table(ordered), self._summary(ordered)])
        # Write the file with an explicit encoding for Windows and Linux.
        self.path.write_text(text, encoding="utf-8")
        logger.debug("Wrote the register file with %d characters", len(text))

    def _header(self, rows: list[VerdictRow]) -> str:
        """Return the register header text."""
        # Record the generation date so a reader knows how fresh the record is.
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return (
            "# CodeQL verdict register\n\n"
            f"**Rule**: `{self.rule_id}`\n\n"
            "**Owner**: The repository security reviewer.\n\n"
            f"**Generated**: {today}\n\n"
            f"**Rows**: {len(rows)}\n\n"
            "A dismissed CodeQL alert is a decision to accept a security risk. This register\n"
            "records that decision. Each row names the alert, the code location, the verdict,\n"
            "the reason, the account that decided, and the date of the next review.\n\n"
            "Do not edit this file by hand. The tool `codeql-verdict-register`\n"
            "writes it from the GitHub code scanning API.\n\n"
            "Refresh the register with the command below.\n\n"
            "```bash\n"
            "codeql-verdict-register generate\n"
            "```\n\n"
            "Check the register against the API with the command below. The command exits\n"
            "with a non-zero status when a row and an alert disagree.\n\n"
            "```bash\n"
            "codeql-verdict-register check\n"
            "```\n\n"
            "CI runs this command in the `CodeQL verdict register check` job. The job\n"
            "fails on drift, invalid records, denied API access, or a failed API read.\n"
            "The job needs `contents: read` and `security-events: read`. It uses only\n"
            "the job token. A fork with denied API access fails without a token fallback.\n\n"
            "This check is a live audit, not a reproducible build. An unchanged commit\n"
            "can fail after live alert metadata changes. CI never generates the register.\n"
            "Refresh it only after you review the current metadata.\n\n"
            "The check compares every decision field, including the reason and author.\n"
            "It ignores a change that only moves the line or anchor. The writer removes\n"
            "surrounding cell spaces, restores escaped pipes, and converts comment\n"
            "whitespace to spaces. The generation date does not cause drift. A complete,\n"
            "successful API response must confirm an empty alert set.\n\n"
            "The `Anchor` column holds the file path and the line of the reported expression.\n"
            "It is not a stable finding identity. This register covers only the rule above.\n"
            "A row with the reason `Warning: the dismissal recorded no reason` needs a written\n"
            "reason. Add the reason to the alert on GitHub, then run `generate` again.\n"
        )

    def _table(self, rows: list[VerdictRow]) -> str:
        """Return the markdown table for the rows."""
        # Build the header row from the fixed column order.
        head = "| " + " | ".join(COLUMNS) + " |"
        # Build the separator row that markdown requires under the header.
        rule = "| " + " | ".join("-" for _ in COLUMNS) + " |"
        # Build one line for each row.
        body = [row.to_markdown() for row in rows]
        return "\n".join(["\n## Register", "", head, rule, *body, ""])

    def _summary(self, rows: list[VerdictRow]) -> str:
        """Return the counts that a reviewer checks first."""
        # Count the rows for each verdict so a reader sees the risk shape.
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.verdict] = counts.get(row.verdict, 0) + 1
        # Count the rows that still need a written reason.
        missing = sum(1 for row in rows if row.reason == MISSING_REASON_TEXT)
        # Build one table line for each verdict.
        lines = [f"| {name} | {count} |" for name, count in sorted(counts.items())]
        return "\n".join(
            [
                "\n## Summary",
                "",
                "| Verdict | Rows |",
                "| - | - |",
                *lines,
                f"| **Total** | {len(rows)} |",
                "",
                f"Rows that still need a written reason: {missing}.",
                "",
            ]
        )


class RegisterReconciler:
    """Compare the register against the live alert data."""

    def __init__(self, path: Path) -> None:
        # Store the register path that the check reads.
        self.path = path

    def parse(self) -> dict[int, VerdictRow]:
        """Read complete rows and reject an absent or malformed register."""
        logger.info("Reading register rows from %s", self.path)
        sections = self.path.read_text(encoding="utf-8").split("\n## Register\n")
        if len(sections) != 2:
            raise ValueError("The register must contain exactly one Register section.")
        lines = sections[1].split("\n## Summary\n", 1)[0].strip().splitlines()
        header = "| " + " | ".join(COLUMNS) + " |"
        separator = "| " + " | ".join("-" for _ in COLUMNS) + " |"
        if lines[:2] != [header, separator]:
            raise ValueError("The register table must keep the eleven column names and their order.")
        found: dict[int, VerdictRow] = {}
        for line in lines[2:]:
            row = self._parse_row(line)
            if row.alert in found:
                raise ValueError(f"The register repeats alert {row.alert}.")
            found[row.alert] = row
        logger.debug("Parsed %d rows from the register", len(found))
        return found

    def _parse_row(self, line: str) -> VerdictRow:
        """Decode one complete row without losing escaped cell content."""
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|") or "\x00" in line:
            raise ValueError("A register row has an invalid table boundary or null character.")
        # Mask escaped pipes before splitting, then restore them inside each cell.
        masked = line[1:-1].replace("\\|", "\x00")
        cells = [cell.strip().replace("\x00", "|") for cell in masked.split("|")]
        if len(cells) != len(COLUMNS) or any(not cell for cell in cells):
            raise ValueError("Each register row must contain eleven nonempty cells.")
        if not cells[0].isdecimal() or int(cells[0]) <= 0:
            raise ValueError("Each register row must have a positive integer alert number.")
        return VerdictRow(int(cells[0]), *cells[1:])

    def compare(self, rows: list[VerdictRow]) -> list[str]:
        """Return one message for each difference between the register and the API."""
        logger.info("Comparing the register against %d live rows", len(rows))
        recorded = self.parse()
        # Apply the same cell formatting rules to both sides of the comparison.
        live = {row.alert: self._parse_row(row.to_markdown()) for row in rows}
        if len(live) != len(rows):
            raise ValueError("The live rows contain duplicate alert numbers.")
        problems = self._differences(recorded, live)
        logger.debug("Compared the register and found %d differences", len(problems))
        return problems

    def _differences(self, recorded: dict[int, VerdictRow], live: dict[int, VerdictRow]) -> list[str]:
        """Name changed fields without copying audit comments into CI logs."""
        problems: list[str] = []
        for number in sorted(set(live) - set(recorded)):
            problems.append(f"Alert {number} is dismissed and the register holds no row.")
        for number in sorted(set(recorded) - set(live)):
            problems.append(f"The register holds row {number} and no dismissed alert matches it.")
        for number in sorted(set(recorded) & set(live)):
            changed = self._changed_decision_columns(
                number, recorded[number], live[number]
            )  # Ignore location-only drift.
            if changed:
                problems.append(f"Alert {number} differs in these columns: {', '.join(changed)}.")
        return problems

    def _changed_decision_columns(self, number: int, recorded: VerdictRow, live: VerdictRow) -> list[str]:
        """Return changed columns that carry a review decision."""
        changed: list[str] = []  # Store decision-bearing differences that fail the check.
        positional: list[str] = []  # Store generated location drift for the audit log.
        for column, field in zip(COLUMNS, fields(VerdictRow), strict=True):  # Compare fields in register order.
            if getattr(recorded, field.name) == getattr(live, field.name):  # Skip fields with stable values.
                continue  # Continue so only real drift reaches the decision logic.
            if column in POSITIONAL_COLUMNS:  # Separate generated location drift from security decisions.
                positional.append(column)  # Keep location drift visible without failing the gate.
                continue  # Continue so a location-only move does not become a problem.
            changed.append(column)  # Fail on changed audit decisions or changed alert identity.
        if positional:  # Emit an audit trail when the generated location moved.
            self._log_positional_drift(number, recorded, live, positional)  # Log ignored generated location drift.
        return changed

    def _log_positional_drift(self, number: int, recorded: VerdictRow, live: VerdictRow, positional: list[str]) -> None:
        """Log line drift that does not change the accepted risk."""
        logger.info(  # Log location-only drift so a reviewer can refresh the register later.
            "Alert %d moved from line %s to line %s. Ignored positional columns: %s",
            number,
            recorded.line,
            live.line,
            ", ".join(positional),
        )


class RegisterConsole:
    """Run the tool from the command line."""

    def __init__(self, repository: str, path: Path, rule_id: str) -> None:
        # Build the alert source for the repository and the rule.
        self.source = AlertSource(repository, rule_id)
        # Build the row builder that maps an alert onto a register row.
        self.builder = RowBuilder()
        # Store the register path for the writer and the reconciler.
        self.path = path
        # Store the rule so the writer states it in the header.
        self.rule_id = rule_id

    def generate(self) -> int:
        """Write the register and report the row count."""
        logger.info("Starting the register generation")
        # Build one row for each dismissed alert.
        rows = [self.builder.build(alert) for alert in self.source.fetch()]
        # Write the register file.
        RegisterWriter(self.path, self.rule_id).write(rows)
        print(f"Wrote {len(rows)} rows to {self.path}")
        logger.debug("Finished the register generation with %d rows", len(rows))
        return 0

    def check(self) -> int:
        """Compare the register against the API and report every difference."""
        logger.info("Starting the register reconciliation")
        # Build the live rows from the API.
        rows = [self.builder.build(alert) for alert in self.source.fetch()]
        # Compare the live rows against the recorded rows.
        problems = RegisterReconciler(self.path).compare(rows)
        # Report success when the register and the API agree.
        if not problems:
            print(f"The register matches all {len(rows)} dismissed alerts.")
            return 0
        # Report each difference on its own line for a readable log.
        for problem in problems:
            print(problem)
        print(f"The reconciliation found {len(problems)} differences.")
        logger.debug("Finished the reconciliation with %d differences", len(problems))
        return 1


def _default_repository_from_gh() -> str | None:
    """Return the repository from the local checkout through the GitHub CLI."""
    logger.info("Reading the repository name from gh repo view")  # Log before the external command.
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=_GH_TIMEOUT_SECONDS,
        )  # Bound the CLI read, because a credential prompt can stall.
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        logger.warning("Could not read the repository name with gh: %s", error)  # Log the failed fallback.
        return None  # The caller prints the public error message.
    repository = result.stdout.strip()  # gh writes only the owner/name value for this query.
    logger.debug("Read repository name %s from gh repo view", repository)  # Log the successful fallback.
    return repository or None  # Treat blank output as no answer.


def default_repository() -> str | None:
    """Return the repository from the environment or the local checkout."""
    logger.info("Resolving the repository default")  # Log before the default chain starts.
    repository: str | None = os.environ.get(
        _REPOSITORY_VARIABLE,
        "",
    ).strip()  # GitHub Actions sets owner/name here.
    if repository:
        logger.debug("Using %s from the environment", _REPOSITORY_VARIABLE)  # Log which source won.
        return repository  # The environment is the most specific runtime source.
    repository = _default_repository_from_gh()  # Fall back to the checkout metadata.
    if repository:
        logger.debug("Using %s from gh repo view", repository)  # Log the fallback source.
    return repository  # None tells main to print a clear command-line error.


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the selected mode."""
    # Configure logging before any action, so every step reaches the log.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build and check the CodeQL verdict register.")
    # Accept the mode as the single positional argument.
    parser.add_argument("mode", choices=("generate", "check"))
    # Accept a repository override for a fork or a test run.
    parser.add_argument("--repository", default=None)
    # Accept a register path override for a test run.
    parser.add_argument("--path", type=Path, default=DEFAULT_REGISTER_PATH)
    # Accept a rule override, because the tool can govern another rule later.
    parser.add_argument("--rule-id", default=DEFAULT_RULE_ID)
    args = parser.parse_args(argv)
    repository = args.repository or default_repository()
    if repository is None:
        print(
            "Could not determine the repository. Set GITHUB_REPOSITORY or pass --repository owner/name.",
            file=sys.stderr,
        )
        return 2
    # Build the console for the selected repository, path, and rule.
    console = RegisterConsole(repository, args.path, args.rule_id)
    # Run the selected mode and return its exit status.
    try:
        return console.generate() if args.mode == "generate" else console.check()
    except (OSError, RuntimeError, ValueError) as error:
        logger.exception("The register %s failed: %s", args.mode, error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
