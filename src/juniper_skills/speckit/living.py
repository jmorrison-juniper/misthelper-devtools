"""Living-spec drift and sync support for Juniper skill documents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.juniper_skills.speckit.models import SkillDocument

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class LivingSpecHarness(Protocol):
    """Define the artifact harness surface used by living sync."""

    def emit_for_document(self, document: SkillDocument, package_files: list[Path] | None = None) -> Path:
        """Emit artifacts for one source document."""
        ...  # Protocol method has no runtime implementation.


@dataclass(frozen=True)
class LivingDriftReport:
    """State whether a source document moved after the skill build."""

    checked: bool
    drifted: bool
    feature_dir: Path
    source_path: Path | None
    stored_hash: str
    current_hash: str
    detail: str


class LivingSpecManager:
    """Check and refresh living SpecKit artifacts for one source document."""

    def drift(self, feature_dir: Path) -> LivingDriftReport:
        """Return a hash drift report for one generated feature directory."""
        logger.info("Running Juniper living-spec drift check")  # Record the living-drift command start.
        context = self._context(feature_dir)  # Read the Companion context that stores the source hash.
        source_path = self._source_path(context)  # Resolve the source Markdown path recorded at build time.
        stored_hash = str(context.get("sourceHash", ""))  # Read the previous hash from the generated context.
        report = self._drift_report(feature_dir, source_path, stored_hash)  # Compare stored and current hashes.
        logger.debug("Living-spec drift check returned %s", report.detail)  # Record the report summary.
        return report

    def sync(self, document: SkillDocument, harness: LivingSpecHarness) -> Path:
        """Regenerate artifacts after a watcher reports changed source content."""
        logger.info("Running Juniper living-spec sync")  # Record the living-sync command start.
        feature_dir = harness.emit_for_document(document)  # Rebuild artifacts so stored hashes match the source.
        logger.debug("Living-spec sync refreshed artifacts at %s", feature_dir)  # Record the destination path.
        return feature_dir

    def _context(self, feature_dir: Path) -> dict[str, object]:
        """Read the Companion context for a generated feature."""
        logger.info("Reading Companion context for drift")  # Record the context read before opening the file.
        path = feature_dir / ".spec-context.json"  # Use the canonical Companion context file name.
        if not path.exists():  # Missing context means no living-spec state can be checked.
            logger.debug("Companion context is missing at %s", path)  # Record the missing path.
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))  # Parse the context as JSON.
        logger.debug("Read Companion context with %d keys", len(data))  # Record the context size.
        return data if isinstance(data, dict) else {}

    def _source_path(self, context: dict[str, object]) -> Path | None:
        """Return the source path stored by the SpecKit harness."""
        logger.info("Resolving living-spec source path")  # Record the source path lookup.
        raw = str(context.get("sourcePath", ""))  # Read the recorded source path without changing it.
        path = Path(raw) if raw else None  # Convert a present value into a pathlib path.
        logger.debug("Resolved living-spec source path present=%s", path is not None)  # Record presence only.
        return path

    def _drift_report(self, feature_dir: Path, source_path: Path | None, stored_hash: str) -> LivingDriftReport:
        """Build a drift report from stored and current source hashes."""
        logger.info("Comparing stored and current source hashes")  # Record the drift comparison.
        if not source_path or not stored_hash:  # Missing state means the workflow did not record living metadata.
            return LivingDriftReport(False, True, feature_dir, source_path, stored_hash, "", "living metadata missing")
        if not source_path.exists():  # Missing source means the factory must not claim the skill is current.
            return LivingDriftReport(True, True, feature_dir, source_path, stored_hash, "", "source file missing")
        current_hash = SkillDocument(source_path, "", "", 0, "", "").content_hash  # Hash the current source bytes.
        drifted = stored_hash != current_hash  # Compare exact hashes so content changes are visible.
        detail = "source hash changed" if drifted else "source hash unchanged"  # State the result for reports.
        logger.debug("Compared hashes for drifted=%s", drifted)  # Record the Boolean result.
        return LivingDriftReport(True, drifted, feature_dir, source_path, stored_hash, current_hash, detail)
