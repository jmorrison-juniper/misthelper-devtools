"""Tests for the module-level symbol table comparator.

Issue #1796 reports that an automated comment sweep deleted a live declaration.
The declaration used an annotated assignment, which Python parses as
``ast.AnnAssign``. The annotated assignment case is therefore the most important
test in this file. A comparator that matches ``ast.Assign`` alone reports a clean
result on the exact defect that it exists to catch.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from pathlib import Path  # Builds the temporary file path for the syntax error case.

import pytest  # Supplies the tmp_path fixture and the parametrize marker.

from tools.symbol_diff.comparator import SymbolDelta, SymbolTableComparator  # The code under test.


@pytest.fixture(name="comparator")
def fixture_comparator() -> SymbolTableComparator:
    """Return a fresh comparator for one test."""
    return SymbolTableComparator()  # The class holds no state, so a plain instance is enough.


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("FAST_MODE_ENABLED: bool = False", "FAST_MODE_ENABLED"),  # The issue #1796 defect shape.
        ("PLAIN_NAME = 1", "PLAIN_NAME"),  # A plain assignment.
        ("class SampleClass:\n    pass", "SampleClass"),  # A class definition.
        ("def sample_function():\n    pass", "sample_function"),  # A function definition.
        ("async def sample_coroutine():\n    pass", "sample_coroutine"),  # An async function definition.
        ("import logging", "logging"),  # A plain import binds the root name.
        ("import os.path", "os"),  # A dotted import binds the root name only.
        ("import logging as log_module", "log_module"),  # An aliased import binds the alias.
        ("from pathlib import Path", "Path"),  # A from-import binds the imported name.
        ("FIRST, SECOND = 1, 2", "SECOND"),  # An unpacking target binds every nested name.
    ],
)
def test_collect_names_finds_each_definition_shape(
    comparator: SymbolTableComparator, source: str, expected: str
) -> None:
    """Every module-level definition shape contributes its bound name."""
    names = comparator.collect_names(source, "sample.py")  # Parse the one-statement module.
    assert names is not None  # A valid source always parses.
    assert expected in names  # The parser reports the bound name.


def test_collect_names_ignores_a_nested_definition(comparator: SymbolTableComparator) -> None:
    """A name bound inside a function is not a module-level name."""
    source = "def outer():\n    inner_name = 1\n    return inner_name"  # inner_name is a local name.
    names = comparator.collect_names(source, "sample.py")  # Parse the module.
    assert names == {"outer"}  # Only the function name reaches module level.


def test_collect_names_reports_a_syntax_error_without_raising(
    comparator: SymbolTableComparator, capsys: pytest.CaptureFixture[str]
) -> None:
    """An uncompilable file yields None and a message that names the file and the line."""
    names = comparator.collect_names("--- not python ---", "broken.py")  # Defect 2 of issue #1796.
    assert names is None  # None means "unreadable", which differs from an empty set.
    captured = capsys.readouterr().out  # Read the printed message.
    assert "broken.py" in captured  # The message names the file.
    assert "line" in captured  # The message names the line.


def test_compare_reports_a_lost_annotated_global(comparator: SymbolTableComparator) -> None:
    """A deleted annotated declaration appears in the lost names."""
    base = comparator.collect_names("FAST_MODE_ENABLED: bool = False\n", "base")  # Before the sweep.
    head = comparator.collect_names("\n", "head")  # After the sweep deleted the declaration.
    assert base is not None and head is not None  # Both sides parse.
    delta = comparator.compare(base, head, "MistHelper.py")  # Compare the two symbol tables.
    assert delta.lost == ("FAST_MODE_ENABLED",)  # The comparator names the lost declaration.
    assert delta.added == ()  # The sweep added no name.


def test_compare_reports_an_added_name(comparator: SymbolTableComparator) -> None:
    """A new module-level name appears in the added names, because it can shadow an import."""
    base = comparator.collect_names("import logging\n", "base")  # Before the change.
    head = comparator.collect_names("import logging\nlogging_helper = 1\n", "head")  # After the change.
    assert base is not None and head is not None  # Both sides parse.
    delta = comparator.compare(base, head, "sample.py")  # Compare the two symbol tables.
    assert delta.added == ("logging_helper",)  # The comparator names the added declaration.
    assert delta.lost == ()  # The change lost no name.


def test_report_returns_zero_when_no_name_changed(
    comparator: SymbolTableComparator, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clean comparison returns exit code 0, so a sweep continues."""
    exit_code = comparator.report([SymbolDelta(path="sample.py")])  # No lost and no added name.
    assert exit_code == 0  # Exit code 0 lets the sweep continue.
    assert "no module-level name changed" in capsys.readouterr().out  # The clean result is stated.


def test_report_returns_one_and_names_the_lost_symbol(
    comparator: SymbolTableComparator, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lost name returns exit code 1 and prints the name, so a sweep stops."""
    delta = SymbolDelta(path="MistHelper.py", lost=("FAST_MODE_ENABLED",))  # The issue #1796 case.
    exit_code = comparator.report([delta])  # Print the report and read the exit code.
    assert exit_code == 1  # Exit code 1 stops a sweep that changed the symbol table.
    captured = capsys.readouterr().out  # Read the printed report.
    assert "MistHelper.py" in captured  # The report names the file.
    assert "FAST_MODE_ENABLED" in captured  # The report names the lost declaration.


def test_read_revision_returns_none_for_an_unknown_path(comparator: SymbolTableComparator) -> None:
    """git cannot read a path that no revision holds, so the method returns None."""
    text = comparator.read_revision("HEAD", Path("no_such_file_for_symbol_diff.py"))  # Unknown path.
    assert text is None  # The method reports the failure instead of raising.


def test_read_revision_reads_a_tracked_file(comparator: SymbolTableComparator) -> None:
    """git reads a tracked file at HEAD, which proves the base side of a comparison."""
    text = comparator.read_revision("HEAD", Path("pyproject.toml"))  # A file every revision holds.
    assert text is not None  # git resolved the revision and the path.
    assert "[tool.ruff]" in text  # The text is the project configuration, not an error message.


def test_run_reports_a_clean_tree_for_an_unchanged_file(
    comparator: SymbolTableComparator, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tracked file that the work tree did not change holds the same names, so the run exits 0."""
    exit_code = comparator.run("HEAD", ["tools/ste_linter/scoring.py"])  # A tracked, unchanged file.
    captured = capsys.readouterr().out  # Read the printed report.
    assert exit_code == 0  # An unchanged file changes no module-level name.
    assert "no module-level name changed" in captured  # The run compared the file rather than skipping it.
    assert "cannot read" not in captured  # Prove that git resolved the path, so the pass is not vacuous.


def test_run_skips_a_path_that_the_work_tree_lacks(
    comparator: SymbolTableComparator, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An unreadable path yields no delta, so the run reports a clean result."""
    missing = tmp_path / "absent.py"  # A path that no file backs.
    exit_code = comparator.run("HEAD", [str(missing)])  # Compare a path that nobody can read.
    assert exit_code == 0  # A skipped path never invents a lost name.
    assert "cannot read" in capsys.readouterr().out  # The run states why it skipped the path.
