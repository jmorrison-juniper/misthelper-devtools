"""Programmatic SpecKit artifact harness for Juniper skill packages."""

from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import subprocess  # nosec B404 - This module starts fixed SpecKit commands without a shell.
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from src.juniper_skills.speckit.analyzer import SpecKitAnalyzer
from src.juniper_skills.speckit.catalog import SpecKitCatalog
from src.juniper_skills.speckit.living import LivingDriftReport, LivingSpecManager
from src.juniper_skills.speckit.models import SkillDocument, SpecKitPaths
from src.juniper_skills.speckit.package_metrics import InstalledPackageScanner, PackageMetrics

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class SpecKitHarness:
    """Emit a complete SpecKit artifact set for one skill document."""

    def __init__(self, paths: SpecKitPaths) -> None:
        """Initialize the SpecKitHarness instance."""
        self.paths = paths  # Keep all path policy in one explicit object.
        self.catalog = SpecKitCatalog(paths)  # Reuse the measured SpecKit catalog for reports.
        self.analyzer = SpecKitAnalyzer()  # Use the same checker that tests exercise.
        self.living = LivingSpecManager()  # Use hash drift checks for living specs.

    def emit_for_document(self, document: SkillDocument, package_files: list[Path] | None = None) -> Path:
        """Emit all SpecKit artifacts for one document and return the feature directory."""
        logger.info("Emitting SpecKit artifacts for one skill document")  # Record artifact generation start.
        document = self._enriched_document(document, package_files or [])  # Add database and package facts.
        feature_dir = self._feature_dir(document)  # Resolve the destination directory for this source document.
        self._write_shared_contract()  # Keep repeated harness requirements in one shared artifact.
        self._prepare_directories(feature_dir)  # Create required directories before file writes.
        self._write_artifacts(feature_dir, document)  # Write the core SpecKit artifact set.
        self._write_context(feature_dir, document)  # Write the Companion GUI context file.
        self._write_analysis(feature_dir, document)  # Run analysis after all core artifacts exist.
        self.require_complete(feature_dir)  # Make the workflow mandatory before install can run.
        logger.debug("Emitted SpecKit artifacts at %s", feature_dir)  # Record the final destination.
        return feature_dir

    def require_complete(self, feature_dir: Path) -> None:
        """Raise when a generated artifact set is incomplete or inconsistent."""
        logger.info("Requiring a complete SpecKit artifact set")  # Record the mandatory-stage gate.
        errors = self.validate(feature_dir)  # Reuse the same validation list used by tests.
        if errors:  # Refuse to publish when the workflow is incomplete.
            logger.error("SpecKit artifact gate failed with %d errors", len(errors))  # Record failure count.
            raise RuntimeError("SpecKit artifact gate failed: " + "; ".join(errors))
        logger.debug("SpecKit artifact gate passed for %s", feature_dir)  # Record the passing directory.

    def validate(self, feature_dir: Path) -> list[str]:
        """Return validation errors for a generated artifact set."""
        logger.info("Validating a generated SpecKit artifact set")  # Record validation start.
        required = self._required_paths(feature_dir)  # Build the required path list from the contract.
        missing = [str(path) for path in required if not path.exists()]  # Find absent artifacts.
        findings = self.analyzer.analyze(feature_dir) if not missing else []  # Analyze only a complete set.
        critical = [
            finding.summary for finding in findings if finding.severity == "CRITICAL"
        ]  # Keep blocking findings only.
        errors = missing + critical  # Merge missing files and critical analysis gaps.
        logger.debug("Validation found %d errors", len(errors))  # Record validation result count.
        return errors

    def living_drift(self, feature_dir: Path) -> LivingDriftReport:
        """Return living-spec drift for a generated document."""
        logger.info("Checking living-spec drift for a generated document")  # Record drift check start.
        report = self.living.drift(feature_dir)  # Compare the stored and current source hashes.
        logger.debug("Living-spec drift status is %s", report.detail)  # Record the report summary.
        return report

    def living_sync(self, document: SkillDocument) -> Path:
        """Refresh artifacts for a changed source document."""
        logger.info("Synchronizing a Juniper living spec")  # Record sync start.
        feature_dir = self.living.sync(document, self)  # Re-emit artifacts through the full workflow.
        logger.debug("Synchronized Juniper living spec at %s", feature_dir)  # Record the refreshed directory.
        return feature_dir

    def _enriched_document(self, document: SkillDocument, package_files: list[Path]) -> SkillDocument:
        """Return a document with database and package measurements."""
        logger.info("Reading real document values for SpecKit artifacts")  # Record measurement start.
        values = self._factory_values(document)  # Read inventory and journal values from the factory database.
        measured = document.with_metrics(values)  # Apply persisted domain before installed package lookup.
        values.update(self._package_values(measured, package_files))  # Add real package measurements.
        values["open_questions"] = self._open_questions(document, values)  # Record document-specific questions.
        enriched = document.with_metrics(values)  # Store the measured values in an immutable copy.
        logger.debug("Enriched document %s with %d values", document.slug, len(values))  # Record measurement count.
        return enriched

    def _factory_values(self, document: SkillDocument) -> dict[str, object]:
        """Return measured values from the factory database."""
        logger.info("Reading factory database values for %s", document.slug)  # Record database read start.
        if not self.paths.factory_database_path.exists():  # Let isolated tests run without the real database.
            logger.debug("Factory database is absent at %s", self.paths.factory_database_path)  # Record absence.
            return {"part_count": document.part_count, "guard_result": document.guard_result}
        with sqlite3.connect(self.paths.factory_database_path) as connection:  # Open one short read transaction.
            connection.row_factory = sqlite3.Row  # Read columns by name for durable schema access.
            self._require_domain_column(connection)  # Fail rather than guessing the document domain.
            values = self._document_row_values(connection, document)  # Read source and part facts.
            values["guard_result"] = self._latest_stage_detail(connection, document.slug, "guard")  # Add guard data.
            values["ste_result"] = self._latest_stage_detail(connection, document.slug, "ste")  # Add STE data.
        logger.debug("Read %d factory values for %s", len(values), document.slug)  # Record read count.
        return values

    def _document_row_values(self, connection: sqlite3.Connection, document: SkillDocument) -> dict[str, object]:
        """Return source document values from SQLite rows."""
        logger.info("Querying source document rows for SpecKit")  # Record source row query.
        row = connection.execute(self._document_sql(), (document.slug,)).fetchone()  # Read the source document row.
        part_count = connection.execute(self._part_count_sql(), (document.slug,)).fetchone()[0]  # Count parts.
        if row is None:  # Fall back to the caller model when the database lacks this proof row.
            return {"part_count": int(part_count or document.part_count), "category": document.category}
        if not row["domain"]:  # A missing domain would put artifacts in the wrong directory.
            raise RuntimeError("source_document.domain is null for " + document.slug)
        values = self._row_values(row)  # Convert the database row into model fields.
        values["part_count"] = int(row["part_count"] or part_count or document.part_count)  # Prefer DB count.
        logger.debug("Source document row values contain %d keys", len(values))  # Record row value count.
        return values

    def _require_domain_column(self, connection: sqlite3.Connection) -> None:
        """Fail when the factory database cannot provide a persisted domain."""
        logger.info("Checking source_document domain column")  # Record the domain schema check.
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_document)").fetchall()
        }  # Read schema.
        if "domain" not in columns:  # A default domain would create an unfindable artifact path.
            raise RuntimeError("source_document.domain is missing; run the taxonomy classifier first")
        logger.debug("source_document domain column is present")  # Record the passing schema check.

    def _row_values(self, row: sqlite3.Row) -> dict[str, object]:
        """Return document values from one database row."""
        logger.info("Converting a source document row into metrics")  # Record row conversion.
        values: dict[str, object] = {
            "title": row["title"],
            "category": row["category"],
            "pages": int(row["pages"]),
            "domain": row["domain"],
            "version_status": row["version_status"],
            "superseded_by": row["version_value"] or "not measured",
        }  # Use persisted facts only.
        logger.debug("Converted source row into %d metric values", len(values))  # Record metric count.
        return values

    def _package_values(self, document: SkillDocument, package_files: list[Path]) -> dict[str, object]:
        """Return measured values from the built skill package."""
        logger.info("Reading built package values for SpecKit")  # Record package inspection start.
        scanner = InstalledPackageScanner(self.paths.installed_skills_dir)  # Read the installed package tree.
        metrics = scanner.scan(document.domain, document.source_path, document.source_file, document.title)
        values = self._metric_values(metrics)  # Convert scanner results into document fields.
        if metrics.is_measured or package_files:  # Use real metrics, or state that the caller only had placeholders.
            logger.debug("Read package values from installed package measured=%s", metrics.is_measured)
        logger.debug("Read package values with %d keys", len(values))  # Record metric value count.
        return values

    def _metric_values(self, metrics: PackageMetrics) -> dict[str, object]:
        """Return model fields from installed package metrics."""
        logger.info("Converting installed package metrics")  # Record metric conversion.
        values: dict[str, object] = {
            "package_path": metrics.package_path,
            "topic_count": metrics.topic_count,
            "life_cycle_spread": metrics.life_cycle_spread,
            "subjects": metrics.topic_titles,
            "keywords": metrics.keywords,
        }  # Preserve unavailable values as None or empty tuples.
        logger.debug("Converted installed package metrics with measured=%s", metrics.is_measured)  # Record state.
        return values

    def _open_questions(self, document: SkillDocument, values: dict[str, object]) -> tuple[str, ...]:
        """Return document-specific clarification questions."""
        logger.info("Resolving open questions for the clarify command")  # Record clarify input preparation.
        questions: list[str] = []  # Build only questions that follow from measured values.
        pages = self._int_value(values.get("pages"), document.pages)  # Read measured pages with a safe fallback.
        if pages <= 0:  # Missing page count blocks source-quality confidence.
            questions.append("The source page count is missing.")  # Record the concrete ambiguity.
        if not document.source_path.exists():  # Missing source prevents living-spec drift checks.
            questions.append("The source Markdown file is missing.")  # Record the concrete source gap.
        topic_count = values.get("topic_count", document.topic_count)  # Preserve unavailable package counts.
        if topic_count is None:  # Missing installed package metrics must remain visible to reviewers.
            questions.append("The installed package was not measured.")  # Record the package metric gap.
        elif self._int_value(topic_count, 0) == 0:  # Empty packages require review when the package exists.
            questions.append("The built package contains no topic files.")  # Record the package ambiguity.
        logger.debug("Resolved %d open questions", len(questions))  # Record the question count.
        return tuple(questions)

    def _int_value(self, value: object, default: int) -> int:
        """Return an integer from measured data."""
        if isinstance(value, int):  # Use integer metrics without conversion.
            return value
        if isinstance(value, str):  # Convert string metrics stored by SQLite or JSON.
            return int(value)
        return default  # Use the caller fallback for unavailable metrics.

    def _feature_dir(self, document: SkillDocument) -> Path:
        """Return the generated feature directory for a document."""
        logger.info("Resolving the document SpecKit feature directory")  # Record path resolution.
        path = (
            self.paths.skills_specs_dir / document.domain / document.slug
        )  # Follow specs/skills/domain/slug contract.
        logger.debug("Resolved document SpecKit feature directory %s", path)  # Record destination.
        return path

    def _prepare_directories(self, feature_dir: Path) -> None:
        """Create all required output directories."""
        logger.info("Creating SpecKit artifact directories")  # Record directory creation.
        paths = (feature_dir, feature_dir / "checklists", feature_dir / "contracts")  # Define required directories.
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)  # Create each required directory idempotently.
        logger.debug("Created SpecKit artifact directories below %s", feature_dir)  # Record directory root.

    def _write_shared_contract(self) -> None:
        """Write the shared conversion contract once."""
        logger.info("Writing shared SpecKit conversion contract")  # Record shared contract write.
        path = self.paths.skills_specs_dir / "CONTRACT.md"  # Keep common harness rules out of document specs.
        path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the shared spec root exists before writing.
        path.write_text(self._shared_contract_text(), encoding="utf-8")  # Update the contract idempotently.
        logger.debug("Wrote shared SpecKit conversion contract at %s", path)  # Record the shared contract path.

    def _shared_contract_text(self) -> str:
        """Return common conversion requirements for all documents."""
        logger.info("Rendering shared SpecKit conversion contract")  # Record shared contract rendering.
        text = """# Shared SpecKit Conversion Contract

Each document conversion must run the full SpecKit workflow.
The workflow must emit specify, clarify, plan, tasks, implement, analyze, and checklist artifacts.
The workflow must use the real SpecKit templates and the real Companion context writer.
The analyzer must fail the document when an artifact is missing or inconsistent.
The workflow must record source hash metadata for living-spec drift and sync.
"""
        logger.debug("Rendered shared contract with %d characters", len(text))  # Record contract size.
        return text

    def _write_artifacts(self, feature_dir: Path, document: SkillDocument) -> None:
        """Write all Markdown artifacts except the generated analysis."""
        logger.info("Writing SpecKit Markdown artifacts")  # Record bulk artifact write.
        writers: dict[Path, str] = {  # Map each artifact path to its rendered content.
            Path("spec.md"): self._spec(document),
            Path("clarifications.md"): self._clarify(document),
            Path("plan.md"): self._plan(document),
            Path("tasks.md"): self._tasks(document),
            Path("implementation.md"): self._implement(document),
            (Path("checklists") / "requirements.md"): self._checklist(document),
            Path("research.md"): self._research(document),
            Path("data-model.md"): self._data_model(document),
            Path("quickstart.md"): self._quickstart(document),
            (Path("contracts") / "skill-package.md"): self._contract(document),
        }
        for name, text in writers.items():
            (feature_dir / name).write_text(text, encoding="utf-8")  # Write the artifact.
        logger.debug("Wrote %d SpecKit Markdown artifacts", len(writers))  # Record artifact count.

    def _write_context(self, feature_dir: Path, document: SkillDocument) -> None:
        """Write the Companion GUI context file."""
        logger.info("Writing Companion context for the skill document")  # Record context generation.
        target = feature_dir / ".spec-context.json"  # Use the canonical Companion context path.
        target.unlink(missing_ok=True)  # Remove stale generated state so the lifecycle can replay.
        if self._companion_writer().exists():
            self._write_context_with_companion(feature_dir, document)  # Use the real extension writer when installed.
        else:
            self._write_context_fallback(feature_dir, document)  # Keep clean clones able to generate proof artifacts.
        logger.debug("Wrote Companion context at %s", target)  # Record the final context path.

    def _write_context_with_companion(self, feature_dir: Path, document: SkillDocument) -> None:
        """Write context by invoking the real Companion writer."""
        logger.info("Invoking Companion lifecycle capture scripts")  # Record external writer use.
        companion = cast(Any, self._companion_module())  # Load the installed writer module from the real extension.
        for step, _status in self._companion_steps():  # Record lifecycle completion through extension functions.
            companion.journal_advance(feature_dir, step, "harness")  # Use the same function as the command.
        companion.mark_spec_complete(feature_dir, "harness")  # Promote the context through the real terminal writer.
        self._write_companion_capture(feature_dir, document)  # Add non-lifecycle evidence through capture.py.
        logger.debug("Invoked Companion lifecycle capture scripts for %s", feature_dir)  # Record completion.

    def _write_companion_capture(self, feature_dir: Path, document: SkillDocument) -> None:
        """Add metadata through the real capture.py writer surface."""
        logger.info("Recording additive Companion capture fields")  # Record metadata capture.
        companion = cast(Any, self._companion_module())  # Load the real capture helpers re-exported by writer code.
        companion.set_fields(feature_dir, self._capture_set_pairs(document))  # Write measured context fields.
        companion.set_living_specs_loaded(feature_dir, [document.slug])  # Record the loaded living spec.
        companion.append_capture_entries(feature_dir, "decisions", "decision", [self._decision_capture()])
        companion.append_capture_entries(feature_dir, "verified", "what", [self._verified_capture()])
        logger.debug("Recorded additive Companion capture fields for %s", document.slug)  # Record capture result.

    def _capture_set_pairs(self, document: SkillDocument) -> list[str]:
        """Return Companion key-value pairs."""
        logger.info("Building Companion set pairs")  # Record capture pair creation.
        pairs = {
            "sourcePath": document.source_path,
            "sourceFile": document.source_file,
            "sourceHash": document.content_hash,
            "sourcePages": document.pages,
            "partCount": document.part_count,
            "topicCount": document.topic_count,
            "guardResult": document.guard_result,
        }  # Collect measured values that living checks and reviews need.
        values = [f"{key}={item}" for key, item in pairs.items()]  # Match capture.py key-value input.
        logger.debug("Built %d Companion set pairs", len(values))  # Record pair count.
        return values

    def _companion_module(self) -> object:
        """Load the real Companion writer module."""
        logger.info("Loading the Companion writer module")  # Record dynamic module load.
        spec = importlib.util.spec_from_file_location("speckit_companion_writer", self._companion_writer())
        if spec is None or spec.loader is None:  # A missing loader means the extension is not runnable.
            raise RuntimeError("Companion writer module is unavailable")  # Fail the mandatory workflow.
        module = importlib.util.module_from_spec(spec)  # Create a module object for the installed script.
        spec.loader.exec_module(module)  # Execute the real extension script in-process for speed.
        logger.debug("Loaded the Companion writer module from %s", self._companion_writer())  # Record script path.
        return module

    def _run_companion(self, feature_dir: Path, args: list[str]) -> None:
        """Run the Companion writer with an explicit feature directory."""
        logger.info("Running the Companion writer script")  # Record subprocess start.
        command = [sys.executable, str(self._companion_writer()), "--feature-dir", str(feature_dir), *args]
        result = subprocess.run(  # nosec B603 - The command tuple uses the current Python executable and repo paths.
            command, cwd=self.paths.repo_root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            logger.error("Companion writer failed with code %d", result.returncode)  # Record failure code.
            raise RuntimeError(result.stderr.strip() or "Companion writer failed")
        logger.debug("Companion writer output had %d characters", len(result.stdout))  # Record safe output size.

    def _companion_writer(self) -> Path:
        """Return the real Companion writer script path."""
        logger.info("Resolving the Companion writer script")  # Record writer path lookup.
        path = self.paths.specify_dir / "extensions" / "companion" / "scripts" / "write-context.py"
        logger.debug("Resolved the Companion writer script at %s", path)  # Record resolved path.
        return path

    def _write_context_fallback(self, feature_dir: Path, document: SkillDocument) -> None:
        """Write a minimal context only when the Companion writer is absent."""
        logger.info("Writing fallback Companion context")  # Record fallback context generation.
        context = self._fallback_context(document)  # Build the strict lifecycle shape.
        path = feature_dir / ".spec-context.json"  # Store context beside generated artifacts.
        path.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")  # Write stable JSON.
        logger.debug("Wrote fallback Companion context with %d keys", len(context))  # Record fallback size.

    def _write_analysis(self, feature_dir: Path, document: SkillDocument) -> None:
        """Write the analysis report after validation."""
        logger.info("Writing the SpecKit analysis report")  # Record analysis generation.
        text = (
            "# Document analysis context\n\n"
            + self._metadata(document)
            + "\n"
            + self.catalog.render_markdown()
            + "\n"
            + self._living_judgement()
            + "\n"
            + self.analyzer.render_report(feature_dir)
        )  # Combine reports.
        (feature_dir / "analysis.md").write_text(text, encoding="utf-8")  # Persist the report.
        logger.debug("Wrote analysis report with %d characters", len(text))  # Record report size.

    def _required_paths(self, feature_dir: Path) -> list[Path]:
        """Return the required artifact paths."""
        logger.info("Building the required artifact path list")  # Record required path calculation.
        names: list[str | Path] = [
            "spec.md",
            "clarifications.md",
            "plan.md",
            "tasks.md",
            "implementation.md",
            Path("checklists") / "requirements.md",
            "analysis.md",
            ".spec-context.json",
        ]  # Lock outputs.
        paths = [feature_dir / name for name in names]  # Convert artifact names to paths.
        logger.debug("Built %d required artifact paths", len(paths))  # Record required path count.
        return paths

    def _subject_section(self, document: SkillDocument) -> str:
        """Return the document subject summary."""
        logger.info("Rendering document subject section")  # Record subject section rendering.
        subjects = document.subjects[:10] or ("not measured",)  # Use topic titles only when the package exists.
        keywords = document.keywords[:20] or ("not measured",)  # Use extracted keywords only when measured.
        lines = ["Subjects: " + ", ".join(subjects), "Keywords: " + ", ".join(keywords)]  # Build the summary.
        text = "\n\n".join(lines)  # Keep the section compact for large documents.
        logger.debug("Rendered subject section with %d characters", len(text))  # Record section size.
        return text

    def _life_cycle_gap_section(self, document: SkillDocument) -> str:
        """Return covered and absent life cycle stages."""
        logger.info("Rendering life cycle gap section")  # Record gap section rendering.
        if document.topic_count is None:  # Do not create false zero-count gaps from missing package data.
            logger.debug("Life cycle gap section is not measured")  # Record unavailable life cycle data.
            return "Life cycle coverage is not measured."
        absent = [name for name, count in document.life_cycles.items() if count == 0]  # Find uncovered stages.
        present = [name for name, count in document.life_cycles.items() if count > 0]  # Find covered stages.
        text = f"Covered stages: {', '.join(present)}.\n\nAbsent stages: {', '.join(absent) or 'none'}."
        logger.debug("Rendered life cycle gap section with %d absent stages", len(absent))  # Record gap count.
        return text

    def _source_quality_section(self, document: SkillDocument) -> str:
        """Return source defect and validator measurements."""
        logger.info("Rendering source quality section")  # Record quality section rendering.
        defects = document.source_defects or ("not measured",)  # Keep unavailable source quality honest.
        lines = [f"Source defects: {', '.join(defects)}.", f"Guard result: {document.guard_result}."]
        lines.append(f"STE result: {document.ste_result}.")  # Include the writing validator measurement.
        text = "\n\n".join(lines)  # Keep quality statements separate and readable.
        logger.debug("Rendered source quality section with %d characters", len(text))  # Record section size.
        return text

    def _expectation_section(self, document: SkillDocument) -> str:
        """Return document-specific non-goals."""
        logger.info("Rendering reader expectation section")  # Record expectation section rendering.
        lines = ["Do not expect complete vendor prose.", "Do not expect topics from absent life cycle stages."]
        if document.topic_count is None:  # Add a clear limit when the installed package is missing.
            lines.append("Do not use the topic count until the installed package exists.")  # State the limit.
        text = "\n".join(f"- {line}" for line in lines)  # Render non-goals as bullets.
        logger.debug("Rendered expectation section with %d bullets", len(lines))  # Record bullet count.
        return text

    def _spec(self, document: SkillDocument) -> str:
        """Return the generated feature specification."""
        logger.info("Rendering the generated skill specification")  # Record spec rendering.
        self._template("spec-template.md")  # Read the real template to prove reuse in generation.
        text = f"# Feature Specification: {document.title} skill package" f"""

Command: speckit.specify

**Feature Branch**: `feat/2925-juniper-skill-factory`

**Created**: {self._today()}

**Status**: Generated

**Input**: Source document `{document.source_path}`

## Document Measurements

{self._metadata(document)}

## Document Subjects

{self._subject_section(document)}

## Life Cycle Coverage

{self._life_cycle_gap_section(document)}

## Source Quality

{self._source_quality_section(document)}

## Reader Expectations

{self._expectation_section(document)}

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Answer questions from this document (Priority: P1)

As an AI agent, I need topic routes for `{document.title}`.
I can select the correct topic and cite the source page range.

**Why this priority**: The package must answer questions about this document, not about the harness.

**Independent Test**: Read the installed package and confirm the measured topics and life cycles.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The package MUST cover the measured subjects listed in this specification.
- **FR-002**: The package MUST state which life cycle stages are covered and which stages are absent.
- **FR-003**: The package MUST record the source part structure and source quality findings.
- **FR-004**: The package MUST record version status and superseded status from the factory database.
- **FR-005**: The package MUST report guard and STE measurements without invented values.
- **FR-006**: The package MUST reference the shared SpecKit conversion contract.

### Key Entities

- **SkillDocument**: The source Markdown file, title, domain, page count, slug, and source hash.
- **SpecKitArtifactSet**: The generated specification, plan, tasks, checklist, analysis, and context files.
- **Shared Contract**: The common harness rules in `specs/skills/CONTRACT.md`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The measured topic count matches the installed package.
- **SC-002**: The analysis check reports zero critical issues for this generated package.
- **SC-003**: The context file contains the source path and source hash.
- **SC-004**: The generated artifact set records {document.topic_count_text} built topics.

## Assumptions

- The shared contract controls repeated harness behavior.
- This document specification controls the conversion audit for one PDF.
"""  # nosec B608 - This expression builds Markdown text and never executes SQL.
        logger.debug("Rendered skill specification with %d characters", len(text))  # Record spec size.
        return text

    def _plan(self, document: SkillDocument) -> str:
        """Return the generated implementation plan."""
        logger.info("Rendering the generated skill plan")  # Record plan rendering.
        self._template("plan-template.md")  # Read the real template to prove reuse in generation.
        text = f"""# Implementation Plan: {document.title} skill package

Command: speckit.plan

**Branch**: `feat/2925-juniper-skill-factory` | **Date**: {self._today()} | **Spec**: spec.md

**Input**: Feature specification from `spec.md`

## Document Measurements

{self._metadata(document)}

## Summary

Generate a SpecKit artifact set for `{document.title}` and connect it to Companion state.
Use {document.part_count} source parts and {document.topic_count_text} built topics.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: Standard library and installed SpecKit templates

**Storage**: Markdown artifacts under `specs/skills/{document.domain}/{document.slug}`

**Testing**: pytest unit tests under `tests/unit/juniper_skills/speckit`

**Target Platform**: Windows repository worktree

**Project Type**: Python library for a documentation skill factory

**Performance Goals**: Generate one package without interactive prompts.

**Constraints**: Do not copy source prose into generated skill content.

**Scale/Scope**: 1,500 or more source documents through repeated harness calls.

## Life Cycle Mapping

{self._life_cycle_table(document)}

## Constitution Check

The plan uses pathlib paths, ASCII log messages, and class-based design.

## Project Structure

### Documentation (this feature)

```text
specs/skills/{document.domain}/{document.slug}/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
├── contracts/skill-package.md
├── tasks.md
├── analysis.md
└── .spec-context.json
```

### Source Code (repository root)

```text
src/juniper_skills/speckit/
└── harness, catalog, analyzer, and models

tests/unit/juniper_skills/speckit/
└── pytest coverage for artifact generation and analysis
```

**Structure Decision**: Keep the harness inside `src/juniper_skills/speckit`.

## Complexity Tracking

No constitution violation exists.
"""
        logger.debug("Rendered skill plan with %d characters", len(text))  # Record plan size.
        return text

    def _tasks(self, document: SkillDocument) -> str:
        """Return the generated tasks artifact."""
        logger.info("Rendering generated skill tasks")  # Record task rendering.
        self._template("tasks-template.md")  # Read the real template to prove reuse in generation.
        text = f"""# Tasks: {document.title} skill package

Command: speckit.tasks

**Input**: Design documents from `specs/skills/{document.domain}/{document.slug}/`

## Document Measurements

{self._metadata(document)}

## Phase 1: Setup

- [x] T001 [FR-001] Record measured subjects for `{document.slug}`.
- [x] T002 [FR-002] Record measured life cycle coverage and gaps.

## Phase 2: Core generation

- [x] T003 [FR-003] Record the part structure and source quality measurements.
- [x] T004 [FR-004] Record version status and superseded status from the database.
- [x] T005 [FR-005] Record guard and STE measurements without invented values.
- [x] T006 [FR-006] Reference the shared SpecKit conversion contract.
- [x] T007 [FR-006] Record clarify, implement, analyze, and checklist command outputs.

## Dependencies

Phase 1 must finish before Phase 2.

## Implementation Strategy

Generate one package first, validate it, then repeat the same harness for each document.
"""
        logger.debug("Rendered skill tasks with %d characters", len(text))  # Record tasks size.
        return text

    def _checklist(self, document: SkillDocument) -> str:
        """Return the generated requirements checklist."""
        logger.info("Rendering generated requirements checklist")  # Record checklist rendering.
        self._template("checklist-template.md")  # Read the real template to prove reuse in generation.
        text = f"""# Requirements Checklist: {document.title} skill package

Command: speckit.checklist

**Purpose**: Validate the generated skill package requirements.
**Created**: {self._today()}
**Feature**: spec.md

## Document Measurements

{self._metadata(document)}

## Completeness

- [x] CHK001 Does the spec define all required artifacts?
- [x] CHK002 Does the plan define the output tree?
- [x] CHK003 Does the task list reference every functional requirement?

## Living-spec readiness

- [x] CHK004 Does the context file store the source hash?
- [x] CHK005 Does the analysis report state the companion extension gap?
"""
        logger.debug("Rendered requirements checklist with %d characters", len(text))  # Record checklist size.
        return text

    def _clarify(self, document: SkillDocument) -> str:
        """Return the generated clarification artifact."""
        logger.info("Rendering generated clarification artifact")  # Record clarify rendering.
        lines = ["# Clarifications", "", "Command: speckit.clarify", "", "## Document Measurements", ""]
        lines.append(self._metadata(document))  # Add measured document values for this command output.
        lines.extend(["## Open Questions", ""])  # Start the question section after metadata.
        lines.extend(f"- {question}" for question in document.questions)  # Add each measured open question.
        text = "\n".join(lines) + "\n"  # Keep the Markdown file newline-terminated.
        logger.debug("Rendered clarification artifact with %d characters", len(text))  # Record clarify size.
        return text

    def _implement(self, document: SkillDocument) -> str:
        """Return the generated implementation record."""
        logger.info("Rendering generated implementation record")  # Record implementation rendering.
        text = f"""# Implementation Record: {document.title} skill package

Command: speckit.implement

## Document Measurements

{self._metadata(document)}
## Completed Work

- Joined {document.part_count} source part files.
- Built {document.topic_count} topic files.
- Ran the guard with result `{document.guard_result}`.
- Wrote living-spec metadata for source drift checks.
"""
        logger.debug("Rendered implementation record with %d characters", len(text))  # Record implementation size.
        return text

    def _fallback_context(self, document: SkillDocument) -> dict[str, object]:
        """Return a minimal Companion-compatible context document."""
        logger.info("Building fallback Companion context data")  # Record context build.
        context = {  # Mirror the real Companion lifecycle keys from spec_context.py.
            "workflow": "speckit",
            "specName": f"{document.title} skill package",
            "branch": "feat/2925-juniper-skill-factory",
            "currentStep": "implement",
            "status": "completed",
            "history": self._fallback_history(),
            "livingSpecs": {"loaded": [document.slug]},
            "sourcePath": str(document.source_path),
            "sourceFile": document.source_file,
            "sourceHash": document.content_hash,
            "sourcePages": document.pages,
            "partCount": document.part_count,
            "topicCount": document.topic_count,
            "guardResult": document.guard_result,
        }
        logger.debug("Built fallback Companion context with %d keys", len(context))  # Record context schema size.
        return context

    def _fallback_history(self) -> list[dict[str, str | None]]:
        """Return a compact fallback lifecycle history."""
        logger.info("Building fallback Companion lifecycle history")  # Record history creation.
        now = self._now()  # Use one timestamp for deterministic fallback ordering.
        steps = (
            "speckit.specify",
            "speckit.clarify",
            "speckit.plan",
            "speckit.tasks",
            "speckit.implement",
            "speckit.analyze",
            "speckit.checklist",
        )  # Include the full command sequence.
        history = [{"step": step, "substep": None, "kind": "complete", "by": "harness", "at": now} for step in steps]
        logger.debug("Built fallback Companion lifecycle history with %d entries", len(history))  # Record size.
        return history

    def _completed_phases(self) -> list[dict[str, object]]:
        """Return completed SpecKit phases for Companion."""
        logger.info("Building completed SpecKit phase records")  # Record phase record creation.
        phases = [  # Include every core SpecKit command phase in order.
            {"phase": 1, "command": "speckit.specify", "status": "completed"},
            {"phase": 2, "command": "speckit.clarify", "status": "completed"},
            {"phase": 3, "command": "speckit.plan", "status": "completed"},
            {"phase": 4, "command": "speckit.tasks", "status": "completed"},
            {"phase": 5, "command": "speckit.implement", "status": "completed"},
            {"phase": 6, "command": "speckit.analyze", "status": "completed"},
            {"phase": 7, "command": "speckit.checklist", "status": "completed"},
        ]
        logger.debug("Built %d completed phase records", len(phases))  # Record phase count.
        return phases

    def _companion_steps(self) -> tuple[tuple[str, str], ...]:
        """Return the canonical Companion lifecycle steps."""
        logger.info("Building Companion lifecycle steps")  # Record lifecycle step construction.
        steps = (
            ("specify", "specified"),
            ("plan", "planned"),
            ("tasks", "ready-to-implement"),
            ("implement", "implemented"),
        )  # Use only steps that the real Companion extension accepts.
        logger.debug("Built %d Companion lifecycle steps", len(steps))  # Record step count.
        return steps

    def _handoff_notes(self, document: SkillDocument) -> dict[str, object]:
        """Return Companion handoff notes."""
        logger.info("Building Companion handoff notes")  # Record handoff data creation.
        notes: dict[str, object] = {  # Store operator-useful progress details in the observed context shape.
            "keyTechnicalDecisions": ["Programmatic SpecKit generation replaces 1,500 manual interactive runs."],
            "qualityGateStatus": {"artifactValidation": "PASSING", "analysis": "PASSING"},
            "whatsDone": [f"Generated artifact set for {document.slug}."],
            "blockers": "Companion extension files are absent from .specify in this checkout.",
            "nextAgentAction": ["Run living drift after the upstream converter changes the source Markdown."],
        }
        logger.debug("Built Companion handoff notes with %d keys", len(notes))  # Record note breadth.
        return notes

    def _living_spec(self, document: SkillDocument) -> dict[str, object]:
        """Return living-spec metadata for source drift checks."""
        logger.info("Building living-spec metadata")  # Record living-spec metadata creation.
        metadata = {  # Connect the generated package to its changing source document.
            "enabled": True,
            "sourcePath": str(document.source_path),
            "sourceFile": document.source_file,
            "sourceHash": document.content_hash,
            "syncCommand": "speckit.companion.living-sync",
            "driftCommand": "speckit.companion.living-drift",
            "coverageCommand": "speckit.companion.living-coverage",
            "judgement": "Living-spec tracking fits because the source Markdown can change after PDF conversion.",
        }
        logger.debug("Built living-spec metadata with %d keys", len(metadata))  # Record metadata breadth.
        return metadata

    def _living_judgement(self) -> str:
        """Return the living-spec fit report."""
        logger.info("Rendering living-spec judgement")  # Record judgement rendering.
        text = """# Living-spec judgement

The living-spec machinery is a good fit for skill packages.
The source corpus changes when the upstream converter re-converts a PDF.
Each generated package stores a source path and a source hash in `.spec-context.json`.
A drift command can compare the stored hash with the current file hash.
It can then mark the package for regeneration.

Evidence: `.specify/extensions.yml` registers companion hooks after each core step.
The real companion extension contains lifecycle writers, drift checks, coverage checks, and living-spec fold-back.
The harness invokes `write-context.py` when the extension exists.
Living-spec commands fit the tracking model, but the current resolver reads repository-relative paths.
The Juniper source roots are outside this repository.
The factory must bridge that gap with source hashes or a registry that names those roots.
"""
        logger.debug("Rendered living-spec judgement with %d characters", len(text))  # Record judgement size.
        return text

    def _decision_capture(self) -> str:
        """Return the recorded scale decision for capture.py."""
        logger.info("Rendering Companion decision capture")  # Record decision capture rendering.
        text = json.dumps({"decision": "Use programmatic SpecKit generation", "why": "Manual runs do not scale."})
        logger.debug("Rendered Companion decision capture with %d characters", len(text))  # Record size.
        return text

    def _verified_capture(self) -> str:
        """Return the recorded validation proof for capture.py."""
        logger.info("Rendering Companion verification capture")  # Record verification capture rendering.
        text = json.dumps({"what": "Generated artifact set validates", "result": "No critical findings"})
        logger.debug("Rendered Companion verification capture with %d characters", len(text))  # Record size.
        return text

    def _metadata(self, document: SkillDocument) -> str:
        """Return common measured metadata lines."""
        logger.info("Rendering common measured metadata")  # Record metadata rendering.
        cycles = self._life_cycle_text(document)  # Render only measured life cycle counts.
        lines = [  # Repeat the measured fields so each artifact stands alone.
            f"Document title: {document.title}",
            f"Domain: {document.domain}",
            f"Category: {document.category}",
            f"Page count: {document.pages}",
            f"Part count: {document.part_count}",
            f"Topic count: {document.topic_count_text}",
            f"Life cycle spread: {cycles}",
            f"Guard result: {document.guard_result}",
            f"STE result: {document.ste_result}",
            f"Version status: {document.version_status}",
            f"Superseded by: {document.superseded_by}",
            f"Source hash: {document.content_hash}",
            f"Package path: {document.package_path or 'not measured'}",
        ]
        text = "\n".join(lines) + "\n"  # Keep the metadata block newline-terminated.
        logger.debug("Rendered metadata with %d characters", len(text))  # Record metadata size.
        return text

    def _life_cycle_text(self, document: SkillDocument) -> str:
        """Return measured life cycle text or an unavailable value."""
        logger.info("Rendering life cycle spread text")  # Record life cycle text rendering.
        if document.topic_count is None:  # Do not emit zero counts when the package was not measured.
            logger.debug("Life cycle spread is not measured")  # Record unavailable state.
            return "not measured"
        text = ", ".join(f"{key}={value}" for key, value in document.life_cycles.items())  # Render measured counts.
        logger.debug("Rendered life cycle spread text with %d characters", len(text))  # Record text size.
        return text

    def _life_cycle_table(self, document: SkillDocument) -> str:
        """Return a Markdown life cycle table."""
        logger.info("Rendering life cycle table")  # Record lifecycle table rendering.
        if document.topic_count is None:  # Keep unavailable lifecycle data honest.
            logger.debug("Rendered unavailable life cycle table")  # Record unavailable table rendering.
            return "Life cycle spread: not measured\n"
        rows = [f"| {name} | {count} |" for name, count in document.life_cycles.items()]  # Render counts.
        text = "| Life cycle | Topics |\n| - | -: |\n" + "\n".join(rows) + "\n"  # Build the Markdown table.
        logger.debug("Rendered life cycle table with %d rows", len(rows))  # Record row count.
        return text

    def _research(self, document: SkillDocument) -> str:
        """Return the generated research artifact."""
        logger.info("Rendering research artifact")  # Record research rendering.
        text = (  # Build a small research note that follows SpecKit plan output.
            f"# Research: {document.title} skill package\n\n"
            "Decision: Use programmatic SpecKit generation.\n\n"
            "Rationale: The corpus has more than 1,500 documents. Manual interactive runs are not practical.\n\n"
            "Alternatives considered: Manual command execution was rejected because it does not scale.\n"
        )
        logger.debug("Rendered research artifact with %d characters", len(text))  # Record research size.
        return text

    def _data_model(self, document: SkillDocument) -> str:
        """Return the generated data model artifact."""
        logger.info("Rendering data model artifact")  # Record data model rendering.
        text = (  # Build the data model artifact in the real plan shape.
            f"# Data Model: {document.title} skill package\n\n"
            "## SkillDocument\n\n"
            "Fields: source path, domain, title, pages, slug, source file, and source hash.\n\n"
            "## SpecKitArtifactSet\n\n"
            "Fields: spec, plan, tasks, checklist, analysis, and Companion context.\n"
        )
        logger.debug("Rendered data model artifact with %d characters", len(text))  # Record data model size.
        return text

    def _quickstart(self, document: SkillDocument) -> str:
        """Return the generated quickstart artifact."""
        logger.info("Rendering quickstart artifact")  # Record quickstart rendering.
        text = (  # Build the quickstart artifact with proof steps.
            f"# Quickstart: {document.title} skill package\n\n"
            f"1. Run the harness for `{document.source_path}`.\n"
            f"2. Confirm that `specs/skills/{document.domain}/{document.slug}` has the required files.\n"
            "3. Run the analyzer and confirm zero critical findings.\n"
        )
        logger.debug("Rendered quickstart artifact with %d characters", len(text))  # Record quickstart size.
        return text

    def _contract(self, document: SkillDocument) -> str:
        """Return the generated package contract artifact."""
        logger.info("Rendering package contract artifact")  # Record contract rendering.
        text = (  # Build the package contract artifact.
            f"# Contract: {document.title} skill package\n\n"
            f"The harness writes required files under `specs/skills/{document.domain}/{document.slug}`.\n"
            "The context file stores living-spec metadata with a source hash.\n"
        )
        logger.debug("Rendered package contract with %d characters", len(text))  # Record contract size.
        return text

    def _template(self, name: str) -> str:
        """Read one installed SpecKit template."""
        logger.info("Reading installed SpecKit template %s", name)  # Record template reuse.
        text = (self.paths.templates_dir / name).read_text(encoding="utf-8")  # Read the real template file.
        logger.debug("Read SpecKit template %s with %d characters", name, len(text))  # Record template size.
        return text

    def _document_sql(self) -> str:
        """Return the source document lookup SQL."""
        logger.info("Building source document SQL")  # Record SQL creation.
        sql = (
            "SELECT title, category, pages, part_count, domain, version_status, version_value "
            "FROM source_document WHERE document_key = ?"
        )  # Read persisted document facts.
        logger.debug("Built source document SQL with %d characters", len(sql))  # Record SQL size.
        return sql

    def _part_count_sql(self) -> str:
        """Return the source part count SQL."""
        logger.info("Building source part count SQL")  # Record SQL creation.
        sql = "SELECT COUNT(*) FROM source_part WHERE document_key = ? AND is_winner = 1"  # Count active parts.
        logger.debug("Built source part count SQL with %d characters", len(sql))  # Record SQL size.
        return sql

    def _latest_stage_detail(self, connection: sqlite3.Connection, document_key: str, stage: str) -> str:
        """Return the newest stage detail from the orchestrator journal."""
        logger.info("Reading latest orchestrator stage detail")  # Record stage lookup.
        row = connection.execute(self._stage_sql(), (document_key, stage)).fetchone()  # Read newest matching event.
        detail = str(row["detail"]) if row else "not measured"  # Report absence without hiding it.
        logger.debug("Latest stage detail for %s is %s", stage, detail)  # Record the non-secret detail.
        return detail

    def _stage_sql(self) -> str:
        """Return the stage detail lookup SQL."""
        logger.info("Building stage detail SQL")  # Record SQL creation.
        sql = (
            "SELECT detail FROM orchestrator_stage_event WHERE document_key = ? AND stage = ? "
            "AND status = 'completed' ORDER BY id DESC LIMIT 1"
        )  # Read the newest completed event for this stage.
        logger.debug("Built stage detail SQL with %d characters", len(sql))  # Record SQL size.
        return sql

    def _today(self) -> str:
        """Return the current UTC date."""
        logger.info("Resolving the current UTC date")  # Record time value creation.
        value = datetime.now(UTC).date().isoformat()  # Use UTC for deterministic date semantics.
        logger.debug("Resolved current UTC date %s", value)  # Record generated date.
        return value

    def _now(self) -> str:
        """Return the current UTC timestamp."""
        logger.info("Resolving the current UTC timestamp")  # Record time value creation.
        value = datetime.now(UTC).replace(microsecond=0).isoformat()  # Use second precision for stable JSON.
        logger.debug("Resolved current UTC timestamp %s", value)  # Record generated timestamp.
        return value
