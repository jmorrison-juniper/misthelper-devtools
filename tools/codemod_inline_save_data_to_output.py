"""Issue #431 codemod: rewrite ``DataExporter.save_data_to_output(...)`` calls
to use the canonical ``DataExporter.write_with_format_selection(...)`` method.

Both methods live in the same class in ``MistHelper.py``. The deprecated
``save_data_to_output`` is a 1-line forwarder. This codemod inlines every
call site so the forwarder can be deleted, eliminating one ARCH-DELEGATE
violation while preserving runtime behavior.

Signature compatibility (verified via AST scan 2026-06-23):
- ``save_data_to_output(data, filename, api_function_name=None)`` -> 74 sites,
  ALL using exactly 2 positional args (data, filename).
- ``write_with_format_selection(data, filename_or_table, format_override=None,
  api_function_name=None, raw_data=None, fieldnames=None)`` accepts identical
  positional/keyword arguments.

Because no call passes a positional 3rd argument, the rename is a 1-to-1
direct substitution with no risk of positional-slot drift.

Run from the repository root::

    python tools/codemod_inline_save_data_to_output.py MistHelper.py
"""

from __future__ import annotations  # Enable PEP 604 union syntax on Python 3.13.

import argparse  # Stdlib CLI argument parser.
import logging  # We log our own progress through the codemod.
from pathlib import Path  # Portable path handling on Windows + POSIX.

import libcst as cst  # AST-preserving CST library for safe rewrites.


class SaveDataToOutputInliner(cst.CSTTransformer):
    """Rewrite ``X.save_data_to_output(...)`` -> ``X.write_with_format_selection(...)``.

    Also rewrites bare attribute references like ``save_data_fn=X.save_data_to_output``
    (function-reference passing for dependency injection) to point at the canonical
    method. That way the canonical method is the only one any caller ever names.
    """

    def __init__(self) -> None:
        """Initialize counters used to report progress at the end of the run."""
        super().__init__()  # Required to initialize libcst transformer state.
        self.rewrites = 0  # Tally of sites converted this pass (calls + references combined).

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        """If the call is X.save_data_to_output(...), rewrite to X.write_with_format_selection(...)."""
        func = updated_node.func  # Pull the callable expression once.
        if not isinstance(func, cst.Attribute):  # Only attribute-style calls qualify.
            return updated_node  # Not a method call -- leave unchanged.
        if func.attr.value != "save_data_to_output":  # Not the method we are inlining.
            return updated_node  # Leave unchanged.
        new_func = func.with_changes(  # Build the replacement callable with the canonical method name.
            attr=cst.Name("write_with_format_selection")
        )
        self.rewrites += 1  # Track the conversion.
        logging.debug(  # Action-log every rewrite so reviewers can audit.
            "rewrote call site to write_with_format_selection"
        )
        return updated_node.with_changes(func=new_func)  # Return tree with the new callable in place.

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        """Rewrite bare ``X.save_data_to_output`` attribute references (e.g. ``save_data_fn=X.save_data_to_output``).

        We rely on libcst's traversal order: leave_Call fires AFTER leave_Attribute
        for the callee inside a call, but the codemod sees `leave_Attribute` BEFORE
        the call rewrite happens. To avoid double-rewriting, leave_Attribute checks
        whether the attribute is currently the func position of a Call -- libcst
        does not expose parent info in the visitor, so instead we only rewrite the
        attribute if its name is the target name. The Call rewrite then operates on
        the already-renamed attribute, which is a safe no-op (the second branch in
        leave_Call sees attr.value already == 'write_with_format_selection').
        """
        if updated_node.attr.value != "save_data_to_output":  # Only the target attribute qualifies.
            return updated_node  # Leave every other attribute access alone.
        self.rewrites += 1  # Count this rewrite too so the summary reflects all touched sites.
        logging.debug("rewrote attribute reference to write_with_format_selection")  # Action log.
        return updated_node.with_changes(attr=cst.Name("write_with_format_selection"))  # Rename the attr.


def _configure_logging() -> None:
    """Configure progress logging on stderr for the codemod run."""
    # WHY: extracted so main() drops below the 25-line STRUCT-LENGTH cap.
    logging.basicConfig(  # Emit INFO+ records to stderr with timestamp.
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Build the CLI arg parser and return parsed args."""
    # WHY: extracted so main() drops below the 25-line STRUCT-LENGTH cap.
    parser = argparse.ArgumentParser(  # One parser per invocation.
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Python files to rewrite (one or more)")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write")
    return parser.parse_args(argv)  # Parse and return CLI arguments.


def _read_source(path: Path) -> str | None:
    """Read a source file, logging and returning None on failure."""
    # WHY: extracted so _process_file() keeps CC low and errors return cleanly.
    try:
        return path.read_text(encoding="utf-8")  # Read the target file.
    except OSError as exc:  # Cover missing-file + permission errors.
        logging.error("failed to read %s: %s", path, exc)  # Surface the failure.
        return None  # Signal failure to caller.


def _parse_source(source: str, path: Path) -> cst.Module | None:
    """Parse source into a libcst module; log+return None on parse failure."""
    # WHY: extracted so _process_file() keeps CC low and errors return cleanly.
    try:
        return cst.parse_module(source)  # Parse the file into libcst module.
    except cst.ParserSyntaxError as exc:  # libcst-specific parse error type.
        logging.error("libcst parse failed for %s: %s", path, exc)  # Surface the failure.
        return None  # Signal failure to caller.


def _process_file(path: Path, dry_run: bool) -> int:
    """Rewrite one file; returns rewrite count, or -1 on read/parse failure."""
    # WHY: extracted so main() drops CC 6->3 and length 37->~8 lines.
    logging.info("codemod start: path=%s dry_run=%s", path, dry_run)  # Action-log entry.
    source = _read_source(path)  # Read target file (None on IO failure).
    if source is None:
        return -1  # Signal failure to caller.
    module = _parse_source(source, path)  # Parse to libcst (None on parse failure).
    if module is None:
        return -1  # Signal failure to caller.
    transformer = SaveDataToOutputInliner()  # Build visitor with counters reset.
    new_module = module.visit(transformer)  # Walk tree; build the rewritten module.
    logging.info("  rewrites in %s: %d", path, transformer.rewrites)  # Per-file progress summary.
    if not dry_run and new_module.code != source:  # Tree changed and not a dry run.
        path.write_text(new_module.code, encoding="utf-8")  # Persist the rewrite.
        logging.info("  wrote %d bytes to %s", len(new_module.code), path)  # Confirm write.
    return transformer.rewrites  # Return per-file rewrite count.


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on parse/IO failure."""
    _configure_logging()  # Set up stderr logging.
    args = _parse_args(argv)  # Parse CLI arguments.
    total_rewrites = 0  # Tally across every input file for the summary line.
    for path in args.paths:  # Process each requested file in turn.
        count = _process_file(path, args.dry_run)  # Delegate per-file work.
        if count < 0:  # Read or parse failure signaled.
            return 1  # Documented IO/parse-error exit code.
        total_rewrites += count  # Aggregate for the final summary.
    logging.info("total rewrites across %d file(s): %d", len(args.paths), total_rewrites)  # Final summary.
    return 0  # Success.


if __name__ == "__main__":  # Standard CLI guard so import-time has no side effects.
    raise SystemExit(main())  # Propagate the documented exit code.
