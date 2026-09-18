"""Emit contract-shaped Juniper domain skill packages."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from src.juniper_skills.orchestrate.models import WorkItem
from src.juniper_skills.rewrite import RewriteResult
from src.juniper_skills.segment import JoinedDocument

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SkillPackageEmitter:
    """Write generated skill package files into the canonical store."""

    def __init__(self, store_path: Path) -> None:
        """Initialize the SkillPackageEmitter instance."""
        self.store_path = store_path  # Store the canonical repository root from the locked contract.
        self.skills_path = store_path / "skills"  # Keep package directories below the canonical skills folder.

    def emit(self, item: WorkItem, joined: JoinedDocument, outputs: list[RewriteResult]) -> list[Path]:
        """Run the emit operation."""
        logger.info("Emitting Juniper skill package files for %s", item.document_key)  # Log before file writes.
        package = self._package_dir(item)  # Resolve the domain package folder.
        document_dir = package / "documents" / self._slug(item.title)  # Resolve the document topic folder.
        document_dir.mkdir(parents=True, exist_ok=True)  # Create package directories before writing files.
        topic_paths = self._write_topics(item, document_dir, outputs)  # Write topic files first for index counts.
        self._write_document_index(item, joined, document_dir, topic_paths)  # Write the level 2 route index.
        self._write_level_one(item, package, topic_paths)  # Write SKILL.md, INDEX.md, and sources.md.
        paths = [package / "SKILL.md", package / "INDEX.md", package / "sources.md", document_dir / "INDEX.md"]
        logger.debug("Emitted %d package files for %s", len(paths) + len(topic_paths), item.document_key)  # Report.
        return paths + topic_paths  # Return every generated Markdown file for validation.

    def _write_topics(self, item: WorkItem, document_dir: Path, outputs: list[RewriteResult]) -> list[Path]:
        paths: list[Path] = []  # Collect topic paths for guard and STE validation.
        for index, result in enumerate(outputs):  # Keep generated topics in source order.
            path = document_dir / f"{index:02d}-{self._slug('topic-' + str(index))}.md"  # Build a contract name.
            path.write_text(self._topic_text(item, index, result), encoding="utf-8")  # Write one topic file.
            paths.append(path)  # Record the generated file path.
        return paths  # Return all topic files for later validation.

    def _topic_text(self, item: WorkItem, index: int, result: RewriteResult) -> str:
        body = "\n".join(card.to_markdown() for card in result.cards)  # Render all knowledge cards.
        if not body:  # Keep topic files measurable when a backend returns no cards.
            body = f"- **INFO** No publishable fact was emitted. [DOC{index + 1} p.0-0]"  # State the empty result.
        return (
            "---\n"
            f"topic: Topic {index}\n"
            f"domain: {item.domain.removeprefix('juniper-')}\n"
            f"document: {self._slug(item.title)}\n"
            "lifecycle: [day2]\n"
            f"sources: [DOC{index + 1} p.0-0]\n"
            "---\n\n"
            f"# Topic {index}\n\n{body}\n"
        )  # Return the required topic front matter and cards.

    def _write_document_index(
        self, item: WorkItem, joined: JoinedDocument, document_dir: Path, paths: list[Path]
    ) -> None:
        logger.info("Writing the level 2 document index for %s", item.document_key)  # Log before index write.
        rows = [self._topic_row(path, document_dir) for path in paths]  # Build the topic route rows.
        text = self._document_index_text(item, joined, rows)  # Render the full level 2 index.
        (document_dir / "INDEX.md").write_text(text, encoding="utf-8")  # Write the index beside topic files.
        logger.debug("Wrote the level 2 document index with %d topic rows", len(rows))  # Record row count.

    def _write_level_one(self, item: WorkItem, package: Path, topic_paths: list[Path]) -> None:
        logger.info("Writing level 1 package files for %s", item.domain)  # Log before package index writes.
        package.mkdir(parents=True, exist_ok=True)  # Ensure the domain folder exists for metadata files.
        (package / "SKILL.md").write_text(self._skill_text(item, len(topic_paths)), encoding="utf-8")  # Write router.
        (package / "INDEX.md").write_text(self._index_text(item, topic_paths), encoding="utf-8")  # Write route index.
        (package / "sources.md").write_text(self._sources_text(item), encoding="utf-8")  # Write source table.
        logger.debug("Wrote level 1 package files for %s", item.domain)  # Record package writes.

    def _document_index_text(self, item: WorkItem, joined: JoinedDocument, rows: list[str]) -> str:
        return (
            f"# {item.title} index\n\n"
            "## Source\n\n"
            f"Title: {item.title}.\n\nCategory: {item.category}.\n\nPages: {item.pages}.\n\n"
            f"Source file path: {item.source_pdf}.\n\nSource PDF path: {item.source_pdf}.\n\n"
            f"Split part count: {len(joined.parts)}.\n\n"
            "## Topic route\n\n| Ask about | Topic | Read | Life cycle |\n| - | - | - | - |\n"
            + "".join(rows)
            + "\n## Life cycle map\n\n| Stage | Topics | Status |\n| - | -: | - |\n| day0 | 0 | gap |\n"
            f"| day1 | 0 | gap |\n| day2 | {len(rows)} | covered |\n| day2plus | 0 | gap |\n\n"
            "## Citation keys\n\n| Key | Source range | Topic |\n| - | - | - |\n"
            + "".join(self._citation_row(path, index) for index, path in enumerate(rows))
        )  # Return the level 2 index in the locked section order.

    def _skill_text(self, item: WorkItem, topic_count: int) -> str:
        built = datetime.now(UTC).isoformat(timespec="seconds")  # Record build time for package metadata.
        return (
            "---\n"
            f"name: {item.domain}\n"
            "description: >-\n"
            f"  Use this skill for Juniper source topics in {item.title}. The skill holds a document tree.\n"
            "license: The topics restate Juniper Networks documentation. Juniper Networks holds the copyright.\n"
            "metadata:\n"
            "  feature: 2925-juniper-skill-factory\n"
            f"  domain: {item.domain.removeprefix('juniper-')}\n"
            "  documents: 1\n"
            f"  topics: {topic_count}\n"
            f"  source_pages: {item.pages}\n"
            f"  built: {built}\n"
            "---\n\n"
            f"# {self._title(item.domain)}\n\nRead the needed topic before you answer.\n\n"
            "## Route by subject\n\nRead `INDEX.md` to select the document and topic.\n\n"
            "## Class marks\n\nMUST marks a requirement. SHOULD marks a recommendation. INFO marks a fact.\n\n"
            "## Answer rules\n\nUse the cited cards only. Keep commands exact.\n\n"
            "## Copyright rule\n\nDo not copy Juniper source prose. Restate the fact in new words.\n\n"
            "## Precedence\n\nThe source citation controls the answer.\n\n"
            "## Scope\n\nThis skill covers the generated domain.\n"
        )  # Return the level 1 skill router.

    def _index_text(self, item: WorkItem, topic_paths: list[Path]) -> str:
        rows = [self._level_one_row(item, path) for path in topic_paths]  # Build subject route rows.
        return (
            f"# {self._title(item.domain)} index\n\n## Route by subject\n\n"
            "| Ask about | Read | Life cycle |\n| - | - | - |\n"
            + "".join(rows)
            + "\n## Route by source document\n\n| Document | Slug | Pages | Read |\n| - | - | -: | - |\n"
            f"| {item.title} | {self._slug(item.title)} | {item.pages} | "
            f"documents/{self._slug(item.title)}/INDEX.md |\n\n"
            "## Life cycle coverage\n\n| Stage | Topics | Status |\n| - | -: | - |\n"
            f"| day0 | 0 | gap |\n| day1 | 0 | gap |\n| day2 | {len(topic_paths)} | covered |\n"
            "| day2plus | 0 | gap |\n\n"
            "## Coverage gaps\n\nday0, day1, and day2plus need more source topics.\n"
        )  # Return the level 1 index.

    def _sources_text(self, item: WorkItem) -> str:
        return (
            "# Sources\n\n| Key | Title | Category | Pages | Markdown | PDF |\n| - | - | - | -: | - | - |\n"
            f"| DOC1 | {item.title} | {item.category} | {item.pages} | {item.document_key} | {item.source_pdf} |\n"
        )  # Return one source row for this generated document.

    def _topic_row(self, path: Path, base: Path) -> str:
        name = path.stem.replace("-", " ").title()  # Convert the slug to a readable route phrase.
        return f"| {name} | {name} | {path.relative_to(base)} | [day2] |\n"  # Return one level 2 route row.

    def _level_one_row(self, item: WorkItem, path: Path) -> str:
        relative = path.relative_to(self._package_dir(item))  # Store a package-relative topic path.
        return f"| {item.title} | {relative} | [day2] |\n"  # Return one level 1 route row.

    def _citation_row(self, row: str, index: int) -> str:
        return f"| DOC{index + 1} | p.0-0 | {index:02d}-topic-{index}.md |\n"  # Return one citation route row.

    def _package_dir(self, item: WorkItem) -> Path:
        return self.skills_path / item.domain  # Resolve the contract package directory.

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")  # Keep contract-safe lowercase path text.
        return slug or "document"  # Avoid empty path names for unusual titles.

    def _title(self, value: str) -> str:
        return value.replace("-", " ").title()  # Produce a readable Markdown heading.
