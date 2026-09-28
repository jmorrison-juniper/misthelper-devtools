"""Tests for the capture-log-baseline tool.

The tool renders the log calls at fixed source lines and writes the result
to a JSON baseline. A consumer repository then compares its current log
output with that baseline. The comparison itself stays in the consumer,
because only the consumer has the source file and the baseline. These tests
check the line lookup and the command line, with small sources in a
temporary directory.
"""

from __future__ import annotations  # Enable PEP 604 union syntax on Python 3.13.

import json  # Read the baseline that the command writes.
import sys  # Test CLI defaults without inheriting the pytest command line.
from pathlib import Path  # Portable filesystem access on Windows + POSIX.

import libcst as cst  # AST-preserving CST library for re-rendering current source.
import pytest  # Test framework used across the project.

import misthelper_devtools.capture_log_baseline as capture_log_baseline  # Module import lets tests patch fixtures.
from misthelper_devtools.capture_log_baseline import (  # Reuse the rendering primitives.
    _extract_msg_and_args,
    _index_calls_by_line,
    _LineCallCollector,
    _render_call_at_line,
)


def test_indexed_render_chooses_first_call_when_several_start_on_same_line() -> None:
    """Confirm the indexed lookup keeps the old first-call rule."""
    module = cst.parse_module(  # Build a same-line call case.
        'import logging\nlogging.info("outer %s", str("value"))\n'
    )
    call_index = _index_calls_by_line(module)  # Build the candidate line index.
    rendered = _render_call_at_line(module, 2, {"str": str}, call_index)  # Render from the indexed path.
    assert rendered == "outer value"  # The logging call must win over the nested str call.


def test_indexed_render_matches_direct_line_collector() -> None:
    """Confirm the indexed path returns the same call as the old collector."""
    module = cst.parse_module('import logging\nlogging.info("stable %s", "text")\n')  # Small stable fixture.
    direct_collector = _LineCallCollector(2)  # Use the old line collector as the oracle.
    cst.MetadataWrapper(module).visit(direct_collector)  # Collect the direct call for comparison.
    call_index = _index_calls_by_line(module)  # Build the new reusable line index.
    found_call = direct_collector.found  # The call that the old collector matched on line 2.
    assert isinstance(found_call, cst.Call)  # The fixture line holds exactly one logging call.
    direct_msg, direct_args = _extract_msg_and_args(found_call, {})  # Render the old path.
    assert (direct_msg, direct_args) == ("stable %s", ("text",))  # The oracle must read the fixture call.
    index_msg, index_args = _extract_msg_and_args(call_index[2][0], {})  # Render the indexed path.
    assert (index_msg, index_args) == (direct_msg, direct_args)  # The indexed call must match the old lookup.


def test_capture_main_uses_fixture_source_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm one capture run reads a fixture source override and the CLI source."""
    default_source = tmp_path / "default_source.py"  # Source for fixtures without a `source` override.
    override_source = tmp_path / "override_source.py"  # Source for fixtures that set `source`.
    output_path = tmp_path / "baseline.json"  # Capture output stays inside pytest's workspace.
    default_source.write_text('import logging\nlogging.info("default %s", "site")\n', encoding="utf-8")  # Default case.
    override_source.write_text(
        'import logging\nlogging.info(f"override {value}")\n', encoding="utf-8"
    )  # Override case.
    monkeypatch.chdir(tmp_path)  # Make relative fixture source paths resolve like a CLI run.
    monkeypatch.setattr(  # Replace the module fixtures with a small, deterministic catalog.
        capture_log_baseline,
        "FIXTURE_SITES",
        [
            {"site_id": "default", "line": 2, "inputs": {}, "pattern": "already_lazy_negative_control"},
            {
                "site_id": "override",
                "line": 2,
                "source": "override_source.py",
                "inputs": {"value": "site"},
                "pattern": "plain_fstring",
            },
        ],
    )
    exit_code = capture_log_baseline.main(
        ["--source", "default_source.py", "--output", str(output_path)]
    )  # Run the CLI path.
    captured = json.loads(output_path.read_text(encoding="utf-8"))  # Read the produced baseline.
    assert exit_code == 0  # The capture tool must keep the success exit code.
    assert captured == {  # Both fixtures must resolve into the same output shape.
        "default": {"line": 2, "pattern": "already_lazy_negative_control", "rendered": "default site"},
        "override": {"line": 2, "pattern": "plain_fstring", "rendered": "override site"},
    }


def test_capture_main_reports_missing_fixture_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Confirm a missing fixture source reports a clear error and exits with 1."""
    default_source = tmp_path / "default_source.py"  # Existing source proves the override is the missing file.
    output_path = tmp_path / "baseline.json"  # Capture output path should stay absent on read failure.
    default_source.write_text('import logging\nlogging.info("default")\n', encoding="utf-8")  # Valid default source.
    monkeypatch.chdir(tmp_path)  # Make the missing override relative to this test workspace.
    monkeypatch.setattr(  # Replace the module fixtures with one missing override.
        capture_log_baseline,
        "FIXTURE_SITES",
        [{"site_id": "missing", "line": 2, "source": "missing_source.py", "inputs": {}, "pattern": "plain_fstring"}],
    )
    with caplog.at_level("ERROR"):  # Capture the operator-facing read failure.
        exit_code = capture_log_baseline.main(
            ["--source", "default_source.py", "--output", str(output_path)]
        )  # Run CLI path.
    assert exit_code == 1  # Missing source files must keep the failure exit code.
    assert "failed to read missing_source.py" in caplog.text  # The error must name the missing fixture source.
    assert not output_path.exists()  # A failed read must not write a partial baseline.


def test_capture_main_empty_arguments_use_default_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm an empty argument list uses the documented default output path."""
    monkeypatch.chdir(tmp_path)  # Keep the default output away from the committed fixture.
    monkeypatch.setattr(capture_log_baseline, "FIXTURE_SITES", [])  # Avoid reading the large production file.
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)  # Create the default output parent.
    exit_code = capture_log_baseline.main([])  # Empty arguments take the default source and output values.
    output_path = tmp_path / "tests" / "fixtures" / "issue_429_log_baseline.json"  # Default output path.
    assert exit_code == 0  # The default path succeeds when no fixture rows need a source read.
    assert json.loads(output_path.read_text(encoding="utf-8")) == {}  # No fixture rows produce an empty baseline.


def test_capture_main_none_arguments_use_sys_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm a None argument list reads sys.argv and uses default output."""
    monkeypatch.chdir(tmp_path)  # Keep the default output away from the committed fixture.
    monkeypatch.setattr(capture_log_baseline, "FIXTURE_SITES", [])  # Avoid reading the large production file.
    monkeypatch.setattr(sys, "argv", ["capture_log_baseline"])  # Keep pytest flags out of this CLI call.
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)  # Create the default output parent.
    exit_code = capture_log_baseline.main(None)  # None is the module execution path.
    output_path = tmp_path / "tests" / "fixtures" / "issue_429_log_baseline.json"  # Default output path.
    assert exit_code == 0  # The default path succeeds when no fixture rows need a source read.
    assert json.loads(output_path.read_text(encoding="utf-8")) == {}  # No fixture rows produce an empty baseline.
