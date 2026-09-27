"""Issue #439: the logging codemod must rewrite dynamic ``getLogger(...)`` receivers.

The #429 codemod recognized loggers only by name (``logging``/``logger``/``self.logger``).
It silently skipped the dynamic form ``logging.getLogger(__name__).info(f"...")`` -- which
forced a manual hand-conversion during the #433 Phase A sweep. These tests drive the codemod
CLI as a subprocess (public surface) and assert the dynamic receivers are now rewritten to
lazy ``%s`` form, while genuinely-unrelated calls are left untouched.
"""

from __future__ import annotations  # PEP 604 union syntax for Python 3.13.

import subprocess  # Drive the codemod CLI as a subprocess so we exercise the public surface.
import sys  # Path to the active interpreter for the subprocess call.
from pathlib import Path  # Portable filesystem handling on Windows + POSIX.


def _run_codemod(target: Path) -> None:
    """Invoke the codemod on ``target`` (writing in place) and assert a clean exit."""
    cmd = [sys.executable, "-m", "misthelper_devtools.codemod_logging_lazy", str(target)]  # Installed module.
    result = subprocess.run(  # nosec B603 - args are trusted repo paths only.
        cmd,
        capture_output=True,  # Capture stderr so failures are debuggable.
        text=True,  # Decode streams as text.
        check=False,  # Assert below so the captured stderr is surfaced.
    )
    assert result.returncode == 0, f"codemod exited {result.returncode}\nSTDERR:\n{result.stderr}"  # Clean exit.


def test_module_qualified_getlogger_is_rewritten(tmp_path: Path) -> None:
    """``logging.getLogger(__name__).info(f"x={x}")`` becomes the lazy ``%s`` form."""
    target = tmp_path / "dyn_mod.py"  # Per-test temp file so runs do not interfere.
    target.write_text(  # Seed the dynamic module-qualified pattern.
        "import logging\n" "x = 1\n" 'logging.getLogger(__name__).info(f"x={x}")\n',
        encoding="utf-8",
    )
    _run_codemod(target)  # Apply the codemod in place.
    out = target.read_text(encoding="utf-8")  # Read the rewritten source back.
    assert 'logging.getLogger(__name__).info("x=%s", x)' in out, out  # Lazy form emitted.
    assert 'f"x={x}"' not in out, out  # The eager f-string is gone.


def test_from_import_getlogger_is_rewritten(tmp_path: Path) -> None:
    """``getLogger(__name__).warning(f"y={y}")`` (from-import form) is rewritten too."""
    target = tmp_path / "dyn_bare.py"  # Per-test temp file.
    target.write_text(  # Seed the from-import dynamic pattern.
        "from logging import getLogger\n" "y = 2\n" 'getLogger(__name__).warning(f"y={y}")\n',
        encoding="utf-8",
    )
    _run_codemod(target)  # Apply the codemod in place.
    out = target.read_text(encoding="utf-8")  # Read the rewritten source back.
    assert 'getLogger(__name__).warning("y=%s", y)' in out, out  # Lazy form emitted.


def test_non_logger_factory_call_is_untouched(tmp_path: Path) -> None:
    """A same-shaped call on a non-getLogger factory must NOT be rewritten."""
    target = tmp_path / "decoy.py"  # Per-test temp file.
    original = (  # ``build().info(...)`` looks structurally similar but is not a logger.
        "def build():\n" "    return object()\n" "z = 3\n" 'build().info(f"z={z}")\n'
    )
    target.write_text(original, encoding="utf-8")  # Seed the decoy.
    _run_codemod(target)  # Apply the codemod in place.
    out = target.read_text(encoding="utf-8")  # Read the (expected unchanged) source back.
    assert out == original, out  # Non-getLogger receiver left untouched.
