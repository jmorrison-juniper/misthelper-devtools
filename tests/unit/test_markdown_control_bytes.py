"""Unit tests for the Markdown control-byte guard."""

from __future__ import annotations

import shutil
from pathlib import Path

from tools.markdown_control_bytes import MarkdownControlByteScanner

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # Anchor scans at the checkout, not the pytest cwd.


class ControlByteGuardWorkspace:
    """Create project-local files for the control-byte guard tests."""

    root = REPOSITORY_ROOT / "data" / "test-control-byte-guard"  # Keep test files inside the repository workspace.

    def reset(self, name: str) -> Path:
        path = self.root / name  # Use one named directory per test for isolation.
        if path.exists():  # Remove stale files so each test starts clean.
            shutil.rmtree(path)  # Delete only the controlled test directory.
        path.mkdir(parents=True, exist_ok=True)  # Create the directory before the test writes files.
        return path  # Return the directory to the caller.


class TestMarkdownControlBytes:
    """Verify that the guard measures files and reports bad control bytes."""

    def test_tracked_markdown_has_no_disallowed_control_bytes(self) -> None:
        report = MarkdownControlByteScanner().scan_repository(REPOSITORY_ROOT)  # Measure tracked Markdown files.
        print(report.summary())  # State the file count so a zero-file scan is visible in guard output.
        assert report.checked_count > 0, report.summary()  # Fail if the repository Markdown list is empty.
        assert not report.findings, report.summary()  # Fail with exact byte offsets for each affected file.

    def test_guard_reports_vertical_tab_in_project_local_file(self) -> None:
        workspace = ControlByteGuardWorkspace().reset("vertical-tab")  # Create an isolated test directory.
        try:  # Clean project-local proof files even when an assertion fails.
            bad_file = workspace / "bad.md"  # Use a Markdown file so the scanner must inspect it.
            bad_file.write_bytes(b"set routing-options\x0b bad\n")  # Write a vertical tab as failure proof.
            report = MarkdownControlByteScanner().scan_roots((workspace,))  # Scan the controlled bad file.
            print(report.summary())  # State the measured count and the bad byte location.
            assert report.checked_count == 1, report.summary()  # Prove the guard measured the bad file.
            assert len(report.findings) == 1, report.summary()  # Prove the guard reports the file.
            assert report.findings[0].path == bad_file, report.summary()  # Prove the finding names the file.
            assert report.findings[0].offsets == ((19, 0x0B),), report.summary()  # Prove the exact byte offset.
        finally:
            shutil.rmtree(workspace, ignore_errors=True)  # Remove the proof file so the worktree stays clean.
