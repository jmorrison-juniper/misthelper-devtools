"""Build contract-sized topic units from one joined Markdown document."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from src.juniper_skills.segment.commands import CommandBlockDetector
from src.juniper_skills.segment.lifecycle import LifecycleClassifier
from src.juniper_skills.segment.repair import DefectRepairResult, DocumentDefectRepairer
from src.juniper_skills.segment.subjects import TopicSubjectBuilder

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class TopicSegment:
    """One bounded source topic for the rewrite stage."""

    index: int  # Store the stable topic order inside one document.
    title: str  # Store the heading or generated topic title.
    text: str  # Store repaired source text for the rewriter.
    page_start: int  # Store the first cited page in this topic.
    page_end: int  # Store the last cited page in this topic.
    size_bytes: int  # Store the UTF-8 size for contract proof.
    lifecycle: list[str] | None = None  # Store the life cycle tags that route the topic.
    lifecycle_signals: dict[str, list[str]] | None = None  # Store evidence for each selected life cycle tag.
    subject: str = ""  # Store the one-line index subject that helps routing.

    def source_key(self, document_code: str) -> str:
        """Run the source key operation."""
        return f"{document_code} p.{self.page_start}-{self.page_end}"  # Build the contract citation key.


@dataclass(frozen=True)
class SegmenterResult:
    """The segmentation result and its measured proof values."""

    segments: list[TopicSegment]  # Store all bounded topic units.
    repair: DefectRepairResult  # Store converter defect repair counts.
    command_blocks: int  # Store the number of new command fences.
    command_lines: int  # Store the number of command or output lines fenced.
    hard_limit_breaks: list[TopicSegment]  # Store any topic that still violates the hard limit.
    duplicate_names: int = 0  # Store the count of duplicate topic names after title repair.
    non_knowledge_sections: int = 0  # Store the count of sections dropped because they are not knowledge.


@dataclass(frozen=True)
class _Chunk:
    """Internal chunk built from one heading range or one oversize slice."""

    title: str  # Store a readable title for merged topics.
    text: str  # Store the chunk source text.
    page_start: int  # Store the first page marker in the chunk.
    page_end: int  # Store the last page marker in the chunk.


class PageTracker:
    """Attach page numbers to Markdown lines from converter markers."""

    PAGE_PATTERN = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)  # Match universal page markers.

    def annotate(self, lines: list[str]) -> list[tuple[str, int]]:
        """Run the annotate operation."""
        logger.info("Annotating %s lines with page markers", len(lines))  # Log before citation mapping.
        page = 0  # Use zero until the first marker appears.
        annotated: list[tuple[str, int]] = []  # Accumulate each source line with its active page.
        for line in lines:  # Walk the document in source order.
            match = self.PAGE_PATTERN.search(line)  # Detect whether this line changes the active page.
            page = int(match.group(1)) if match else page  # Update the page after a marker appears.
            annotated.append((line, page))  # Attach the current page to the line.
        logger.debug("Annotated %s lines through page %s", len(annotated), page)  # Report mapping size and last page.
        return annotated


class DocumentSegmenter:
    """Segment repaired Markdown on heading hierarchy with contract size limits."""

    HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.+?)\s*$")  # Use level-2 headings as the top source level.
    GENERIC_TITLES = {"overview", "summary"}  # Name headings that need context for routing.
    NON_KNOWLEDGE_PATTERN = re.compile(
        r"^(?:acknowledg|author|biograph|copyright|j-net|legal|technical reviewer|trademark)",
        re.IGNORECASE,
    )  # Match sections that do not teach network operations.
    ORDINAL_PATTERN = re.compile(r"^(?:chapter|part|section)\s+\d+$", re.IGNORECASE)  # Match bare ordinals.

    def __init__(self, soft_limit: int = 12_288, hard_limit: int = 20_480, tiny_limit: int = 2_048) -> None:
        """Initialize the DocumentSegmenter instance."""
        self.soft_limit = soft_limit  # Store the topic size target from the locked contract.
        self.hard_limit = hard_limit  # Store the maximum topic size from the locked contract.
        self.body_limit = hard_limit - 1_536  # Reserve bytes for topic front matter and final fence growth.
        self.tiny_limit = tiny_limit  # Store the merge threshold for one-paragraph sections.
        self.repairer = DocumentDefectRepairer()  # Repair converter defects before heading segmentation.
        self.detector = CommandBlockDetector()  # Re-fence Junos command blocks before topic splitting.
        self.page_tracker = PageTracker()  # Preserve page citation ranges for every topic.
        self.lifecycle = LifecycleClassifier()  # Classify topics with the locked life cycle contract signals.
        self.subjects = TopicSubjectBuilder()  # Build useful one-line subjects for document indexes.

    def segment_text(self, text: str, document_slug: str = "document") -> SegmenterResult:
        """Run the segment text operation."""
        logger.info("Segmenting document %s", document_slug)  # Log before any document transformation.
        body = self._strip_front_matter(text)  # Remove converter front matter from topic content.
        repair = self.repairer.repair(body)  # Repair measured converter defects first.
        command_result = self.detector.refence_text(repair.text)  # Fence commands while preserving exact command text.
        chunks = self._initial_chunks(command_result.text)  # Split on the observed heading hierarchy.
        chunks, dropped = self._filter_non_knowledge(chunks)  # Remove front matter sections that do not teach facts.
        bounded = self._bound_chunks(chunks)  # Split oversize chunks before topic merge.
        segments = self._merge_chunks(bounded)  # Merge tiny adjacent chunks without breaking the hard limit.
        segments = self._repair_titles(segments)  # Make every topic name unique and meaningful.
        segments = self._enrich_segments(segments)  # Add life cycle tags and subjects after final names are stable.
        breaks = [segment for segment in segments if segment.size_bytes > self.hard_limit]  # Report failed topics.
        duplicates = self._duplicate_count(segments)  # Prove that routing names do not collide.
        non_knowledge = repair.non_knowledge_sections + dropped  # Combine text repair drops and section drops.
        logger.debug(
            "Document %s produced %s topics with %s hard breaks", document_slug, len(segments), len(breaks)
        )  # Report.
        return SegmenterResult(
            segments,
            repair,
            command_result.fenced_blocks,
            command_result.command_lines,
            breaks,
            duplicates,
            non_knowledge,
        )  # Return.

    def _strip_front_matter(self, text: str) -> str:
        if not text.startswith("---\n"):  # Keep text unchanged when no front matter exists.
            return text
        end = text.find("\n---", 4)  # Find the closing front matter marker.
        return text[end + 4 :].lstrip("\n") if end != -1 else text  # Remove only valid front matter.

    def _initial_chunks(self, text: str) -> list[_Chunk]:
        logger.info("Splitting document on Markdown heading hierarchy")  # Log before structural segmentation.
        annotated = self.page_tracker.annotate(text.splitlines())  # Attach citation pages to every source line.
        heading_indexes = [index for index, row in enumerate(annotated) if self.HEADING_PATTERN.match(row[0])]  # Find.
        chunks = (
            self._chunks_from_headings(annotated, heading_indexes) if heading_indexes else [self._make_chunk(annotated)]
        )  # Build.
        logger.debug("Heading split produced %s chunks", len(chunks))  # Report initial structural count.
        return chunks

    def _chunks_from_headings(self, rows: list[tuple[str, int]], indexes: list[int]) -> list[_Chunk]:
        starts = [0] if indexes[0] != 0 else []  # Keep preface text as the overview chunk.
        starts.extend(indexes)  # Add every heading as a possible topic boundary.
        stops = [*starts[1:], len(rows)]  # End each chunk at the next boundary.
        chunks: list[_Chunk] = []  # Accumulate heading chunks without a long comprehension.
        for start, stop in zip(starts, stops, strict=False):  # Pair each boundary with the next boundary.
            if rows[start:stop]:  # Skip empty ranges that can appear at a document edge.
                chunks.append(self._make_chunk(rows[start:stop]))  # Build one source chunk for this range.
        return chunks  # Return heading chunks in source order.

    def _make_chunk(self, rows: list[tuple[str, int]]) -> _Chunk:
        text = "\n".join(line for line, _page in rows).strip()  # Keep source lines verbatim inside this chunk.
        title = self._title_for(rows, "overview")  # Use the first heading when one exists.
        pages = [page for _line, page in rows if page > 0]  # Ignore zero before the first page marker.
        page_start = min(pages) if pages else 0  # Record the first cited page.
        page_end = max(pages) if pages else page_start  # Record the last cited page.
        return _Chunk(title, text, page_start, page_end)  # Return the internal chunk.

    def _title_for(self, rows: list[tuple[str, int]], fallback: str) -> str:
        for line, _page in rows:  # Search the chunk for its first heading.
            match = self.HEADING_PATTERN.match(line)  # Parse the heading line when present.
            if match:  # Use the heading text as the topic title.
                return match.group(2).strip()
        return fallback  # Use overview for preface chunks.

    def _bound_chunks(self, chunks: list[_Chunk]) -> list[_Chunk]:
        logger.info("Applying topic hard size limit to %s chunks", len(chunks))  # Log before oversize handling.
        bounded: list[_Chunk] = []  # Accumulate chunks that obey the hard limit.
        for chunk in chunks:  # Check each heading chunk independently.
            if self._size(chunk.text) <= self.body_limit:  # Keep chunks that leave room for final front matter.
                bounded.append(chunk)  # Add the safe chunk to the output list.
                continue
            bounded.extend(self._split_large_chunk(chunk))  # Split oversize chunks at paragraph boundaries.
        logger.debug("Hard limit split produced %s chunks", len(bounded))  # Report bounded chunk count.
        return bounded

    def _split_large_chunk(self, chunk: _Chunk) -> list[_Chunk]:
        rows = self.page_tracker.annotate(chunk.text.splitlines())  # Reattach pages for paragraph splitting.
        rows = [(line, page or chunk.page_start) for line, page in rows]  # Inherit pages when a marker was earlier.
        groups = self._paragraph_groups(rows)  # Use blank lines as lower-level boundaries.
        parts = self._pack_groups(chunk.title, groups)  # Pack paragraphs into hard-limit chunks.
        return parts  # Return the lower-level chunks.

    def _paragraph_groups(self, rows: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
        groups: list[list[tuple[str, int]]] = []  # Accumulate paragraph groups.
        current: list[tuple[str, int]] = []  # Store the current paragraph lines.
        for row in rows:  # Split on blank lines while preserving source line text.
            if row[0].strip():  # Nonblank lines belong to the current paragraph.
                current.append(row)  # Keep the source row in its paragraph.
            elif current:  # A blank closes the current paragraph group.
                groups.append(current)  # Store the completed paragraph.
                current = []  # Start a new paragraph after the blank.
        return [*groups, current] if current else groups  # Include the final paragraph when present.

    def _pack_groups(self, title: str, groups: list[list[tuple[str, int]]]) -> list[_Chunk]:
        chunks: list[_Chunk] = []  # Accumulate packed paragraph chunks.
        current: list[tuple[str, int]] = []  # Store rows for the current packed chunk.
        for group in groups:  # Add groups until the next one would break the hard limit.
            for unit in self._split_large_group(group):  # Reduce an oversize paragraph before packing.
                candidate = [*current, *unit]  # Build a trial chunk for size checking.
                if current and self._size_rows(candidate) > self.body_limit:  # Close the chunk before overflow.
                    chunks.append(self._make_chunk(current))  # Store the current hard-limit chunk.
                    current = unit  # Start the next chunk with the unit that did not fit.
                else:  # The paragraph unit fits in the current chunk.
                    current = candidate  # Keep growing the current chunk.
        return [*chunks, self._make_chunk(current)] if current else chunks  # Return all packed chunks.

    def _split_large_group(self, group: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
        if self._size_rows(group) <= self.body_limit:  # Keep normal paragraphs intact.
            return [group]
        units: list[list[tuple[str, int]]] = []  # Accumulate word-bounded paragraph slices.
        current: list[tuple[str, int]] = []  # Store rows for the current slice.
        for line, page in group:  # Split each oversize prose row without changing word order.
            for unit_line in self._split_large_line(line):  # Break a long row at word boundaries.
                candidate = [*current, (unit_line, page)]  # Test whether the line fits in the current slice.
                if current and self._size_rows(candidate) > self.body_limit:  # Flush before the slice overflows.
                    units.append(current)  # Store the completed paragraph slice.
                    current = [(unit_line, page)]  # Start a new slice with the current line.
                else:  # The line fits in the current slice.
                    current = candidate  # Keep growing the current slice.
        return [*units, current] if current else units  # Return all slices for packing.

    def _split_large_line(self, line: str) -> list[str]:
        if self._size(line) <= self.body_limit:  # Keep normal source lines unchanged.
            return [line]
        words = line.split(" ")  # Split only on spaces so words and numbers stay unchanged.
        lines: list[str] = []  # Accumulate safe line slices.
        current = ""  # Store the current line slice.
        for word in words:  # Pack words until the hard limit would be exceeded.
            candidate = f"{current} {word}".strip()  # Build a trial line slice.
            if current and self._size(candidate) > self.body_limit:  # Flush before overflow.
                lines.append(current)  # Store the completed line slice.
                current = word  # Start the next line slice.
            else:  # The word fits in the current line slice.
                current = candidate  # Keep the word in the current line slice.
        return [*lines, current] if current else lines  # Return all safe line slices.

    def _merge_chunks(self, chunks: list[_Chunk]) -> list[TopicSegment]:
        logger.info("Merging tiny sections into bounded topics")  # Log before small-section merge.
        segments: list[TopicSegment] = []  # Store final topic segments.
        current: list[_Chunk] = []  # Store chunks for the current topic.
        for chunk in chunks:  # Pack chunks until the soft target or hard limit is reached.
            current = self._merge_one_chunk(segments, current, chunk)  # Add or flush one chunk.
        if current:  # Flush the final topic after the loop.
            self._append_segment(segments, current)  # Convert the final chunks into one topic segment.
        logger.debug("Merged chunks into %s topic segments", len(segments))  # Report final topic count.
        return segments

    def _merge_one_chunk(self, segments: list[TopicSegment], current: list[_Chunk], chunk: _Chunk) -> list[_Chunk]:
        candidate = [*current, chunk]  # Build a trial topic for size checking.
        candidate_size = self._size("\n\n".join(item.text for item in candidate))  # Measure the trial topic.
        if current and candidate_size > self.body_limit:  # Flush before adding a chunk that would break the contract.
            self._append_segment(segments, current)  # Store the current topic.
            return [chunk]  # Start the next topic with the chunk that did not fit.
        if candidate_size >= self.soft_limit and self._size(chunk.text) >= self.tiny_limit:  # Avoid over-merging.
            self._append_segment(segments, candidate)  # Store the topic once it reaches the soft target.
            return []  # Start a new empty topic.
        return candidate  # Continue merging tiny sections.

    def _append_segment(self, segments: list[TopicSegment], chunks: list[_Chunk]) -> None:
        text = "\n\n".join(
            chunk.text for chunk in chunks if chunk.text
        ).strip()  # Combine source chunks with separators.
        pages = [page for chunk in chunks for page in [chunk.page_start, chunk.page_end] if page > 0]  # Collect pages.
        title = self._segment_title(chunks)  # Pick the first meaningful title inside the merged topic.
        segment = TopicSegment(
            len(segments), title, text, min(pages) if pages else 0, max(pages) if pages else 0, self._size(text)
        )  # Build topic.
        segments.append(segment)  # Store the final topic in source order.

    def _segment_title(self, chunks: list[_Chunk]) -> str:
        for chunk in chunks:  # Search merged chunks for the first meaningful routing title.
            if not self._is_generic(chunk.title):  # Skip overview, summary, and bare ordinal headings.
                return chunk.title
        return chunks[0].title if chunks else "overview"  # Fall back when a topic has no substantive heading.

    def _filter_non_knowledge(self, chunks: list[_Chunk]) -> tuple[list[_Chunk], int]:
        logger.info("Filtering non-knowledge sections from %s chunks", len(chunks))  # Log before content filtering.
        kept = [chunk for chunk in chunks if not self.NON_KNOWLEDGE_PATTERN.match(chunk.title)]  # Drop front matter.
        dropped = len(chunks) - len(kept)  # Count removed sections for the proof report.
        logger.debug("Dropped %s non-knowledge sections", dropped)  # Report removed section count.
        return kept, dropped  # Return only sections that can become useful topics.

    def _repair_titles(self, segments: list[TopicSegment]) -> list[TopicSegment]:
        logger.info("Repairing %s topic names for routing", len(segments))  # Log before title cleanup.
        titles = self._meaningful_titles(segments)  # Replace bare ordinals and generic headings.
        unique = self._unique_titles(titles)  # Add context until every routing name is unique.
        repaired = [replace(segment, title=unique[index]) for index, segment in enumerate(segments)]  # Apply titles.
        logger.debug("Topic title repair left %s duplicates", self._duplicate_count(repaired))  # Report result.
        return repaired  # Return segments with production-safe topic names.

    def _meaningful_titles(self, segments: list[TopicSegment]) -> list[str]:
        titles: list[str] = []  # Accumulate one title for each segment.
        previous = ""  # Store the last useful subject for generic headings.
        for index, segment in enumerate(segments):  # Inspect titles in source order.
            title = self._derived_title(segment, segments, index, previous)  # Pick the most useful available name.
            previous = title if not self._is_generic(title) else previous  # Keep a context subject for summaries.
            titles.append(title)  # Store the repaired title.
        return titles  # Return title candidates in segment order.

    def _derived_title(self, segment: TopicSegment, segments: list[TopicSegment], index: int, previous: str) -> str:
        title = segment.title.strip()  # Start with the structural heading from the source.
        if self.ORDINAL_PATTERN.match(title):  # Replace a bare ordinal with the next substantive heading.
            return self._next_substantive_title(segments, index) or title  # Use the next topic subject when present.
        if title.lower() in self.GENERIC_TITLES and previous:  # Qualify a generic heading with its parent subject.
            return f"{previous} {title}"  # Preserve the generic word but add routing context.
        return title  # Keep already meaningful headings unchanged.

    def _next_substantive_title(self, segments: list[TopicSegment], index: int) -> str:
        for later in segments[index + 1 :]:  # Search later topics for the first useful subject.
            title = later.title.strip()  # Normalize only for title tests.
            if title and not self._is_generic(title):  # Skip overview, summary, and bare ordinals.
                return title
        return ""  # Return empty when no better title exists.

    def _is_generic(self, title: str) -> bool:
        lowered = title.lower()  # Compare in lower case for stable matching.
        return lowered in self.GENERIC_TITLES or bool(self.ORDINAL_PATTERN.match(title))  # Detect weak names.

    def _unique_titles(self, titles: list[str]) -> list[str]:
        used: dict[str, int] = {}  # Count title slugs that routing would see as identical.
        unique: list[str] = []  # Store collision-free titles.
        for title in titles:  # Process each repaired title in source order.
            slug = self._slug(title)  # Measure uniqueness with the same slug rule used for files.
            count = used.get(slug, 0)  # Read the current collision count.
            used[slug] = count + 1  # Record this title occurrence.
            unique.append(title if count == 0 else f"{title} {count + 1}")  # Add an ordinal only for real collisions.
        return unique  # Return names with no duplicate routing slug.

    def _duplicate_count(self, segments: list[TopicSegment]) -> int:
        slugs = [self._slug(segment.title) for segment in segments]  # Convert titles to routing identifiers.
        return len(slugs) - len(set(slugs))  # Return the number of duplicate route names.

    def _enrich_segments(self, segments: list[TopicSegment]) -> list[TopicSegment]:
        logger.info("Adding life cycle tags and subjects to %s topics", len(segments))  # Log before enrichment.
        enriched = [self._enrich_segment(segment) for segment in segments]  # Classify each final topic independently.
        logger.debug("Added life cycle metadata to %s topics", len(enriched))  # Report enrichment count.
        return enriched  # Return topics with index metadata.

    def _enrich_segment(self, segment: TopicSegment) -> TopicSegment:
        classification = self.lifecycle.classify(segment.title, segment.text)  # Score contract life cycle signals.
        subject = self.subjects.build(segment.title, segment.text, classification.tags)  # Build a useful subject.
        return replace(
            segment, lifecycle=classification.tags, lifecycle_signals=classification.signals, subject=subject
        )  # Store derived metadata on the topic.

    def _size_rows(self, rows: list[tuple[str, int]]) -> int:
        return self._size("\n".join(line for line, _page in rows))  # Measure rows as UTF-8 bytes.

    def _size(self, text: str) -> int:
        return len(text.encode("utf-8"))  # Measure the contract limit in bytes.

    def write_topic_tree(self, result: SegmenterResult, output_dir: Path, document_code: str) -> None:
        """Create the write topic tree output."""
        logger.info("Writing %s segment topic files to %s", len(result.segments), output_dir)  # Log before writes.
        output_dir.mkdir(parents=True, exist_ok=True)  # Create the destination tree for generated topics.
        for segment in result.segments:  # Write each topic segment with citation metadata.
            self._write_segment(segment, output_dir, document_code)  # Write one segment file.
        self._write_index(result, output_dir)  # Write the required level 2 document index.
        logger.debug("Wrote %s segment topic files", len(result.segments))  # Report write completion.

    def _write_segment(self, segment: TopicSegment, output_dir: Path, document_code: str) -> None:
        filename = f"{segment.index:02d}-{self._slug(segment.title)}.md"  # Build a stable topic file name.
        content = self._topic_content(segment, document_code)  # Build the source topic file with front matter.
        (output_dir / filename).write_text(content, encoding="utf-8")  # Write the topic content for downstream use.

    def _topic_content(self, segment: TopicSegment, document_code: str) -> str:
        source_key = segment.source_key(document_code)  # Build the exact citation key for this source topic.
        lifecycle = ", ".join(segment.lifecycle or ["day0"])  # Format tags as a normalized front matter array.
        header = f"---\ntopic: {segment.title}\nlifecycle: [{lifecycle}]\nsources: [{source_key}]\n---"  # Build header.
        return f"{header}\n\n{segment.text}\n"  # Return content with the source text unchanged.

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")  # Make a file-safe slug from the topic title.
        if len(slug) <= 60:  # Keep short names unchanged.
            return slug or "overview"
        trimmed = slug[:60].rsplit("-", 1)[0]  # Cut at a word boundary instead of inside a word.
        return trimmed or slug[:60]  # Fall back only when no word boundary exists.

    def _write_index(self, result: SegmenterResult, output_dir: Path) -> None:
        logger.info("Writing level 2 INDEX.md to %s", output_dir)  # Log before the required index write.
        lines = ["# Document index", "", "| Topic | Pages | Life cycle | Subject |", "| - | - | - | - |"]  # Header.
        for segment in result.segments:  # Add one row for each topic in the document.
            lines.append(self._index_row(segment))  # Store topic routing data for the agent.
        (output_dir / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")  # Write the index file.
        logger.debug("Wrote INDEX.md with %s topic rows", len(result.segments))  # Report index row count.

    def _index_row(self, segment: TopicSegment) -> str:
        pages = f"p.{segment.page_start}-{segment.page_end}"  # Format the exact page range.
        lifecycle = ", ".join(segment.lifecycle or ["day0"])  # Format one or more life cycle tags.
        subject = segment.subject or self._subject(segment)  # Use enriched subject with a safe fallback.
        return f"| {segment.title} | {pages} | {lifecycle} | {subject} |"  # Return one Markdown table row.

    def _subject(self, segment: TopicSegment) -> str:
        return f"Topic covers {segment.title}."  # State the subject without copying source prose.
