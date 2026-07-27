"""Python prose extraction.

Reads Python source and returns the prose from docstrings and comments. The parser
uses ``ast`` for docstrings and ``tokenize`` for comments. Code is never graded.
When the source does not parse, the parser falls back to a comment scan and
returns a note that explains the fallback.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import ast  # Extracts docstrings from the parse tree.
import io  # Turns the source text into a line reader for tokenize.
import re  # Tests comments for directive and separator patterns.
import tokenize  # Extracts comment tokens with their line numbers.

from ..models import ProseSpan  # The output type for each prose block.

# Matches a comment that is a tool directive, a shebang, or an encoding marker.
# The parser skips these because they are not prose.
_DIRECTIVE = re.compile(r"^(!|-\*-|type:|noqa|nosec|pragma|pylint:|mypy:|ruff:|fmt:|isort:|rtk\b)")

# Matches a comment that is only separator characters, for example "-----".
_SEPARATOR = re.compile(r"^[\-=~*#_ ]+$")


class PythonSourceParser:
    """Extracts docstring and comment prose from Python source."""

    def parse(self, text: str) -> tuple[list[ProseSpan], str]:
        """Return the prose spans and a note.

        The note is empty on a clean parse. On a syntax error the note explains
        that only comments were graded.
        """
        note = ""  # Holds a fallback note, empty by default.
        spans: list[ProseSpan] = []  # Holds the finished prose spans.
        try:  # Try the full parse for docstrings.
            spans.extend(self._docstrings(text))  # Add the docstring spans.
        except SyntaxError:  # The source does not parse.
            note = "Source did not parse. Graded comments only."  # Record the fallback.
        spans.extend(self._comments(text))  # Add the comment spans from tokenize.
        return spans, note  # Return the spans and the note.

    def _docstrings(self, text: str) -> list[ProseSpan]:
        """Return one span per module, class, or function docstring."""
        spans: list[ProseSpan] = []  # Holds the docstring spans.
        tree = ast.parse(text)  # Parse the source into a tree, may raise SyntaxError.
        holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)  # Nodes that hold docstrings.
        for node in ast.walk(tree):  # Walk every node in the tree.
            if isinstance(node, holders):  # Only these node kinds carry a docstring.
                doc = ast.get_docstring(node, clean=True)  # Read the cleaned docstring text.
                if doc and node.body:  # Only when a docstring is present.
                    line = getattr(node.body[0], "lineno", 1)  # The line of the docstring statement.
                    spans.append(ProseSpan(text=doc, start_line=line, kind="docstring"))  # Save the span.
        return spans  # Return the docstring spans.

    def _comments(self, text: str) -> list[ProseSpan]:
        """Return one span per prose comment, skipping directives and separators."""
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
        return spans  # Return the comment spans.
