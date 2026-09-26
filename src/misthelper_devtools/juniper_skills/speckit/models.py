"""Shared models for the SpecKit skill factory harness."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class SpecKitPaths:
    """Repository paths that the harness reads and writes."""

    repo_root: Path
    output_root: Path | None = None
    database_path: Path | None = None
    installed_skills_root: Path | None = None

    @property
    def specify_dir(self) -> Path:
        """Return the installed SpecKit directory."""
        logger.info("Resolving the SpecKit directory")  # Record the path lookup before returning it.
        path = self.repo_root / ".specify"  # Build the repository-local SpecKit path with pathlib.
        logger.debug("Resolved the SpecKit directory at %s", path)  # Record the resolved path for diagnostics.
        return path

    @property
    def templates_dir(self) -> Path:
        """Return the installed SpecKit template directory."""
        logger.info("Resolving the SpecKit template directory")  # Record the template lookup before returning it.
        path = self.specify_dir / "templates"  # Use the real installed template directory.
        logger.debug("Resolved the SpecKit template directory at %s", path)  # Record the resolved path.
        return path

    @property
    def skills_specs_dir(self) -> Path:
        """Return the generated skill specification root."""
        logger.info("Resolving the skill specification output directory")  # Record the output lookup.
        path = self.output_root or self.repo_root / "specs" / "skills"  # Default to the required output root.
        logger.debug("Resolved the skill specification output directory at %s", path)  # Record the output path.
        return path

    @property
    def factory_database_path(self) -> Path:
        """Return the skill factory database path."""
        logger.info("Resolving the skill factory database path")  # Record the database lookup.
        path = self.database_path or self.repo_root / "data" / "juniper_skills" / "factory.db"  # Use override or DB.
        logger.debug("Resolved the skill factory database path at %s", path)  # Record the database path.
        return path

    @property
    def installed_skills_dir(self) -> Path:
        """Return the installed skill package root."""
        logger.info("Resolving the installed skill package root")  # Record the installed package lookup.
        path = self.installed_skills_root or Path.home() / "juniper-agent-skills" / "skills"  # Use real package root.
        logger.debug("Resolved the installed skill package root at %s", path)  # Record the resolved package root.
        return path


@dataclass(frozen=True)
class SkillDocument:
    """One source document that receives a SpecKit artifact set."""

    source_path: Path
    domain: str
    title: str
    pages: int
    slug: str
    source_file: str
    category: str = "not measured"
    part_count: int = 1
    topic_count: int | None = None
    life_cycle_spread: dict[str, int] | None = None
    guard_result: str = "not measured"
    ste_result: str = "not measured"
    version_status: str = "not measured"
    superseded_by: str = "not measured"
    package_path: Path | None = None
    subjects: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    source_defects: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    @classmethod
    def from_markdown(cls, source_path: Path, domain: str) -> SkillDocument:
        """Create a document model from source Markdown front matter."""
        logger.info("Reading source Markdown front matter")  # Record the source read before opening the file.
        text = source_path.read_text(encoding="utf-8")  # Read converted source metadata, not copied answer prose.
        logger.debug("Read %d characters from source Markdown", len(text))  # Record the safe source size.
        front_matter = cls._front_matter(text)  # Parse only the front matter block for metadata.
        logger.debug("Parsed %d front matter keys", len(front_matter))  # Record the metadata count.
        return cls(  # Build an immutable document value for repeatable artifact generation.
            source_path=source_path,
            domain=domain,
            title=front_matter.get("title", source_path.stem.replace("-", " ").title()),
            pages=int(front_matter.get("pages", "0") or 0),
            slug=cls._slug(source_path.stem),
            source_file=front_matter.get("source_file", source_path.name),
        )

    def with_metrics(self, values: dict[str, object]) -> SkillDocument:
        """Return a copy with measured document values."""
        logger.info("Applying measured SpecKit document values")  # Record enrichment before replacing fields.
        updated = replace(self, **cast(dict[str, Any], values))  # Keep the source model immutable with checked keys.
        logger.debug("Applied %d measured values to %s", len(values), self.slug)  # Record enrichment count.
        return updated

    @property
    def content_hash(self) -> str:
        """Return a stable hash for living-spec drift checks."""
        logger.info("Hashing the source Markdown for living-spec tracking")  # Record the hash action.
        digest = hashlib.sha256(self.source_path.read_bytes()).hexdigest()  # Hash bytes so drift checks are exact.
        logger.debug("Computed source hash prefix %s", digest[:12])  # Record a safe hash prefix only.
        return digest

    @property
    def life_cycles(self) -> dict[str, int]:
        """Return the life cycle spread with all standard keys."""
        logger.info("Normalizing the life cycle spread")  # Record normalization before rendering artifacts.
        baseline = {"day0": 0, "day1": 0, "day2": 0, "day2plus": 0}  # Keep all life cycle names present.
        baseline.update(self.life_cycle_spread or {})  # Merge measured package values over the stable default.
        logger.debug("Normalized %d life cycle values", len(baseline))  # Record the normalized size.
        return baseline

    @property
    def topic_count_text(self) -> str:
        """Return the measured topic count or a clear unavailable value."""
        logger.info("Rendering topic count text")  # Record topic count rendering.
        value = str(self.topic_count) if self.topic_count is not None else "not measured"  # Avoid invented counts.
        logger.debug("Rendered topic count text %s", value)  # Record the safe rendered value.
        return value

    @property
    def questions(self) -> tuple[str, ...]:
        """Return measured open questions or a safe empty-state question."""
        logger.info("Resolving document open questions")  # Record question preparation for clarify artifacts.
        questions = self.open_questions or ("No open question was detected for this document.",)  # Avoid blanks.
        logger.debug("Resolved %d open questions", len(questions))  # Record question count.
        return questions

    @staticmethod
    def _front_matter(text: str) -> dict[str, str]:
        """Parse simple YAML front matter values from Markdown."""
        logger.info("Parsing source front matter values")  # Record the metadata parse action.
        if not text.startswith("---"):  # Treat documents without front matter as valid sources.
            logger.debug("Source front matter is absent")  # Record the absence for fallback behavior.
            return {}
        block = text.split("---", 2)[1]  # Isolate the first front matter block from source Markdown.
        pairs = re.findall(r'^([^:\n]+):\s*"?([^"\n]+)"?$', block, re.MULTILINE)  # Read simple scalar keys.
        result = {key.strip(): value.strip() for key, value in pairs}  # Normalize whitespace for stable values.
        logger.debug("Parsed front matter keys: %s", sorted(result))  # Record key names without source prose.
        return result

    @staticmethod
    def _slug(value: str) -> str:
        """Return a filesystem-safe slug."""
        logger.info("Creating a document slug")  # Record slug creation for deterministic output.
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")  # Keep a stable lowercase slug.
        logger.debug("Created document slug %s", slug)  # Record the generated slug.
        return slug or "document"
