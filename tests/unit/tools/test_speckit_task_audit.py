import shutil  # Locate the installed public command on each supported platform.
import subprocess  # Run the documented console command as an integration check.
import sys  # Find scripts beside the active test interpreter.
from pathlib import Path  # Build isolated repository trees for the audit tests.

from misthelper_devtools.speckit_task_audit import (  # Import the tool under test.
    AllowList,
    Command,
    SpecTaskAudit,
    TaskFileScanner,
)


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

    def test_checked_task_without_a_citation_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "106-no-citation", "Implemented")  # Mark the spec as complete.
        self._write_tasks(tmp_path, "106-no-citation", "- [X] T001 Done\n")  # Add no proof path.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against no-citation task.
        assert result == 0  # A checked task with no citation does not trigger the path rule.

    def test_checked_task_with_a_missing_citation_fails(self, tmp_path: Path, capsys) -> None:
        self._write_spec(tmp_path, "107-missing-citation", "Implemented")  # Mark the spec as complete.
        text = "- [X] T001 Done. Evidence: `missing.md`.\n"  # Cite a file that the fixture tree lacks.
        self._write_tasks(tmp_path, "107-missing-citation", text)  # Write the checked task with bad proof.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the missing path.
        output = capsys.readouterr().out  # Capture the report so the path is checked.
        assert result == 1  # A complete spec with a missing proof path must fail.
        assert "missing.md (missing)" in output  # The report must name the unresolved path.

    def test_checked_task_with_a_real_root_citation_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "108-real-root", "Implemented")  # Mark the spec as complete.
        tmp_path.joinpath("proof.md").write_text("done\n", encoding="utf-8")  # Create root proof.
        text = "- [X] T001 Done. Evidence: `proof.md`.\n"  # Cite the root-relative proof file.
        self._write_tasks(tmp_path, "108-real-root", text)  # Write the checked task with real proof.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the real path.
        assert result == 0  # A checked task with a real proof path must pass.

    def test_checked_task_citation_inside_a_fence_is_ignored(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "109-fenced-citation", "Implemented")  # Mark the spec as complete.
        text = "```\n- [X] T001 Done. Evidence: `missing.md`.\n```\n"  # Put the citation in an example.
        self._write_tasks(tmp_path, "109-fenced-citation", text)  # Write the fenced citation fixture.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against fenced text.
        assert result == 0  # A proof path in a fenced code block must not count.

    def test_checked_task_with_a_relative_citation_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "110-relative", "Implemented")  # Mark the spec as complete.
        spec_dir = tmp_path / "specs" / "110-relative"  # Build the spec directory path.
        spec_dir.joinpath("proof.md").write_text("done\n", encoding="utf-8")  # Create spec-local proof.
        text = "- [X] T001 Done. Evidence: `proof.md`.\n"  # Cite proof relative to the task file.
        self._write_tasks(tmp_path, "110-relative", text)  # Write the checked task with local proof.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against local proof.
        assert result == 0  # A task-file-relative proof path must pass.

    def test_checked_task_with_a_windows_separator_citation_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "111-windows", "Implemented")  # Mark the spec as complete.
        proof_dir = tmp_path / "docs"  # Build a root-relative proof directory.
        proof_dir.mkdir()  # Create the proof directory for the Windows separator path.
        proof_dir.joinpath("proof.md").write_text("done\n", encoding="utf-8")  # Create the proof file.
        text = "- [X] T001 Done. Evidence: `docs\\proof.md`.\n"  # Cite proof with a Windows separator.
        self._write_tasks(tmp_path, "111-windows", text)  # Write the checked task with Windows syntax.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against Windows syntax.
        assert result == 0  # A Windows separator citation must resolve on all platforms.

    def test_checked_task_with_a_line_suffix_citation_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "112-line-suffix", "Implemented")  # Mark the spec as complete.
        tmp_path.joinpath("proof.py").write_text("pass\n", encoding="utf-8")  # Create proof with any content.
        text = "- [X] T001 Done. Evidence: `proof.py:1`.\n"  # Cite the proof path with a source line.
        self._write_tasks(tmp_path, "112-line-suffix", text)  # Write the checked task with a line suffix.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Run the audit against the cited file.
        assert result == 0  # A real file with a line suffix must pass.

    def test_public_command_audits_a_grouped_feature(self, tmp_path: Path) -> None:
        name = "live/0/20261017-000000-grouped-record"  # Use the managed grouped layout.
        self._write_spec(tmp_path, name, "Complete")  # Mark the nested feature as complete.
        self._write_tasks(tmp_path, name, "- [ ] T001 Open\n")  # Add one task that must block.
        result = self._run_command(tmp_path)  # Run the installed public command.
        assert result.returncode == 1  # The public command must fail for an open task.
        assert "Examined 1 feature record(s)" in result.stdout  # The report must count discovered features.
        assert "1 task file(s)" in result.stdout  # The report must count task files.
        assert "1 task marker(s)" in result.stdout  # The report must count task boxes.
        assert f"specs/{name}: 1 unchecked tasks" in result.stdout  # The report must name the nested feature.

    def test_structural_groups_are_not_reported_as_features(self, tmp_path: Path, capsys) -> None:
        for root_name in ("numbered", "live", "skills", "indexes"):  # Create each managed route container.
            group = tmp_path / "specs" / root_name / "0"  # Create a grouping directory without feature files.
            group.mkdir(parents=True)  # Keep the empty route visible to recursive discovery.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Audit the tree with no feature records.
        output = capsys.readouterr().out  # Capture discovery evidence.
        assert result == 0  # A structural group has no task drift.
        assert "Examined 0 feature record(s)" in output  # The report must not count path containers.
        assert "missing tasks.md" not in output  # Structural groups do not require task files.

    def test_each_managed_root_discovers_nested_feature_records(self, tmp_path: Path, capsys) -> None:
        for root_name in ("numbered", "live", "skills", "indexes"):  # Cover each supported managed root.
            name = f"{root_name}/0/123-{root_name}"  # Give each feature a distinct route.
            self._write_spec(tmp_path, name, "Complete")  # Mark the nested feature as complete.
            self._write_tasks(tmp_path, name, "- [X] T001 Done\n")  # Add one completed task marker.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Audit each managed root.
        output = capsys.readouterr().out  # Capture the aggregate report.
        assert result == 0  # Completed features must preserve the existing completion rule.
        assert "Examined 4 feature record(s), 4 task file(s)" in output  # Count all nested records.

    def test_unmarked_directories_with_nested_features_are_groups(self, tmp_path: Path, capsys) -> None:
        name = "archive/2026/121-grouped"  # Use an unrecognized nested group name.
        self._write_spec(tmp_path, name, "Complete")  # Mark the nested feature as complete.
        self._write_tasks(tmp_path, name, "- [ ] T001 Open\n")  # Add an open task to the feature record.
        result = SpecTaskAudit(tmp_path, AllowList(set())).run()  # Audit the nested feature under the group.
        output = capsys.readouterr().out  # Capture the report and the feature count.
        assert result == 1  # The nested feature's open task must block.
        assert "Examined 1 feature record(s)" in output  # The unmarked group must not count as a feature.
        assert "specs/archive: missing tasks.md" not in output  # The group must not receive a task finding.

    def test_nested_allow_list_key_uses_the_report_path(self, tmp_path: Path, capsys) -> None:
        name = "live/0/120-allowed"  # Use the complete route from a nested report.
        self._write_spec(tmp_path, name, "Complete")  # Mark the nested feature as complete.
        self._write_tasks(tmp_path, name, "- [ ] T001 Open\n")  # Add an approved open task.
        result = SpecTaskAudit(tmp_path, AllowList({"specs/" + name})).run()  # Allow the report path.
        output = capsys.readouterr().out  # Capture the report to verify the exception marker.
        assert result == 0  # A path-keyed exception must suppress the feature failure.
        assert "specs/" + name + ": 1 unchecked tasks (allowed)" in output  # Show the selected exception.

    def test_nested_allow_list_does_not_match_a_sibling(self, tmp_path: Path, capsys) -> None:
        first_name = "live/0/122-shared-name"  # Create a nested record with a shared basename.
        second_name = "live/1/122-shared-name"  # Place a second record under a different route.
        self._write_spec(tmp_path, first_name, "Complete")  # Mark the first feature complete.
        self._write_tasks(tmp_path, first_name, "- [ ] T001 Open\n")  # Add an open first task.
        self._write_spec(tmp_path, second_name, "Complete")  # Mark the sibling feature complete.
        self._write_tasks(tmp_path, second_name, "- [ ] T001 Open\n")  # Add an open sibling task.
        allow_list = AllowList({"specs/" + first_name})  # Allow only the first route-specific record.
        result = SpecTaskAudit(tmp_path, allow_list).run()  # Audit both paths with the narrow exception.
        output = capsys.readouterr().out  # Capture each record's allow-list state.
        assert result == 1  # The unlisted sibling must remain blocking.
        assert f"specs/{first_name}: 1 unchecked tasks (allowed)" in output  # Confirm the exact allowed record.
        assert f"specs/{second_name}: 1 unchecked tasks (open)" in output  # Confirm the sibling still blocks.

    def test_nested_feature_identity_is_rejected_by_the_command(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "118-parent", "Complete")  # Create a feature that owns a nested path.
        self._write_tasks(tmp_path, "118-parent", "- [X] T001 Done\n")  # Complete the parent task record.
        self._write_spec(tmp_path, "118-parent/child", "Complete")  # Add an invalid nested feature identity.
        self._write_tasks(tmp_path, "118-parent/child", "- [X] T001 Done\n")  # Complete the child record.
        result = self._run_command(tmp_path)  # Exercise the public command with the invalid feature tree.
        assert result.returncode == 2  # Invalid nested identities must stop the audit.
        assert "nested feature identity" in result.stderr.lower()  # The failure must name the rule.
        assert "118-parent/child" in result.stderr  # The error must identify the nested record.

    def test_unreadable_task_file_returns_an_explicit_error(self, tmp_path: Path, monkeypatch, capsys) -> None:
        self._write_spec(tmp_path, "119-unreadable", "Complete")  # Create a feature with required inputs.
        task_path = tmp_path / "specs" / "119-unreadable" / "tasks.md"  # Identify the required task file.
        task_path.write_text("- [X] T001 Done\n", encoding="utf-8")  # Create a task file before the read fault.
        original_read_text = Path.read_text  # Preserve normal reads for fixture files.

        def reject_task_read(
            path: Path,
            encoding: str | None = None,
            errors: str | None = None,
            *,
            newline: str | None = None,
        ) -> str:
            if path == task_path:  # Limit the simulated failure to the required task file.
                raise PermissionError("permission denied")  # Prove that the command fails closed.
            return original_read_text(path, encoding=encoding, errors=errors, newline=newline)  # Keep other reads.

        monkeypatch.setattr(Path, "read_text", reject_task_read)  # Replace only the selected file read.
        result = Command.main(["--root", str(tmp_path)])  # Run the documented command entry point.
        error = capsys.readouterr().err  # Read the explicit error report.
        assert result == 2  # An unreadable required file must stop the audit.
        assert "cannot read" in error.lower()  # The operator must receive a clear error.
        assert "tasks.md" in error  # The error must name the unreadable input.

    def test_missing_specs_root_returns_an_explicit_error(self, tmp_path: Path, capsys) -> None:
        result = Command.main(["--root", str(tmp_path)])  # Run the command without a required specs folder.
        error = capsys.readouterr().err  # Capture the input error for an operator assertion.
        assert result == 2  # A missing required root must stop the audit.
        assert "cannot scan speckit directory" in error.lower()  # The error must explain the failed scan.
        assert str(tmp_path / "specs") in error  # The error must name the missing directory.

    def _write_spec(self, root: Path, name: str, status: str) -> None:
        spec_dir = root / "specs" / name  # Build the SpecKit directory path.
        spec_dir.mkdir(parents=True, exist_ok=True)  # Create parents so each fixture is independent.
        spec_dir.joinpath("spec.md").write_text("**Status**: " + status + "\n", encoding="utf-8")  # Write status.

    def _write_tasks(self, root: Path, name: str, text: str) -> None:
        task_path = root / "specs" / name / "tasks.md"  # Build the task file path for the fixture.
        task_path.write_text(text, encoding="utf-8")  # Write the exact task text for the test case.

    def _run_command(self, root: Path) -> subprocess.CompletedProcess[str]:
        script_directory = str(Path(sys.executable).parent)  # Find scripts beside the active test interpreter.
        command = shutil.which("speckit-task-audit", path=script_directory)  # Locate the installed public command.
        assert command is not None, "Install the project before the public command test."  # Fail with setup context.
        return subprocess.run(  # Run the command and capture its full result.
            [command, "--root", str(root)],
            check=False,  # Keep expected failures available to the tests.
            capture_output=True,  # Read the output and error streams.
            text=True,  # Decode the command output for assertions.
        )
