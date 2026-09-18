"""Unit tests for the Juniper skill rewrite components."""

from __future__ import annotations  # Keep annotations cheap during test collection.

from pathlib import Path  # Build test file paths with portable path objects.

from src.juniper_skills.rewrite import (  # Import the public rewrite API used by orchestrator agents.
    CardClassMark,
    CardExtractor,
    KnowledgeCard,
    PromptTemplateBuilder,
    RewriteWorkPacket,
    RuleBasedBackend,
    SimilarityCheckInput,
    SteValidator,
    VerbatimSimilarityGuard,
)


class TestVerbatimSimilarityGuard:
    """Verify that the copyright similarity guard measures prose correctly."""

    def test_guard_catches_twenty_word_copied_passage(self, tmp_path: Path) -> None:
        """The guard fails when generated prose copies a long source passage."""
        words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"  # Build the first ten copied words.
        copied = words + " kilo lima mike november oscar papa quebec romeo sierra tango"  # Add ten more words.
        generated = tmp_path / "topic.md"  # Put the generated topic in the pytest work directory.
        generated.write_text(copied, encoding="utf-8")  # Write the copied passage for the guard.
        check = SimilarityCheckInput(generated, (copied,))  # Compare the generated file to its source.
        report = VerbatimSimilarityGuard().check((check,))  # Run the guard against one file.
        assert report.files_checked == 1  # Prove the guard measured one file.
        assert report.results[0].longest_run == 20  # Prove the copied passage was measured.
        assert not report.passed  # Prove the guard rejects the copied passage.

    def test_guard_ignores_verbatim_cli_block(self, tmp_path: Path) -> None:
        """The guard passes a long CLI block because commands stay verbatim."""
        command_template = "set interfaces ge-0/0/%d unit 0 family inet address 192.0.2.%d/24"  # Set CLI shape.
        commands = [command_template % (index, index) for index in range(40)]  # Build config lines.
        cli = "\n".join(commands)  # Build a long config block.
        generated = tmp_path / "topic.md"  # Put the generated topic in the pytest work directory.
        generated.write_text(cli, encoding="utf-8")  # Write the verbatim CLI block.
        check = SimilarityCheckInput(generated, (cli,))  # Compare the generated file to the source CLI.
        report = VerbatimSimilarityGuard().check((check,))  # Run the similarity guard.
        assert report.files_checked == 1  # Prove the guard measured the file.
        assert report.results[0].longest_run == 0  # Prove the CLI block did not count as prose.
        assert report.passed  # Prove approved verbatim classes pass.

    def test_guard_catches_light_edit_of_source_prose(self, tmp_path: Path) -> None:
        """The guard fails a copied paragraph with a small edit."""
        source = "The router can apply another output filter after the first filter"  # Build source.
        source = source + " reduces the display"  # Add the phrase that the copied text edits.
        source = source + " and the operator reads useful rows."  # Add enough words to catch light edits.
        generated = tmp_path / "topic.md"  # Put the generated topic in the pytest work directory.
        copied = "The router can apply another output filter after the first filter reduces the output"  # Build edit.
        copied = copied + " and the operator reads useful rows."  # Keep copied words around the edit.
        generated.write_text(copied, encoding="utf-8")  # Write the light-edit plagiarism sample.
        check = SimilarityCheckInput(generated, (source,))  # Compare the generated file to its source.
        report = VerbatimSimilarityGuard().check((check,))  # Run the guard against one file.
        assert report.results[0].longest_run > 12  # Prove the edit still leaves a long copied run.
        assert not report.passed  # Prove the current contract catches this light edit.

    def test_guard_clears_genuine_restatement(self, tmp_path: Path) -> None:
        """The guard passes prose that states the same fact with new phrasing."""
        source = "The router can apply another output filter after the first filter reduces the display."  # Set source.
        generated = tmp_path / "topic.md"  # Put the generated topic in the pytest work directory.
        restated = "Junos lets you narrow command results more than once with chained match operations."  # Restate.
        generated.write_text(restated, encoding="utf-8")  # Write the restated sample.
        check = SimilarityCheckInput(generated, (source,))  # Compare the generated file to its source.
        report = VerbatimSimilarityGuard().check((check,))  # Run the guard against one file.
        assert report.results[0].longest_run < 12  # Prove the restatement stays below the threshold.
        assert report.passed  # Prove the guard clears genuine restatement.

    def test_guard_reports_cleared_warned_and_failed_bands(self, tmp_path: Path) -> None:
        """The guard reports the three similarity bands."""
        source = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike"  # Build source.
        checks = self._band_checks(tmp_path, source)  # Build one file for each report band.
        report = VerbatimSimilarityGuard().check(checks)  # Run the guard across all three files.
        assert report.files_cleared == 1  # Prove the report counts the clear band.
        assert report.files_warned == 1  # Prove the report counts the review band.
        assert report.files_failed == 1  # Prove the report counts the hard-fail band.
        assert not report.passed  # Prove the failed file still stops the build.

    def _band_checks(self, tmp_path: Path, source: str) -> tuple[SimilarityCheckInput, ...]:
        """Return one generated file for each similarity band."""
        texts = ("alpha bravo charlie", source.rsplit(" ", 4)[0], source)  # Create clear, warn, and fail text.
        paths = tuple(tmp_path / f"topic-{index}.md" for index in range(3))  # Create stable topic file paths.
        for path, text in zip(paths, texts, strict=True):  # Write each test file before the guard reads it.
            path.write_text(text, encoding="utf-8")  # Store the generated test text.
        return tuple(SimilarityCheckInput(path, (source,)) for path in paths)  # Return guard inputs.

    def test_guard_fails_zero_files(self) -> None:
        """The guard fails when it checks no files."""
        report = VerbatimSimilarityGuard().check(tuple())  # Run the guard with no inputs.
        assert report.files_checked == 0  # Prove the report states zero checked files.
        assert not report.passed  # Prove the contract failure is enforced.
        assert report.errors  # Prove the report explains the failure.


class TestKnowledgeCards:
    """Verify the card model and extractor."""

    def test_card_round_trip(self) -> None:
        """A card keeps its mark, fact, and citation through Markdown."""
        fact = "Set the interface address before you commit."  # Use a short STE fact for the round trip.
        card = KnowledgeCard(CardClassMark.MUST, fact, "[JUNOS-BEG p.66]")  # Build a contract card.
        line = card.to_markdown()  # Render the card to the topic-file format.
        parsed = CardExtractor().from_markdown(line)  # Parse the card from Markdown.
        assert parsed == card  # Prove the card survives the round trip.


class TestRewriteHarness:
    """Verify the deterministic rewrite seam."""

    def test_prompt_template_contains_required_rules(self) -> None:
        """The prompt template carries the locked compliance rules."""
        commands = ("show interfaces terse",)  # Give the prompt one command that must stay verbatim.
        packet = RewriteWorkPacket(  # Build a packet with routing context.
            "A router supports BGP.",
            "p.66",
            commands,
            "routing",
            ("day2",),
            "JUNOS-BEG",
        )
        prompt = PromptTemplateBuilder().build(packet)  # Render the future agent backend prompt.
        assert "Do not copy Juniper source prose." in prompt  # Prove the copyright rule is present.
        assert "Simplified Technical English" in prompt  # Prove the STE rule is present.
        assert "Keep commands" in prompt  # Prove the verbatim-class rule is present.
        assert "Life cycle tags: day2." in prompt  # Prove life cycle tags route the output.

    def test_rule_based_backend_reports_limits(self) -> None:
        """The rule backend extracts structure and states its limitation."""
        source = "| Field | Value |\n| - | - |\n| Speed | 100 Gb/s |"  # Build structured source content.
        packet = RewriteWorkPacket(source, "p.10", tuple(), "switching", ("day0",), "QFX-GUIDE")  # Build a packet.
        result = RuleBasedBackend().rewrite(packet)  # Run deterministic extraction.
        assert result.cards  # Prove structured content creates cards.
        assert result.limitations  # Prove the backend states what it cannot do.


class TestSteValidator:
    """Verify STE validation through the existing linter."""

    def test_ste_validator_scores_file(self, tmp_path: Path) -> None:
        """The STE validator returns one score for one generated file."""
        topic = tmp_path / "topic.md"  # Put the test topic in the pytest work directory.
        prose = "- **INFO** The router stores the route. [JUNOS-BEG p.1]\n"  # Use simple STE prose.
        topic.write_text(prose, encoding="utf-8")  # Write simple STE prose.
        report = SteValidator(minimum_score=1).validate((topic,))  # Run the validator with a low test threshold.
        assert report.files_checked == 1  # Prove the validator measured one file.
        assert report.reports[0].score >= 1  # Prove the validator returned a usable score.
        assert report.passed  # Prove the report passed the configured threshold.
