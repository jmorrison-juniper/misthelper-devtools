"""LibCST-based codemod that converts eager logging formatting to lazy %s args.

This tool implements the transforms required by GitHub issue #429 (the
``CONV-LOG-FSTRING`` sweep). It walks ``MistHelper.py`` (or any provided
target), finds every ``logging.<level>`` / ``<logger>.<level>`` call whose
first argument uses eager formatting, and rewrites the call to use lazy
``%s``-style positional arguments so the logging framework can defer
interpolation when the record is filtered out.

Supported rewrites:

* **G004 (logging-f-string)** -- ``logging.info(f"x={x}")`` becomes
  ``logging.info("x=%s", x)``.
* **G003 (logging-string-concat)** -- ``logging.info("x=" + str(x))`` becomes
  ``logging.info("x=%s", x)``. Also handles ``.format()`` and ``%``-pre-format
  message construction.
* **G201 (logging-exc-info)** -- inside an ``except`` block,
  ``logging.error(msg, exc_info=True)`` becomes ``logging.exception(msg)``.

Recognized logger receivers (any of these on the left of ``.<level>(...)``):

* a known logger name -- ``logging``, ``logger``, ``log``, ``self.logger`` (see
  ``LOGGER_NAMES``);
* a **dynamic** ``getLogger(...)`` call -- ``logging.getLogger(__name__).info(...)``
  or ``getLogger(__name__).info(...)`` (issue #439).

CLI flags (all optional):

* ``--dry-run`` -- write nothing; report what would change.
* ``--max-sites N`` -- stop after rewriting N sites in this invocation.
* ``--start-line L`` -- only consider call sites whose line number is >= L.
* ``--end-line L`` -- only consider call sites whose line number is <= L.
* ``--skip-lines L1,L2,...`` -- explicit line numbers to leave untouched
  (used by the security audit to opt sites out).
* ``--report PATH`` -- write a per-site JSON change report to PATH.

Exit codes:

* ``0`` -- success (zero or more rewrites; tree-clean exit).
* ``1`` -- parse error reading the input file.
* ``2`` -- encountered an unrecognized format spec inside an f-string and
  ``--allow-skip-unknown`` was NOT passed.
* ``3`` -- encountered an audit-flagged line that was NOT included in
  ``--skip-lines`` (operator must decide before continuing).

Issue: https://github.com/jmorrison-juniper/MistHelper/issues/429
"""

from __future__ import annotations  # Enable PEP 604 unions on Python 3.13.

import argparse  # Stdlib CLI argument parser.
import json  # For optional --report JSON output.
import logging  # We log our own progress through the codemod.
from dataclasses import dataclass, field  # Lightweight result records.
from pathlib import Path  # Portable path handling on Windows + POSIX.

import libcst as cst  # The AST-preserving CST library we transform against.
from libcst.metadata import PositionProvider  # Lets us read 1-based line numbers.

LOGGER_NAMES: frozenset[str] = frozenset(  # Names the codemod recognizes as a logger object.
    {"logging", "logger", "log", "LOG", "_logger", "_log"}
)
LEVEL_METHODS: frozenset[str] = frozenset(  # Method names that take a message + args.
    {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
)
GET_LOGGER_NAME: str = "getLogger"  # The stdlib factory whose result is a Logger we can rewrite against.


@dataclass
class CodemodReport:
    """Per-site record of what the codemod did (or skipped)."""

    file: str  # Absolute or relative path of the file scanned.
    rewrites: list[dict] = field(default_factory=list)  # One dict per converted site.
    skipped: list[dict] = field(default_factory=list)  # One dict per skipped site (with reason).
    parse_error: str | None = None  # libcst parse error message, if any.

    def to_json(self) -> str:
        """Serialize the report to indented JSON for human + diff review."""
        return json.dumps(  # Pretty-print so reviewers can read it.
            {
                "file": self.file,
                "rewrites": self.rewrites,
                "skipped": self.skipped,
                "parse_error": self.parse_error,
            },
            indent=2,
            sort_keys=True,
        )


class LoggingLazyCodemod(cst.CSTTransformer):
    """LibCST transformer that rewrites eager logging calls to lazy form.

    Phase-0 scaffold: this class wires up metadata and the public surface,
    but the transformer methods (G004/G003/G201) are stubs that simply
    record visits in ``self.report`` without modifying the tree. The
    behavior-modifying transforms land in Phase 1.
    """

    METADATA_DEPENDENCIES = (PositionProvider,)  # Required for line-number filtering.

    def __init__(
        self,
        *,
        start_line: int = 0,
        end_line: int = 10**9,
        skip_lines: frozenset[int] = frozenset(),
        max_sites: int | None = None,
        dry_run: bool = False,
    ) -> None:
        """Construct the transformer with filtering knobs from the CLI."""
        super().__init__()  # Required to initialize the libcst transformer state.
        self.start_line = start_line  # Inclusive lower line bound for sites.
        self.end_line = end_line  # Inclusive upper line bound for sites.
        self.skip_lines = skip_lines  # Sites the security audit told us to skip.
        self.max_sites = max_sites  # Stop rewriting after this many sites this run.
        self.dry_run = dry_run  # If True, transforms run but result is discarded by CLI.
        self.report = CodemodReport(file="")  # Filled in by the CLI before transform.
        self._rewrite_count = 0  # Running count of sites rewritten this invocation.
        self._except_depth = 0  # Tracks lexical depth inside `except:` blocks for G201.
        logging.debug(  # Action-logging rule: emit configuration after construction.
            "LoggingLazyCodemod configured: start=%s end=%s skip=%d max=%s dry_run=%s",
            start_line,
            end_line,
            len(skip_lines),
            max_sites,
            dry_run,
        )

    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> None:
        """Track lexical depth inside `except:` blocks for G201 detection."""
        self._except_depth += 1  # Children are now considered "inside an except".
        logging.debug("entered except handler: depth=%d", self._except_depth)  # Action log.

    def leave_ExceptHandler(  # libcst expects leave_X to mirror visit_X.
        self, original_node: cst.ExceptHandler, updated_node: cst.ExceptHandler
    ) -> cst.ExceptHandler:
        """Pop the except-handler depth counter on the way out."""
        self._except_depth -= 1  # Restore depth so siblings are not counted as "in except".
        logging.debug("left except handler: depth=%d", self._except_depth)  # Action log.
        return updated_node  # No tree change at this node.

    def leave_Call(  # libcst hook invoked once per Call node after children visited.
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        """Detect a logging call and rewrite it to the lazy %s form."""
        if not self._is_logging_call(updated_node):  # Skip non-logging calls fast.
            return updated_node  # Tree unchanged.
        line = self._line_of(original_node)  # Resolve the 1-based source line.
        if line < self.start_line or line > self.end_line:  # Outside requested range.
            return updated_node  # Tree unchanged.
        if line in self.skip_lines:  # Operator opted this site out.
            self.report.skipped.append(  # Record the skip so the report shows it.
                {"line": line, "reason": "in --skip-lines"}
            )
            return updated_node  # Tree unchanged.
        if self.max_sites is not None and self._rewrite_count >= self.max_sites:  # Reached the per-run cap.
            self.report.skipped.append(  # Record the skip with the reason.
                {"line": line, "reason": "max_sites reached"}
            )
            return updated_node  # Tree unchanged.
        try:
            new_node, change_kind = self._try_rewrite(updated_node)  # Attempt the transform.
        except _RewriteSkip as exc:  # Recognized-but-unsupported pattern -> skip cleanly.
            self.report.skipped.append({"line": line, "reason": str(exc)})  # Record reason.
            logging.debug("skipped line %d: %s", line, exc)  # Action log on skip.
            return updated_node  # Tree unchanged for this site.
        if new_node is updated_node:  # Defensive: rewriter returned unchanged tree.
            self.report.skipped.append({"line": line, "reason": "rewriter returned unchanged"})
            return updated_node  # Tree unchanged.
        self._rewrite_count += 1  # Bump the per-run counter for --max-sites enforcement.
        self.report.rewrites.append({"line": line, "kind": change_kind})  # Record what we did so reviewers can audit.
        logging.info("rewrote line %d (%s)", line, change_kind)  # Action log on success.
        return new_node  # Updated tree replaces the original Call node.

    def _try_rewrite(self, call: cst.Call) -> tuple[cst.Call, str]:
        """Apply G201 then G004/G003 transforms; return (new_call, kind_label)."""
        rewritten = call  # Start from the input; each transform produces a new immutable tree.
        kind_parts: list[str] = []  # Track which transforms applied for the report.
        rewritten, did_g201 = self._maybe_g201_exception(rewritten)  # G201 first (changes method name).
        if did_g201:  # Record we touched the exc_info pattern.
            kind_parts.append("G201")  # For the report.
        rewritten, did_msg = self._maybe_lazy_message(rewritten)  # Then convert msg arg to lazy form.
        if did_msg:  # G004 or G003 fired.
            kind_parts.append(did_msg)  # did_msg is the rule label ("G004" or "G003").
        if not kind_parts:  # Neither transform applied.
            raise _RewriteSkip("no recognized eager pattern at this site")  # Skip cleanly.
        return (rewritten, "+".join(kind_parts))  # Combined label like "G201+G004".

    def _maybe_g201_exception(self, call: cst.Call) -> tuple[cst.Call, bool]:
        """If call is `.error(..., exc_info=True)` inside an except, return `.exception(...)`."""
        if self._except_depth <= 0:  # G201 only applies inside an `except` block.
            return (call, False)  # Outside except: leave the call alone.
        func = call.func  # Pull the callable expression.
        if not isinstance(func, cst.Attribute) or func.attr.value != "error":  # Only .error(...) matches.
            return (call, False)  # Not the pattern.
        exc_info_args = [a for a in call.args if a.keyword is not None and a.keyword.value == "exc_info"]
        if not exc_info_args:  # No exc_info kwarg present.
            return (call, False)  # G201 does not apply.
        exc_info_arg = exc_info_args[0]  # The arg we may need to remove.
        if not (isinstance(exc_info_arg.value, cst.Name) and exc_info_arg.value.value == "True"):
            return (call, False)  # Only `exc_info=True` triggers the rename (per ruff G201 docs).
        new_func = func.with_changes(attr=cst.Name("exception"))  # Rename .error -> .exception.
        new_args = tuple(a for a in call.args if a is not exc_info_arg)  # Drop the exc_info kwarg.
        new_args = _strip_trailing_comma(new_args)  # Avoid `(msg,)` artifacts on single-arg results.
        return (call.with_changes(func=new_func, args=new_args), True)  # Replace the call.

    def _maybe_lazy_message(self, call: cst.Call) -> tuple[cst.Call, str | None]:
        """If first positional arg is an eager-formatted string, rewrite to lazy form."""
        if not call.args:  # No args -> nothing to rewrite.
            return (call, None)  # Unchanged.
        msg_idx = self._first_positional_index(call.args)  # Skip kwargs at the front.
        if msg_idx is None:  # No positional arg found.
            return (call, None)  # Unchanged.
        msg_arg = call.args[msg_idx]  # The wrapper Arg node we will replace.
        msg_value = msg_arg.value  # The actual expression.
        if isinstance(msg_value, cst.FormattedString):  # G004: f-string message.
            template, extra_args = _convert_fstring_to_lazy(msg_value)  # Build (template, args).
            kind = "G004"  # Label for the report.
        elif isinstance(msg_value, cst.ConcatenatedString):  # Implicit concat (multi-line).
            if not _contains_fstring(msg_value):  # Pure literal concat is already lazy.
                return (call, None)  # Leave already-lazy templates untouched.
            template, extra_args = _convert_concat_string_to_lazy(msg_value)  # Recurse.
            kind = "G004"  # Implicit concat with at least one f-string still counts as G004.
        elif isinstance(msg_value, cst.BinaryOperation) and isinstance(msg_value.operator, cst.Add):
            template, extra_args = _convert_concat_to_lazy(msg_value)  # G003: `"a=" + x + "b="`.
            kind = "G003"  # Label for the report.
        else:  # Already lazy (SimpleString), or unsupported expression.
            return (call, None)  # Unchanged.
        if not extra_args:  # No interpolated parts -> just a plain string, no need to add args.
            new_msg = cst.Arg(value=cst.SimpleString(_python_string_literal(template)))  # Lazy plain.
            new_args = tuple(  # Replace the message arg in place, keep everything else.
                new_msg if i == msg_idx else a for i, a in enumerate(call.args)
            )
            return (call.with_changes(args=_strip_trailing_comma(new_args)), kind)  # Done.
        new_msg = cst.Arg(value=cst.SimpleString(_python_string_literal(template)))  # Lazy template.
        positional_args = [cst.Arg(value=expr) for expr in extra_args]  # Wrap each expr in Arg.
        before = list(call.args[:msg_idx])  # Args before the message (likely empty).
        after = list(call.args[msg_idx + 1 :])  # Args after the message (kwargs etc.).
        merged = before + [new_msg] + positional_args + after  # Build the final arg list.
        return (call.with_changes(args=tuple(_strip_trailing_comma(merged))), kind)  # Final tree.

    @staticmethod
    def _first_positional_index(args: tuple[cst.Arg, ...] | list[cst.Arg]) -> int | None:
        """Return the index of the first positional arg, or None if all are keyword."""
        for i, arg in enumerate(args):  # Linear scan; arg lists are small.
            if arg.keyword is None:  # Positional arg detected.
                return i  # First match wins.
        return None  # All-kwarg arg list.

    def _is_logging_call(self, node: cst.Call) -> bool:
        """Return True if the Call looks like ``<logger>.<level>(...)``."""
        func = node.func  # Pull out the callable expression once.
        if isinstance(func, cst.Attribute):  # Common case: foo.bar(...) calls.
            method = func.attr.value  # Method name (the .bar part).
            if method not in LEVEL_METHODS:  # Not a logging method name.
                return False  # Bail out fast.
            value = func.value  # Object the method is called on (the foo part).
            if isinstance(value, cst.Name) and value.value in LOGGER_NAMES:  # logging.info(...)
                return True  # Direct match against known logger names.
            if isinstance(value, cst.Attribute) and value.attr.value in LOGGER_NAMES:  # self.logger.info(...)
                return True  # Matches attribute-chain access to a logger.
            if isinstance(value, cst.Call) and self._is_get_logger_call(value):  # logging.getLogger(__name__).info(...)
                return True  # Issue #439: dynamic logger obtained inline from a getLogger(...) call.
        return False  # Anything else is not a logging call we touch.

    @staticmethod
    def _is_get_logger_call(expr: cst.BaseExpression) -> bool:
        """Return True if ``expr`` is a ``getLogger(...)`` call (issue #439 dynamic-logger receiver)."""
        if not isinstance(expr, cst.Call):  # Dynamic loggers come from a call expression, not a bare name.
            return False  # Not a call -> not a getLogger receiver.
        get_logger_func = expr.func  # The callable producing the Logger (the getLogger part).
        if isinstance(get_logger_func, cst.Attribute) and get_logger_func.attr.value == GET_LOGGER_NAME:
            return True  # Module-qualified form: logging.getLogger(...).
        if isinstance(get_logger_func, cst.Name) and get_logger_func.value == GET_LOGGER_NAME:
            return True  # From-import form: getLogger(...) after `from logging import getLogger`.
        return False  # Some other call expression -> not a logger factory.

    def _line_of(self, node: cst.CSTNode) -> int:
        """Look up the 1-based source line for a node via libcst metadata."""
        pos = self.get_metadata(PositionProvider, node)  # Returns CodeRange.
        return pos.start.line  # Top-left line of the node.


class _RewriteSkip(Exception):
    """Raised when a recognized eager-format pattern cannot be safely converted."""


def _strip_trailing_comma(args: list[cst.Arg] | tuple[cst.Arg, ...]) -> tuple[cst.Arg, ...]:
    """Ensure the last Arg has no trailing comma (libcst preserves it otherwise)."""
    args = tuple(args)  # Normalize to tuple for return type consistency.
    if not args:  # Nothing to clean up.
        return args  # Return empty tuple unchanged.
    last = args[-1]  # The arg that might be holding a trailing comma.
    if last.comma is cst.MaybeSentinel.DEFAULT:  # libcst will pick a sensible default.
        return args  # No explicit comma -> nothing to do.
    if isinstance(last.comma, cst.Comma):  # Concrete trailing comma present.
        return args[:-1] + (last.with_changes(comma=cst.MaybeSentinel.DEFAULT),)  # Drop it.
    return args  # Defensive fallback for any other state.


def _python_string_literal(text: str) -> str:
    """Return a Python source-form double-quoted string literal for `text`.

    Uses repr() to get a properly escaped Python literal, then normalizes
    to double quotes (matching the project's style) unless the text
    contains a double quote, in which case repr's choice is kept.
    """
    repr_form = repr(text)  # Python literal with all escapes correct.
    if repr_form.startswith("'") and '"' not in text:  # Prefer "..." when safe.
        body = repr_form[1:-1].replace("\\'", "'")  # Unescape any apostrophes.
        return f'"{body}"'  # Wrap in double quotes (project convention).
    return repr_form  # Keep repr's quoting when text contains both quote kinds.


def _escape_percent(text: str) -> str:
    """Escape every literal `%` to `%%` for use inside a `%`-style template."""
    return text.replace("%", "%%")  # Single replacement covers every literal %.


def _format_spec_to_percent(spec: str) -> str:
    """Convert a small subset of f-string format specs to `%`-style specs.

    Raises `_RewriteSkip` for any spec we have not validated equivalence for.
    """
    if not spec:  # Empty spec means default formatting -> %s.
        return "%s"  # Matches str(value) for most types.
    if spec.startswith(".") and spec.endswith("f"):  # `.2f` -> `%.2f`.
        mid = spec[1:-1]  # The precision digits.
        if mid.isdigit():  # Reject anything other than plain digits.
            return f"%.{mid}f"  # `%.2f` equivalent.
    if spec.startswith(".") and spec.endswith("e"):  # `.2e` -> `%.2e` (scientific).
        mid = spec[1:-1]  # Precision digits.
        if mid.isdigit():  # Plain digits only.
            return f"%.{mid}e"  # `%.2e` equivalent.
    if spec.startswith(".") and spec.endswith("g"):  # `.2g` -> `%.2g`.
        mid = spec[1:-1]  # Precision digits.
        if mid.isdigit():  # Plain digits only.
            return f"%.{mid}g"  # `%.2g` equivalent.
    if spec == "d":  # Integer with no padding.
        return "%d"  # Direct equivalent.
    if spec == "f":  # Float with default precision.
        return "%f"  # Direct equivalent.
    if spec == "x":  # Lowercase hex.
        return "%x"  # Direct equivalent.
    if spec == "X":  # Uppercase hex.
        return "%X"  # Direct equivalent.
    if spec == "o":  # Octal.
        return "%o"  # Direct equivalent.
    raise _RewriteSkip(f"unrecognized format spec '{spec}' (extend codemod or skip site)")


def _conversion_to_percent(conversion: str | None) -> str:
    """Map an f-string conversion (`!r`/`!s`/`!a`/None) to a `%` spec."""
    if conversion is None:  # No conversion -> default to %s (matches f-string semantics).
        return "%s"  # str() on the value.
    if conversion == "r":  # `!r` -> `%r`.
        return "%r"  # repr() on the value.
    if conversion == "s":  # `!s` -> `%s`.
        return "%s"  # str() on the value.
    if conversion == "a":  # `!a` -> `%a`.
        return "%a"  # ascii() on the value.
    raise _RewriteSkip(f"unrecognized conversion '{conversion}'")


def _convert_fstring_to_lazy(
    node: cst.FormattedString,
) -> tuple[str, list[cst.BaseExpression]]:
    """Walk a FormattedString and emit (template, args) for the lazy form."""
    template_parts: list[str] = []  # Accumulate the lazy template text.
    extra_args: list[cst.BaseExpression] = []  # Args that will become positional.
    for part in node.parts:  # Walk every part of the f-string.
        if isinstance(part, cst.FormattedStringText):  # Literal segment.
            template_parts.append(_escape_percent(part.value))  # Escape any % in literal text.
        elif isinstance(part, cst.FormattedStringExpression):  # Interpolated segment.
            spec_text = ""  # Default: no format spec.
            if part.format_spec is not None:  # Format spec present.
                spec_text = "".join(  # Concatenate every FormattedStringText inside the spec.
                    p.value for p in part.format_spec if isinstance(p, cst.FormattedStringText)
                )
                if any(  # Format spec must be pure text (no nested interpolation).
                    not isinstance(p, cst.FormattedStringText) for p in part.format_spec
                ):
                    raise _RewriteSkip("format spec contains nested interpolation")
            if spec_text:  # Spec present -> map to %-style.
                template_parts.append(_format_spec_to_percent(spec_text))  # May raise _RewriteSkip.
            else:  # No spec -> conversion (or default).
                template_parts.append(_conversion_to_percent(part.conversion))  # Map !r/!s/!a/None.
            if _has_side_effect_risk(part.expression):  # Walrus / generator etc.
                raise _RewriteSkip("interpolated expression has side-effect risk")
            extra_args.append(part.expression)  # Carry the expression to the args list.
        else:  # Unknown libcst part subclass.
            raise _RewriteSkip(f"unsupported fstring part: {type(part).__name__}")
    return ("".join(template_parts), extra_args)  # (template, args) for the lazy call.


def _convert_concat_string_to_lazy(
    node: cst.ConcatenatedString,
) -> tuple[str, list[cst.BaseExpression]]:
    """Walk an implicitly-concatenated string node (`"a" f"b"`) and emit lazy form."""
    template_parts: list[str] = []  # Accumulate template text.
    extra_args: list[cst.BaseExpression] = []  # Accumulate positional args.
    stack: list[cst.BaseExpression] = [node.right, node.left]  # DFS, left-first via reverse push.
    while stack:  # Iterative walk avoids recursion-depth concerns.
        current = stack.pop()  # Take the next node.
        if isinstance(current, cst.ConcatenatedString):  # Nested implicit concat.
            stack.append(current.right)  # Push right first so left is processed first.
            stack.append(current.left)  # Push left last so it pops first.
        elif isinstance(current, cst.SimpleString):  # Plain literal segment.
            template_parts.append(_escape_percent(current.evaluated_value))  # Escape %.
        elif isinstance(current, cst.FormattedString):  # Nested f-string segment.
            sub_tpl, sub_args = _convert_fstring_to_lazy(current)  # Reuse f-string converter.
            template_parts.append(sub_tpl)  # Already escaped/spec-mapped by recursion.
            extra_args.extend(sub_args)  # Carry args forward.
        else:  # Unknown string-like node.
            raise _RewriteSkip(f"unsupported string node in concat: {type(current).__name__}")
    return ("".join(template_parts), extra_args)  # Combined template + args.


def _convert_concat_to_lazy(
    node: cst.BinaryOperation,
) -> tuple[str, list[cst.BaseExpression]]:
    """Walk a `+`-concatenation expression (G003) and emit (template, args)."""
    template_parts: list[str] = []  # Template text accumulator.
    extra_args: list[cst.BaseExpression] = []  # Args accumulator.
    stack: list[cst.BaseExpression] = [node.right, node.left]  # DFS left-first via reverse push.
    while stack:  # Iterative walk.
        current = stack.pop()  # Take the next node.
        if isinstance(current, cst.BinaryOperation) and isinstance(current.operator, cst.Add):
            stack.append(current.right)  # Push right then left -> left processed first.
            stack.append(current.left)  # Push left last so it pops first.
        elif isinstance(current, cst.SimpleString):  # Plain literal segment.
            template_parts.append(_escape_percent(current.evaluated_value))  # Escape %.
        elif isinstance(current, cst.FormattedString):  # Nested f-string.
            sub_tpl, sub_args = _convert_fstring_to_lazy(current)  # Reuse f-string converter.
            template_parts.append(sub_tpl)  # Carry template text.
            extra_args.extend(sub_args)  # Carry args.
        elif isinstance(current, cst.ConcatenatedString):  # Implicit concat appearing in `+` tree.
            sub_tpl, sub_args = _convert_concat_string_to_lazy(current)  # Reuse concat converter.
            template_parts.append(sub_tpl)  # Carry template text.
            extra_args.extend(sub_args)  # Carry args.
        elif isinstance(current, cst.Call) and _is_str_call(current):  # `str(x)` -> arg + %s.
            inner = current.args[0].value if current.args else current  # Unwrap the str() call.
            template_parts.append("%s")  # %s placeholder for default str rendering.
            extra_args.append(inner)  # Pass the inner expression directly.
        else:  # Arbitrary expression: rely on default %s rendering.
            template_parts.append("%s")  # %s placeholder.
            extra_args.append(current)  # Pass through unchanged.
    return ("".join(template_parts), extra_args)  # Combined template + args.


def _contains_fstring(node: cst.BaseExpression) -> bool:
    """Return True if `node` (a string-like tree) contains a FormattedString anywhere."""
    found = False  # Shared flag captured by the visitor below.

    class _FStringVisitor(cst.CSTVisitor):  # Local visitor avoids polluting module scope.
        def visit_FormattedString(self, _: cst.FormattedString) -> None:  # Hit on any f-string.
            nonlocal found  # Allow the visitor to update the outer flag.
            found = True  # Record that we saw at least one f-string.

    node.visit(_FStringVisitor())  # Walk the subtree once.
    return found  # True if any f-string was anywhere inside the tree.


def _is_str_call(node: cst.Call) -> bool:
    """Return True if `node` looks like a bare `str(x)` call."""
    func = node.func  # Inspect the callable.
    return (
        isinstance(func, cst.Name)  # Bare name (not attribute access).
        and func.value == "str"  # Specifically `str`.
        and len(node.args) == 1  # Single positional arg.
        and node.args[0].keyword is None  # That arg is positional, not keyword.
    )


def _has_side_effect_risk(expr: cst.BaseExpression) -> bool:
    """Return True for expressions we refuse to migrate (walrus, await, yield)."""
    found_risk = False  # Module-level mutable flag captured by the visitor below.

    class _RiskVisitor(cst.CSTVisitor):  # Local class avoids polluting the module namespace.
        def visit_NamedExpr(self, node: cst.NamedExpr) -> None:  # `(x := ...)` walrus.
            nonlocal found_risk  # Allow the visitor to set the outer flag.
            found_risk = True  # Walrus assignments inside f-strings change evaluation context.

        def visit_Await(self, node: cst.Await) -> None:  # `await ...` inside f-string.
            nonlocal found_risk  # Forward the flag.
            found_risk = True  # Async semantics are out of scope for this codemod.

        def visit_Yield(self, node: cst.Yield) -> None:  # `yield` inside f-string.
            nonlocal found_risk  # Forward the flag.
            found_risk = True  # Yield expressions change generator semantics.

    expr.visit(_RiskVisitor())  # Walk the expression tree once.
    return found_risk  # True if any risky construct was found.


def _parse_skip_lines(raw: str | None) -> frozenset[int]:
    """Parse the ``--skip-lines L1,L2,...`` CLI argument into a frozenset."""
    if not raw:  # Empty / missing -> no sites skipped.
        return frozenset()  # Immutable empty set is the safe default.
    parts = [p.strip() for p in raw.split(",") if p.strip()]  # Tolerate whitespace + trailing commas.
    return frozenset(int(p) for p in parts)  # ValueError surfaces bad input clearly.


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the argparse instance for the CLI entry point."""
    parser = argparse.ArgumentParser(  # One parser per invocation.
        prog="codemod_logging_lazy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,  # Keep docstring layout.
    )
    parser.add_argument("path", type=Path, help="Python file to rewrite (e.g. MistHelper.py)")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write")
    parser.add_argument("--max-sites", type=int, default=None, help="stop after N rewrites")
    parser.add_argument("--start-line", type=int, default=0, help="only sites with line >= L")
    parser.add_argument("--end-line", type=int, default=10**9, help="only sites with line <= L")
    parser.add_argument("--skip-lines", type=str, default="", help="comma-separated line numbers to skip")
    parser.add_argument("--report", type=Path, default=None, help="write JSON report to PATH")
    parser.add_argument(  # Allow the codemod to skip unknown format specs without aborting.
        "--allow-skip-unknown",
        action="store_true",
        help="if set, unrecognized format specs are skipped instead of triggering exit 2",
    )
    return parser  # Returned so __main__ can call parser.parse_args().


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the documented exit code (0/1/2/3)."""
    logging.basicConfig(  # Default to INFO so progress lines render on stderr.
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = build_argument_parser()  # Build the CLI parser.
    args = parser.parse_args(argv)  # Parse caller-supplied or sys.argv arguments.
    logging.info("codemod start: path=%s dry_run=%s", args.path, args.dry_run)  # Action log entry.
    try:
        source = args.path.read_text(encoding="utf-8")  # Read the target file.
    except OSError as exc:  # Cover both missing file and permission errors.
        logging.error("failed to read %s: %s", args.path, exc)  # Surface the failure.
        return 1  # Documented "parse / read error" exit.
    try:
        module = cst.parse_module(source)  # Parse the file into a libcst Module.
    except cst.ParserSyntaxError as exc:  # libcst-specific parse error type.
        logging.error("libcst parse failed for %s: %s", args.path, exc)  # Surface it.
        return 1  # Same exit code as a read failure (both are "couldn't get the AST").
    wrapper = cst.MetadataWrapper(module)  # Required to enable PositionProvider metadata.
    transformer = LoggingLazyCodemod(  # Build the transformer with CLI knobs.
        start_line=args.start_line,
        end_line=args.end_line,
        skip_lines=_parse_skip_lines(args.skip_lines),
        max_sites=args.max_sites,
        dry_run=args.dry_run,
    )
    transformer.report.file = str(args.path)  # Stamp the file path for the report.
    new_module = wrapper.visit(transformer)  # Run the visitor; returns a new (or unchanged) module.
    detected = len(transformer.report.skipped) + len(transformer.report.rewrites)  # Combined visit count.
    logging.info(  # Summary line after the walk completes.
        "codemod done: detected=%d rewrites=%d skipped=%d",
        detected,
        len(transformer.report.rewrites),
        len(transformer.report.skipped),
    )
    if args.report is not None:  # Caller wants a machine-readable artifact.
        args.report.write_text(transformer.report.to_json(), encoding="utf-8")  # Emit the JSON.
        logging.info("wrote report to %s", args.report)  # Acknowledge the write.
    if args.dry_run:  # Dry-run: never touch the source file.
        logging.info("dry-run: source file untouched")  # Be explicit about the no-op.
        return 0  # Phase 0 scaffold exits cleanly when there's nothing to do.
    if new_module.code != source:  # Tree changed during transform.
        args.path.write_text(new_module.code, encoding="utf-8")  # Persist the rewrite.
        logging.info("wrote %d bytes to %s", len(new_module.code), args.path)  # Confirm write.
    return 0  # Success.


if __name__ == "__main__":  # Standard CLI guard so import-time has no side effects.
    raise SystemExit(main())  # Propagate the documented exit code.
