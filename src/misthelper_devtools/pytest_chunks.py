"""Run pytest targets in bounded chunks.

Large Windows worktrees can stall before pytest prints a final summary. This
command builds small pytest chunks from caller-named test paths. Each chunk has
a wall-clock timeout, and the command prints one final summary for the run.
"""

from __future__ import annotations  # Keep annotations stable for the console script.

import argparse  # Parse the command line for the generic runner.
import importlib.util  # Detect pytest-timeout without importing pytest.
import logging  # Record chunk discovery and execution.
import subprocess  # Run pytest as a bounded child process.
import sys  # Use the active interpreter for pytest.
import time  # Measure the final elapsed time.
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from misthelper_devtools.repository_root import resolve_repository_root

logger = logging.getLogger(__name__)  # Use a module logger so callers can choose the detail level.

EXCLUDED_DIR_NAMES = frozenset({"__pycache__"})  # Ignore cache folders that hold no tests.
DEFAULT_CHUNK_TIMEOUT_SECONDS = 300  # Match the MistHelper local shard script.
DEFAULT_TEST_TIMEOUT_SECONDS = 120  # Match the MistHelper per-test timeout.
DEFAULT_FILE_BATCH_SIZE = 8  # Keep sibling files in the same batch size as the source script.
DEFAULT_LARGE_PACKAGE_NAME = "upgrade_portal"  # MistHelper package that needs smaller chunks.


@dataclass(frozen=True, slots=True)
class TestChunk:
    """One bounded pytest command."""

    paths: tuple[Path, ...]  # The paths that pytest must run in this chunk.
    ignores: tuple[Path, ...] = ()  # The paths that the parent chunk leaves for smaller chunks.


@dataclass(frozen=True, slots=True)
class ChunkRun:
    """The result of one pytest chunk."""

    label: str  # Store the printed chunk label.
    exit_code: int  # Zero means the chunk passed.
    timed_out: bool = False  # True means the wall-clock timeout stopped the chunk.


class PytestChunkRunner:
    """Build pytest chunks and run each one with bounded time."""

    def __init__(
        self,
        root: Path,
        test_paths: tuple[Path, ...],
        split_paths: tuple[Path, ...],
        chunk_timeout: int,
        test_timeout: int,
        durations: int,
        runner: Callable[[Sequence[str], Path, int], int] | None = None,
    ) -> None:
        self.root = root.resolve()  # Run every pytest command from the repository root.
        self.test_paths = tuple(self._absolute(path) for path in test_paths)  # Normalize caller paths.
        self.split_paths = tuple(self._absolute(path) for path in split_paths)  # Normalize split roots.
        self.chunk_timeout = chunk_timeout  # Store the wall-clock cap for each pytest process.
        self.test_timeout = test_timeout  # Store the pytest-timeout cap for one test.
        self.durations = durations  # Store how many slow tests pytest reports.
        self.runner = runner or self._run_subprocess  # Tests can pass a fake runner.
        self.timeout_available = _pytest_timeout_available()  # Add --timeout only when the plugin exists.

    def build_chunks(self) -> tuple[TestChunk, ...]:
        """Return the chunks that cover the requested test paths."""
        logger.info("Building pytest chunks under %s", self.root)  # Log before filesystem discovery.
        if not self.test_paths:  # Pytest with no path would recurse into the real suite by accident.
            raise ValueError("At least one test path is required.")
        parent = TestChunk(self.test_paths, self.split_paths)  # Run the broad paths and ignore split roots.
        chunks = [parent]  # The parent chunk keeps unsplit tests together.
        for split_path in self.split_paths:  # Each split root becomes child chunks.
            chunks.extend(self._children(split_path))  # Add one level of child chunks.
        logger.info("Built %d pytest chunk(s)", len(chunks))  # Log after discovery.
        return tuple(chunks)  # Return an immutable sequence for tests.

    def run(self) -> int:
        """Run every chunk and print one final summary line."""
        started = time.monotonic()  # Use a monotonic clock for elapsed time.
        exit_code = 0  # Keep the first non-zero chunk status.
        chunks = self.build_chunks()  # Discover chunks before starting pytest.
        for index, chunk in enumerate(chunks, start=1):  # Run chunks in a fixed order.
            result = self._run_chunk(index, len(chunks), chunk)  # Execute one bounded command.
            if result.exit_code != 0 and exit_code == 0:  # Preserve the first failure status.
                exit_code = result.exit_code  # Report failure after all chunks get a chance to run.
        elapsed = time.monotonic() - started  # Measure the whole run.
        status = "passed" if exit_code == 0 else "failed"  # Keep the final line easy to scan.
        print(f"pytest-chunks: {status} {len(chunks)} chunks in {elapsed:.1f}s exit_code={exit_code}", flush=True)
        return exit_code  # Return non-zero when any chunk failed or timed out.

    def _children(self, path: Path) -> list[TestChunk]:
        """Return stable child chunks from one split folder."""
        logger.info("Reading split path %s", path)  # Log before reading the directory.
        if not path.exists():  # A missing split path is a caller error.
            raise ValueError(f"The split path does not exist: {path}")
        if path.is_file():  # A split file is already the smallest useful chunk.
            return [TestChunk((path,))]  # Return the file as one chunk.
        children = sorted(path.iterdir(), key=lambda child: child.name)  # Keep order stable across systems.
        files: list[Path] = []  # Keep direct test files in bounded batches.
        chunks: list[TestChunk] = []  # Collect directory and file chunks.
        for child in children:  # Read each direct child once.
            if child.name in EXCLUDED_DIR_NAMES:  # Cache folders hold no source tests.
                continue  # Skip the cache folder.
            if child.is_file() and child.name.startswith("test_") and child.suffix == ".py":  # Direct test file.
                files.append(child)  # Add the file to a sibling file batch.
            elif child.is_dir():  # A child package or folder is one bounded chunk.
                chunks.append(TestChunk((child,)))  # Add the folder chunk.
        if files:  # Direct files in the split root need bounded batches.
            chunks[0:0] = self._file_chunks(files)  # Put direct files before subdirectories.
        return chunks  # Return discovered chunks.

    def _file_chunks(self, files: list[Path]) -> list[TestChunk]:
        """Split sibling test files into bounded batches."""
        chunks: list[TestChunk] = []  # Collect each file batch.
        for offset in range(0, len(files), DEFAULT_FILE_BATCH_SIZE):  # Walk the file list by batch.
            batch = tuple(files[offset : offset + DEFAULT_FILE_BATCH_SIZE])  # Keep neighboring tests together.
            chunks.append(TestChunk(batch))  # Add the batch chunk.
        return chunks  # Return file chunks in source order.

    def _run_chunk(self, index: int, total: int, chunk: TestChunk) -> ChunkRun:
        """Run one pytest chunk with the wall-clock timeout."""
        relative = tuple(path.relative_to(self.root) for path in chunk.paths)  # Print short paths.
        ignores = tuple(path.relative_to(self.root) for path in chunk.ignores)  # Print short ignore paths.
        label = _chunk_label(relative, ignores)  # Build a readable progress label.
        command = self.pytest_command(relative, ignores)  # Build the subprocess argument list.
        logger.info("Running pytest chunk %d of %d: %s", index, total, label)  # Log before execution.
        print(f"pytest-chunks: chunk {index}/{total} {label}", flush=True)  # Show progress to the operator.
        try:
            code = self.runner(command, self.root, self.chunk_timeout)  # Execute the child process.
        except subprocess.TimeoutExpired:
            logger.error("Pytest chunk timed out after %d seconds: %s", self.chunk_timeout, label)
            print(f"pytest-chunks: timed out after {self.chunk_timeout}s at {label}", flush=True)
            return ChunkRun(label, 124, True)  # Use the common timeout exit code.
        logger.info("Pytest chunk %s exited with code %d", label, code)  # Log after execution.
        return ChunkRun(label, code)  # Return the chunk result.

    def pytest_command(self, relative: tuple[Path, ...], ignores: tuple[Path, ...]) -> list[str]:
        """Build one pytest command."""
        command = [
            sys.executable,  # Use the active virtual environment.
            "-m",  # Run pytest as a module.
            "pytest",  # The test runner.
            *(str(path) for path in relative),  # The bounded test paths.
            "-q",  # Keep each chunk readable.
            "--no-cov",  # Local chunks need pass or fail, not a coverage denominator.
        ]  # Build a list so no shell quoting is needed.
        if self.timeout_available:  # The generic tool can run without pytest-timeout installed.
            command.append(f"--timeout={self.test_timeout}")  # Let the plugin name a hung test.
        command.extend(f"--ignore={path}" for path in ignores)  # Leave split roots for child chunks.
        if self.durations > 0:  # A measurement run asks pytest for slow-test evidence.
            command.append(f"--durations={self.durations}")  # Print slow tests for this chunk.
        return command  # The subprocess runner receives one safe argument list.

    def _absolute(self, path: Path) -> Path:
        """Return a path resolved under the repository root."""
        candidate = path if path.is_absolute() else self.root / path  # Interpret relative paths under root.
        return candidate.resolve()  # Resolve once for comparisons and command labels.

    @staticmethod
    def _run_subprocess(command: Sequence[str], cwd: Path, timeout: int) -> int:
        """Run one pytest subprocess and return its status."""
        completed = subprocess.run(
            list(command),  # Convert to list for subprocess on all platforms.
            cwd=cwd,  # Run from the repository root.
            check=False,  # Read the exit code and continue to the final summary.
            timeout=timeout,  # Bound the whole chunk.
        )
        return completed.returncode  # Return the pytest status.


def _pytest_timeout_available() -> bool:
    """Return True when pytest-timeout can receive the --timeout option."""
    return importlib.util.find_spec("pytest_timeout") is not None  # Avoid a hard dependency on the plugin.


def _chunk_label(relative: tuple[Path, ...], ignores: tuple[Path, ...]) -> str:
    """Build one readable label for a chunk."""
    if len(relative) == 1:  # A directory chunk needs no count.
        label = str(relative[0])  # Print the only path.
    else:  # A combined chunk needs a compact label.
        label = f"{relative[0].parent} ({len(relative)} paths)"  # Keep the progress line short.
    if ignores:  # The parent chunk excludes paths that child chunks measure.
        return f"{label} ignoring {len(ignores)} path(s)"  # State that the scope is split.
    return label  # Return the simple label for normal chunks.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for pytest-chunks."""
    parser = argparse.ArgumentParser(
        prog="pytest-chunks",
        description="Run pytest paths in bounded chunks and print a final summary.",
    )  # Name the console script in help output.
    parser.add_argument("--root", type=Path, default=None, help="Repository root for the run.")
    parser.add_argument("--split", action="append", default=[], type=Path, help="Split this folder one level lower.")
    parser.add_argument(
        "--preset",
        choices=("unit", "other"),
        default=None,
        help="Use the MistHelper unit or other shard path defaults.",
    )
    parser.add_argument(
        "--large-package-name",
        default=DEFAULT_LARGE_PACKAGE_NAME,
        help="Package folder to split for MistHelper presets. Default: upgrade_portal.",
    )
    parser.add_argument("--chunk-timeout", type=int, default=DEFAULT_CHUNK_TIMEOUT_SECONDS, help="Seconds per chunk.")
    parser.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT_SECONDS, help="Seconds per test.")
    parser.add_argument("--durations", type=int, default=0, help="Slow-test count to print per chunk.")
    parser.add_argument("paths", nargs="*", type=Path, help="Test files or folders to run.")
    return parser  # The CLI parses this object once.


def _paths_for_preset(root: Path, preset: str, large_package_name: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return MistHelper-compatible paths and split roots for one preset."""
    if preset == "unit":
        unit = root / "tests" / "unit"
        return (unit,), (unit / large_package_name,)
    contract = root / "tests" / "contract"
    guardrails = root / "tests" / "guardrails"
    integration = root / "tests" / "integration"
    return (
        (contract, guardrails, integration),
        (contract / large_package_name, integration / large_package_name),
    )


def _resolve_cli_scope(
    root: Path,
    paths: tuple[Path, ...],
    splits: tuple[Path, ...],
    preset: str | None,
    large_package_name: str,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return the pytest paths and split roots requested by the command line."""
    selected_preset = preset
    if selected_preset is None and len(paths) == 1 and paths[0].as_posix() in {"unit", "other"} and not splits:
        selected_preset = paths[0].as_posix()
    if selected_preset is not None:
        return _paths_for_preset(root, selected_preset, large_package_name)
    return paths, splits


def main(argv: list[str] | None = None) -> int:
    """Run the generic pytest chunk command."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Print concise progress messages.
    parser = build_parser()  # Build the parser once for CLI and tests.
    args = parser.parse_args(argv)  # Let argparse handle usage errors with status 2.
    root = resolve_repository_root(args.root)  # Resolve the target repository root.
    paths, splits = _resolve_cli_scope(
        root,
        tuple(args.paths),
        tuple(args.split),
        args.preset,
        str(args.large_package_name),
    )
    runner = PytestChunkRunner(
        root,
        paths,
        splits,
        args.chunk_timeout,
        args.test_timeout,
        args.durations,
    )  # Store all run settings.
    try:
        return runner.run()  # Run the chunks and return the aggregate status.
    except ValueError as error:
        print(str(error), file=sys.stderr)  # Print caller errors without a traceback.
        return 2  # Match usage error status.


if __name__ == "__main__":  # Console module entry point.
    raise SystemExit(main())  # Return the CLI status.
