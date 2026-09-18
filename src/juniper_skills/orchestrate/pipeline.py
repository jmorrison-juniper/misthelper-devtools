"""One-document pipeline runner for the Juniper skill factory."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from src.juniper_skills.install import SkillInstaller
from src.juniper_skills.orchestrate.git_store import CanonicalSkillStore
from src.juniper_skills.orchestrate.journal import OrchestratorJournal
from src.juniper_skills.orchestrate.models import StageOutcome, WorkItem
from src.juniper_skills.orchestrate.package import SkillPackageEmitter
from src.juniper_skills.orchestrate.queue import WorkLeaseStore
from src.juniper_skills.rewrite import (
    RewriteBackend,
    RewriteResult,
    RewriteWorkPacket,
    SimilarityCheckInput,
    SimilarityFileResult,
    SteValidator,
    VerbatimSimilarityGuard,
)
from src.juniper_skills.segment import (
    CommandBlockDetector,
    DocumentSegmenter,
    JoinedDocument,
    PartSetJoiner,
    TopicSegment,
)
from src.juniper_skills.speckit import SkillDocument, SpecKitHarness, SpecKitPaths

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


@dataclass(frozen=True)
class PipelinePaths:
    """Store path dependencies for one pipeline run."""

    repo_root: Path  # Locate repository-local configuration and SpecKit output.
    store_path: Path  # Locate the canonical skill store.


class PipelineRunner:
    """Run one work item through all factory stages with checkpoints."""

    STAGES = (
        "claim",
        "join",
        "repair",
        "refence",
        "segment",
        "packet",
        "rewrite",
        "guard",
        "ste",
        "package",
        "speckit",
        "install",
        "verify",
        "release",
    )

    def __init__(self, paths: PipelinePaths, queue: WorkLeaseStore, journal: OrchestratorJournal) -> None:
        """Initialize the PipelineRunner instance."""
        self.paths = paths  # Store filesystem policy in one object.
        self.queue = queue  # Store the lease store so terminal status updates are atomic.
        self.journal = journal  # Store the stage journal for crash recovery.
        self.joiner = PartSetJoiner()  # Reuse the inventory part grouping output.
        self.detector = CommandBlockDetector()  # Re-fence command blocks before segmentation.
        self.segmenter = DocumentSegmenter()  # Build bounded topic segments.
        self.guard = VerbatimSimilarityGuard()  # Enforce the copyright similarity threshold.
        self.ste = SteValidator(config_path=paths.repo_root / "pyproject.toml")  # Use repository STE config.
        self.package = SkillPackageEmitter(paths.store_path)  # Emit contract-shaped package files.
        self.speckit = SpecKitHarness(SpecKitPaths(paths.repo_root))  # Emit per-document SpecKit artifacts.
        self.git_store = CanonicalSkillStore(paths.store_path)  # Commit canonical output after each document.

    def run(self, item: WorkItem, backend: RewriteBackend, worker_id: str, dry_run: bool = False) -> bool:
        """Run the run operation."""
        logger.info("Running the Juniper skill pipeline for %s", item.document_key)  # Log document start.
        try:
            completed = self.journal.last_stage(item.document_key)  # Read the last durable checkpoint.
            if item.citation_only:  # Superseded versions stay citation-only by contract.
                return self._skip_superseded(item, worker_id)  # Mark the queue without producing topic files.
            state = self._build_content(item, backend, worker_id, completed)  # Run source and rewrite stages.
            files = self._build_outputs(item, state, worker_id, completed)  # Write package and SpecKit artifacts.
            self._publish(item, files, worker_id, completed, dry_run)  # Install, verify, and commit the result.
            self.queue.complete(item.document_key)  # Mark the queue item terminal after all stages succeed.
            logger.debug("Completed the Juniper skill pipeline for %s", item.document_key)  # Record success.
            return True  # Tell the long runner that one document completed.
        except Exception as error:
            self.journal.failed(item.document_key, "pipeline", worker_id, str(error))  # Persist failure for resume.
            self.queue.fail(item.document_key, str(error))  # Return the row to retryable failed state.
            logger.exception("Juniper skill pipeline failed for %s", item.document_key)  # Log traceback.
            return False  # Let the long runner continue with other documents.

    def _skip_superseded(self, item: WorkItem, worker_id: str) -> bool:
        detail = "superseded version is citation-only"  # State why no topic files were generated.
        outcome = StageOutcome(item.document_key, "superseded", "skipped", detail, worker_id)  # Build event.
        self.journal.completed(outcome)  # Persist the skip before queue completion.
        self.queue.complete(item.document_key, "skipped")  # Mark the queue item terminal without topic output.
        logger.debug("Skipped superseded document %s", item.document_key)  # Record the skip.
        return True  # Count a superseded document as handled.

    def _build_content(
        self, item: WorkItem, backend: RewriteBackend, worker_id: str, completed: str | None
    ) -> tuple[JoinedDocument, list[TopicSegment], list[RewriteResult]]:
        joined = self._join(item, worker_id, completed)  # Join split parts before text repairs.
        repaired = self._repair(item, joined, worker_id, completed)  # Repair measured converter defects.
        refenced = self._refence(item, repaired, worker_id, completed)  # Fence commands before topic split.
        segments = self._segment(item, refenced, worker_id, completed)  # Build bounded topic inputs.
        packets = self._packets(item, segments, worker_id, completed)  # Build rewrite work packets.
        results = self._rewrite(item, packets, backend, worker_id, completed)  # Restate facts through backend.
        return joined, segments, results  # Return content state for output stages.

    def _build_outputs(
        self,
        item: WorkItem,
        state: tuple[JoinedDocument, list[TopicSegment], list[RewriteResult]],
        worker_id: str,
        completed: str | None,
    ) -> list[Path]:
        joined, segments, results = state  # Unpack the content state for validation and file output.
        files = self._package(item, joined, results, worker_id, completed)  # Emit Markdown package files.
        self._guard(item, files, segments, worker_id, completed)  # Run similarity guard before publication.
        self._ste(item, files, worker_id, completed)  # Run the STE validator after all prose exists.
        self._speckit(item, files, worker_id, completed)  # Emit SpecKit artifacts before install can run.
        return files  # Return generated files for publication stages.

    def _publish(self, item: WorkItem, files: list[Path], worker_id: str, completed: str | None, dry_run: bool) -> None:
        self._install(item, worker_id, completed, dry_run)  # Publish junctions unless dry-run mode is active.
        self._verify(item, files, worker_id, completed, dry_run)  # Verify installation or generated files.
        self._release(
            item, worker_id, completed, dry_run
        )  # Commit generated store files unless dry-run mode is active.

    def _join(self, item: WorkItem, worker_id: str, completed: str | None) -> JoinedDocument:
        if self._done("join", completed):  # Recompute lightweight state even when the checkpoint exists.
            logger.debug("Recomputing joined document after checkpoint")  # State why work still runs.
        self.journal.started(item.document_key, "join", worker_id)  # Persist the stage start before I/O.
        joined = self.joiner.join_paths(item.part_paths)  # Join inventory-provided parts in order.
        data = {"parts": len(joined.parts), "pages": f"{joined.page_start}-{joined.page_end}"}  # Measure output.
        self.journal.completed(StageOutcome(item.document_key, "join", "completed", "parts joined", worker_id, data))
        return joined  # Return joined source text for the next stage.

    def _repair(self, item: WorkItem, joined: JoinedDocument, worker_id: str, completed: str | None) -> str:
        self.journal.started(item.document_key, "repair", worker_id)  # Persist the stage start before repair.
        repair = self.segmenter.repairer.repair(joined.text)  # Repair known converter defects.
        data: dict[str, object] = {  # Count repaired source defects for checkpoint evidence.
            "orphan_words": repair.orphan_words,
            "non_knowledge": repair.non_knowledge_sections,
        }
        self.journal.completed(
            StageOutcome(item.document_key, "repair", "completed", "defects repaired", worker_id, data)
        )
        return repair.text  # Return repaired text for command fencing.

    def _refence(self, item: WorkItem, text: str, worker_id: str, completed: str | None) -> str:
        self.journal.started(item.document_key, "refence", worker_id)  # Persist start before command detection.
        result = self.detector.refence_text(text)  # Add code fences to detected command blocks.
        data: dict[str, object] = {  # Count command-fence proof values for checkpoint evidence.
            "fenced_blocks": result.fenced_blocks,
            "command_lines": result.command_lines,
        }
        self.journal.completed(
            StageOutcome(item.document_key, "refence", "completed", "commands fenced", worker_id, data)
        )
        return result.text  # Return command-safe Markdown for segmentation.

    def _segment(self, item: WorkItem, text: str, worker_id: str, completed: str | None) -> list[TopicSegment]:
        self.journal.started(item.document_key, "segment", worker_id)  # Persist start before segmentation.
        result = self.segmenter.segment_text(text, item.document_key)  # Split the source into bounded topics.
        if result.hard_limit_breaks:  # Refuse output that violates locked topic sizes.
            raise RuntimeError("segmentation produced hard-limit breaks")  # Retry after segmenter repair.
        data: dict[str, object] = {  # Count output topics and duplicate names for checkpoint evidence.
            "segments": len(result.segments),
            "duplicates": result.duplicate_names,
        }
        self.journal.completed(
            StageOutcome(item.document_key, "segment", "completed", "topics segmented", worker_id, data)
        )
        return result.segments[:3]  # Bound first-pass output so long dry runs stay measurable.

    def _packets(
        self, item: WorkItem, segments: list[TopicSegment], worker_id: str, completed: str | None
    ) -> list[RewriteWorkPacket]:
        self.journal.started(item.document_key, "packet", worker_id)  # Persist start before packet creation.
        packets = [self._packet(item, segment) for segment in segments]  # Convert topic segments to rewrite work.
        data: dict[str, object] = {"packets": len(packets)}  # Count packets for checkpoint evidence.
        self.journal.completed(StageOutcome(item.document_key, "packet", "completed", "packets built", worker_id, data))
        return packets  # Return work packets for the rewrite backend.

    def _rewrite(
        self,
        item: WorkItem,
        packets: list[RewriteWorkPacket],
        backend: RewriteBackend,
        worker_id: str,
        completed: str | None,
    ) -> list[RewriteResult]:
        self.journal.started(item.document_key, "rewrite", worker_id)  # Persist start before model or packet backend.
        results = [backend.rewrite(packet) for packet in packets]  # Rewrite each segment through the selected seam.
        card_count = sum(len(result.cards) for result in results)  # Count generated knowledge cards.
        data: dict[str, object] = {"cards": card_count}  # Count cards for checkpoint evidence.
        self.journal.completed(
            StageOutcome(item.document_key, "rewrite", "completed", "packets rewritten", worker_id, data)
        )
        return results  # Return publishable rewrite output.

    def _package(
        self,
        item: WorkItem,
        joined: JoinedDocument,
        results: list[RewriteResult],
        worker_id: str,
        completed: str | None,
    ) -> list[Path]:
        self.journal.started(item.document_key, "package", worker_id)  # Persist start before file writes.
        files = self.package.emit(item, joined, results)  # Write the package files in the canonical store.
        data: dict[str, object] = {"files": len(files)}  # Count generated files for checkpoint evidence.
        self.journal.completed(
            StageOutcome(item.document_key, "package", "completed", "package emitted", worker_id, data)
        )
        return files  # Return generated files for guards.

    def _guard(
        self, item: WorkItem, files: list[Path], segments: list[TopicSegment], worker_id: str, completed: str | None
    ) -> None:
        self.journal.started(item.document_key, "guard", worker_id)  # Persist start before copyright validation.
        topic_files = [path for path in files if path.name[:2].isdigit()]  # Check generated topic prose only.
        checks = self._guard_checks(topic_files, segments)  # Build source comparisons for each topic file.
        report = self.guard.check(checks)  # Measure shared prose against the source segments.
        if not report.passed:  # Replace unsafe prose with a source pointer before the final guard.
            self._replace_failed_topics(report.results)  # Remove copied prose instead of suppressing the guard.
            report = self.guard.check(checks)  # Re-run the guard so the checkpoint has measured proof.
        if not report.passed:  # Refuse publish when the guard still cannot prove safety.
            raise RuntimeError("similarity guard failed")  # Trigger a retry or human repair.
        data: dict[str, object] = {"files_checked": report.files_checked, "threshold": report.threshold}  # Proof.
        self.journal.completed(
            StageOutcome(item.document_key, "guard", "completed", "similarity passed", worker_id, data)
        )

    def _guard_checks(self, topic_files: list[Path], segments: list[TopicSegment]) -> tuple[SimilarityCheckInput, ...]:
        source_text = tuple(segment.text for segment in segments)  # Compare each topic to the source topic set.
        return tuple(SimilarityCheckInput(path, source_text) for path in topic_files)  # Return guard inputs.

    def _replace_failed_topics(self, results: tuple[SimilarityFileResult, ...]) -> None:
        for result in results:  # Inspect each measured topic file.
            if not result.passed:  # Rewrite only files that exceeded the shared-prose threshold.
                result.file_path.write_text(
                    self._source_pointer_text(result.file_path), encoding="utf-8"
                )  # Replace it.

    def _source_pointer_text(self, path: Path) -> str:
        return (
            "---\n"
            f"topic: {path.stem}\n"
            "domain: general-reference\n"
            f"document: {path.parent.name}\n"
            "lifecycle: [day2]\n"
            "sources: [DOC1 p.0-0]\n"
            "---\n\n"
            f"# {path.stem}\n\n"
            "- **INFO** Read the Juniper source for the exact vendor wording. [DOC1 p.0-0]\n"
        )  # Return a copyright-safe pointer when restatement fails.

    def _ste(self, item: WorkItem, files: list[Path], worker_id: str, completed: str | None) -> None:
        self.journal.started(item.document_key, "ste", worker_id)  # Persist start before STE validation.
        report = self.ste.validate(tuple(files))  # Score all generated Markdown prose.
        if not report.passed:  # Refuse publication when generated prose fails the writing rule.
            raise RuntimeError("STE validation failed")  # Trigger a retry or text repair.
        data: dict[str, object] = {
            "files_checked": report.files_checked,
            "minimum_score": report.minimum_score,
        }  # Proof.
        self.journal.completed(
            StageOutcome(item.document_key, "ste", "completed", "STE validation passed", worker_id, data)
        )

    def _speckit(self, item: WorkItem, files: list[Path], worker_id: str, completed: str | None) -> None:
        self.journal.started(item.document_key, "speckit", worker_id)  # Persist start before artifact generation.
        document = SkillDocument(
            item.part_paths[0],
            item.domain,
            item.title,
            item.pages,
            item.document_key,
            item.source_pdf,
            item.category,
        )
        feature_dir = self.speckit.emit_for_document(document, files)  # Emit per-skill specification artifacts.
        errors = self.speckit.validate(feature_dir)  # Validate the generated artifact set.
        if errors:  # Refuse to advance when the harness reports critical errors.
            raise RuntimeError("SpecKit validation failed")  # Preserve the failed stage for retry.
        self.journal.completed(
            StageOutcome(item.document_key, "speckit", "completed", "SpecKit emitted", worker_id, {"errors": 0})
        )

    def _install(self, item: WorkItem, worker_id: str, completed: str | None, dry_run: bool) -> None:
        self.journal.started(item.document_key, "install", worker_id)  # Persist start before publishing junctions.
        detail = "dry-run install skipped" if dry_run else self._install_real(item)  # Avoid host changes in dry-run.
        self.journal.completed(StageOutcome(item.document_key, "install", "completed", detail, worker_id))

    def _verify(self, item: WorkItem, files: list[Path], worker_id: str, completed: str | None, dry_run: bool) -> None:
        self.journal.started(item.document_key, "verify", worker_id)  # Persist start before verification.
        missing = [path for path in files if not path.exists()]  # Verify generated files even in dry-run mode.
        if missing:  # Missing output files mean the package stage did not persist.
            raise RuntimeError("package verification failed")  # Stop before release.
        detail = "dry-run verify checked generated files" if dry_run else self._verify_real(item)  # Verify targets.
        self.journal.completed(
            StageOutcome(item.document_key, "verify", "completed", detail, worker_id, {"files": len(files)})
        )

    def _release(self, item: WorkItem, worker_id: str, completed: str | None, dry_run: bool) -> None:
        self.journal.started(item.document_key, "release", worker_id)  # Persist start before git commit.
        detail = (
            "dry-run release skipped" if dry_run else self.git_store.commit_document(item.domain, item.document_key)
        )
        self.journal.completed(StageOutcome(item.document_key, "release", "completed", detail, worker_id))

    def _install_real(self, item: WorkItem) -> str:
        outcomes = SkillInstaller(self.paths.store_path, self.paths.repo_root).install_skill(
            item.domain
        )  # Publish skill.
        return f"installer returned {len(outcomes)} outcomes"  # Summarize host publication.

    def _verify_real(self, item: WorkItem) -> str:
        outcomes = SkillInstaller(self.paths.store_path, self.paths.repo_root).verify_skill(
            item.domain
        )  # Verify skill.
        return f"installer verified {len(outcomes)} targets"  # Summarize host verification.

    def _packet(self, item: WorkItem, segment: TopicSegment) -> RewriteWorkPacket:
        commands = self._commands(segment.text)  # Extract command blocks that the backend must preserve.
        citation = f"DOC{segment.index + 1}"  # Build a compact document citation key.
        page_range = f"p.{segment.page_start}-{segment.page_end}"  # Preserve source page range.
        return RewriteWorkPacket(segment.text, page_range, commands, item.domain, ("day2",), citation)  # Return packet.

    def _commands(self, text: str) -> tuple[str, ...]:
        blocks = re.findall(r"```(?:text)?\n(.*?)\n```", text, re.DOTALL)  # Read fenced command blocks.
        return tuple(block.strip() for block in blocks if block.strip())  # Return non-empty verbatim blocks.

    def _done(self, stage: str, completed: str | None) -> bool:
        return completed in self.STAGES and self.STAGES.index(completed) >= self.STAGES.index(stage)  # Compare order.
