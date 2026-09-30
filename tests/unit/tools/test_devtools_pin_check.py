"""Unit tests for the misthelper-devtools requirement pin check."""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import io  # Capture the report of the check.
from pathlib import Path  # Write the sample requirement files.

import pytest  # Change the working directory for the default file.

from misthelper_devtools.devtools_pin_check import (
    ANNOTATION_TITLE,
    RequirementPin,
    annotation_path,
    escape_data,
    escape_property,
    find_pins,
    main,
    pin_pattern,
)

OLD = "0b969be7f60599f9a19ebc3069161f9353a1d830"  # The commit of release 0.5.2.
NEW = "1234567890abcdef1234567890abcdef12345678"  # A later commit of the shared workflows.
URL = "git+https://github.com/jmorrison-juniper/misthelper-devtools.git"  # The URL that the consumers pin.
PIN_LINE = f"misthelper-devtools @ {URL}@{OLD}"  # The line that MistHelper and MistCircuitStats hold.
PATTERN = pin_pattern("jmorrison-juniper/misthelper-devtools")  # The pattern of the default repository.


def run_check(*arguments: str, workspace: Path | None = None) -> tuple[int, str]:
    """Run the check main function and return the status and the output."""
    output = io.StringIO()  # Capture the report lines.
    environ = {} if workspace is None else {"GITHUB_WORKSPACE": str(workspace)}  # Supply the job variables.
    status = main(list(arguments), stdout=output, environ=environ)  # Run the check with no real environment.
    return status, output.getvalue()  # Return both values for the assertions.


def pins_of(text: str) -> tuple[RequirementPin, ...]:
    """Return the pins of the default repository in one requirement text."""
    return find_pins(Path("requirements-dev.txt"), text, PATTERN)  # The path is a label only.


class TestFindPins:
    """Verify which lines hold a pin, and where the reference is."""

    def test_the_consumer_line_gives_one_pin_with_its_columns(self) -> None:
        pins = pins_of(f"pytest==9.0.0\n{PIN_LINE}\n")  # The pin is on line 2.
        assert len(pins) == 1, f"One pin line must give one pin: {pins!r}"  # Prove the count.
        pin = pins[0]  # Read the only pin.
        assert (pin.lineno, pin.ref) == (2, OLD), f"The line or the reference is wrong: {pin!r}"  # Prove the fields.
        assert pin.code[pin.start : pin.end] == OLD, "The columns must mark the reference."  # Prove the columns.

    def test_a_comment_holds_no_pin(self) -> None:
        text = f"# {PIN_LINE}\npytest==9.0.0  # was {URL}@{OLD}\n"  # A comment line and an end-of-line comment.
        assert pins_of(text) == (), "Pip ignores a comment, so the check must ignore it too."  # Prove no pin.

    def test_the_url_fragment_is_not_part_of_the_reference(self) -> None:
        pins = pins_of(f"-e {URL}@{OLD}#egg=misthelper-devtools\n")  # The editable form with an egg fragment.
        assert [pin.ref for pin in pins] == [OLD], f"The fragment leaked into the reference: {pins!r}"  # Prove it.

    def test_the_ssh_form_without_the_suffix_matches_in_any_case(self) -> None:
        line = (
            f"MistHelper-DevTools @ git+ssh://git@GitHub.com/JMorrison-Juniper/misthelper-devtools@{OLD}"  # SSH form.
        )
        assert [pin.ref for pin in pins_of(line)] == [OLD], "The SSH form must give a pin."  # Prove the match.

    def test_another_repository_holds_no_pin(self) -> None:
        text = (
            f"git+https://github.com/someone/misthelper-devtools.git@{OLD}\n"  # A fork with the same name.
            f"git+https://github.com/jmorrison-juniper/misthelper-devtools-extra.git@{OLD}\n"  # A longer name.
        )
        assert pins_of(text) == (), "A different repository must give no pin."  # Prove no pin.


class TestRequirementPin:
    """Verify the commit comparison and the new line text."""

    @pytest.mark.parametrize("ref", [OLD, OLD[:7], OLD.upper()])
    def test_a_full_or_abbreviated_commit_matches(self, ref: str) -> None:
        pin = pins_of(f"misthelper-devtools @ {URL}@{ref}")[0]  # Pin the commit in this form.
        assert pin.matches(OLD), f"The reference {ref!r} names the commit {OLD}."  # Prove the match.

    @pytest.mark.parametrize("ref", ["v0.5.2", "main", OLD[:6], NEW])
    def test_a_tag_a_branch_a_short_name_or_another_commit_does_not_match(self, ref: str) -> None:
        pin = pins_of(f"misthelper-devtools @ {URL}@{ref}")[0]  # Pin the reference in this form.
        assert not pin.matches(OLD), f"The reference {ref!r} must not match the commit {OLD}."  # Prove no match.

    def test_the_new_line_changes_the_reference_and_leaves_out_the_comment(self) -> None:
        line = f'misthelper-devtools @ {URL}@{OLD} ; python_version >= "3.13"  # v0.5.2'  # Markers and a comment.
        pin = pins_of(line)[0]  # Read the pin of the line.
        expected = f'misthelper-devtools @ {URL}@{NEW} ; python_version >= "3.13"'  # The new text keeps the markers.
        assert pin.replacement(NEW) == expected, f"The new text is wrong: {pin.replacement(NEW)!r}"  # Prove the text.
        assert pin.comment == "# v0.5.2", f"The comment must stay available for the message: {pin.comment!r}"


class TestMain:
    """Verify the report lines, the warning, and the exit status."""

    def test_a_pin_at_the_workflow_commit_writes_no_warning(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # The file of the consumer.
        requirements.write_text(f"{PIN_LINE}\n", encoding="utf-8")  # Pin the workflow commit.
        status, output = run_check("--commit", OLD, str(requirements), workspace=tmp_path)  # Run the check.
        assert status == 0, f"The check must pass. Output: {output!r}"  # Prove the status.
        expected = (
            "requirements-dev.txt line 1 installs jmorrison-juniper/misthelper-devtools "
            f"at {OLD}, as this workflow does.\n"
        )  # The line names the file, the line, and the commit.
        assert output == expected, f"The in-step line changed: {output!r}"  # Prove the exact line.

    def test_a_stale_pin_writes_one_warning_with_the_new_line(self, tmp_path: Path) -> None:
        requirements = tmp_path / "tools" / "requirements-dev.txt"  # A file below the repository root.
        requirements.parent.mkdir()  # Make the folder of the file.
        requirements.write_text(f"pytest==9.0.0\n{PIN_LINE}\n", encoding="utf-8")  # Pin the old commit on line 2.
        status, output = run_check("--commit", NEW, str(requirements), workspace=tmp_path)  # Run the check.
        message = (
            f"tools/requirements-dev.txt line 2 installs jmorrison-juniper/misthelper-devtools at {OLD}, "
            f"but this workflow runs commit {NEW}. Change the line to: misthelper-devtools @ {URL}@{NEW}"
        )  # The message names the file, the line, and the new text.
        title = ANNOTATION_TITLE  # The title holds no character that needs an escape.
        assert status == 0, f"A stale pin is a notice and must not fail the gate. Output: {output!r}"  # Prove it.
        assert output == f"::warning file=tools/requirements-dev.txt,line=2,title={title}::{message}\n", output

    def test_a_stale_pin_with_a_comment_names_the_comment_before_the_new_line(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # The form that MistCircuitStats uses.
        requirements.write_text(f"{PIN_LINE}  # v0.5.2\n", encoding="utf-8")  # Name the old release in a comment.
        status, output = run_check("--commit", NEW, str(requirements), workspace=tmp_path)  # Run the check.
        expected_end = (
            'The line also holds the comment "# v0.5.2". Keep the comment only if it is still correct. '
            f"Change the line to: misthelper-devtools @ {URL}@{NEW}\n"
        )  # The new text is last and holds no old release name.
        assert status == 0, f"A stale pin must not fail the gate. Output: {output!r}"  # Prove the status.
        assert output.endswith(expected_end), f"The comment sentence or the new text is wrong: {output!r}"

    def test_the_default_file_is_requirements_dev(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "requirements-dev.txt").write_text(f"{PIN_LINE}\n", encoding="utf-8")  # The default file.
        monkeypatch.chdir(tmp_path)  # Run the check from the repository root.
        status, output = run_check("--commit", NEW)  # Name no file.
        assert status == 0, f"The check must pass. Output: {output!r}"  # Prove the status.
        assert output.startswith("::warning file=requirements-dev.txt,line=1,"), output  # Prove the file was read.

    def test_a_missing_file_gives_a_note_and_no_warning(self, tmp_path: Path) -> None:
        missing = tmp_path / "requirements-dev.txt"  # The file does not exist.
        status, output = run_check("--commit", NEW, str(missing))  # Run the check.
        assert status == 0, f"A missing file must not fail the gate. Output: {output!r}"  # Prove the status.
        assert f"The file {missing.as_posix()} does not exist." in output, output  # Prove the note.
        assert "The check has nothing to compare." in output, output  # Prove the end line.
        assert "::warning" not in output, output  # Prove no warning.

    def test_a_file_without_a_pin_gives_no_warning(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # The file of a consumer that installs no pin.
        requirements.write_text("ruff==0.15.0\npytest==9.0.0\n", encoding="utf-8")  # Pin other tools only.
        status, output = run_check("--commit", NEW, str(requirements))  # Run the check.
        expected = (
            "No requirement line installs jmorrison-juniper/misthelper-devtools from Git. "
            "The check has nothing to compare.\n"
        )
        assert (status, output) == (0, expected), output  # Prove the status and the exact line.

    @pytest.mark.parametrize("commit", ["", "   ", "v0.5.2", "abc12"])
    def test_a_value_that_is_no_commit_name_reads_no_file(self, commit: str, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # A stale pin that the check must not reach.
        requirements.write_text(f"{PIN_LINE}\n", encoding="utf-8")  # Pin the old commit.
        status, output = run_check("--commit", commit, str(requirements))  # Run the check.
        assert status == 0, f"A missing commit must not fail the gate. Output: {output!r}"  # Prove the status.
        assert output == f"The workflow commit {commit!r} is not a commit name. The check has nothing to compare.\n"

    def test_an_unreadable_file_gives_a_note_and_the_next_file_is_read(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.txt"  # A file that is not UTF-8 text.
        broken.write_bytes(b"\xff\xfe\xfa not utf-8\n")  # Write bytes that UTF-8 cannot decode.
        good = tmp_path / "requirements-dev.txt"  # A file with a stale pin.
        good.write_text(f"{PIN_LINE}\n", encoding="utf-8")  # Pin the old commit.
        status, output = run_check("--commit", NEW, str(broken), str(good), workspace=tmp_path)  # Read both.
        assert status == 0, f"An unreadable file must not fail the gate. Output: {output!r}"  # Prove the status.
        assert f"The check could not read {broken.as_posix()}:" in output, output  # Prove the note.
        assert "::warning file=requirements-dev.txt,line=1," in output, output  # Prove the next file was read.

    def test_a_byte_order_mark_is_not_part_of_the_line(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # A file that an editor saved with a byte order mark.
        requirements.write_text(f"{PIN_LINE}\n", encoding="utf-8-sig")  # Write the mark before the pin.
        status, output = run_check("--commit", NEW, str(requirements), workspace=tmp_path)  # Run the check.
        assert status == 0, output  # Prove the status.
        assert output.rstrip("\n").endswith(f"Change the line to: misthelper-devtools @ {URL}@{NEW}"), output

    def test_the_repository_option_reads_the_pins_of_a_fork(self, tmp_path: Path) -> None:
        requirements = tmp_path / "requirements-dev.txt"  # A fork pins its own repository.
        fork_line = f"misthelper-devtools @ git+https://github.com/someone/fork.git@{OLD}\n"  # The pin of the fork.
        requirements.write_text(fork_line, encoding="utf-8")  # Write the pin of the fork.
        status, output = run_check("--commit", OLD, "--repository", "someone/fork", str(requirements))  # Run it.
        assert status == 0, output  # Prove the status.
        assert "installs someone/fork at" in output and "as this workflow does." in output, output  # Prove the match.


class TestAnnotationHelpers:
    """Verify the escape rules and the annotation path."""

    def test_the_message_escape(self) -> None:
        assert escape_data("50%\r\nnext") == "50%25%0D%0Anext", "The message escape changed."  # Prove the rule.

    def test_the_property_escape(self) -> None:
        assert escape_property("a:b,c%d\n") == "a%3Ab%2Cc%25d%0A", "The property escape changed."  # Prove the rule.

    def test_a_file_outside_the_workspace_keeps_its_path(self, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere" / "requirements-dev.txt"  # A file outside the workspace.
        workspace = tmp_path / "workspace"  # The repository root of the job.
        assert annotation_path(outside, str(workspace)) == outside.as_posix(), "The path must stay the same."

    def test_no_workspace_keeps_the_path(self) -> None:
        path = Path("sub") / "requirements-dev.txt"  # A path relative to the working directory.
        assert annotation_path(path, None) == "sub/requirements-dev.txt", "The path must use forward slashes."

    def test_a_relative_path_counts_from_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = tmp_path / "service"  # The working directory that a caller gives to the radon job.
        service.mkdir()  # Create the directory below the workspace.
        monkeypatch.chdir(service)  # Run from that directory, as the job step does.
        path = annotation_path(Path("requirements-dev.txt"), str(tmp_path))  # The default file of the command.
        assert path == "service/requirements-dev.txt", f"The path must count from the workspace root: {path!r}"
