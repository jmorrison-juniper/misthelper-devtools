"""Tests for Juniper inventory duplicate resolution."""

from pathlib import Path  # Use Path to create realistic part locations.

from src.juniper_skills.inventory.engine import DuplicateResolver, PriorityScorer  # Test the production rules.
from src.juniper_skills.inventory.models import DocumentGroup, MarkdownPart, SourceRoot  # Build inventory records.


class TestDuplicateResolver:
    """Verify duplicate winner selection."""

    def test_prefers_newest_root_then_front_matter_then_text_yield(self, tmp_path: Path) -> None:
        old_root = SourceRoot("archive-markdown", tmp_path / "old", 1)  # Give the archive the lowest recency rank.
        new_root = SourceRoot("juniper-harvest-md", tmp_path / "new", 3)  # Give the harvest root the newest rank.
        old_group = self._group(old_root, 900, True)  # Build an older high-yield conversion.
        new_group = self._group(new_root, 100, True)  # Build a newer lower-yield conversion.
        decisions = DuplicateResolver().resolve([old_group, new_group])  # Resolve duplicate groups by rule.
        assert decisions[0].winner.root.name == "juniper-harvest-md"  # Newest conversion wins before yield.
        assert decisions[0].losers[0].duplicate_of == decisions[0].canonical_key  # Losers keep the winner link.

    def test_priority_scores_cli_reference_above_flyer(self, tmp_path: Path) -> None:
        root = SourceRoot("juniper-harvest-md", tmp_path, 3)  # Use one root so category drives the comparison.
        cli_group = self._group(root, 100, True, category="cli-reference", pages=1000)  # Build a high-value CLI doc.
        flyer_group = self._group(root, 100, True, category="flyers", pages=1000)  # Build a low-value flyer.
        scorer = PriorityScorer()  # Use the tunable production scorer.
        assert scorer.score(cli_group) > scorer.score(flyer_group)  # CLI reference must outrank a flyer.

    def _group(
        self,
        root: SourceRoot,
        text_chars: int,
        front_matter: bool,
        category: str = "guides",
        pages: int = 100,
    ) -> DocumentGroup:
        root.path.mkdir(parents=True, exist_ok=True)  # Create the source root folder for realistic paths.
        path = root.path / "doc.md"  # Use one physical Markdown path for the group.
        path.write_text("x" * text_chars, encoding="utf-8")  # Write content that matches the test yield.
        fields = (
            {"source_file": "guides/doc.pdf", "title": "Doc", "pages": str(pages)} if front_matter else {}
        )  # Add metadata.
        part = MarkdownPart(
            str(path), root, path, "doc.md", "hash", text_chars, text_chars, fields, {}, "ok"
        )  # Build the part.
        return DocumentGroup(
            "candidate", "Doc", category, "guides/doc.pdf", root, [part], pages, "ok", "source_file"
        )  # Return group.
