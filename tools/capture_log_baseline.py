"""Capture a frozen baseline of rendered log output for issue #429.

The codemod for `CONV-LOG-FSTRING` (#429) rewrites every eager
`logging.<level>(f"...")` call in `MistHelper.py` into the lazy
`logging.<level>("...%s...", arg)` form. The parity test asserts that
the rendered string is byte-identical pre- and post-refactor.

This script:

1. Reads a list of fixture sites (line number, test inputs).
2. For each site, locates the `logging.*` call in `MistHelper.py` via
   libcst, evaluates it under a synthetic capture handler, and records
   the rendered `LogRecord.getMessage()` string.
3. Writes the resulting `{site_id: {...}}` map to
   `tests/fixtures/issue_429_log_baseline.json`.

The script is re-runnable: running it again on `MistHelper.py` after the
codemod has converted a line to lazy form MUST produce the same rendered
string. That property is what the parity test verifies in CI.

Run from the repository root:

    python tools/capture_log_baseline.py --output tests/fixtures/issue_429_log_baseline.json

Issue: https://github.com/jmorrison-juniper/MistHelper/issues/429
"""

from __future__ import annotations  # Allow PEP 604 unions on Python 3.13.

import argparse  # Stdlib CLI argument parsing.
import json  # Output is JSON for deterministic, diff-friendly storage.
import logging  # We render messages through real LogRecord.getMessage().
from pathlib import Path  # Portable path handling on Windows + POSIX.
from typing import Any  # Type hint for the heterogeneous test-input dicts.

import libcst as cst  # AST-preserving CST library for safe line extraction.
from libcst.metadata import PositionProvider  # Resolves 1-based line numbers.

FIXTURE_SITES: list[dict[str, Any]] = [  # Minimal but representative baseline.
    {  # Plain f-string near the top of the file (Python version warning).
        "site_id": "L315",
        "line": 315,
        "inputs": {"version_str": "3.10.0", "required_str": "3.13"},
        "pattern": "plain_fstring",
    },
    {  # Negative control: already lazy on `main`; must remain unchanged after codemod.
        "site_id": "L801",
        "line": 801,
        "inputs": {},
        "pattern": "already_lazy_negative_control",
    },
    {  # Attribute access in interpolation (sys.executable).
        "site_id": "L1343",
        "line": 1343,
        "inputs": {"sys_executable": "/usr/bin/python3"},
        "pattern": "attribute_access",
    },
    {  # Numeric format spec `.1f`.
        "site_id": "L11634",
        "line": 11634,
        "inputs": {"elapsed": 4.567},
        "pattern": "format_spec_1f",
    },
    {  # G003 string concatenation (the codemod merges into a lazy template).
        "site_id": "L6120",
        "line": 6120,
        "inputs": {"table_text": "row1\nrow2"},
        "pattern": "g003_concat",
    },
    {  # Multi-argument f-string with arithmetic on substitution.
        "site_id": "L6988",
        "line": 6988,
        "inputs": {
            "failed_device_id": "dev-abc",
            "delay": 2.5,
            "attempt": 0,
            "max_retries": 3,
        },
        "pattern": "format_spec_2f_with_arithmetic",
    },
]


def _render_call_at_line(module: cst.Module, target_line: int, inputs: dict[str, Any]) -> str:
    """Locate the logging call at target_line and render its message text.

    Returns the string that ``LogRecord.getMessage()`` would produce when
    the logging framework formats the call with the supplied inputs.
    """
    logging.debug("rendering call at line %d", target_line)  # Action log before search.
    wrapper = cst.MetadataWrapper(module)  # MetadataWrapper enables PositionProvider.
    collector = _LineCallCollector(target_line)  # Per-call-line collector instance.
    wrapper.visit(collector)  # Walk the module; collector records the matching Call.
    if collector.found is None:  # Defensive: line may have moved between captures.
        msg = f"no logging call found at line {target_line}"  # Diagnostic for operator.
        raise LookupError(msg)  # Caller will surface this via the CLI exit code.
    msg_text, args_tuple = _extract_msg_and_args(collector.found, inputs)  # Pull (msg, args).
    record = logging.LogRecord(  # Synthesize a record exactly like the framework would.
        name="issue429_capture",
        level=logging.INFO,
        pathname=__file__,
        lineno=target_line,
        msg=msg_text,
        args=args_tuple,
        exc_info=None,
    )
    rendered = record.getMessage()  # Same path the real logger uses; no shortcuts.
    logging.debug("rendered line %d -> %r", target_line, rendered)  # Confirm in action log.
    return rendered  # The frozen baseline value for this site.


def _extract_msg_and_args(  # Convert a libcst Call into (msg, args) for LogRecord.
    call: cst.Call, inputs: dict[str, Any]
) -> tuple[str, tuple[Any, ...]]:
    """Best-effort eval of the call's first positional arg + remaining args.

    Phase-0 supports plain string literals, f-strings with only simple
    Name / Attribute substitutions, and string concatenation. Anything
    more complex raises ValueError so the operator can add coverage.
    """
    if not call.args:  # Defensive: malformed source would already have failed parse.
        return ("", ())  # Empty call -> empty message and no args.
    msg_arg = call.args[0].value  # The first positional argument is the message.
    extra_args = tuple(  # Remaining positional args become the LogRecord args tuple.
        _eval_simple(a.value, inputs) for a in call.args[1:] if a.keyword is None
    )
    if isinstance(msg_arg, cst.SimpleString):  # Already-lazy form: plain string literal.
        return (msg_arg.evaluated_value, extra_args)  # Use libcst's evaluated_value helper.
    if isinstance(msg_arg, cst.ConcatenatedString):  # Adjacent implicit string concatenation.
        return (_render_concatenated_string(msg_arg, inputs), extra_args)  # Render joined parts.
    if isinstance(msg_arg, cst.FormattedString):  # Eager f-string: render with inputs.
        return (_render_fstring(msg_arg, inputs), extra_args)  # Pre-render exactly like Python.
    if isinstance(msg_arg, cst.BinaryOperation):  # G003 string concatenation.
        return (_render_concat(msg_arg, inputs), extra_args)  # Render the full concatenation.
    msg = f"unsupported message expression: {type(msg_arg).__name__}"  # Tell the operator.
    raise ValueError(msg)  # Phase-1 extensions will widen the supported set.


def _render_concatenated_string(node: cst.ConcatenatedString, inputs: dict[str, Any]) -> str:
    """Render two implicitly-concatenated string nodes (e.g. `"a" "b"` or `"a" f"b"`)."""
    left = _render_string_like(node.left, inputs)  # Resolve the left half.
    right = _render_string_like(node.right, inputs)  # Resolve the right half.
    return left + right  # Implicit Python concatenation = direct string join.


def _render_string_like(node: cst.BaseExpression, inputs: dict[str, Any]) -> str:
    """Render any string-typed expression (Simple, Formatted, Concatenated)."""
    if isinstance(node, cst.SimpleString):  # Plain literal.
        return node.evaluated_value  # libcst already parsed the literal value.
    if isinstance(node, cst.FormattedString):  # Nested f-string.
        return _render_fstring(node, inputs)  # Reuse the f-string renderer.
    if isinstance(node, cst.ConcatenatedString):  # Recursive: a "b" "c" "d" chains.
        return _render_concatenated_string(node, inputs)  # Reuse this function recursively.
    msg = f"unsupported string-like node: {type(node).__name__}"  # Diagnostic message.
    raise ValueError(msg)  # Forces operator to extend support before silent miscompare.


def _render_fstring(node: cst.FormattedString, inputs: dict[str, Any]) -> str:
    """Render a libcst FormattedString to its evaluated text using inputs."""
    parts: list[str] = []  # Accumulate each literal / interpolated part.
    for part in node.parts:  # Walk every part of the f-string.
        if isinstance(part, cst.FormattedStringText):  # Literal segment between {}.
            parts.append(part.value)  # Pass through unchanged.
        elif isinstance(part, cst.FormattedStringExpression):  # Expression in {}.
            value = _eval_simple(part.expression, inputs)  # Evaluate against inputs.
            if part.format_spec is not None:  # Format spec like ":.2f" present.
                spec = "".join(  # Reassemble the format spec text from its parts.
                    p.value for p in part.format_spec if isinstance(p, cst.FormattedStringText)
                )
                parts.append(format(value, spec))  # Apply via builtin format().
            elif part.conversion == "r":  # Repr conversion (`!r`).
                parts.append(repr(value))  # Match Python f-string semantics.
            elif part.conversion == "s":  # Str conversion (`!s`).
                parts.append(str(value))  # Match Python f-string semantics.
            elif part.conversion == "a":  # Ascii conversion (`!a`).
                parts.append(ascii(value))  # Match Python f-string semantics.
            else:  # No conversion, no format spec.
                parts.append(format(value))  # Default formatting (same as str() for many types).
        else:  # Unknown part subclass -- be loud.
            msg = f"unsupported fstring part: {type(part).__name__}"  # Diagnostic message.
            raise ValueError(msg)  # Surfaces unrecognized libcst constructs immediately.
    return "".join(parts)  # Stitch together exactly like Python would.


def _render_concat(node: cst.BinaryOperation, inputs: dict[str, Any]) -> str:
    """Render a G003 string concatenation expression (`"a=" + str(a)`)."""
    if not isinstance(node.operator, cst.Add):  # Only `+` counts as concatenation here.
        msg = f"non-add binary op in message: {type(node.operator).__name__}"  # Tell operator.
        raise ValueError(msg)  # Phase-1 can extend if needed.
    left = _eval_simple(node.left, inputs)  # Evaluate the left operand.
    right = _eval_simple(node.right, inputs)  # Evaluate the right operand.
    return f"{left}{right}"  # Python `+` on str is the same as string concatenation.


def _eval_simple(node: cst.BaseExpression, inputs: dict[str, Any]) -> Any:
    """Evaluate a narrow subset of libcst expressions against the inputs dict."""
    if isinstance(node, cst.SimpleString):  # String literal.
        return node.evaluated_value  # libcst pre-evaluates the Python literal for us.
    if isinstance(node, cst.Integer):  # Integer literal.
        return int(node.value)  # Parse the literal value.
    if isinstance(node, cst.Float):  # Float literal.
        return float(node.value)  # Parse the literal value.
    if isinstance(node, cst.Name):  # Bare name -> lookup in inputs dict.
        if node.value not in inputs:  # Missing input is an operator error, not a silent miss.
            msg = f"input '{node.value}' not provided in fixture inputs"  # Be precise.
            raise KeyError(msg)  # Force the operator to add the missing input.
        return inputs[node.value]  # Return the operator-supplied value.
    if isinstance(node, cst.Attribute):  # `obj.attr` access.
        base = _eval_simple(node.value, inputs)  # Recursively resolve the base object.
        return getattr(base, node.attr.value)  # Standard attribute access semantics.
    if isinstance(node, cst.Call):  # Function / method call like `str(x)` or `table.get_string()`.
        func = _eval_simple(node.func, inputs)  # Resolve the callable.
        call_args = [_eval_simple(a.value, inputs) for a in node.args if a.keyword is None]  # Positional args.
        return func(*call_args)  # Invoke; raises naturally if signature mismatches.
    if isinstance(node, cst.BinaryOperation):  # Allow simple arithmetic like `attempt + 1`.
        left = _eval_simple(node.left, inputs)  # Resolve left operand.
        right = _eval_simple(node.right, inputs)  # Resolve right operand.
        if isinstance(node.operator, cst.Add):  # Only handle the operators we expect to encounter.
            return left + right  # Numeric or string addition.
        if isinstance(node.operator, cst.Subtract):  # Subtraction for attempt-style counters.
            return left - right  # Numeric subtraction.
        msg = f"unsupported binary op in eval: {type(node.operator).__name__}"  # Tell operator.
        raise ValueError(msg)  # Phase-1 can extend if needed.
    msg = f"unsupported expression in eval: {type(node).__name__}"  # Surface unknown node types.
    raise ValueError(msg)  # Forces the operator to add support before silent miscompare.


class _LineCallCollector(cst.CSTVisitor):
    """Collect the first cst.Call node whose start line equals target_line."""

    METADATA_DEPENDENCIES = (PositionProvider,)  # Required for line-number lookup.

    def __init__(self, target_line: int) -> None:
        """Remember the line we are searching for and prepare the result slot."""
        super().__init__()  # Required to initialize libcst visitor state.
        self.target_line = target_line  # 1-based source line we want to locate.
        self.found: cst.Call | None = None  # Set once when we hit the matching Call.

    def visit_Call(self, node: cst.Call) -> bool | None:  # Visit hook for every Call.
        if self.found is not None:  # Stop searching once we already matched.
            return False  # Visitor convention: False prunes children.
        pos = self.get_metadata(PositionProvider, node)  # Resolve the call's line range.
        if pos.start.line == self.target_line:  # Match on the call's start line.
            self.found = node  # Capture the node for downstream extraction.
            return False  # No need to descend further into the matched call.
        return True  # Otherwise keep descending.


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argparse instance for the capture script."""
    parser = argparse.ArgumentParser(  # One parser per invocation.
        prog="capture_log_baseline",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,  # Preserve docstring layout.
    )
    parser.add_argument(  # Target Python file we are sampling from.
        "--source",
        type=Path,
        default=Path("MistHelper.py"),
        help="Python source file to read (default: MistHelper.py)",
    )
    parser.add_argument(  # Where the baseline JSON gets written.
        "--output",
        type=Path,
        default=Path("tests/fixtures/issue_429_log_baseline.json"),
        help="Output JSON path (default: tests/fixtures/issue_429_log_baseline.json)",
    )
    return parser  # Returned for the main() entry point to call parse_args().


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on parse/read failure."""
    logging.basicConfig(  # Configure INFO-level progress logging on stderr.
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = build_argument_parser().parse_args(argv)  # Parse CLI arguments.
    logging.info("capture start: source=%s output=%s", args.source, args.output)  # Action-log entry point parameters.
    try:
        source = args.source.read_text(encoding="utf-8")  # Read the target source file.
    except OSError as exc:  # Cover missing file + permission errors uniformly.
        logging.error("failed to read %s: %s", args.source, exc)  # Surface the failure.
        return 1  # Documented "read error" exit code.
    try:
        module = cst.parse_module(source)  # Build the libcst module once for reuse.
    except cst.ParserSyntaxError as exc:  # libcst parse failure type.
        logging.error("libcst parse failed: %s", exc)  # Surface the failure.
        return 1  # Documented "parse error" exit code.
    captured: dict[str, dict[str, Any]] = {}  # Site-id -> baseline record map.
    site_inputs_extra: dict[str, Any] = {  # Extra fixtures need a fake `sys` object for L1343.
        "sys": type("FakeSys", (), {"executable": "/usr/bin/python3"})()
    }
    for site in FIXTURE_SITES:  # Iterate the operator-curated fixture catalog.
        merged_inputs = {**site["inputs"]}  # Copy so we never mutate the source dict.
        if site["pattern"] == "attribute_access":  # L1343 references sys.executable.
            merged_inputs["sys"] = site_inputs_extra["sys"]  # Inject the fake sys object.
        if site["pattern"] == "g003_concat":  # L6120 uses table.get_string().

            class _FakeTable:  # Minimal stand-in for prettytable.PrettyTable.
                def __init__(self, text: str) -> None:  # Holds the rendered table text.
                    self._text = text  # Stored for get_string().

                def get_string(self) -> str:  # Matches PrettyTable's public API.
                    return self._text  # Return whatever the fixture asked for.

            merged_inputs["table"] = _FakeTable(merged_inputs.pop("table_text"))  # Wrap once.
        try:
            rendered = _render_call_at_line(module, site["line"], merged_inputs)  # Real render.
        except (LookupError, ValueError, KeyError, AttributeError) as exc:  # Tolerate per-site failures.
            logging.warning(  # Skip without aborting so the baseline still has the others.
                "skipping site %s: %s", site["site_id"], exc
            )
            continue  # Move to the next fixture entry.
        captured[site["site_id"]] = {  # Record both the inputs and the rendered string.
            "line": site["line"],
            "pattern": site["pattern"],
            "rendered": rendered,
        }
        logging.info("captured %s -> %r", site["site_id"], rendered)  # Progress feedback.
    args.output.parent.mkdir(parents=True, exist_ok=True)  # Ensure the output dir exists.
    args.output.write_text(  # Persist with stable formatting for clean diffs.
        json.dumps(captured, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    logging.info("wrote baseline: %d entries -> %s", len(captured), args.output)  # Final summary line.
    return 0  # Success.


if __name__ == "__main__":  # Standard CLI guard.
    raise SystemExit(main())  # Propagate the exit code.
