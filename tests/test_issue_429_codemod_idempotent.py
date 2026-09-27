"""Idempotency tests for the issue #429 logging codemod.

The tests copy a synthetic input file to a temporary directory and run the
codemod on it two times. A dry run must leave the file unchanged. A rewrite
must make the lazy form in the first pass, and the second pass must leave the
output of the first pass unchanged.
"""

from __future__ import annotations  # PEP 604 union syntax for Python 3.13.

import subprocess  # Drive the codemod CLI as a subprocess so we test the public surface.
import sys  # Path to the active python interpreter for the subprocess call.
from pathlib import Path  # Portable filesystem handling.

REPO_ROOT = Path(__file__).resolve().parent.parent  # tests/ -> repo root.
SYNTHETIC_INPUT = REPO_ROOT / "tests" / "fixtures" / "issue_429_codemod_synthetic_input.py"  # Test target.


def test_codemod_dry_run_is_idempotent(tmp_path: Path) -> None:
    """Running the codemod in --dry-run twice produces an unchanged file."""
    target = tmp_path / "subject.py"  # Copy the synthetic input to a temp dir per test.
    target.write_text(SYNTHETIC_INPUT.read_text(encoding="utf-8"), encoding="utf-8")  # Seed input.
    before = target.read_text(encoding="utf-8")  # Snapshot byte content prior to first pass.
    _run_codemod(target, dry_run=True)  # First pass: dry-run so no write should occur.
    after_first = target.read_text(encoding="utf-8")  # Snapshot after first invocation.
    _run_codemod(target, dry_run=True)  # Second pass: dry-run again.
    after_second = target.read_text(encoding="utf-8")  # Snapshot after second invocation.
    assert before == after_first, "dry-run modified the file on first pass"  # Dry-run contract.
    assert after_first == after_second, "dry-run not idempotent across two passes"  # Stability check.


def test_codemod_rewrite_is_idempotent(tmp_path: Path) -> None:
    """A second rewrite pass must leave the output of the first pass unchanged."""
    target = tmp_path / "subject.py"  # Copy the synthetic input to a temp dir per test.
    target.write_text(SYNTHETIC_INPUT.read_text(encoding="utf-8"), encoding="utf-8")  # Seed input.
    _run_codemod(target, dry_run=False)  # First pass rewrites the eager logging calls in place.
    after_first = target.read_text(encoding="utf-8")  # Snapshot the rewritten source.
    _run_codemod(target, dry_run=False)  # Second pass must find nothing left to rewrite.
    after_second = target.read_text(encoding="utf-8")  # Snapshot after the second pass.
    assert 'logger.info("x=%s", value)' in after_first, after_first  # The first pass made the lazy form.
    assert after_first == after_second, "rewrite not idempotent across two passes"  # Stability check.


def _run_codemod(target: Path, *, dry_run: bool) -> None:
    """Run the `misthelper_devtools.codemod_logging_lazy` module in a subprocess and assert exit 0."""
    cmd = [sys.executable, "-m", "misthelper_devtools.codemod_logging_lazy", str(target)]  # Installed module.
    if dry_run:  # Forward the dry-run flag when requested.
        cmd.append("--dry-run")  # Tell the codemod to write nothing.
    result = subprocess.run(  # nosec B603 - subprocess args constructed from trusted paths.
        cmd,
        capture_output=True,  # Suppress stderr noise in pytest output unless we need it.
        text=True,  # Decode stdout/stderr as text for easy debug printing.
        check=False,  # We assert below so failures show stderr.
    )
    assert (
        result.returncode == 0
    ), f"codemod exited {result.returncode}\nSTDERR:\n{result.stderr}"  # Surface the captured stderr on failure.
