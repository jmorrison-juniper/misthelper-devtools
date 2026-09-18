"""Tests for Juniper quality repair and cleanup tools."""

from dataclasses import dataclass  # Create a small card fixture.

from src.juniper_skills.quality import (  # Test built-store cleanup tools.  # Test repair measurement.
    CardDeduplicator,
    CommandFenceCleaner,
    SourceTextRepairer,
    WordFrequencyDictionary,
)


@dataclass(frozen=True)
class DummyCard:
    """A card fixture that matches the deduplicator protocol."""

    fact: str  # Store the fact text for normalized matching.
    citation_key: str  # Store citation detail for tie breaking.


def test_repair_accuracy_bar_disables_command_repair_when_low() -> None:
    dictionary = WordFrequencyDictionary()  # Use only seeded terms for a deterministic low-accuracy repair.
    repairer = SourceTextRepairer(dictionary)  # Create the repairer under test.
    lines = ("set class operator-and-boot allow-commands request system reboot",)  # Use a command line sample.
    report = repairer.measure_accuracy(lines)  # Strip and repair the held-out command line.
    assert not report.auto_repair_commands  # Commands must stay disabled below the safety bar.
    assert "disabled" in report.decision  # The decision must explain the safety result.


def test_repair_line_replaces_untrusted_command_with_source_pointer() -> None:
    repairer = SourceTextRepairer(WordFrequencyDictionary())  # Create a repairer without command proof.
    repaired = repairer.repair_line("setclassoperator-and-bootallow-commands", repair_commands=False)  # Repair command.
    assert repaired.startswith("Command unavailable")  # Unsafe command repair must not publish plausible syntax.


def test_card_deduplicator_keeps_more_specific_citation() -> None:
    cards = (DummyCard("Configure the login class.", "DOC p.0-0"), DummyCard("Configure the login class.", "DOC p.4-5"))
    report = CardDeduplicator().deduplicate(cards)  # Merge duplicate cards by normalized card text.
    assert report.merge_count == 1  # One duplicate card must be removed.
    assert report.cards[0].citation_key == "DOC p.4-5"  # The most specific citation must win.


def test_card_deduplicator_removes_markdown_card_duplicates() -> None:
    text = "\n".join(
        [
            "# Topic",
            "- INFO: Configure the login class. [DOC p.0-0]",
            "- INFO: Configure the login class. [DOC p.4-5]",
        ]
    )
    cleaned, merges = CardDeduplicator().deduplicate_markdown_text(text)  # Clean duplicate topic card lines.
    assert merges == 1  # One duplicate Markdown card must be removed.
    assert "DOC p.4-5" in cleaned  # The more specific citation must remain.
    assert "DOC p.0-0" not in cleaned  # The placeholder citation must be removed.


def test_command_fence_cleaner_removes_prose_leaks() -> None:
    text = "\n".join(
        [
            "```text",
            "commit. There is no reason to skip this.",
            "request Make system-level requests",
            "show interfaces terse",
            "```",
        ]
    )  # Build a fence with two prose leak examples.
    report = CommandFenceCleaner().clean_text(text)  # Clean the command fence.
    assert report.lines_removed == 2  # Prose and help output must be rejected.
    assert "show interfaces terse" in report.text  # Valid Junos commands must remain.
    assert "request Make" not in report.text  # CLI help descriptions must not remain as commands.


def test_command_fence_cleaner_rejects_sentence_period() -> None:
    text = "```text\nshow interface terse ge-0/0/0.\nset system services ssh\n```"  # Add a trailing period defect.
    report = CommandFenceCleaner().clean_text(text)  # Clean the command fence.
    assert report.lines_removed == 1  # Sentence punctuation inside a command fence must be removed.
    assert "set system services ssh" in report.text  # A valid configuration line must remain.
