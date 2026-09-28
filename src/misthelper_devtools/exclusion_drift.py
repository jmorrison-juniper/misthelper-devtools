"""Measure documented quality-gate exclusions and report count drift.

Exclusion counts rot when a repository changes. This command measures the
current count for each documented exclusion and reports drift without blocking
the main quality gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from misthelper_devtools.repository_root import resolve_repository_root

DEFAULT_MANIFEST_NAME = "quality_gate_exclusions.json"
ERROR_MARKER = re.compile(r": error:")
MYPY_COMMAND_LENGTH_BUDGET = 30000
SUBPROCESS_TIMEOUT_SECONDS = 300

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Exclusion:
    """Describe one excluded path and its recorded finding count."""

    gate: str
    path: str
    scan_path: str | list[str]
    recorded_count: int


class ExclusionDriftReporter:
    """Measure exclusion counts without changing the blocking quality gates."""

    def __init__(self, root: Path | str | None = None, manifest_path: Path | str | None = None) -> None:
        """Set the manifest path and per-manifest caches."""
        self.root = resolve_repository_root(root)  # Resolve once, so every tool runs in the caller repository.
        named_manifest = Path(manifest_path) if manifest_path is not None else Path(DEFAULT_MANIFEST_NAME)
        self.manifest_path = named_manifest if named_manifest.is_absolute() else self.root / named_manifest
        self._manifest_key = ""
        self._mypy_file_cache: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        self._completed_run_cache: dict[tuple[str, str, tuple[str, ...]], list[subprocess.CompletedProcess[str]]] = {}

    def load_exclusions(self) -> list[Exclusion]:
        """Load and validate the machine-readable exclusion manifest."""
        logger.info("Reading the exclusion manifest at %s", self.manifest_path)  # Log before reading caller data.
        manifest_text = self.manifest_path.read_text(encoding="utf-8")
        self._manifest_key = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        self._mypy_file_cache.clear()
        self._completed_run_cache.clear()
        payload = json.loads(manifest_text)
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("The exclusion manifest entries value must be a list.")
        exclusions = [Exclusion(**entry) for entry in entries]  # Keep manifest order for stable reports.
        logger.debug("Read %d exclusion entries", len(exclusions))  # Log after reading caller data.
        return exclusions

    def measure(self, exclusion: Exclusion) -> dict[str, Any]:
        """Measure one exclusion and return a machine-readable result."""
        if self._path_missing(exclusion.scan_path):
            return {
                "gate": exclusion.gate,
                "path": exclusion.path,
                "recorded_count": exclusion.recorded_count,
                "current_count": 0,
                "delta": -exclusion.recorded_count,
                "status": "missing",
                "tool_exit_code": 0,
                "measured_at": date.today().isoformat(),
            }
        completed_runs = self._completed_runs_for(exclusion)
        output = "\n".join(run.stdout + run.stderr for run in completed_runs)
        if "No module named" in output:
            return {
                "gate": exclusion.gate,
                "path": exclusion.path,
                "recorded_count": exclusion.recorded_count,
                "current_count": None,
                "delta": None,
                "status": "tool_unavailable",
                "tool_exit_code": max(run.returncode for run in completed_runs),
                "measured_at": date.today().isoformat(),
            }
        current_count = self._count_findings(exclusion.gate, output)
        return {
            "gate": exclusion.gate,
            "path": exclusion.path,
            "recorded_count": exclusion.recorded_count,
            "current_count": current_count,
            "delta": current_count - exclusion.recorded_count,
            "status": "measured",
            "tool_exit_code": max(run.returncode for run in completed_runs),
            "measured_at": date.today().isoformat(),
        }

    def run(self) -> list[dict[str, Any]]:
        """Measure every documented exclusion in manifest order."""
        return [self.measure(exclusion) for exclusion in self.load_exclusions()]

    def _completed_runs_for(self, exclusion: Exclusion) -> list[subprocess.CompletedProcess[str]]:
        """Return tool results, reusing identical scan commands."""
        targets = self._targets_for(exclusion.scan_path)
        cache_key = (self._manifest_key, exclusion.gate, tuple(targets))
        if cache_key not in self._completed_run_cache:
            logger.info("Measuring %s exclusion %s", exclusion.gate, exclusion.path)  # Log before tool runs.
            self._completed_run_cache[cache_key] = [
                subprocess.run(
                    command,
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                )
                for command in self._commands_for(exclusion)
            ]  # Bound each tool, because an analyzer can stall.
            logger.debug("Measured %s exclusion %s", exclusion.gate, exclusion.path)  # Log after tool runs.
        return self._completed_run_cache[cache_key]

    def _command_for(self, exclusion: Exclusion) -> list[str]:
        """Build a command that scans the excluded path directly."""
        scan_paths = exclusion.scan_path
        targets = scan_paths if isinstance(scan_paths, list) else [scan_paths]
        if exclusion.gate == "ruff":
            return [sys.executable, "-m", "ruff", "check", *targets, "--output-format=json"]
        if exclusion.gate == "mypy":
            files = [str(path) for target in targets for path in (self.root / target).rglob("*.py")]
            if not files:
                files = targets
            return [sys.executable, "-m", "mypy", *files, "--config-file", "pyproject.toml"]
        if exclusion.gate == "bandit":
            return [sys.executable, "-m", "bandit", "-r", *targets, "-f", "json"]
        if exclusion.gate == "pylint":
            return [
                sys.executable,
                "-m",
                "pylint",
                *targets,
                "--output-format=json",
                "--reports=n",
                "--ignore-paths=^$",
            ]
        raise ValueError(f"Unsupported quality gate: {exclusion.gate}")

    def _commands_for(self, exclusion: Exclusion) -> list[list[str]]:
        """Build bounded commands for tools with large file sets."""
        if exclusion.gate != "mypy":
            return [self._command_for(exclusion)]
        targets = exclusion.scan_path if isinstance(exclusion.scan_path, list) else [exclusion.scan_path]
        files = self._mypy_files_for(targets)
        return self._mypy_commands_for(files)

    def _mypy_files_for(self, targets: list[str]) -> list[str]:
        """Return cached Python files for one manifest revision and target set."""
        cache_key = (self._manifest_key, tuple(targets))
        if cache_key not in self._mypy_file_cache:
            files = [str(path) for target in targets for path in (self.root / target).rglob("*.py")]
            self._mypy_file_cache[cache_key] = files or targets
        return self._mypy_file_cache[cache_key]

    @staticmethod
    def _mypy_commands_for(files: list[str]) -> list[list[str]]:
        """Build mypy commands that stay under a conservative shell limit."""
        commands: list[list[str]] = []
        batch: list[str] = []
        prefix = [sys.executable, "-m", "mypy"]
        suffix = ["--config-file", "pyproject.toml"]
        for file_path in files:
            candidate = [*prefix, *batch, file_path, *suffix]
            if batch and ExclusionDriftReporter._command_length(candidate) > MYPY_COMMAND_LENGTH_BUDGET:
                commands.append([*prefix, *batch, *suffix])
                batch = [file_path]
            else:
                batch.append(file_path)
        if batch:
            commands.append([*prefix, *batch, *suffix])
        return commands

    @staticmethod
    def _command_length(command: list[str]) -> int:
        """Estimate the Windows command line length for bounded batches."""
        return sum(len(part) for part in command) + max(len(command) - 1, 0)

    @staticmethod
    def _targets_for(scan_path: str | list[str]) -> list[str]:
        """Normalize one or more scan paths for cache keys and commands."""
        return scan_path if isinstance(scan_path, list) else [scan_path]

    @staticmethod
    def _count_findings(gate: str, output: str) -> int:
        """Count findings from the selected tool output."""
        if gate == "mypy":
            return sum(1 for line in output.splitlines() if ERROR_MARKER.search(line))
        if gate == "ruff":
            payload = ExclusionDriftReporter._json_from_output(output, [])
            return len(payload) if isinstance(payload, list) else 0
        payload = ExclusionDriftReporter._json_from_output(output, {"results": []})
        if gate == "bandit":
            return len(payload.get("results", [])) if isinstance(payload, dict) else 0
        return len(payload) if isinstance(payload, list) else 0

    @staticmethod
    def _json_from_output(output: str, default: Any) -> Any:
        """Extract JSON after tools write human log lines."""
        decoder = json.JSONDecoder()
        for index, character in enumerate(output):
            if character in "[{":
                try:
                    return decoder.raw_decode(output[index:])[0]
                except json.JSONDecodeError:
                    continue
        return default

    def _path_missing(self, scan_path: str | list[str]) -> bool:
        """Identify absent paths so optional exclusions report zero findings."""
        targets = scan_path if isinstance(scan_path, list) else [scan_path]
        return not any((self.root / target).exists() for target in targets)


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the report format and output destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Repository root to scan.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(DEFAULT_MANIFEST_NAME),
        help="Manifest path. A relative path resolves below --root.",
    )
    parser.add_argument("--format", choices=("json", "github"), default="json")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _render_github(results: list[dict[str, Any]]) -> str:
    """Render advisory annotations for GitHub Actions logs."""
    lines = []
    for result in results:
        if result["delta"] in (0, None):
            continue
        level = "warning"
        if result["current_count"] == 0 and result["recorded_count"] > 0:
            message = "reached zero, remove this exclusion"
        elif result["delta"] > 0:
            message = f"grew by {result['delta']}"
        else:
            message = f"changed by {result['delta']:+d}"
        lines.append(
            f"::{level} title=Exclusion drift::{result['gate']} {result['path']}: "
            f"{result['recorded_count']} -> {result['current_count']} ({message})"
        )
    return "\n".join(lines) or "No exclusion count drift detected."


def main(argv: list[str] | None = None) -> int:
    """Run the advisory report and always return a non-blocking status."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")  # Match the other console tools.
    arguments = _parse_arguments(argv)
    logger.info("Starting the exclusion drift report")  # Log before measurement starts.
    results = ExclusionDriftReporter(arguments.root, arguments.manifest).run()
    report = json.dumps({"measured_at": date.today().isoformat(), "results": results}, indent=2)
    if arguments.output:
        arguments.output.write_text(report + "\n", encoding="utf-8")
    print(_render_github(results) if arguments.format == "github" else report)
    logger.info("Finished the exclusion drift report with %d result(s)", len(results))  # Log after rendering.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
