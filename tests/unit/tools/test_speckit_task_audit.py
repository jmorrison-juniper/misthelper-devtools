from pathlib import Path  # Build isolated repository trees for the audit tests.

from tools.speckit_task_audit import AllowList, SpecTaskAudit, TaskFileScanner  # Import the tool under test.


class TestSpecTaskAudit:
    def test_a_complete_spec_with_checked_tasks_passes(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "100-done", "Implemented")  # Mark the spec as shipped for the gate.
        self._write_tasks(tmp_path, "100-done", "- [X] T001 Done\n")  # Add only a finished task.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the fixture tree.
        output = capsys.readouterr().out  # Capture the report for a human-output assertion.
        assert result == 0  # A complete spec with no open task must pass.
        assert "No unchecked task boxes found." in output  # The report must prove the scan ran.

    def test_a_complete_spec_with_unchecked_tasks_fails(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "101-open", "Implemented")  # Mark the spec as complete so drift blocks.
        self._write_tasks(tmp_path, "101-open", "- [ ] T001 Open\n")  # Add one open task box.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the open task.
        output = capsys.readouterr().out  # Read the report so the test checks the count.
        assert result == 1  # A complete spec with an open task must fail.
        assert "specs/101-open: 1 unchecked tasks" in output  # The report must name the failing spec.

    def test_an_allow_list_suppresses_a_complete_spec_failure(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "102-allowed", "Implemented")  # Mark the spec as complete.
        self._write_tasks(tmp_path, "102-allowed", "- [ ] T001 Open\n")  # Add an intentional open task.
        result = SpecTaskAudit(tmp_path, AllowList({"102-allowed"})).run()  # Allow this spec by name.
        output = capsys.readouterr().out  # Capture the report to confirm the marker.
        assert result == 0  # The allow list must suppress the failure.
        assert "(allowed)" in output  # The report must show that an exception applied.

    def test_a_missing_tasks_file_is_reported(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "103-missing", "Draft")  # Create a spec folder with no task record.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the missing file.
        output = capsys.readouterr().out  # Capture the report that names missing tasks.
        assert result == 0  # A missing file is a report item, not unchecked-task drift.
        assert "specs/103-missing: missing tasks.md" in output  # The report must name the missing file.

    def test_empty_and_checkbox_free_files_pass(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "104-empty", "Implemented")  # Create a complete spec for an empty file.
        self._write_tasks(tmp_path, "104-empty", "")  # Empty task records contain no unchecked task.
        self._write_spec(tmp_path, "105-none", "Implemented")  # Create a second complete spec.
        self._write_tasks(tmp_path, "105-none", "A note without boxes.\n")  # Add prose with no markers.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against both edge files.
        output = capsys.readouterr().out  # Capture the clean report.
        assert result == 0  # No checkbox means no unchecked task drift.
        assert "No unchecked task boxes found." in output  # The report must stay clear and explicit.

    def test_malformed_boxes_do_not_count_as_tasks(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.md"  # Keep this unit case outside a full spec tree.
        path.write_text("- [todo] T001 Bad\n", encoding="utf-8")  # Write a malformed checkbox marker.
        counts = TaskFileScanner().scan(path)  # Scan the file directly to inspect all counters.
        assert counts.unchecked == 0  # A malformed marker must not become an open task.
        assert counts.checked == 0  # A malformed marker must not become a done task.
        assert counts.malformed == 1  # The scanner must report the malformed marker.

    def test_nested_lists_count_as_tasks(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.md"  # Keep this scanner edge case small.
        path.write_text("  - [ ] T001 Nested\n", encoding="utf-8")  # Write a nested task box.
        counts = TaskFileScanner().scan(path)  # Scan the nested list marker.
        assert counts.unchecked == 1  # Nested task boxes are real task boxes.
        assert counts.checked == 0  # No done marker exists in this fixture.

    def test_fenced_code_blocks_do_not_count(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.md"  # Keep the fence behavior test direct.
        text = "```text\n- [ ] T001 Example\n```\n- [ ] T002 Real\n"  # Put one example and one real task.
        path.write_text(text, encoding="utf-8")  # Write the fixture with a fenced code block.
        counts = TaskFileScanner().scan(path)  # Scan the file with the fenced example.
        assert counts.unchecked == 1  # Only the real task outside the fence counts.
        assert counts.checked == 0  # No checked marker exists in the fixture.

    def _write_spec(self, root: Path, name: str, status: str) -> None:
        spec_dir = root / "specs" / name  # Build the SpecKit directory path.
        spec_dir.mkdir(parents=True, exist_ok=True)  # Create parents so each fixture is independent.
        spec_dir.joinpath("spec.md").write_text("**Status**: " + status + "\n", encoding="utf-8")  # Write status.

    def _write_tasks(self, root: Path, name: str, text: str) -> None:
        task_path = root / "specs" / name / "tasks.md"  # Build the task file path for the fixture.
        task_path.write_text(text, encoding="utf-8")  # Write the exact task text for the test case.
