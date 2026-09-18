"""Tests for the SpecKit skill factory harness."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from src.juniper_skills.speckit import SkillDocument, SpecKitAnalyzer, SpecKitHarness, SpecKitPaths


class TestSpecKitHarness:
    """Verify programmatic SpecKit artifact generation."""

    def test_emits_complete_artifact_set(self) -> None:
        """Confirm the harness writes every required artifact."""
        workspace = self._workspace("complete")  # Use a repository-local test workspace.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build the harness with isolated output.
        feature_dir = harness.emit_for_document(document)  # Generate the full artifact set.
        required = self._required(feature_dir)  # Build the exact required artifact list.
        assert all(path.exists() for path in required)  # Prove every required artifact exists.
        assert harness.validate(feature_dir) == []  # Prove the generated set has no critical gaps.

    def test_context_matches_companion_schema(self) -> None:
        """Confirm the context file has the Companion keys."""
        workspace = self._workspace("context")  # Use a repository-local test workspace.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build the harness with isolated output.
        feature_dir = harness.emit_for_document(document)  # Generate the Companion context.
        data = json.loads((feature_dir / ".spec-context.json").read_text(encoding="utf-8"))  # Read context JSON.
        for key in ("workflow", "specName", "branch", "currentStep", "status", "history"):
            assert key in data  # Prove the real Companion lifecycle keys exist.
        assert data["currentStep"] == "implement"  # Prove completion keeps the last real step.
        assert data["status"] == "completed"  # Prove mark-complete wrote the terminal status.
        assert data["livingSpecs"]["loaded"] == [document.slug]  # Prove living-spec loading used capture.py.
        assert data["sourceHash"] == document.content_hash  # Prove drift tracking uses source bytes.

    def test_analyzer_catches_missing_requirement_task(self) -> None:
        """Confirm the analyzer catches a real coverage inconsistency."""
        workspace = self._workspace("inconsistent")  # Use a repository-local test workspace.
        feature_dir = workspace / "specs" / "skills" / "junos" / "bad-doc"  # Create an isolated feature path.
        feature_dir.mkdir(parents=True, exist_ok=True)  # Ensure the analysis directory exists.
        (feature_dir / "spec.md").write_text(
            "- **FR-001**: The harness MUST write files.\n", encoding="utf-8"
        )  # Write one requirement.
        (feature_dir / "plan.md").write_text(
            "# Plan\n\nNo placeholders remain.\n", encoding="utf-8"
        )  # Write a valid minimal plan.
        (feature_dir / "tasks.md").write_text(
            "- [x] T001 Create files without an id.\n", encoding="utf-8"
        )  # Omit FR-001.
        findings = SpecKitAnalyzer().analyze(feature_dir)  # Run the real coverage check.
        assert any(finding.severity == "CRITICAL" for finding in findings)  # Prove the inconsistency fails.
        assert any("FR-001" in finding.summary for finding in findings)  # Prove the missing id is named.

    def test_mandatory_stage_fails_when_artifact_is_missing(self) -> None:
        """Confirm the mandatory gate refuses a skipped command artifact."""
        workspace = self._workspace("mandatory-failure")  # Use a repository-local test workspace.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build the harness with isolated output.
        feature_dir = harness.emit_for_document(document)  # Generate a valid artifact set first.
        (feature_dir / "clarifications.md").unlink()  # Simulate a skipped speckit.clarify command.
        with pytest.raises(RuntimeError, match="SpecKit artifact gate failed"):
            harness.require_complete(feature_dir)  # Prove install cannot proceed without all artifacts.

    def test_living_drift_reports_changed_source_hash(self) -> None:
        """Confirm living-drift reports a re-converted source document."""
        workspace = self._workspace("drift")  # Use a repository-local test workspace.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build the harness with isolated output.
        feature_dir = harness.emit_for_document(document)  # Generate context with the first source hash.
        document.source_path.write_text(self._source_text() + "\nNew section.\n", encoding="utf-8")  # Change source.
        report = harness.living_drift(feature_dir)  # Run the document-specific drift check.
        assert report.checked  # Prove the drift check read the recorded source path.
        assert report.drifted  # Prove the changed source hash is visible.
        assert report.detail == "source hash changed"  # Prove the report explains the drift.

    def test_idempotent_rerun_updates_existing_artifacts(self) -> None:
        """Confirm a rerun updates files instead of duplicating directories."""
        workspace = self._workspace("idempotent")  # Use a repository-local test workspace.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build the harness with isolated output.
        first_dir = harness.emit_for_document(document)  # Generate the first artifact set.
        before = sorted(path.relative_to(first_dir) for path in first_dir.rglob("*") if path.is_file())  # Count files.
        second_dir = harness.emit_for_document(document)  # Run the workflow again for the same document.
        after = sorted(path.relative_to(second_dir) for path in second_dir.rglob("*") if path.is_file())  # Count again.
        assert second_dir == first_dir  # Prove the rerun targets the same feature directory.
        assert after == before  # Prove the rerun did not create duplicate artifacts.

    def test_package_measurements_use_installed_topic_files(self) -> None:
        """Confirm package metrics come from installed topics, not placeholders."""
        workspace = self._workspace("package-measurements")  # Use a repository-local test workspace.
        self._factory_database(workspace, domain="juniper-junos-fundamentals")  # Add a persisted domain.
        self._installed_topics(workspace)  # Add measured installed topic files.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build a harness pointed at fixture roots.
        feature_dir = harness.emit_for_document(document, [workspace / "placeholder" / "00-topic-0.md"])
        spec = (feature_dir / "spec.md").read_text(encoding="utf-8")  # Read the generated specification.
        assert "Topic count: 2" in spec  # Prove the placeholder file did not supply the topic count.
        assert "Life cycle spread: day0=1, day1=2, day2=0, day2plus=0" in spec  # Prove lifecycle parsing.

    def test_missing_domain_column_fails_before_guessing(self) -> None:
        """Confirm the harness fails when the taxonomy classifier did not persist a domain."""
        workspace = self._workspace("missing-domain")  # Use a repository-local test workspace.
        self._factory_database(workspace, include_domain=False)  # Create the old schema without a domain column.
        document = self._document(workspace)  # Create one source document fixture.
        harness = self._harness(workspace)  # Build a harness that must read the database.
        with pytest.raises(RuntimeError, match="source_document.domain is missing"):
            harness.emit_for_document(document)  # Prove no default domain is allowed.

    def _workspace(self, name: str) -> Path:
        """Return a clean repository-local test workspace."""
        root = self._repo_root() / "data" / "juniper_skills" / "test_speckit" / name  # Avoid system temporary paths.
        if root.exists():
            shutil.rmtree(root)  # Remove only this test-owned workspace for repeatability.
        root.mkdir(parents=True)  # Create the clean workspace before test writes.
        return root

    def _document(self, workspace: Path) -> SkillDocument:
        """Create one source Markdown fixture."""
        source = workspace / "source" / "guides" / "junos-beginners-guide.md"  # Mirror the real source path shape.
        source.parent.mkdir(parents=True, exist_ok=True)  # Create the fixture directory.
        source.write_text(self._source_text(), encoding="utf-8")  # Write front matter needed by the parser.
        return SkillDocument.from_markdown(source, "junos")  # Build the document model from real Markdown shape.

    def _harness(self, workspace: Path) -> SpecKitHarness:
        """Return a harness with isolated output."""
        paths = SpecKitPaths(  # Use the real templates with isolated data roots.
            self._repo_root(),
            workspace / "specs" / "skills",
            workspace / "factory.db",
            workspace / "installed" / "skills",
        )
        return SpecKitHarness(paths)  # Return the class under test.

    def _factory_database(self, workspace: Path, domain: str = "", include_domain: bool = True) -> None:
        """Create a small factory database for SpecKit measurements."""
        columns = ", domain TEXT" if include_domain else ""  # Let tests cover the old schema failure.
        values = ", ?" if include_domain else ""  # Match the optional domain column.
        with sqlite3.connect(workspace / "factory.db") as connection:  # Create the isolated database.
            connection.execute(self._source_schema(columns))  # Create the source document table.
            connection.execute(self._part_schema())  # Create the source part table.
            connection.execute(self._event_schema())  # Create the orchestrator event table.
            connection.execute(self._insert_source(values), self._source_values(domain, include_domain))  # Insert row.
            connection.execute(self._insert_part(), ("part-1", "junos-beginners-guide"))  # Insert one winner part.

    def _installed_topics(self, workspace: Path) -> None:
        """Create installed package topics with multiple lifecycle values."""
        root = workspace / "installed" / "skills" / "juniper-junos-fundamentals" / "documents" / "junos-beginners-guide"
        root.mkdir(parents=True, exist_ok=True)  # Create the installed document package path.
        (root / "00-routing.md").write_text(self._topic("Routing", "[day0, day1]"), encoding="utf-8")  # Topic 1.
        (root / "01-cli.md").write_text(self._topic("CLI", "[day1]"), encoding="utf-8")  # Topic 2.
        (root / "INDEX.md").write_text("# Index\n", encoding="utf-8")  # Add an index that must not count.

    def _source_schema(self, domain_column: str) -> str:
        """Return source document schema SQL."""
        return (
            "CREATE TABLE source_document (document_key TEXT, title TEXT, category TEXT, pages INTEGER, "
            "part_count INTEGER, version_status TEXT, version_value TEXT" + domain_column + ")"
        )

    def _part_schema(self) -> str:
        """Return source part schema SQL."""
        return "CREATE TABLE source_part (part_key TEXT, document_key TEXT, is_winner INTEGER)"

    def _event_schema(self) -> str:
        """Return stage event schema SQL."""
        return (
            "CREATE TABLE orchestrator_stage_event (id INTEGER PRIMARY KEY, document_key TEXT, "
            "stage TEXT, status TEXT, detail TEXT)"
        )

    def _insert_source(self, domain_value: str) -> str:
        """Return source insert SQL."""
        return "INSERT INTO source_document VALUES (?, ?, ?, ?, ?, ?, ?" + domain_value + ")"

    def _source_values(self, domain: str, include_domain: bool) -> tuple[object, ...]:
        """Return source row values."""
        values = ("junos-beginners-guide", "Test Guide", "guides", 356, 1, "current", "")  # Base document row.
        return values + ((domain,) if include_domain else ())  # Add the domain only for the new schema.

    def _insert_part(self) -> str:
        """Return source part insert SQL."""
        return "INSERT INTO source_part VALUES (?, ?, 1)"

    def _topic(self, title: str, lifecycle: str) -> str:
        """Return one installed topic fixture."""
        return f"---\ntopic: {title}\nlifecycle: {lifecycle}\n---\n\n# {title}\n"  # Keep fixture content small.

    def _required(self, feature_dir: Path) -> list[Path]:
        """Return required artifact paths."""
        names = [
            "spec.md",
            "clarifications.md",
            "plan.md",
            "tasks.md",
            "implementation.md",
            Path("checklists") / "requirements.md",
            "analysis.md",
            ".spec-context.json",
        ]  # Lock required files.
        return [feature_dir / name for name in names]  # Convert names to paths for assertions.

    def _repo_root(self) -> Path:
        """Return the repository root from this test file."""
        return Path(__file__).resolve().parents[4]  # Avoid pytest working directory changes.

    def _source_text(self) -> str:
        """Return a minimal source document with real front matter keys."""
        return """---
source_file: "guides/junos-beginners-guide.pdf"
title: "Day One: Beginner's Guide to Learning Junos"
pages: 356
---

## Chapter 1

Junos Fundamentals
"""  # Keep source text short and copyright-safe.
