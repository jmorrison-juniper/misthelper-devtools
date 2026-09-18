"""Tests for Juniper inventory versioned family resolution."""

import sqlite3  # Create isolated invariant databases under tmp_path.
from pathlib import Path  # Use Path to create realistic source roots.

import pytest  # Verify validator failures without touching the live database.

from src.juniper_skills.inventory.engine import (  # Test the production version resolver.
    SourceDocumentVersionUpdater,
    VersionedFamilyResolver,
    VersionFamilyInvariantValidator,
)
from src.juniper_skills.inventory.models import DocumentGroup, MarkdownPart, SourceRoot  # Build inventory records.


class TestVersionedFamilyResolver:
    """Verify current and superseded version handling."""

    def test_compares_versions_numerically(self, tmp_path: Path) -> None:
        old = self._group(tmp_path, "Juniper Routing Director 2.9.0 User Guide", 2095)  # Build version 2.9.0.
        current = self._group(tmp_path, "Juniper Routing Director 2.10.0 User Guide", 2191)  # Build version 2.10.0.
        families = VersionedFamilyResolver().resolve([old, current])  # Resolve the versioned family.
        assert families[0].current_version == "2.10.0"  # Numeric order must put 2.10.0 above 2.9.0.
        assert old.version_status == "superseded"  # Older versions must stay citable but skip topic builds.
        assert current.build_topics is True  # The newest version must remain eligible for topic generation.

    def test_uses_newest_version_in_multi_version_title(self, tmp_path: Path) -> None:
        group = self._group(tmp_path, "Juniper Apstra 4.2.2 / 4.2.1 / 4.2.0 User Guide", 1663)  # Build multi-title.
        VersionedFamilyResolver().resolve(
            [group, self._group(tmp_path, "Juniper Apstra 5.1 User Guide", 1935)]
        )  # Resolve.
        assert group.version_value == "4.2.2"  # The resolver must keep the newest version named in one title.

    def test_groups_real_qfx_release_titles_into_one_family(self, tmp_path: Path) -> None:
        groups = [self._group(tmp_path, title, pages) for pages, title in self._qfx_titles()]  # Build QFX records.
        families = VersionedFamilyResolver().resolve(groups)  # Resolve the real QFX title shapes together.
        qfx_groups = [group for group in groups if "QFX Series" in group.title]  # Keep this assertion focused on QFX.
        family_keys = {group.version_family_key for group in qfx_groups}  # Measure how the parser grouped titles.
        current = [group for group in qfx_groups if group.version_status == "current"]  # Measure current winners.
        assert family_keys == {"complete-software-guide-for-junos-os-for-the-qfx-series"}  # One family key is used.
        assert len(families) == 1  # The nine titles form one version family.
        assert len(current) == 1  # The invariant permits one current guide only.
        assert current[0].version_value == "13.2X52-D10"  # The X52 release wins by numeric field comparison.

    def test_groups_hpe_apstra_rebrand_with_juniper_apstra(self, tmp_path: Path) -> None:
        hpe = self._group(
            tmp_path, "HPE Networking Apstra Data Center Director 6.2 User Guide", 2237
        )  # Build HPE title.
        juniper = self._group(tmp_path, "Juniper Apstra 6.1 User Guide", 2158)  # Build Juniper title.
        families = VersionedFamilyResolver().resolve([hpe, juniper])  # Resolve the rebranded family.
        assert families[0].family_key == "apstra-user-guide"  # The safe rebrand rule must join Apstra user guides.
        assert families[0].current_version == "6.2"  # The rebranded HPE document is the current version.

    def test_validator_rejects_two_current_members(self, tmp_path: Path) -> None:
        first = self._group(tmp_path, "Guide 2.0", 20)  # Build one member of an already marked family.
        second = self._group(tmp_path, "Guide 2.1", 21)  # Build a second member with the same family key.
        for group in [first, second]:  # Mark both as current to reproduce the data-integrity defect.
            group.version_family_key = "guide"  # Put both rows in one measured family.
            group.version_status = "current"  # Create the forbidden duplicate current state.
        with pytest.raises(ValueError, match="multiple current members"):  # The validator must fail closed.
            VersionFamilyInvariantValidator().validate_groups([first, second])  # Validate the bad state.

    def test_validator_rejects_zero_current_member(self, tmp_path: Path) -> None:
        first = self._group(tmp_path, "Guide 2.0", 20)  # Build one measured version family member.
        second = self._group(tmp_path, "Guide 2.1", 21)  # Build a second measured version family member.
        for group in [first, second]:  # Mark both as superseded to reproduce a no-current family.
            group.version_family_key = "guide"  # Put both rows in one measured family.
            group.version_status = "superseded"  # Create the forbidden no-current state.
        with pytest.raises(ValueError, match="zero current members"):  # The validator must fail closed.
            VersionFamilyInvariantValidator().validate_groups([first, second])  # Validate the bad state.

    def test_database_validator_accepts_empty_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "factory.db"  # Keep the test database away from the shared factory database.
        with sqlite3.connect(db_path) as connection:  # Create an empty source table in an isolated database.
            self._create_source_document_table(connection)  # Create only the columns that the validator reads.
            result = VersionFamilyInvariantValidator().validate_database(connection)  # Validate the empty database.
        assert result.documents_checked == 0  # Empty tables have no documents to group.
        assert result.families_checked == 0  # Empty tables correctly have zero version families.

    def test_database_validator_rejects_populated_database_with_zero_families(self, tmp_path: Path) -> None:
        db_path = tmp_path / "factory.db"  # Keep the test database away from the shared factory database.
        with sqlite3.connect(db_path) as connection:  # Create a populated table with no resolved families.
            self._create_source_document_table(connection)  # Create only the columns that the validator reads.
            self._insert_document(connection, "old", "Guide 2.9", 29)  # Add one title that should form a family.
            self._insert_document(connection, "new", "Guide 2.10", 30)  # Add a second title that should form a family.
            with pytest.raises(ValueError, match="2 documents and 0 families"):  # Candidate families must be marked.
                VersionFamilyInvariantValidator().validate_database(connection)  # Prove the guard cannot skip.

    def test_database_updater_enforces_current_invariant(self, tmp_path: Path) -> None:
        db_path = tmp_path / "factory.db"  # Use tmp_path so no test writes into data/juniper_skills.
        with sqlite3.connect(db_path) as connection:  # Seed a small database that resembles source_document.
            self._create_source_document_table(connection)  # Create the resolver input table.
            self._insert_document(connection, "old", "Guide 2.9", 29)  # Add a lower numeric version.
            self._insert_document(connection, "new", "Guide 2.10", 30)  # Add a higher numeric version.
        families, result = SourceDocumentVersionUpdater(db_path).refresh()  # Recompute and validate in place.
        with sqlite3.connect(db_path) as connection:  # Read the persisted result for a direct invariant proof.
            current = connection.execute(
                "SELECT document_key FROM source_document WHERE version_status = 'current'"
            ).fetchall()  # Query only the current rows.
        assert len(families) == 1  # The two rows are one measured version family.
        assert result.documents_checked == 2  # The database validator reports the checked document count.
        assert result.families_checked == 1  # The database validator reports the checked family count.
        assert current == [("new",)]  # Numeric comparison must rank 2.10 above 2.9.

    def _group(self, tmp_path: Path, title: str, pages: int) -> DocumentGroup:
        root = SourceRoot("harvest", tmp_path, 3)  # Use one root because the resolver works after deduplication.
        part = self._part(root, title, pages)  # Create one physical part for the document.
        return DocumentGroup(title, title, "guides", f"{title}.pdf", root, [part], pages, "ok", "source_file")

    def _part(self, root: SourceRoot, title: str, pages: int) -> MarkdownPart:
        return MarkdownPart(title, root, root.path / "doc.md", "doc.md", "hash", 1, 1, {}, {"title": title}, "ok")

    def _qfx_titles(self) -> list[tuple[int, str]]:
        return [
            (7042, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2X51-D20"),
            (6986, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2X52-D10"),
            (6964, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2X51-D25"),
            (6744, "Complete Software Guide for Junos OS for the QFX Series, Release 13.1"),
            (6630, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2X51-D15"),
            (6623, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2X51-D15"),
            (6178, "Complete Software Guide for Junos OS for the QFX Series, Release 13.2"),
            (6000, "Complete Software Guide for Junos OS for the QFX Series, Release 12.3"),
            (5760, "Complete Software Guide for Junos OS for the QFX Series, Release 12.2"),
        ]  # Preserve the real QFX title shapes that originally failed to group.

    def _create_source_document_table(self, connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE source_document (
                document_key TEXT PRIMARY KEY, title TEXT, category TEXT, source_pdf TEXT,
                root_name TEXT, pages INTEGER, status TEXT, group_method TEXT, part_count INTEGER,
                text_chars INTEGER, priority INTEGER, duplicate_losers INTEGER,
                version_family_key TEXT, version_value TEXT, version_status TEXT, build_topics INTEGER
            )
            """)  # Create a minimal source_document table for isolated invariant tests.

    def _insert_document(self, connection: sqlite3.Connection, key: str, title: str, pages: int) -> None:
        connection.execute(
            "INSERT INTO source_document VALUES (?, ?, 'guides', ?, 'test', ?, 'ok', 'source_file', "
            "1, 1, 0, 0, '', '', 'unversioned', 1)",
            (key, title, f"{key}.pdf", pages),
        )  # Insert one logical document row for the database updater.
