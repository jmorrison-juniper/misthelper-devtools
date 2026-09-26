"""Unit tests for the radon complexity gate."""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import io  # Build text streams for the gate input and output.
import json  # Encode the sample radon reports.
import subprocess  # nosec B404  # WHY: run the earlier inline gate script with the current interpreter only.
import sys  # Name the current interpreter for the parity run.
from pathlib import Path  # Write a saved report for the --input option.
from typing import Any  # Type the sample radon reports.

from tools.complexity_gate import PASS_MESSAGE, ComplexityGate, main

# The inline script that MistCircuitStats ran before this tool. The parity test
# runs it and compares its output with the output of the tool.
EARLIER_INLINE_SCRIPT = """
import json, sys
data = json.load(sys.stdin)
bad = []
for path, blocks in data.items():
    for b in blocks:
        if b['complexity'] > 15:
            bad.append(f"{path}:{b['lineno']} {b['name']} CC={b['complexity']}")
if bad:
    print('Cyclomatic complexity violations (>15):')
    for item in bad:
        print(f'  {item}')
    sys.exit(1)
print('All functions within complexity threshold.')
"""


def block(name: str, lineno: int, complexity: int, kind: str = "function", **extra: Any) -> dict[str, Any]:
    """Return one radon block in the form that radon 6 writes."""
    entry: dict[str, Any] = {"type": kind, "rank": "A", "lineno": lineno, "complexity": complexity}  # Core fields.
    entry.update({"endline": lineno + 5, "name": name, "col_offset": 0})  # Add the position fields.
    entry.update(extra)  # Add the class, method, or closure fields of the caller.
    return entry  # Return the block to the sample builder.


def sample_report() -> dict[str, Any]:
    """Return a radon report with a class, a method, a closure, and two files."""
    method = block("m", 6, 17, kind="method", classname="K", closures=[])  # A method above the limit.
    closure = block("inner", 15, 30, closures=[])  # A closure above the limit, which radon nests only.
    return {
        "app.py": [
            block("K", 5, 18, kind="class", methods=[method]),  # Radon lists the class with its methods.
            method,  # Radon lists each method at the top level too.
            block("top", 1, 15, closures=[]),  # A function at the limit, which passes.
            block("outer", 14, 1, closures=[closure]),  # A function whose closure is above the limit.
        ],
        "lib/util.py": [block("helper", 3, 16, closures=[])],  # A second file with one violation.
    }  # Return the report in radon order.


def run_gate(report: object, *arguments: str) -> tuple[int, str]:
    """Run the gate main function on one report and return the status and the output."""
    output = io.StringIO()  # Capture the report lines.
    status = main(list(arguments), stdout=output, stdin=io.StringIO(json.dumps(report)))  # Pipe the report in.
    return status, output.getvalue()  # Return both values for the assertions.


class TestComplexityGate:
    """Verify the violation rules, the report form, and the exit status."""

    def test_clean_report_passes(self) -> None:
        report = {"app.py": [block("small", 1, 3, closures=[])]}  # One block far below the limit.
        status, output = run_gate(report)  # Run the gate with the default limit.
        assert status == 0, f"A clean report must pass. Output: {output!r}"  # Prove the pass status.
        assert output == PASS_MESSAGE + "\n", f"The pass line changed: {output!r}"  # Prove the exact pass line.

    def test_violations_use_the_earlier_report_form(self) -> None:
        status, output = run_gate(sample_report())  # Run the gate on the mixed sample.
        expected = (
            "Cyclomatic complexity violations (>15):\n"
            "  app.py:5 K CC=18\n"
            "  app.py:6 m CC=17\n"
            "  lib/util.py:3 helper CC=16\n"
        )  # The class and the method are top-level entries, so both fail.
        assert status == 1, f"A violation must fail the gate. Output: {output!r}"  # Prove the fail status.
        assert output == expected, f"The report form changed: {output!r}"  # Prove the exact report lines.

    def test_value_equal_to_the_limit_passes(self) -> None:
        result = ComplexityGate(15).evaluate({"app.py": [block("edge", 2, 15, closures=[])]})  # A block at the limit.
        assert result.passed, f"A block at the limit must pass: {result}"  # Prove the strict greater-than rule.
        assert result.checked_count == 1, f"The gate must count the block: {result}"  # Prove the measured count.

    def test_closures_stay_out_of_the_check(self) -> None:
        closure = block("inner", 15, 40, closures=[])  # A closure far above the limit.
        report = {"app.py": [block("outer", 14, 2, closures=[closure])]}  # Radon nests the closure only.
        result = ComplexityGate(15).evaluate(report)  # Check the report with the default limit.
        assert result.passed, f"The earlier script checked top-level blocks only: {result}"  # Prove the parity.

    def test_custom_limit(self) -> None:
        status, output = run_gate({"app.py": [block("f", 1, 11, closures=[])]}, "--max", "10")  # MistHelper limit.
        assert status == 1, f"A block above a limit of 10 must fail. Output: {output!r}"  # Prove the limit option.
        assert output.startswith("Cyclomatic complexity violations (>10):\n"), output  # Prove the header value.

    def test_radon_error_entry_fails_with_a_clear_line(self) -> None:
        report = {"bad.py": {"error": "invalid syntax (<unknown>, line 1)"}}  # The radon form for a parse fault.
        status, output = run_gate(report)  # Run the gate on the error entry.
        expected = "Files that radon could not analyze:\n  bad.py: invalid syntax (<unknown>, line 1)\n"  # Error.
        assert status == 1, f"A file that radon could not analyze must fail. Output: {output!r}"  # Prove the fail.
        assert output == expected, f"The error section changed: {output!r}"  # Prove the exact error lines.

    def test_bad_json_returns_status_two(self) -> None:
        output = io.StringIO()  # Capture any report line.
        status = main([], stdout=output, stdin=io.StringIO("not json"))  # Pipe text that is not JSON.
        assert status == 2, f"A bad input must return status 2, not {status}"  # Prove the input fault status.
        assert output.getvalue() == "", f"A bad input must print no report: {output.getvalue()!r}"  # Prove no report.

    def test_report_that_is_not_an_object_returns_status_two(self) -> None:
        status, output = run_gate([1, 2, 3])  # A JSON list is not a radon report.
        assert status == 2, f"A list report must return status 2, not {status}. Output: {output!r}"  # Prove status.

    def test_limit_below_one_returns_status_two(self) -> None:
        status, output = run_gate({}, "--max", "0")  # A limit of zero fails every block.
        assert status == 2, f"A limit below one must return status 2, not {status}. Output: {output!r}"  # Prove it.

    def test_input_option_reads_a_saved_report(self, tmp_path: Path) -> None:
        saved = tmp_path / "radon.json"  # Name a saved report file.
        saved.write_text(json.dumps(sample_report()), encoding="utf-8")  # Save the mixed sample.
        output = io.StringIO()  # Capture the report lines.
        status = main(["--input", str(saved)], stdout=output, stdin=io.StringIO(""))  # Read the file, not stdin.
        assert status == 1, f"The saved sample has violations. Output: {output.getvalue()!r}"  # Prove the read.
        assert "  lib/util.py:3 helper CC=16" in output.getvalue(), output.getvalue()  # Prove the file content.

    def test_output_matches_the_earlier_inline_script(self) -> None:
        for report in (sample_report(), {"app.py": [block("small", 1, 3, closures=[])]}):  # A fail and a pass case.
            payload = json.dumps(report)  # Encode the report once for both runs.
            earlier = subprocess.run(  # nosec B603  # WHY: a fixed script runs with the current interpreter.
                [sys.executable, "-c", EARLIER_INLINE_SCRIPT],
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=60,
            )  # Run the earlier inline gate script.
            status, output = run_gate(report)  # Run the tool on the same report.
            assert status == earlier.returncode, f"Status {status} != {earlier.returncode}"  # Prove the same status.
            assert output == earlier.stdout, f"Output {output!r} != {earlier.stdout!r}"  # Prove the same output.
