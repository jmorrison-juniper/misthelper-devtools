"""Parsing package for the STE linter.

Turns a source file into a ``Document`` with prose spans, sentences, and
paragraphs. The document builder picks the parser by file type, segments the
prose, counts words by the STE rules, and maps each sentence back to its source
line. It also labels each sentence as procedural or descriptive.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import logging  # Records the parse stage for observability.
import os  # Reads the file extension to pick a parser.

from ..models import Document, ProseSpan, Sentence  # The document model types.
from .markdown import MarkdownParser  # Extracts prose from Markdown.
from .python_source import PythonSourceParser  # Extracts prose from Python.
from .segmentation import Segmenter  # Splits prose into paragraphs and sentences.
from .wordcount import WordCounter  # Counts words by the STE rules.

# The logger for the parsing stage. The CLI configures the handlers.
_LOG = logging.getLogger("ste_linter.parsing")

# Common base-form verbs that start an instruction. A sentence that starts with
# one of these is treated as procedural, so it uses the 20-word limit.
_IMPERATIVE_VERBS = frozenset(
    {
        "set",
        "remove",
        "install",
        "run",
        "open",
        "close",
        "click",
        "press",
        "type",  # Interface actions.
        "do",
        "make",
        "check",
        "connect",
        "disconnect",
        "apply",
        "use",
        "add",
        "delete",  # General actions.
        "select",
        "enter",
        "turn",
        "examine",
        "obey",
        "put",
        "hold",
        "cut",
        "continue",  # More actions.
        "replace",
        "tighten",
        "loosen",
        "disable",
        "enable",
        "start",
        "stop",
        "read",  # More actions.
        "write",
        "call",
        "send",
        "confirm",
        "verify",
        "update",
        "reboot",
        "copy",
        "move",  # More actions.
        "create",
        "build",
        "download",
        "upload",
        "format",
        "debug",
        "attach",
        "detach",  # More actions.
    }
)


class DocumentBuilder:
    """Builds a ``Document`` from a file path and its text."""

    def __init__(self) -> None:
        """Create the builder with its parser and helper instances."""
        self._markdown = MarkdownParser()  # The Markdown parser.
        self._python = PythonSourceParser()  # The Python parser.
        self._segmenter = Segmenter()  # The paragraph and sentence splitter.
        self._counter = WordCounter()  # The STE word counter.

    def build(self, path: str, text: str) -> Document:
        """Return a graded-ready ``Document`` for ``path`` and its ``text``."""
        _LOG.info("Parsing prose from %s", path)  # Log before the parse stage.
        spans, note = self._extract_spans(path, text)  # Get prose spans and any note.
        document = Document(path=path, spans=spans, parse_note=note)  # Start the document.
        for span in spans:  # Walk each prose span.
            self._add_span_sentences(document, span)  # Segment the span into sentences.
        document.word_count = sum(sentence.word_count for sentence in document.sentences)  # Total words.
        _LOG.debug(
            "Parsed %d spans, %d sentences, %d words from %s",
            len(spans),
            len(document.sentences),
            document.word_count,
            path,
        )  # Log the parse result after the stage.
        return document  # Return the finished document.

    def _extract_spans(self, path: str, text: str) -> tuple[list[ProseSpan], str]:
        """Return the prose spans and a note based on the file type."""
        extension = os.path.splitext(path)[1].lower()  # The lower-case file extension.
        if extension == ".py":  # Python source uses the Python parser.
            return self._python.parse(text)  # Return the spans and note from the Python parser.
        return self._markdown.parse(text), ""  # Markdown and other text use the Markdown parser.

    def _add_span_sentences(self, document: Document, span: ProseSpan) -> None:
        """Segment one span and add its sentences and paragraphs to the document."""
        for paragraph_text, paragraph_offset in self._segmenter.split_paragraphs(span.text):  # Each paragraph.
            paragraph: list[Sentence] = []  # Holds the sentences of this paragraph.
            pairs = self._segmenter.split_sentences(paragraph_text, paragraph_offset)  # Split into sentences.
            for sentence_text, offset in pairs:  # Each sentence with its offset in the span text.
                sentence = self._make_sentence(span, sentence_text, offset)  # Build the sentence model.
                document.sentences.append(sentence)  # Add to the flat sentence list.
                paragraph.append(sentence)  # Add to the current paragraph.
            if paragraph:  # Only keep a paragraph that holds sentences.
                document.paragraphs.append(paragraph)  # Add the paragraph group.

    def _make_sentence(self, span: ProseSpan, sentence_text: str, offset: int) -> Sentence:
        """Build a ``Sentence`` with its source line, word count, and mode."""
        line = span.start_line + span.text.count("\n", 0, offset)  # Map the offset to a source line.
        words = self._counter.count(sentence_text)  # Count words by the STE rules.
        mode = self._mode(sentence_text)  # Decide procedural or descriptive.
        return Sentence(text=sentence_text, line=line, word_count=words, mode=mode)  # Return the model.

    def _mode(self, sentence_text: str) -> str:
        """Return "procedural" when the sentence starts with an instruction verb."""
        first = sentence_text.strip().split(" ", 1)[0].lower().strip(".,:;")  # The first word, cleaned.
        if first in _IMPERATIVE_VERBS:  # A leading instruction verb marks a step.
            return "procedural"  # Use the 20-word limit for this sentence.
        return "descriptive"  # Otherwise use the 25-word limit.
