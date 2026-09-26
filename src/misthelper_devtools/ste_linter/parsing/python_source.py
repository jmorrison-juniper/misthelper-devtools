"""Python prose extraction.

Reads Python source and returns prose from docstrings, comments, and configured
string literal calls. The parser uses ``ast`` for docstrings and configured
calls. It uses ``tokenize`` for comments. When the source does not parse, the
parser falls back to a comment scan and returns a note that explains the
fallback.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import ast  # Extracts docstrings from the parse tree.
import io  # Turns the source text into a line reader for tokenize.
import logging  # Records each parser action for traceability.
import re  # Tests comments for directive and separator patterns.
import tokenize  # Extracts comment tokens with their line numbers.

from ..config import LinterConfig  # The parser switches for optional string spans.
from ..models import ProseSpan  # The output type for each prose block.

# Matches a comment that is a tool directive, a shebang, or an encoding marker.
# The parser skips these because they are not prose.
_DIRECTIVE = re.compile(r"^(!|-\*-|type:|noqa|nosec|pragma|pylint:|mypy:|ruff:|fmt:|isort:|rtk\b)")

# Matches a comment that is only separator characters, for example "-----".
_SEPARATOR = re.compile(r"^[\-=~*#_ ]+$")

# Matches old-style logging placeholders, which are templates and not prose.
_PERCENT_PLACEHOLDER = re.compile(r"%(\([^)]+\))?[#0\- +]*(\d+|\*)?(\.\d+)?[hlL]?[diouxXeEfFgGcrsa%]")

# Matches brace placeholders from format strings and f-string expressions.
_BRACE_PLACEHOLDER = re.compile(r"\{[^{}]*\}")

# Matches a quoted token that looks like code, an option name, or an identifier.
_QUOTED_CODE_TOKEN = re.compile(r"([`'\"])[A-Za-z_][A-Za-z0-9_.:-]*\1")

# Matches a bare identifier, which STE must not ask a writer to alter.
_IDENTIFIER_ONLY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The logger for Python source parsing.
_LOG = logging.getLogger("ste_linter.parsing.python_source")


class PythonSourceParser:
    """Extracts docstring and comment prose from Python source."""

    def __init__(self, config: LinterConfig | None = None) -> None:
        """Store the active parser configuration."""
        self._config = config or LinterConfig()  # Use defaults when a test builds the parser directly.

    def parse(self, text: str) -> tuple[list[ProseSpan], str]:
        """Return the prose spans and a note.

        The note is empty on a clean parse. On a syntax error the note explains
        that only comments were graded.
        """
        _LOG.info("Parsing Python prose spans")  # Mark the start of the parse stage.
        note = ""  # Holds a fallback note, empty by default.
        spans: list[ProseSpan] = []  # Holds the finished prose spans.
        try:  # Try the full parse for docstrings.
            tree = ast.parse(text)  # Parse once so docstrings and call strings share the same tree.
            spans.extend(self._docstrings(tree))  # Add the docstring spans.
            spans.extend(PythonStringSpanExtractor(self._config).extract(tree))  # Add optional string spans.
        except SyntaxError:  # The source does not parse.
            note = "Source did not parse. Graded comments only."  # Record the fallback.
        spans.extend(self._comments(text))  # Add the comment spans from tokenize.
        _LOG.debug("Parsed %d Python prose spans", len(spans))  # Record the final span count.
        return spans, note  # Return the spans and the note.

    def _docstrings(self, tree: ast.AST) -> list[ProseSpan]:
        """Return one span per module, class, or function docstring."""
        _LOG.info("Extracting Python docstrings")  # Mark the docstring pass.
        spans: list[ProseSpan] = []  # Holds the docstring spans.
        holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)  # Nodes that hold docstrings.
        for node in ast.walk(tree):  # Walk every node in the tree.
            if isinstance(node, holders):  # Only these node kinds carry a docstring.
                doc = ast.get_docstring(node, clean=True)  # Read the cleaned docstring text.
                if doc and node.body:  # Only when a docstring is present.
                    line = getattr(node.body[0], "lineno", 1)  # The line of the docstring statement.
                    spans.append(ProseSpan(text=doc, start_line=line, kind="docstring"))  # Save the span.
        _LOG.debug("Extracted %d Python docstring spans", len(spans))  # Record the docstring count.
        return spans  # Return the docstring spans.

    def _comments(self, text: str) -> list[ProseSpan]:
        """Return one span per prose comment, skipping directives and separators."""
        _LOG.info("Extracting Python comments")  # Mark the comment pass.
        spans: list[ProseSpan] = []  # Holds the comment spans.
        try:  # Tokenize can raise on malformed source.
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))  # Read all tokens.
        except (tokenize.TokenError, IndentationError):  # The source is too broken to tokenize.
            return spans  # Return whatever was collected, which is nothing here.
        for token in tokens:  # Walk each token.
            if token.type != tokenize.COMMENT:  # Only comment tokens hold prose.
                continue  # Skip non-comment tokens.
            body = token.string.lstrip("#").strip()  # Drop the hash marks and outer spaces.
            if not body or _DIRECTIVE.match(body) or _SEPARATOR.match(body):  # Skip non-prose comments.
                continue  # Ignore directives, separators, and empty comments.
            if not any(character.isalpha() for character in body):  # Skip comments with no letters.
                continue  # Ignore comments that are only symbols or numbers.
            spans.append(ProseSpan(text=body, start_line=token.start[0], kind="comment"))  # Save the span.
        _LOG.debug("Extracted %d Python comment spans", len(spans))  # Record the comment count.
        return spans  # Return the comment spans.


class PythonStringSpanExtractor:
    """Extract configured string literal calls from Python source."""

    def __init__(self, config: LinterConfig) -> None:
        """Store the active string extraction configuration."""
        self._config = config  # Keep the switches and call lists together for matching.
        self._logging_names = self._normalize(config.logging_call_names)  # Match logging calls without case drift.
        self._user_names = self._normalize(config.user_facing_call_names)  # Match user calls without case drift.

    def extract(self, tree: ast.AST) -> list[ProseSpan]:
        """Return optional string literal spans from configured calls."""
        spans: list[ProseSpan] = []  # Hold each extracted span before returning.
        if self._config.grade_logging_strings:  # The operator opted in to logging messages.
            _LOG.info("Extracting logging string spans")  # Mark the logging string pass.
            spans.extend(self._logging_spans(tree))  # Add the configured logging spans.
            _LOG.debug("Logging string spans now total %d", len(spans))  # Record the running count.
        if self._config.grade_user_facing_strings:  # The operator opted in to user-facing messages.
            _LOG.info("Extracting user-facing string spans")  # Mark the user-facing string pass.
            spans.extend(self._user_facing_spans(tree))  # Add the configured user-facing spans.
            _LOG.debug("Python string spans now total %d", len(spans))  # Record the final string count.
        return spans  # Return the optional string spans.

    def _logging_spans(self, tree: ast.AST) -> list[ProseSpan]:
        """Return spans from configured logging calls."""
        spans: list[ProseSpan] = []  # Hold the logging spans in source order.
        for node in ast.walk(tree):  # Walk all nodes because logging calls can appear anywhere.
            if not isinstance(node, ast.Call):  # Only call nodes can match configured functions.
                continue  # Skip non-call nodes without extra work.
            if not self._matches(node.func, self._logging_names):  # Only configured log calls carry log text.
                continue  # Skip calls that are not in the logging surface.
            span = self._span_from_node(node.args[0], "logging") if node.args else None  # Read the template.
            if span is not None:  # A usable string literal created prose.
                spans.append(span)  # Keep the logging prose span.
        return spans  # Return all logging spans.

    def _user_facing_spans(self, tree: ast.AST) -> list[ProseSpan]:
        """Return spans from configured prompt and print calls."""
        spans: list[ProseSpan] = []  # Hold the user-facing spans in source order.
        for node in ast.walk(tree):  # Walk all nodes because prompts can sit in any function.
            if not isinstance(node, ast.Call):  # Only call nodes can match configured functions.
                continue  # Skip non-call nodes without extra work.
            if not self._matches(node.func, self._user_names):  # Only configured calls carry user text.
                continue  # Skip calls that are not in the user-facing surface.
            spans.extend(self._user_spans_from_call(node))  # Add one or more prompt or print spans.
        return spans  # Return all user-facing spans.

    def _user_spans_from_call(self, node: ast.Call) -> list[ProseSpan]:
        """Return the user-facing spans from one configured call."""
        name = self._call_name(node.func).split(".")[-1]  # Use the short name for call-specific rules.
        if name == "print":  # Print can show more than one literal to the user.
            return [span for arg in node.args if (span := self._span_from_node(arg, "user-facing"))]  # Keep prose.
        prompt = node.args[0] if node.args else self._prompt_keyword(node)  # Safe input carries one prompt.
        span = self._span_from_node(prompt, "user-facing") if prompt is not None else None  # Extract prompt prose.
        return [span] if span is not None else []  # Return a list so callers can extend uniformly.

    def _prompt_keyword(self, node: ast.Call) -> ast.expr | None:
        """Return the prompt keyword value from a call, if it exists."""
        for keyword in node.keywords:  # Walk keyword arguments because prompt can be named.
            if keyword.arg == "prompt":  # The prompt keyword carries user-facing text.
                return keyword.value  # Return the expression for normal string handling.
        return None  # The call had no named prompt to grade.

    def _span_from_node(self, node: ast.expr, kind: str) -> ProseSpan | None:
        """Return a prose span from one string expression."""
        text = self._literal_text(node)  # Read only literal text and f-string constant parts.
        if text is None:  # Nonliteral values must not be graded.
            return None  # Skip computed text because it can hold identifiers or data.
        cleaned = self._clean_text(text)  # Remove placeholders and code tokens before grading.
        if not self._is_prose(cleaned):  # Empty or identifier-only text is not gradable prose.
            return None  # Skip text that the STE rules must not change.
        return ProseSpan(text=cleaned, start_line=getattr(node, "lineno", 1), kind=kind)  # Preserve source line.

    def _literal_text(self, node: ast.expr) -> str | None:
        """Return literal text from a string expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Plain and implicit strings land here.
            return node.value  # Return the literal value, including implicit concatenation.
        if isinstance(node, ast.JoinedStr):  # F-strings carry constants and expressions as separate nodes.
            return "".join(self._fstring_part(value) for value in node.values)  # Drop expressions safely.
        return None  # Other expressions are computed, so the parser must skip them.

    def _fstring_part(self, node: ast.expr) -> str:
        """Return the gradable part of one f-string node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):  # Constant f-string text is prose.
            return node.value  # Keep only text that the source wrote directly.
        return " "  # Replace expressions with a separator so words do not join.

    def _clean_text(self, text: str) -> str:
        """Return string text with templates and code tokens removed."""
        without_percent = _PERCENT_PLACEHOLDER.sub(" ", text)  # Remove lazy logging placeholders.
        without_braces = _BRACE_PLACEHOLDER.sub(" ", without_percent)  # Remove brace placeholders.
        without_code = _QUOTED_CODE_TOKEN.sub(" ", without_braces)  # Remove quoted code tokens.
        return " ".join(without_code.split())  # Collapse gaps so the segmenter sees normal spaces.

    def _is_prose(self, text: str) -> bool:
        """Return true when cleaned text is prose that the linter can grade."""
        if not text or not any(character.isalpha() for character in text):  # No letters means no prose exists.
            return False  # Skip empty strings and symbol-only templates.
        if "_" in text and _IDENTIFIER_ONLY.fullmatch(text):  # A snake-case identifier must not be graded.
            return False  # Skip identifier-only strings as required by the STE rule.
        return True  # The remaining text has gradable prose.

    def _matches(self, node: ast.expr, names: set[str]) -> bool:
        """Return true when a call expression matches the configured names."""
        name = self._call_name(node).lower()  # Normalize the call expression once.
        short_name = name.split(".")[-1]  # Let configs match a short helper name.
        return name in names or short_name in names  # Accept exact dotted names and short names.

    def _call_name(self, node: ast.expr) -> str:
        """Return the dotted name for a call expression."""
        if isinstance(node, ast.Name):  # Simple functions such as print land here.
            return node.id  # Return the name so it can match the config.
        if isinstance(node, ast.Attribute):  # Dotted calls such as logging.info land here.
            parent = self._call_name(node.value)  # Read the left side of the dotted name.
            return f"{parent}.{node.attr}" if parent else node.attr  # Build the dotted call name.
        return ""  # Complex expressions are not default targets.

    def _normalize(self, names: tuple[str, ...]) -> set[str]:
        """Return lower-case call names for quick matching."""
        return {name.lower() for name in names}  # Use a set so each call match is cheap.
