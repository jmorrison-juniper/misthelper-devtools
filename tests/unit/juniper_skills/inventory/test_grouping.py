"""Tests for Juniper inventory part grouping."""

from pathlib import Path  # Use Path so tests match the production path contract.

from src.juniper_skills.inventory.engine import PartSetGrouper  # Exercise the real grouping rule.
from src.juniper_skills.inventory.models import MarkdownPart, SourceRoot  # Build realistic part records.


class TestPartSetGrouper:
    """Verify split part grouping evidence."""

    def test_groups_by_common_title_when_split_source_names_differ(self, tmp_path: Path) -> None:
        root = SourceRoot("harvest", tmp_path, 3)  # Use one root so duplicate resolution does not affect grouping.
        first = self._part(
            root,
            tmp_path / "junos-space-workspaces-dc937b5989.md",
            "Junos Space Workspaces",
            "junos-space-workspaces-dc937b5989.pdf",
        )  # Build part one with a split-like source name.
        second = self._part(
            root,
            tmp_path / "junos-space-workspaces-dc937b5989-2.md",
            "Junos Space Workspaces",
            "junos-space-workspaces-dc937b5989-2.pdf",
        )  # Build part two with a split-like source name.
        groups = PartSetGrouper().group([first, second])  # Group the physical files into document candidates.
        assert len(groups) == 1  # The shared title must join the split set.
        assert groups[0].part_count == 2  # The grouped document must keep both physical parts.
        assert groups[0].group_method == "title"  # The report must state the measured grouping evidence.

    def test_groups_by_filename_similarity_without_front_matter(self, tmp_path: Path) -> None:
        root = SourceRoot("harvest", tmp_path, 3)  # Use one root to isolate filename grouping.
        first = self._part(root, tmp_path / "junos-space-workspaces.md", "", "")  # Build an untagged base file.
        second = self._part(
            root, tmp_path / "junos-space-workspaces-dc937b5989.md", "", ""
        )  # Build an untagged split file.
        groups = PartSetGrouper().group([first, second])  # Apply the fallback filename similarity rule.
        assert len(groups) == 1  # Similar split stems must join when metadata is absent.
        assert groups[0].group_method == "filename"  # The group must record filename evidence.

    def test_keeps_same_source_file_separate_across_roots(self, tmp_path: Path) -> None:
        first_root = SourceRoot("first", tmp_path / "first", 1)  # Create one conversion root.
        second_root = SourceRoot("second", tmp_path / "second", 2)  # Create a separate conversion root.
        first = self._part(
            first_root, first_root.path / "cli.md", "CLI", "cli-reference/cli-reference.pdf"
        )  # Build root one.
        second = self._part(
            second_root, second_root.path / "cli.md", "CLI", "cli-reference/cli-reference.pdf"
        )  # Build root two.
        groups = PartSetGrouper().group([first, second])  # Group parts before duplicate resolution.
        assert len(groups) == 2  # Cross-root copies are duplicates, not parts of one document.
        assert {group.root.name for group in groups} == {"first", "second"}  # Each root must stay separate.

    def _part(self, root: SourceRoot, path: Path, title: str, source_file: str) -> MarkdownPart:
        path.parent.mkdir(parents=True, exist_ok=True)  # Create the parent folder used by the part path.
        path.write_text("body", encoding="utf-8")  # Write a small body so the path exists for relative logic.
        fields = (
            {"title": title, "source_file": source_file, "pages": "2"} if title or source_file else {}
        )  # Add metadata.
        return MarkdownPart(
            str(path), root, path, path.name, "hash", 4, 4, fields, {"category": "guides"}, "ok"
        )  # Return part.
