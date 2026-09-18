"""Unit tests for the Juniper Markdown segmenter."""

from __future__ import annotations

import shutil
from pathlib import Path

from src.juniper_skills.segment import (  # Import the public segmenter surface under test.
    CommandBlockDetector,  # Test command re-fencing.
    DocumentSegmenter,  # Test topic segmentation.
    LifecycleClassifier,  # Test life cycle signal classification.
    OrphanWordRepairer,  # Test inline word repair.
    PartSetJoiner,  # Test part ordering.
    TopicSubjectBuilder,  # Test useful index subjects.
)


class TestCommandBlockDetector:
    """Verify command detection with real Junos guide text."""

    def test_refences_junos_prompt_and_output_verbatim(self) -> None:
        source = (
            "from our Gig-Ethernets:\n\n"
            "root@Router1> show interfaces terse | match ge | match inet\n\n"
            "ge-0/0/0.0 up up inet 10.10.12.1/24\n\n"
            "ge-0/0/1.0 up up inet 10.10.13.1/24\n\n"
            "Very clean!\n"
        )  # Use measured text from guides\junos-beginners-guide.md.
        result = CommandBlockDetector().refence_text(source)  # Fence commands without changing command text.
        assert (
            "```text\nroot@Router1> show interfaces terse | match ge | match inet" in result.text
        )  # Check fence start.
        assert "ge-0/0/0.0 up up inet 10.10.12.1/24" in result.text  # Check output stays verbatim.
        assert result.fenced_blocks == 1  # Check the command and its output stay in one block.
        assert result.command_lines == 3  # Check the command plus two output rows were measured.


class TestOrphanWordRepairer:
    """Verify inline-styled words return to the sentence above."""

    def test_repairs_junos_beginner_ge_orphan(self) -> None:
        source = (
            "at lines that have in them. With any luck, this will filter out all interfaces apart\n\n"
            "ge\n\n"
            "from our Gig-Ethernets:"
        )  # Use the measured defect from guides\junos-beginners-guide.md.
        result = OrphanWordRepairer().repair(source)  # Repair the converter line movement defect.
        assert "at lines that have ge in them." in result.text  # Check the orphan moved to the dangling phrase.
        assert "\nge\n" not in result.text  # Check the standalone orphan line was removed.
        assert result.orphan_words == 1  # Check the repair count is measurable.


class TestDocumentSegmenter:
    """Verify heading cleanup, oversize split, and page citation output."""

    def test_drops_cover_art_headings_before_real_structure(self) -> None:
        source = "\n".join(
            [
                "## DAY ONE",
                "## a Juniper network",
                "## configure",
                "## fake",
                "## LEARNING JUNOS",
                "<!-- page 14 -->",
                "## Chapter 1",
                "## Junos Fundamentals",
                "Body text.",
            ]
        )  # Use cover-art heading shapes from the guide.
        result = DocumentSegmenter().segment_text(source, "junos-beginners-guide")  # Segment the repaired document.
        combined = "\n".join(segment.text for segment in result.segments)  # Combine output for structural assertions.
        assert "## DAY ONE" not in combined  # Check the fake cover heading was removed.
        assert "a Juniper network" not in combined  # Check the cover region text was removed.
        assert "## Chapter 1" in combined  # Check the first real heading remains.
        assert result.repair.cover_headings == 5  # Check the cover-art repair reports its measured count.
        assert result.non_knowledge_sections == 1  # Check the removed cover region is measured.

    def test_splits_oversize_topic_below_hard_limit(self) -> None:
        paragraph = "This paragraph gives a measured routing fact for the segmenter. " * 80  # Build real prose shape.
        source = "<!-- page 1 -->\n## Large Topic\n\n" + "\n\n".join([paragraph] * 12)  # Build one oversize heading.
        result = DocumentSegmenter(hard_limit=4_096, soft_limit=2_048).segment_text(source, "large")  # Segment it.
        assert len(result.segments) > 1  # Check the oversize topic split into multiple topics.
        assert not result.hard_limit_breaks  # Check every topic obeys the hard limit.
        assert all(segment.page_start == 1 for segment in result.segments)  # Check page citation stays present.

    def test_repairs_duplicate_generic_topic_names(self) -> None:
        source = "\n".join(
            [
                "<!-- page 1 -->",
                "## CLI Basics",
                "Body.",
                "## Summary",
                "CLI summary.",
                "## Routing Basics",
                "Body.",
                "## Summary",
                "Routing summary.",
            ]
        )  # Build repeated generic headings like a converted book.
        result = DocumentSegmenter(soft_limit=20, tiny_limit=1).segment_text(source, "generic")  # Segment for names.
        titles = [segment.title for segment in result.segments]  # Read the names that route to topic files.
        assert result.duplicate_names == 0  # Check every topic route name is unique.
        assert "CLI Basics Summary" in titles  # Check a generic heading gains parent context.
        assert "Routing Basics Summary" in titles  # Check the second generic heading gains its own context.

    def test_writes_index_and_word_boundary_slugs(self) -> None:
        output = Path("data") / "juniper_skills" / "segment_write_test"  # Keep generated proof files in data.
        shutil.rmtree(output, ignore_errors=True)  # Remove files from a prior interrupted run.
        title = "Configuring Your Device Active Configs Versus Candidate Configuration"  # Use a long real title.
        source = f"<!-- page 7 -->\n## {title}\nBody text."  # Build a one-topic document with a long heading.
        try:  # Clean generated files even when an assertion fails.
            segmenter = DocumentSegmenter()  # Use the production segmenter defaults.
            result = segmenter.segment_text(source, "slug")  # Segment the test document.
            segmenter.write_topic_tree(result, output, "SLUG")  # Write topic files and the level 2 index.
            files = {path.name for path in output.glob("*.md")}  # Read generated Markdown file names.
            assert "INDEX.md" in files  # Check the required level 2 index exists.
            assert not any(name.endswith("conf.md") for name in files)  # Check the slug does not cut a word.
            assert any(name.endswith("candidate.md") for name in files)  # Check truncation keeps a whole word.
        finally:  # Always clean repository-local generated files.
            shutil.rmtree(output, ignore_errors=True)  # Remove the generated topic tree.


class TestLifecycleIndexMetadata:
    """Verify indexes carry useful life cycle tags and subjects."""

    def test_classifies_each_life_cycle_stage(self) -> None:
        classifier = LifecycleClassifier()  # Use the production classifier rules.
        assert "day0" in classifier.classify("MX Series Routers", "platform model throughput").tags  # Design.
        assert "day1" in classifier.classify("Initial Configuration", "set system host-name router").tags  # Setup.
        assert "day2" in classifier.classify("Interface Statistics", "show interfaces extensive").tags  # Operate.
        assert "day2plus" in classifier.classify("Rescue Configuration", "rollback and rescue").tags  # Change.

    def test_builds_subject_from_commands_and_concepts(self) -> None:
        text = "show interfaces terse | match ge | except inet | count"  # Use filtering command content.
        subject = TopicSubjectBuilder().build("Filtering Output", text, ["day2"])  # Build a routing subject.
        assert "match, count, and except pipe filters" in subject  # Check the subject adds command detail.
        assert "operational command output" in subject  # Check the subject states the question area.


class TestPartSetJoiner:
    """Verify part ordering uses part and page metadata."""

    def test_joins_parts_in_page_order(self) -> None:
        root = (
            Path("data") / "juniper_skills" / "segment_test_parts"
        )  # Keep test files inside the repository data tree.
        shutil.rmtree(root, ignore_errors=True)  # Remove leftovers from a prior interrupted test run.
        root.mkdir(parents=True, exist_ok=True)  # Create the controlled local test directory.
        first = root / "part-001.md"  # Name the first physical part like the converter output.
        second = root / "part-002.md"  # Name the second physical part like the converter output.
        second.write_text(self._part_text(2, "3-4", 3, "second body"), encoding="utf-8")  # Write out of order.
        first.write_text(self._part_text(1, "1-2", 1, "first body"), encoding="utf-8")  # Write the first part second.
        try:  # Clean test files even when an assertion fails.
            joined = PartSetJoiner().join_paths([second, first])  # Join paths in a deliberately wrong order.
            assert joined.text.index("first body") < joined.text.index("second body")  # Check page order wins.
            assert joined.page_start == 1  # Check the joined citation start.
            assert joined.page_end == 4  # Check the joined citation end.
        finally:  # Always clean repository-local test files.
            shutil.rmtree(root, ignore_errors=True)  # Remove the controlled local test directory.

    def _part_text(self, part: int, page_range: str, page: int, body: str) -> str:
        lines = [  # Build converter-style Markdown lines.
            "---",  # Start front matter like the converter.
            'source_file: "guides/example.pdf"',  # Use one source file for the part set.
            f"part: {part}",  # Store explicit part order.
            f'page_range: "{page_range}"',  # Store explicit page order.
            "---",  # End front matter like the converter.
            "",  # Separate front matter from body.
            f"<!-- page {page} -->",  # Store the universal citation marker.
            body,  # Store the part body.
        ]
        return "\n".join(lines) + "\n"  # Return one converter-style Markdown document.
