"""Tests for the parsing package."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

from tools.ste_linter.config import LinterConfig  # The parser configuration under test.
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


def test_segmenter_splits_a_docstring_field_block() -> None:
    """Issue #1993: a field block joined into one long sentence that nobody could repair.

    Why:
        The ordinary boundary needs a capital letter after the end mark. A
        Google-style entry starts with a lower-case name, so the segmenter
        joined every entry of one block. The length rule then reported a
        violation against a block of short entries.
    """
    block = "session: The cloud session. The caller owns it.\nsite_id: The site to read.\ndevices: The rows."
    pairs = Segmenter().split_sentences(block)  # Four short sentences, not one long one.
    assert len(pairs) == 4  # Each entry and each trailing sentence stands alone.
    assert pairs[2][0] == "site_id: The site to read."  # The second entry keeps its own text.


def test_segmenter_keeps_a_wrapped_prose_line_whole() -> None:
    """The field rule must not split ordinary prose that wraps onto a new line."""
    prose = "The portal reads the site.\nIt then writes the record."  # A capital starts the second line.
    assert len(Segmenter().split_sentences(prose)) == 2  # The ordinary rule already split these two.


def test_segmenter_splits_before_a_code_span() -> None:
    """A technical sentence often starts with an inline code span, not a capital."""
    text = "The browser sends a MAC address. ``to_device_targets`` reads the type."  # Two sentences.
    assert len(Segmenter().split_sentences(text)) == 2  # The code span starts its own sentence.


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


def test_python_extracts_logging_string_when_enabled() -> None:
    """The Python parser reads logging strings when configured."""
    config = LinterConfig(grade_logging_strings=True)  # Enable the opt-in logging surface.
    source = 'import logging\nlogging.info("The devices have been fetched.")\n'  # A passive log message.
    spans, note = PythonSourceParser(config).parse(source)  # Parse with logging strings enabled.
    logging_spans = [span for span in spans if span.kind == "logging"]  # Keep only the new span kind.
    assert note == ""  # The source parsed cleanly.
    assert logging_spans[0].text == "The devices have been fetched."  # The message prose was kept.
    assert logging_spans[0].start_line == 2  # The span points at the logging call line.


def test_python_skips_logging_string_by_default() -> None:
    """The Python parser keeps the old default surface."""
    source = 'import logging\nlogging.info("The devices have been fetched.")\n'  # A passive log message.
    spans, _ = PythonSourceParser().parse(source)  # Parse with default settings.
    assert not any(span.kind == "logging" for span in spans)  # The default run adds no logging spans.


def test_python_cleans_lazy_logging_placeholder() -> None:
    """The Python parser removes a lazy logging placeholder."""
    config = LinterConfig(grade_logging_strings=True)  # Enable the opt-in logging surface.
    source = 'import logging\nlogging.info("Fetched %s devices", total)\n'  # A lazy logging template.
    spans, _ = PythonSourceParser(config).parse(source)  # Parse the source with the new surface.
    logging_spans = [span for span in spans if span.kind == "logging"]  # Keep the logging span.
    assert logging_spans[0].text == "Fetched devices"  # The placeholder is not graded.


def test_python_extracts_user_facing_strings_when_enabled() -> None:
    """The Python parser reads print text and safe input prompts."""
    config = LinterConfig(grade_user_facing_strings=True)  # Enable prompts and printed text.
    source = 'print("The task has been completed.")\nsafe_input("Enter the site name: ")\n'  # User text.
    spans, note = PythonSourceParser(config).parse(source)  # Parse with user-facing strings enabled.
    user_spans = [span for span in spans if span.kind == "user-facing"]  # Keep the new span kind.
    assert note == ""  # The source parsed cleanly.
    assert [span.text for span in user_spans] == ["The task has been completed.", "Enter the site name:"]  # Text.
    assert [span.start_line for span in user_spans] == [1, 2]  # Each span points at its call line.


def test_python_extracts_prompt_keyword() -> None:
    """The Python parser reads the named safe input prompt."""
    config = LinterConfig(grade_user_facing_strings=True)  # Enable the user-facing surface.
    source = 'safe_input(prompt="Enter the device name: ", context="menu")\n'  # A named prompt argument.
    spans, _ = PythonSourceParser(config).parse(source)  # Parse the source with the new surface.
    user_spans = [span for span in spans if span.kind == "user-facing"]  # Keep prompt spans only.
    assert user_spans[0].text == "Enter the device name:"  # The prompt keyword was graded.


def test_python_string_edge_cases() -> None:
    """The Python parser handles string forms without grading code tokens."""
    config = LinterConfig(grade_logging_strings=True, grade_user_facing_strings=True)  # Enable both surfaces.
    source = "\n".join(
        [
            "import logging",  # Give the dotted call a real name.
            "logging.info(f'Fetched {device_count} devices')",  # F-string expressions are not prose.
            "logging.info('Use ' 'the site name.')",  # Implicit concatenation stays one literal.
            "print('')",  # Empty strings are not prose.
            "print('Café is ready.')",  # Non-ASCII prose remains gradable.
            "print('device_id')",  # Identifier-only text is not prose.
        ]
    )  # Build a small source file with all requested edge cases.
    spans, _ = PythonSourceParser(config).parse(source)  # Parse with both opt-in surfaces enabled.
    texts = [span.text for span in spans if span.kind in {"logging", "user-facing"}]  # Keep string spans.
    assert texts == ["Fetched devices", "Use the site name.", "Café is ready."]  # Only prose remains.


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
