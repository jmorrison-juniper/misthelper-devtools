"""Join ordered Markdown parts before segmentation."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class DocumentPartContent:
    """One ordered physical Markdown part."""

    path: Path  # Store the physical file for audit output.
    text: str  # Store body text without front matter.
    front_matter: dict[str, str]  # Store converter metadata for ordering and citation.
    part_number: int  # Store the explicit or inferred part number.
    page_start: int  # Store the first page marker or front matter page.
    page_end: int  # Store the last page marker or front matter page.
    source_root: Path  # Store the corpus root that prevents cross-root joins.


@dataclass(frozen=True)
class JoinedDocument:
    """The logical Markdown document built from ordered physical parts."""

    text: str  # Store the joined body for segmentation.
    parts: list[DocumentPartContent]  # Store ordered part records for proof output.
    source_file: str  # Store the source PDF name from front matter when present.
    title: str  # Store the document title from front matter when present.
    page_start: int  # Store the first page covered by the joined document.
    page_end: int  # Store the last page covered by the joined document.


class FrontMatterReader:
    """Read simple YAML-like front matter from converted Markdown."""

    PAGE_PATTERN = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)  # Match converter page markers.

    def read(self, text: str) -> tuple[dict[str, str], str]:
        """Run the read operation."""
        logger.info("Reading Markdown front matter")  # Log before parsing converter metadata.
        if not text.startswith("---\n"):  # A document without front matter still can be segmented.
            logger.debug("Read %s front matter keys", 0)  # Report the missing front matter.
            return {}, text
        end = text.find("\n---", 4)  # Locate the closing marker for the front matter block.
        if end == -1:  # Malformed front matter must not stop segmentation.
            logger.debug("Read %s front matter keys", 0)  # Report that parsing failed safely.
            return {}, text
        front_matter = self._parse_block(text[4:end])  # Parse key and scalar value pairs.
        body = text[end + 4 :].lstrip("\n")  # Remove only the front matter block from the body.
        logger.debug("Read %s front matter keys", len(front_matter))  # Report parsed key count.
        return front_matter, body

    def page_markers(self, text: str) -> list[int]:
        """Run the page markers operation."""
        logger.info("Reading page markers from Markdown")  # Log before page marker extraction.
        pages = [int(match.group(1)) for match in self.PAGE_PATTERN.finditer(text)]  # Read all page marker numbers.
        logger.debug("Read %s page markers", len(pages))  # Report citation marker count.
        return pages

    def _parse_block(self, block: str) -> dict[str, str]:
        front_matter: dict[str, str] = {}  # Accumulate scalar front matter keys only.
        for line in block.splitlines():  # Parse each front matter line independently.
            if ":" not in line:  # Skip comments or malformed converter lines.
                continue
            key, value = line.split(":", 1)  # Split once so subjects can contain colons.
            front_matter[key.strip()] = value.strip().strip('"')  # Store unquoted scalar values.
        return front_matter  # Return the parsed metadata map.


class PartSetJoiner:
    """Join physical Markdown parts from one source root in page order."""

    RANGE_PATTERN = re.compile(r"(\d+)\s*-\s*(\d+)")  # Match page_range front matter.
    PART_PATTERN = re.compile(r"part-(\d+)", re.IGNORECASE)  # Match converter part file names.

    def __init__(self) -> None:
        """Initialize the PartSetJoiner instance."""
        self.reader = FrontMatterReader()  # Reuse the same parser for every part.

    def join_paths(self, paths: Sequence[Path]) -> JoinedDocument:
        """Run the join paths operation."""
        logger.info("Joining %s Markdown parts from explicit paths", len(paths))  # Log path input count.
        parts = [self._read_path(Path(path)) for path in paths]  # Read each path into a structured part record.
        self._validate_same_root(parts)  # Prevent cross-root duplicate joins from becoming one document.
        joined = self._join_parts(parts)  # Sort and concatenate the parts into one logical document.
        logger.debug(
            "Joined %s parts covering pages %s-%s", len(joined.parts), joined.page_start, joined.page_end
        )  # Report.
        return joined

    def join_inventory_group(self, group: Any) -> JoinedDocument:
        """Run the join inventory group operation."""
        logger.info("Joining Markdown parts from inventory group")  # Log that the inventory authority supplied parts.
        paths = [Path(part.path) for part in group.parts]  # Consume the inventory grouping output without regrouping.
        joined = self.join_paths(paths)  # Reuse the same order and validation path.
        logger.debug("Joined inventory group with %s parts", len(joined.parts))  # Report inventory join count.
        return joined

    def _read_path(self, path: Path) -> DocumentPartContent:
        logger.info("Reading Markdown part %s", path)  # Log before disk access.
        text = path.read_text(encoding="utf-8", errors="ignore")  # Decode converter Markdown safely.
        front_matter, body = self.reader.read(text)  # Remove front matter before joining bodies.
        pages = self._page_span(front_matter, body)  # Read the citation range from metadata or page markers.
        part_number = self._part_number(path, front_matter, pages[0])  # Pick the most precise order key.
        root = self._source_root(path)  # Identify the corpus root for cross-root protection.
        logger.debug("Read part %s as part %s with pages %s-%s", path.name, part_number, pages[0], pages[1])  # Report.
        return DocumentPartContent(path, body, front_matter, part_number, pages[0], pages[1], root)  # Return part.

    def _join_parts(self, parts: list[DocumentPartContent]) -> JoinedDocument:
        ordered = sorted(parts, key=lambda part: (part.part_number, part.page_start, part.path.name))  # Sort by pages.
        text = "\n\n".join(part.text.strip("\n") for part in ordered if part.text.strip())  # Join bodies only.
        first = ordered[0]  # Use the first ordered part for shared metadata.
        source_file = first.front_matter.get("source_file", first.path.name)  # Preserve source PDF evidence.
        title = first.front_matter.get("title", first.path.stem)  # Preserve title evidence for reports.
        page_start = min(part.page_start for part in ordered)  # Compute exact citation start.
        page_end = max(part.page_end for part in ordered)  # Compute exact citation end.
        return JoinedDocument(text, ordered, source_file, title, page_start, page_end)  # Return joined document.

    def _page_span(self, front_matter: dict[str, str], body: str) -> tuple[int, int]:
        range_value = front_matter.get("page_range", "")  # Prefer explicit split metadata when present.
        match = self.RANGE_PATTERN.search(range_value)  # Parse values such as 251-500.
        if match:  # Return the explicit range when the converter supplied it.
            return int(match.group(1)), int(match.group(2))
        pages = self.reader.page_markers(body)  # Fall back to universal page markers.
        return (min(pages), max(pages)) if pages else (0, 0)  # Return zero range only when citation data is absent.

    def _part_number(self, path: Path, front_matter: dict[str, str], page_start: int) -> int:
        value = front_matter.get("part", "")  # Prefer explicit part metadata.
        if value.isdigit():  # Use the converter part number when it exists.
            return int(value)
        match = self.PART_PATTERN.search(path.stem)  # Fall back to part-N file names.
        return int(match.group(1)) if match else page_start  # Page order is the final deterministic key.

    def _source_root(self, path: Path) -> Path:
        resolved = path.resolve()  # Normalize the path before root comparison.
        markers = {"juniper-harvest-md", "markdown", "markdown2"}  # Name the locked source roots.
        for index, part in enumerate(resolved.parts):  # Find the first known corpus root name.
            if part in markers:  # Stop when the path sits inside a locked root.
                return Path(*resolved.parts[: index + 1])  # Return the root directory itself.
        return resolved.parent  # Fall back to the file parent for test fixtures.

    def _validate_same_root(self, parts: list[DocumentPartContent]) -> None:
        roots = {part.source_root for part in parts}  # Collect unique source roots from the proposed part set.
        if len(roots) > 1:  # A repeated source_file across roots is a duplicate, not a split document.
            raise ValueError("Part set contains files from more than one source root")
