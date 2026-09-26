"""Convenience launcher so the compliance analyzer can run as a plain script.

Usage from a source checkout of this repository::

    python src/misthelper_devtools/check_compliance.py <file-or-dir> [...] -o report.md

This is equivalent to the ``compliance-analyzer`` console script of an installed
package, but it does not require the package to be installed.
"""

from __future__ import annotations  # Enable modern annotation syntax.

import sys  # Adjust the import path before importing the package.
from pathlib import Path  # Resolve the source root portably.

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Add `src/` so the package imports.

from misthelper_devtools.compliance_analyzer.__main__ import main  # Import after the path fix (E402 ignored).

if __name__ == "__main__":  # Only run when executed directly.
    raise SystemExit(main())  # Run the CLI and propagate its exit code.
