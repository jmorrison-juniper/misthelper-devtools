"""Convenience launcher so the compliance analyzer can run as a plain script.

Usage from anywhere::

    python tools/check_compliance.py <file-or-dir> [...] -o report.md

This is equivalent to ``python -m tools.compliance_analyzer`` but does not
require the repository root to already be on ``sys.path``.
"""

from __future__ import annotations  # Enable modern annotation syntax.

import sys  # Adjust the import path before importing the package.
from pathlib import Path  # Resolve the repository root portably.

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Add repo root for package import.

from tools.compliance_analyzer.__main__ import ComplianceCLI  # Import after the path fix (E402 ignored).

if __name__ == "__main__":  # Only run when executed directly.
    raise SystemExit(ComplianceCLI().run())  # Run the CLI and propagate its exit code.
