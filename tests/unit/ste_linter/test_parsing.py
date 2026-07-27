"""Tests for the parsing package."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from tools.ste_linter.parsing.markdown import MarkdownParser  # The Markdown parser under test.
from tools.ste_linter.parsing.python_source import PythonSourceParser  # The Python parser under test.
from tools.ste_linter.parsing.segmentation import Segmenter  # The segmenter under test.
from tools.ste_linter.parsing.wordcount import WordCounter  # The word counter under test.


def test_wordcount_basic() -> None:
    """A plain sentence counts each word once."""
    assert WordCounter().count("Set the switch to ON") == 5  # Five plain words.


def test_wordcount_number_and_unit() -> None:
    """A number joined to a unit counts as one word."""
    assert WordCounter().count("Apply 10 mA now") == 3  # "10 mA" is one word plus "Apply" and "now".


def test_wordcount_quoted_span() -> None:
    """A quoted span counts as one word."""
    assert WordCounter().count('He set "do not touch here"') == 3  # He, set, and the quoted span.


def test_segmenter_splits_sentences() -> None:
    """The segmenter splits a paragraph into sentences."""
    pairs = Segmenter().split_sentences("The cat sat. It ran fast.")  # Two sentences.
    assert len(pairs) == 2  # The splitter found both sentences.


def test_segmenter_keeps_abbreviation() -> None:
    """The segmenter does not split after a known abbreviation."""
    pairs = Segmenter().split_sentences("Use a tool, e.g. a wrench, for the job.")  # One sentence.
    assert len(pairs) == 1  # The abbreviation did not end the sentence.


def test_markdown_skips_code_fence() -> None:
    """The Markdown parser skips fenced code."""
    spans = MarkdownParser().parse("Prose here.\n\n```\ncode line\n```\nMore prose.")  # Mixed content.
    joined = " ".join(span.text for span in spans)  # Join the prose spans.
    assert "code line" not in joined  # The code did not enter the prose.
    assert "Prose here." in joined  # The prose stayed.


def test_markdown_keeps_link_text() -> None:
    """The Markdown parser keeps link text and drops the URL."""
    spans = MarkdownParser().parse("See the [guide](http://example.com) now.")  # A link.
    joined = " ".join(span.text for span in spans)  # Join the prose.
    assert "guide" in joined and "example.com" not in joined  # Text kept, URL dropped.


def test_python_extracts_docstring_and_comment() -> None:
    """The Python parser reads docstrings and comments."""
    source = '"""Module doc here."""\n# A helpful comment.\nx = 1  # inline note\n'  # Sample source.
    spans, note = PythonSourceParser().parse(source)  # Parse the source.
    kinds = {span.kind for span in spans}  # The span kinds found.
    assert note == ""  # The source parsed cleanly.
    assert "docstring" in kinds and "comment" in kinds  # Both prose kinds were found.


def test_python_skips_directive_comment() -> None:
    """The Python parser skips a tool directive comment."""
    spans, _ = PythonSourceParser().parse("# type: ignore\nx = 1\n")  # A directive comment.
    assert not any(span.kind == "comment" for span in spans)  # The directive was skipped.


def test_document_builder_marks_mode(build_doc) -> None:
    """The builder marks an imperative sentence as procedural."""
    document = build_doc("Set the switch to ON. The light is green here.")  # One step, one description.
    modes = {sentence.text.split()[0]: sentence.mode for sentence in document.sentences}  # Map first word.
    assert modes["Set"] == "procedural"  # The step is procedural.
    assert modes["The"] == "descriptive"  # The description is descriptive.
