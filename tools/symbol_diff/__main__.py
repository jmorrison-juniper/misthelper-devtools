"""Module entry point for the symbol table comparator.

Lets the tool run with ``python -m tools.symbol_diff``. It parses the command
line, calls one method on ``SymbolTableComparator``, and uses its exit code.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import argparse  # Parses the base revision and the path list.
import logging  # Configures the log level before the first read.

from . import __version__  # The reported version of the report format.
from .comparator import SymbolTableComparator  # The one class that does the work.

_DESCRIPTION = "Report the module-level names that a change lost and the names that it added."


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the tool."""
    parser = argparse.ArgumentParser(prog="tools.symbol_diff", description=_DESCRIPTION)  # Name the tool.
    parser.add_argument("--base", required=True, help="The git revision to compare against.")  # Base side.
    parser.add_argument("paths", nargs="+", help="One or more repository paths to compare.")  # Head side.
    parser.add_argument("--verbose", action="store_true", help="Print the DEBUG log lines.")  # Log level.
    parser.add_argument("--version", action="version", version=__version__)  # Report the format version.
    return parser  # The caller parses the command line with this object.


if __name__ == "__main__":  # Run only when called as a module.
    arguments = _build_parser().parse_args()  # Read the base revision and the path list.
    logging.basicConfig(  # Configure logging before the first read, so no startup line is dropped.
        level=logging.DEBUG if arguments.verbose else logging.INFO,  # The flag selects the level.
        format="%(asctime)s %(levelname)s %(message)s",  # An ASCII only format, per the logging rule.
    )
    raise SystemExit(SymbolTableComparator().run(arguments.base, arguments.paths))  # Use its exit code.
