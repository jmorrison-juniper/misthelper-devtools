"""SpecKit catalog inventory for the skill factory harness."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable  # Type measured catalog collections for strict mypy.
from pathlib import Path
from typing import cast  # Narrow JSON-derived inventory values before iteration.

from src.juniper_skills.speckit.models import SpecKitPaths

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SpecKitCatalog:
    """Read the installed SpecKit catalog without guessing files."""

    CORE_COMMANDS = (
        "speckit.specify",
        "speckit.clarify",
        "speckit.plan",
        "speckit.tasks",
        "speckit.implement",
        "speckit.analyze",
        "speckit.checklist",
        "speckit.constitution",
        "speckit.converge",
        "speckit.taskstoissues",
    )
    COMPANION_COMMANDS = (
        "speckit.companion.after-specify",
        "speckit.companion.after-plan",
        "speckit.companion.after-tasks",
        "speckit.companion.after-implement",
        "speckit.companion.auto",
        "speckit.companion.classify",
        "speckit.companion.implement",
        "speckit.companion.living-adopt",
        "speckit.companion.living-sync",
        "speckit.companion.living-drift",
        "speckit.companion.living-coverage",
        "speckit.companion.living-move",
        "speckit.companion.mark-complete",
        "speckit.companion.plan",
        "speckit.companion.resume",
        "speckit.companion.specify",
        "speckit.companion.status",
        "speckit.companion.tasks",
    )

    def __init__(self, paths: SpecKitPaths) -> None:
        """Initialize the SpecKitCatalog instance."""
        self.paths = paths  # Keep all repository paths in one tested object.

    def inventory(self) -> dict[str, object]:
        """Return the real installed SpecKit catalog."""
        logger.info("Inventorying the installed SpecKit catalog")  # Record the catalog scan start.
        commands = self._commands()  # Read command contracts once so the count stays typed.
        data: dict[str, object] = {  # Build one compact inventory for analysis and generated reports.
            "settings": self._read_known_files(),
            "templates": self._files_under(self.paths.templates_dir),
            "scripts": self._files_under(self.paths.specify_dir / "scripts"),
            "extensions": self._files_under(self.paths.specify_dir / "extensions"),
            "workflows": self._files_under(self.paths.specify_dir / "workflows"),
            "commands": commands,
        }
        logger.debug("Inventory contains %d command records", len(commands))  # Record catalog size.
        return data

    def render_markdown(self) -> str:
        """Return a written catalog for generated analysis reports."""
        logger.info("Rendering the SpecKit catalog as Markdown")  # Record report rendering.
        inventory = self.inventory()  # Read live catalog data so the report matches this checkout.
        lines = ["# SpecKit catalog", ""]  # Start the written catalog with a stable heading.
        lines.extend(self._settings_lines(inventory))  # Add the installed settings and integration files.
        lines.extend(self._command_lines(inventory))  # Add command consume and emit notes.
        lines.extend(self._file_lines("Templates", inventory["templates"]))  # Add template evidence.
        lines.extend(self._file_lines("Scripts", inventory["scripts"]))  # Add script evidence.
        lines.extend(self._file_lines("Extensions", inventory["extensions"]))  # Add extension evidence.
        logger.debug("Rendered %d catalog lines", len(lines))  # Record the catalog length.
        return "\n".join(lines) + "\n"

    def _read_known_files(self) -> dict[str, object]:
        """Read the required SpecKit metadata files."""
        logger.info("Reading SpecKit metadata files")  # Record the metadata read action.
        names = ["extensions.yml", "feature.json", "init-options.json", "integration.json"]  # Lock required files.
        result = {name: self._read_text_or_json(self.paths.specify_dir / name) for name in names}  # Read each file.
        logger.debug("Read %d SpecKit metadata files", len(result))  # Record the number of files read.
        return result

    def _commands(self) -> list[dict[str, str]]:
        """Return installed and expected command records."""
        logger.info("Cataloging SpecKit command files")  # Record command catalog creation.
        installed = self._installed_command_names()  # Measure command files in this checkout.
        names = sorted(
            set(installed) | set(self.CORE_COMMANDS) | set(self.COMPANION_COMMANDS)
        )  # Keep expected gaps visible.
        records = [self._command_record(name, installed) for name in names]  # Build one row per known command.
        logger.debug("Cataloged %d SpecKit command rows", len(records))  # Record command row count.
        return records

    def _installed_command_names(self) -> set[str]:
        """Return command names found in repository files."""
        logger.info("Finding installed SpecKit command files")  # Record the command file scan.
        roots = [self.paths.repo_root / ".github" / "agents", self.paths.specify_dir / "extensions"]  # Use real roots.
        paths = [
            path for root in roots for path in self._iter_files(root) if path.name.startswith("speckit.")
        ]  # Filter commands.
        names = {path.name.replace(".agent.md", "").replace(".md", "") for path in paths}  # Convert filenames to ids.
        logger.debug("Found %d installed SpecKit command names", len(names))  # Record installed command count.
        return names

    def _command_record(self, name: str, installed: set[str]) -> dict[str, str]:
        """Return consume and emit notes for one command."""
        logger.info("Building a SpecKit command catalog row")  # Record one command row build.
        record = {  # State the real install status and command contract summary.
            "name": name,
            "installed": "yes" if name in installed else "no",
            "consumes": self._consumes(name),
            "emits": self._emits(name),
        }
        logger.debug("Built command catalog row for %s", name)  # Record the command id.
        return record

    def _read_text_or_json(self, path: Path) -> object:
        """Read text or JSON from a known SpecKit file."""
        logger.info("Reading SpecKit file %s", path)  # Record the file read action.
        if not path.exists():
            logger.debug("SpecKit file is missing: %s", path)  # Record a missing required file.
            return {"missing": True}
        text = path.read_text(encoding="utf-8")  # Read the file with explicit UTF-8.
        logger.debug("Read %d characters from %s", len(text), path.name)  # Record safe file size.
        return json.loads(text) if path.suffix == ".json" else text  # Preserve JSON shape where available.

    def _files_under(self, root: Path) -> list[str]:
        """Return repository-relative files under a root."""
        logger.info("Listing SpecKit files under %s", root)  # Record the directory scan.
        files = [
            str(path.relative_to(self.paths.repo_root)) for path in self._iter_files(root)
        ]  # Use repo-relative paths.
        logger.debug("Listed %d files under %s", len(files), root)  # Record the file count.
        return files

    def _iter_files(self, root: Path) -> list[Path]:
        """Return files below root in stable order."""
        logger.info("Scanning files below %s", root)  # Record the low-level scan.
        files = sorted(root.rglob("*")) if root.exists() else []  # Return no files when an optional root is absent.
        result = [path for path in files if self._is_catalog_file(path)]  # Keep only source files for catalog output.
        logger.debug("Scanned %d files below %s", len(result), root)  # Record the scan result.
        return result

    def _is_catalog_file(self, path: Path) -> bool:
        """Return whether a file belongs in the SpecKit catalog."""
        logger.info("Checking whether a SpecKit file is catalog source")  # Record catalog filter action.
        is_cache = "__pycache__" in path.parts or path.suffix == ".pyc"  # Exclude runtime cache files.
        result = path.is_file() and not is_cache  # Catalog only durable installed files.
        logger.debug("SpecKit catalog source check for %s returned %s", path, result)  # Record decision.
        return result

    def _settings_lines(self, inventory: dict[str, object]) -> list[str]:
        """Return Markdown lines for settings files."""
        logger.info("Rendering SpecKit settings lines")  # Record report section rendering.
        settings = cast(Iterable[str], inventory["settings"])  # Read the already measured settings object.
        lines = ["## Installed settings", ""]  # Start the settings section.
        lines.extend(f"- `{name}`: present" for name in settings)  # State each required metadata file.
        logger.debug("Rendered %d settings lines", len(lines))  # Record section size.
        return lines + [""]

    def _command_lines(self, inventory: dict[str, object]) -> list[str]:
        """Return Markdown lines for command rows."""
        logger.info("Rendering SpecKit command lines")  # Record command section rendering.
        lines = [
            "## Commands",
            "",
            "| Command | Installed | Consumes | Emits |",
            "| - | - | - | - |",
        ]  # Add table head.
        for record in cast(Iterable[dict[str, object]], inventory["commands"]):
            lines.append("| `{name}` | {installed} | {consumes} | {emits} |".format(**record))  # Add one command row.
        logger.debug("Rendered %d command table lines", len(lines))  # Record command table size.
        return lines + [""]

    def _file_lines(self, heading: str, files: object) -> list[str]:
        """Return Markdown lines for one file list."""
        logger.info("Rendering SpecKit file list for %s", heading)  # Record file list rendering.
        lines = [f"## {heading}", ""]  # Start the file list section.
        lines.extend(f"- `{file_name}`" for file_name in cast(Iterable[str], files))  # Add each measured file path.
        logger.debug("Rendered %d file lines for %s", len(lines), heading)  # Record section size.
        return lines + [""]

    def _consumes(self, name: str) -> str:
        """Return a concise consumed artifact list for one command."""
        logger.info("Resolving consumed artifacts for command %s", name)  # Record mapping action.
        mapping = {
            "speckit.specify": "feature text and spec template",
            "speckit.clarify": "spec.md",
            "speckit.plan": "spec.md, constitution, and plan template",
            "speckit.tasks": "spec.md, plan.md, research.md, data-model.md, contracts, and tasks template",
            "speckit.checklist": "spec.md, plan.md, tasks.md, and checklist template",
            "speckit.analyze": "spec.md, plan.md, tasks.md, and constitution",
        }
        if name.startswith("speckit.companion."):
            result = self._companion_consumes(name)  # Use real Companion command contracts.
        else:
            result = mapping.get(name, "current feature artifacts and extension state")  # Use a safe default.
        logger.debug("Command %s consumes %s", name, result)  # Record the command mapping.
        return result

    def _companion_consumes(self, name: str) -> str:
        """Return consumed artifacts for Companion commands."""
        logger.info("Resolving Companion consumed artifacts for %s", name)  # Record mapping action.
        if name.endswith(("after-specify", "after-plan", "after-tasks")):
            result = "active feature directory and lifecycle hook state"
        elif name.endswith("after-implement"):
            result = "active feature tasks.md and lifecycle hook state"
        elif ".living-" in name:
            result = "living-specs registry, capability specs, git history, and changed files"
        else:
            result = "active feature artifacts, .spec-context.json, and companion config"
        logger.debug("Companion command %s consumes %s", name, result)  # Record mapping result.
        return result

    def _emits(self, name: str) -> str:
        """Return a concise emitted artifact list for one command."""
        logger.info("Resolving emitted artifacts for command %s", name)  # Record mapping action.
        mapping = {
            "speckit.specify": "spec.md",
            "speckit.clarify": "updated spec.md",
            "speckit.plan": "plan.md, research.md, data-model.md, quickstart.md, and contracts",
            "speckit.tasks": "tasks.md",
            "speckit.checklist": "checklists/*.md",
            "speckit.analyze": "read-only analysis report",
            "speckit.implement": "checked tasks and source changes",
        }
        if name.startswith("speckit.companion."):
            result = self._companion_emits(name)  # Use real Companion command contracts.
        else:
            result = mapping.get(name, ".spec-context.json or extension-specific state")  # Note extension state.
        logger.debug("Command %s emits %s", name, result)  # Record the command mapping.
        return result

    def _companion_emits(self, name: str) -> str:
        """Return emitted artifacts for Companion commands."""
        logger.info("Resolving Companion emitted artifacts for %s", name)  # Record mapping action.
        if name.endswith(("after-specify", "after-plan", "after-tasks", "after-implement")):
            result = ".spec-context.json lifecycle history and status"
        elif name.endswith("living-coverage"):
            result = "read-only requirement coverage report"
        elif name.endswith("living-drift"):
            result = "read-only drift report"
        elif name.endswith("living-sync"):
            result = "reviewable living spec edits and synced context names"
        elif name.endswith(("living-adopt", "living-move")):
            result = "capability spec files and living-specs registry updates"
        else:
            result = ".spec-context.json progress or pipeline output"
        logger.debug("Companion command %s emits %s", name, result)  # Record mapping result.
        return result
