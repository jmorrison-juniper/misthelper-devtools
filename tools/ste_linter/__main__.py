"""Module entry point for the STE linter.

Lets the linter run with ``python -m tools.ste_linter``. It calls the command-line
interface and uses its exit code.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from .cli import main  # The command-line entry function.

if __name__ == "__main__":  # Run only when called as a module.
    raise SystemExit(main())  # Run the linter and use its exit code.
