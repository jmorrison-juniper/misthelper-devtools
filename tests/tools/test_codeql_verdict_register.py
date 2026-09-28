"""Test the shared CodeQL verdict register tool."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from misthelper_devtools import codeql_verdict_register as register


def _alert(number: int, reason: str, comment: str | None) -> dict[str, Any]:
    """Return one API alert record for a test."""
    # Build the smallest record shape that the row builder reads.
    return {
        "number": number,
        "state": "dismissed",
        "rule": {"id": register.DEFAULT_RULE_ID},
        "dismissed_reason": reason,
        "dismissed_comment": comment,
        "dismissed_by": {"login": "tester"},
        "dismissed_at": "2026-01-01T00:00:00Z",
        "most_recent_instance": {"location": {"path": "src/demo.py", "start_line": 12}},
    }


@pytest.fixture
def api_run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace the API process so every test stays offline."""
    runner = Mock()
    monkeypatch.setattr(register.subprocess, "run", runner)
    return runner


class TestRowBuilder:
    """Check that an API alert becomes a correct register row."""

    def test_used_in_tests_maps_to_a_verdict(self) -> None:
        """The reason `used in tests` must map to a permitted verdict."""
        # Build the row for an alert that the team dismissed as a test fixture.
        row = register.RowBuilder().build(_alert(7, "used in tests", "A test fixture holds it."))
        # Confirm the mapping, because clause C-7 had no value for this reason.
        assert row.verdict == "test_fixture"

    def test_a_blank_comment_never_yields_a_blank_reason(self) -> None:
        """A dismissal with no comment must still carry a written reason."""
        # Build the row for an alert that carries no dismissal comment.
        row = register.RowBuilder().build(_alert(8, "false positive", None))
        # Confirm the warning text, because clause C-4 forbids a blank cell.
        assert row.reason == register.MISSING_REASON_TEXT
        # Confirm the trigger names the missing reason as the review event.
        assert row.trigger == register.MISSING_REASON_TRIGGER

    @pytest.mark.parametrize(
        ("stamp", "decided", "review"),
        [("2026-01-01T00:00:00Z", "2026-01-01", "2026-06-30"), (None, "-", "-")],
    )
    def test_the_row_records_a_review_date(self, stamp: str | None, decided: str, review: str) -> None:
        """A non-fixed row must carry a review date, as clause C-5 requires."""
        alert = _alert(9, "false positive", "The value is a label.")
        alert["dismissed_at"] = stamp
        row = register.RowBuilder().build(alert)
        assert row.decided == decided
        assert row.review == review

    def test_the_row_reads_the_issue_reference(self) -> None:
        """The builder must copy an issue reference out of the comment."""
        # Build the row for a comment that names its tracking issue.
        row = register.RowBuilder().build(_alert(10, "won't fix", "Refs #1735 and accepted."))
        # Confirm the issue cell carries the reference for the audit trail.
        assert row.issue == "#1735"

    def test_a_pipe_in_the_comment_cannot_break_the_table(self, tmp_path: Path) -> None:
        """A pipe inside a comment must not end the markdown cell early."""
        # Build the row for a comment that holds a raw pipe character.
        row = register.RowBuilder().build(_alert(11, "false positive", "a | b"))
        # Confirm the writer escapes the pipe so the cell survives the table.
        assert "\\|" in row.to_markdown()
        # Write the register and read it back, because the round trip proves the format.
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([row])
        assert register.RegisterReconciler(path).parse() == {11: row}
        assert register.RegisterReconciler(path).compare([row]) == []


class TestRegisterReconciler:
    """Check that the reconciliation reports every difference."""

    def _write(self, tmp_path: Path, rows: list[Any]) -> Path:
        """Write a register file for a test and return its path."""
        # Build the target path inside the temporary directory.
        path = tmp_path / "register.md"
        # Write the rows with the real writer, so the test reads the real format.
        register.RegisterWriter(path).write(rows)
        return path

    def test_a_matching_register_reports_no_difference(self, tmp_path: Path) -> None:
        """A register that matches the API must report no difference."""
        # Build one row and write it to the register.
        rows = [register.RowBuilder().build(_alert(3, "false positive", "The value is a label."))]
        path = self._write(tmp_path, rows)
        # Compare the register against the same rows.
        assert register.RegisterReconciler(path).compare(rows) == []

    def test_a_missing_row_fails_the_review(self, tmp_path: Path) -> None:
        """A dismissed alert with no register row must fail the review."""
        # Write a register that holds one row.
        first = register.RowBuilder().build(_alert(3, "false positive", "The value is a label."))
        path = self._write(tmp_path, [first])
        # Compare against two alerts, so the second one has no row.
        second = register.RowBuilder().build(_alert(4, "won't fix", "The team accepts the risk."))
        problems = register.RegisterReconciler(path).compare([first, second])
        # Confirm the reconciliation names the alert that the register misses.
        assert len(problems) == 1
        assert "Alert 4" in problems[0]

    @pytest.mark.parametrize("has_live_rows", [False, True])
    def test_an_absent_register_is_an_error(self, tmp_path: Path, has_live_rows: bool) -> None:
        """An absent register must fail even when the API reports no alerts."""
        path = tmp_path / "absent.md"
        rows = (
            [register.RowBuilder().build(_alert(5, "false positive", "The value is a label."))] if has_live_rows else []
        )
        with pytest.raises(FileNotFoundError):
            register.RegisterReconciler(path).compare(rows)

    @pytest.mark.parametrize(
        ("field", "value", "column"),
        [
            ("alert", 4, "Alert 4"),
            ("issue", "#44", "Issue"),
            ("file", "src/moved.py", "File"),
            ("verdict", "test_fixture", "Verdict"),
            ("reason", "A changed reason.", "Reason"),
            ("author", "another-reviewer", "Author"),
            ("decided", "2026-01-02", "Decided"),
            ("review", "2026-07-01", "Review"),
            ("trigger", "A reviewer receives new evidence.", "Trigger"),
        ],
    )
    def test_every_decision_field_can_cause_drift(self, tmp_path: Path, field: str, value: Any, column: str) -> None:
        """A changed decision field must fail without copying its value into the report."""
        row = register.RowBuilder().build(_alert(3, "false positive", "The value is a label."))  # Build a stable row.
        path = self._write(tmp_path, [row])  # Write the recorded row so the comparison has a baseline.
        problems = register.RegisterReconciler(path).compare(
            [replace(row, **{field: value})]
        )  # Compare one changed field.
        assert len(problems) == (
            2 if field == "alert" else 1
        )  # The alert identity change creates a missing and an extra row.
        assert any(column in problem for problem in problems)  # The report must name the changed decision column.
        if field in ("reason", "author"):  # Sensitive audit text must not leak into CI logs.
            assert value not in " ".join(problems)  # The report must name the column, not the private cell text.

    @pytest.mark.parametrize(
        ("field", "value"),
        [("line", "13"), ("anchor", "src/demo.py::L13")],
    )
    def test_positional_fields_do_not_cause_drift(self, tmp_path: Path, field: str, value: str) -> None:
        """A changed positional field must not fail when all decisions match."""
        row = register.RowBuilder().build(_alert(3, "false positive", "The value is a label."))  # Build a stable row.
        path = self._write(tmp_path, [row])  # Write the recorded row so the comparison has a baseline.
        problems = register.RegisterReconciler(path).compare(
            [replace(row, **{field: value})]
        )  # Move one generated location cell.
        assert problems == []  # A generated location-only move must not fail the gate.

    def test_line_move_without_decision_change_reports_no_problem(self, tmp_path: Path) -> None:
        """A line-only move must not fail the register check."""
        row = register.RowBuilder().build(
            _alert(3066, "false positive", "The value is a label.")
        )  # Build the alert row.
        recorded = replace(row, line="323", anchor="src/demo.py::L323")  # Record the old generated location.
        live = replace(row, line="348", anchor="src/demo.py::L348")  # Simulate a harmless line move.
        path = self._write(tmp_path, [recorded])  # Write the stale generated location to the register.
        problems = register.RegisterReconciler(path).compare([live])  # Compare against the moved live location.
        assert problems == []  # A line-only move must keep the required gate green.

    def test_line_move_with_verdict_change_reports_one_problem(self, tmp_path: Path) -> None:
        """A line move must still fail when the verdict changed."""
        row = register.RowBuilder().build(
            _alert(3066, "false positive", "The value is a label.")
        )  # Build the alert row.
        recorded = replace(row, line="323", anchor="src/demo.py::L323")  # Record the old generated location.
        live = replace(row, line="348", anchor="src/demo.py::L348", verdict="test_fixture")  # Change one decision cell.
        path = self._write(tmp_path, [recorded])  # Write the stale generated location to the register.
        problems = register.RegisterReconciler(path).compare(
            [live]
        )  # Compare against moved code with a changed verdict.
        assert len(problems) == 1  # A decision change must fail exactly once.
        assert "Verdict" in problems[0]  # The report must name the changed decision column.

    def test_line_move_with_reason_change_reports_reason(self, tmp_path: Path) -> None:
        """A line move must still fail when the reason changed."""
        row = register.RowBuilder().build(
            _alert(3066, "false positive", "The value is a label.")
        )  # Build the alert row.
        recorded = replace(row, line="323", anchor="src/demo.py::L323")  # Record the old generated location.
        live = replace(
            row, line="348", anchor="src/demo.py::L348", reason="A changed reason."
        )  # Change one decision cell.
        path = self._write(tmp_path, [recorded])  # Write the stale generated location to the register.
        problems = register.RegisterReconciler(path).compare(
            [live]
        )  # Compare against moved code with a changed reason.
        assert len(problems) == 1  # A decision change must fail exactly once.
        assert "Reason" in problems[0]  # The report must name the changed decision column.

    def test_register_row_without_live_alert_reports_one_problem(self, tmp_path: Path) -> None:
        """A retired alert row must still fail the register check."""
        row = register.RowBuilder().build(_alert(3066, "false positive", "The value is a label."))  # Build a stale row.
        path = self._write(tmp_path, [row])  # Write one register row that has no live match.
        problems = register.RegisterReconciler(path).compare([])  # Compare against no live alerts.
        assert len(problems) == 1  # A stale register row must fail once.
        assert "no dismissed alert matches" in problems[0]  # The report must explain the stale row.


class TestRegisterTable:
    """Reject corrupt tables instead of accepting a partial audit."""

    @pytest.mark.parametrize(
        "damage",
        ["duplicate", "missing-cell", "extra-cell", "blank-cell", "invalid-alert", "boundary", "null", "text"],
    )
    def test_invalid_rows_raise_an_error(self, tmp_path: Path, damage: str) -> None:
        """Each malformed row must stop reconciliation."""
        row = register.RowBuilder().build(_alert(3, "false positive", "A label."))
        line = row.to_markdown()
        damaged = {
            "duplicate": line + "\n" + line,
            "missing-cell": line.rsplit(" | ", 1)[0] + " |",
            "extra-cell": line + " extra |",
            "blank-cell": line.replace(" | tester | ", " |  | "),
            "invalid-alert": line.replace("| 3 |", "| not-an-alert |", 1),
            "boundary": line[1:],
            "null": line.replace("A label.", "A\x00label."),
            "text": "An invalid record.",
        }
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([row])
        path.write_text(path.read_text(encoding="utf-8").replace(line, damaged[damage]), encoding="utf-8")
        with pytest.raises(ValueError):
            register.RegisterReconciler(path).compare([row])

    @pytest.mark.parametrize("text", ["", "# CodeQL verdict register\n", "\n## Register\n| Wrong | Columns |\n"])
    def test_an_invalid_section_is_not_an_empty_register(self, tmp_path: Path, text: str) -> None:
        """An empty API result must not make an invalid document pass."""
        path = tmp_path / "register.md"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            register.RegisterReconciler(path).compare([])

    def test_a_valid_empty_register_matches_an_empty_api_result(self, tmp_path: Path) -> None:
        """A complete empty table is valid when no matching alert exists."""
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([])
        assert register.RegisterReconciler(path).compare([]) == []

    def test_duplicate_live_rows_raise_an_error(self, tmp_path: Path) -> None:
        """A duplicate API identity must not disappear in a dictionary."""
        row = register.RowBuilder().build(_alert(3, "false positive", "A label."))
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([row])
        with pytest.raises(ValueError, match="duplicate"):
            register.RegisterReconciler(path).compare([row, row])

    @pytest.mark.parametrize(
        "comment", ["  A\tlabel.\r\nAnother line.  ", r"A \| B | C\\", "A label.", "A label from Montr\u00e9al."]
    )
    def test_documented_cell_normalization_is_stable(self, tmp_path: Path, comment: str) -> None:
        """Comment whitespace and escaped pipes must survive the table format."""
        row = register.RowBuilder().build(_alert(3, "false positive", comment))
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([row])
        text = path.read_text(encoding="utf-8").replace(" | tester | ", " |   tester   | ")
        path.write_text(text, encoding="utf-8")
        assert register.RegisterReconciler(path).compare([row]) == []
        assert register.RegisterReconciler(path).parse()[3].reason == " ".join(comment.split())


class TestAlertSource:
    """Require complete, valid paginated API responses."""

    def test_all_pages_are_read_before_rule_filtering(self, api_run: Mock) -> None:
        """An alert on page two must not disappear from the audit."""
        first_page = [_alert(number, "false positive", "A label.") for number in range(1, 101)]
        last = _alert(101, "won't fix", "A recorded decision.")
        unrelated = _alert(102, "false positive", "A different rule.")
        unrelated["rule"] = {"id": "py/another-rule"}
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([first_page, [last, unrelated]]))
        assert register.AlertSource("owner/repo").fetch() == [*first_page, last]
        assert api_run.call_args.args[0] == [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/owner/repo/code-scanning/alerts?state=dismissed&per_page=100",
        ]
        assert api_run.call_args.kwargs == {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "check": True,
            "timeout": register._GH_TIMEOUT_SECONDS,
        }

    @pytest.mark.parametrize("payload", ["", "not JSON", "null", "{}", "[]", "[{}]", "[[null]]", "[[7]]"])
    def test_malformed_pages_are_not_an_empty_result(self, api_run: Mock, payload: str) -> None:
        """A malformed response must fail before a comparison can pass."""
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=payload)
        with pytest.raises(ValueError):
            register.AlertSource("owner/repo").fetch()

    @pytest.mark.parametrize(
        "updates",
        [
            {"number": 0},
            {"number": -1},
            {"number": True},
            {"number": "3"},
            {"rule": None},
            {"rule": {}},
            {"rule": {"id": 7}},
            {"rule": {"id": ""}},
            {"state": "open"},
            {"state": None},
        ],
    )
    def test_invalid_identities_fail_before_filtering(self, api_run: Mock, updates: dict[str, Any]) -> None:
        """A rule filter must not silently discard a malformed record."""
        alert = _alert(3, "false positive", "A label.")
        alert.update(updates)
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([[alert]]))
        with pytest.raises(ValueError):
            register.AlertSource("owner/repo").fetch()
        if "number" in updates:
            with pytest.raises(ValueError):
                register.RowBuilder().build(alert)

    def test_a_duplicate_on_a_later_page_fails(self, api_run: Mock) -> None:
        """Repeated API pages must not silently overwrite earlier records."""
        alert = _alert(3, "false positive", "A label.")
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([[alert], [alert]]))
        with pytest.raises(ValueError, match="repeats alert 3"):
            register.AlertSource("owner/repo").fetch()

    def test_a_complete_empty_response_is_valid(self, api_run: Mock) -> None:
        """An empty page is different from a missing response."""
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout="[[]]")
        assert register.AlertSource("owner/repo").fetch() == []


class TestRegisterConsole:
    """Protect process results and read-only behavior at the CLI boundary."""

    def test_generate_then_check_preserves_metadata_and_the_file(
        self, tmp_path: Path, api_run: Mock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean check must ignore the generation date and write nothing."""
        alert = _alert(3, "won't fix", "Recorded decision. Refs #44.")
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([[alert]]))
        path = tmp_path / "register.md"
        assert register.main(["generate", "--repository", "owner/repo", "--path", str(path)]) == 0
        assert register.RegisterReconciler(path).parse() == {3: register.RowBuilder().build(alert)}
        text = path.read_text(encoding="utf-8")
        date_line = next(line for line in text.splitlines() if line.startswith("**Generated**:"))
        path.write_text(text.replace(date_line, "**Generated**: 1999-12-31"), encoding="utf-8")
        before = path.read_bytes()
        capsys.readouterr()
        assert register.main(["check", "--repository", "owner/repo", "--path", str(path)]) == 0
        assert "matches all 1 dismissed alerts" in capsys.readouterr().out
        assert path.read_bytes() == before

    @pytest.mark.parametrize("change", ["missing", "extra", "verdict", "reason", "author"])
    def test_real_drift_returns_one_without_repairing_the_file(
        self, tmp_path: Path, api_run: Mock, capsys: pytest.CaptureFixture[str], change: str
    ) -> None:
        """Drift must fail the exact check command without generating a repair."""
        alert = _alert(3, "false positive", "A label.")
        row = register.RowBuilder().build(alert)
        variants = {"verdict": "test_fixture", "reason": "A stale reason.", "author": "old-reviewer"}
        rows = [replace(row, **{change: variants[change]})] if change in variants else [row]
        if change == "missing":
            rows = []
        if change == "extra":
            rows.append(replace(row, alert=4))
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write(rows)
        before = path.read_bytes()
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([[alert]]))
        assert register.main(["check", "--repository", "owner/repo", "--path", str(path)]) == 1
        output = capsys.readouterr().out
        assert "found 1 differences" in output
        assert "matches all" not in output
        assert path.read_bytes() == before

    @pytest.mark.parametrize("failure", [401, 403, 404, 429, 500, "timeout", "missing-cli"])
    def test_api_errors_return_two_without_exposing_response_content(
        self, tmp_path: Path, api_run: Mock, caplog: pytest.LogCaptureFixture, failure: int | str
    ) -> None:
        """Denied access or a failed read must never produce an empty success."""
        marker = "test-only-private-response"
        if failure == "timeout":
            api_run.side_effect = subprocess.TimeoutExpired("gh", 120, output=marker, stderr=marker)
        elif failure == "missing-cli":
            api_run.side_effect = FileNotFoundError("The gh command is absent.")
        else:
            api_run.side_effect = subprocess.CalledProcessError(
                1, "gh", output=marker, stderr=f"HTTP {failure} {marker}"
            )
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([])
        before = path.read_bytes()
        assert register.main(["check", "--repository", "owner/repo", "--path", str(path)]) == 2
        assert marker not in caplog.text
        if isinstance(failure, int):
            assert "security-events:read" in caplog.text
        assert path.read_bytes() == before

    @pytest.mark.parametrize(
        "updates",
        [
            {"dismissed_reason": None},
            {"dismissed_reason": "unknown"},
            {"dismissed_reason": []},
            {"dismissed_comment": 12},
            {"dismissed_at": 12},
            {"dismissed_at": "not-a-date"},
            {"dismissed_at": "2026-1-1"},
            {"dismissed_by": "tester"},
            {"dismissed_by": {}},
            {"dismissed_by": {"login": None}},
            {"most_recent_instance": None},
            {"most_recent_instance": {}},
            {"most_recent_instance": {"location": {"path": 3}}},
            {"most_recent_instance": {"location": {"path": "src/demo.py", "start_line": "12"}}},
            {"most_recent_instance": {"location": {"path": "src/demo.py", "start_line": 0}}},
        ],
    )
    def test_invalid_dismissal_metadata_cannot_overwrite_the_register(
        self, tmp_path: Path, api_run: Mock, updates: dict[str, Any]
    ) -> None:
        """Invalid API metadata must not become an invented audit decision."""
        alert = _alert(3, "false positive", "A label.")
        path = tmp_path / "register.md"
        register.RegisterWriter(path).write([register.RowBuilder().build(alert)])
        before = path.read_bytes()
        alert.update(updates)
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout=json.dumps([[alert]]))
        assert register.main(["generate", "--repository", "owner/repo", "--path", str(path)]) == 2
        assert path.read_bytes() == before

    @pytest.mark.parametrize("text", [None, "", "An invalid register."])
    def test_invalid_registers_return_two_even_with_no_alerts(
        self, tmp_path: Path, api_run: Mock, text: str | None
    ) -> None:
        """A missing or corrupt file must not pass against an empty API page."""
        path = tmp_path / "register.md"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        api_run.return_value = subprocess.CompletedProcess("gh", 0, stdout="[[]]")
        assert register.main(["check", "--repository", "owner/repo", "--path", str(path)]) == 2
        assert path.read_text(encoding="utf-8") == text if text is not None else not path.exists()


class TestRepositoryDefault:
    """Check that the repository default is caller-owned."""

    def test_environment_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/env-repo")
        monkeypatch.setattr(register, "_default_repository_from_gh", lambda: "owner/gh-repo")

        assert register.default_repository() == "owner/env-repo"

    def test_gh_fallback_reads_name_with_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        calls: list[dict[str, Any]] = []

        def fake_run(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(kwargs)
            return subprocess.CompletedProcess("gh", 0, stdout="owner/gh-repo\n")

        monkeypatch.setattr(register.subprocess, "run", fake_run)

        assert register.default_repository() == "owner/gh-repo"
        assert calls[0]["timeout"] == register._GH_TIMEOUT_SECONDS

    def test_main_returns_two_when_default_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.setattr(register, "_default_repository_from_gh", lambda: None)

        assert register.main(["check", "--path", "missing.md"]) == 2
        assert "Could not determine the repository" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["false positive", "won't fix", "used in tests"])
def test_every_api_reason_maps_to_a_verdict(reason: str) -> None:
    """Each API dismissal reason must produce a permitted verdict."""
    # Build the row for the reason under test.
    row = register.RowBuilder().build(_alert(1, reason, "A written reason."))
    # Confirm the verdict belongs to the permitted set.
    assert row.verdict in {"false_positive", "accepted_with_rationale", "test_fixture", "fixed"}
