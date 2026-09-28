"""Check Mermaid diagram references against Python symbols.

Diagrams age when code moves. A stale class name in a diagram makes the
documentation unsafe to use during a repair. This tool checks Mermaid
identifiers against the Python symbols of the caller repository. Product names
belong to the caller, so the built-in allowlist stays generic.

Exit codes: 0 = all valid, 1 = stale references found, 2 = script error.
"""

from __future__ import annotations

import argparse
import logging
import re
import symtable
import sys
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class StaleReference(TypedDict):
    """Store one diagram reference that has no matching Python symbol."""

    file: str
    line: int
    name: str
    closest: str | None


BUILT_IN_ALLOWLIST = frozenset(
    [
        "API",
        "SSH",
        "CSV",
        "SQLite",
        "GHCR",
        "CI",
        "CD",
        "UUID",
        "PK",
        "ER",
        "EOF",
        "GitHub",
        "Podman",
        "Docker",
        "Flask",
        "Gunicorn",
        "Mermaid",
        "Ruff",
        "Bandit",
        "CodeQL",
        "Playwright",
        "WebSocket",
        "ForceCommand",
        "NOC",
        "TCP",
        "UDP",
        "VLAN",
        "BSSID",
        "MAC",
        "JSON",
        # Mermaid diagram participant/label terms (not Python symbols)
        "User",
        "Menu",
        "Fetch",
        "Rate",
        "Process",
        "Export",
        "Select",
        "Write",
        "Upsert",
        "Accumulate",
        "Route",
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "Utilities",
        "Managers",
    ]
)

DEFAULT_DOCS_DIR = "documentation/diagrams/"
DEFAULT_SOURCE_FILES = ["src/"]

CLASS_SUFFIX_PATTERN = re.compile(
    r"[A-Z][a-zA-Z]+(?:Utils|Manager|Exporter|Config|Runner|Writer"
    r"|Fetcher|Processor|Checker|Monitor|Emitter|Registry|TUI)"
)
MERMAID_BLOCK_PATTERN = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
CLASS_DECLARATION_PATTERN = re.compile(r"class\s+(\w+)")
CLASS_METHOD_PATTERN = re.compile(r"(\w+)\s*:\s*(\w+)\(\)")
CLASS_INHERITANCE_PATTERN = re.compile(r"(\w+)\s*<\|--\s*(\w+)")
SEQUENCE_PARTICIPANT_PATTERN = re.compile(r"participant\s+(\w+)(?:\s+as\s+(.+))?")
SEQUENCE_ARROW_PATTERN = re.compile(r"(\w+)->>(\w+):\s*(\w+)")


class DiagramReferenceValidator:
    """Validates Mermaid diagram references against Python codebase symbols."""

    def __init__(self, allowlist: frozenset[str] | None = None) -> None:
        """Create the validator state for one lint run.

        Why:
            Each run needs separate caches to keep file reads repeatable.
        """
        self.allowlist = allowlist or BUILT_IN_ALLOWLIST
        self.python_symbols: set[str] = set()
        self.stale_references: list[StaleReference] = []
        self.total_checked = 0
        self.files_scanned = 0
        self.markdown_cache: dict[Path, str] = {}
        self.diagram_blocks: dict[Path, list[str]] = {}
        self.source_cache: dict[Path, str] = {}

    def extract_mermaid_blocks(self, content: str) -> list[str]:
        """Extract Mermaid code blocks from markdown content."""
        return MERMAID_BLOCK_PATTERN.findall(content)

    def extract_identifiers(self, block: str) -> list[str]:
        """Extract class/method identifiers from a Mermaid code block."""
        identifiers: list[str] = []
        identifiers.extend(self._extract_class_diagram_ids(block))
        identifiers.extend(self._extract_sequence_ids(block))
        identifiers.extend(self._extract_suffix_matches(block))
        return list(set(identifiers))

    def _extract_class_diagram_ids(self, block: str) -> list[str]:
        """Extract identifiers from classDiagram syntax."""
        results: list[str] = []
        for match in CLASS_DECLARATION_PATTERN.finditer(block):
            name = match.group(1)
            if name[0].isupper():
                results.append(name)
        for match in CLASS_METHOD_PATTERN.finditer(block):
            results.append(match.group(1))
            results.append(match.group(2))
        for match in CLASS_INHERITANCE_PATTERN.finditer(block):
            results.append(match.group(1))
            results.append(match.group(2))
        return results

    def _extract_sequence_ids(self, block: str) -> list[str]:
        """Extract identifiers from sequenceDiagram syntax."""
        results: list[str] = []
        for match in SEQUENCE_PARTICIPANT_PATTERN.finditer(block):
            name = match.group(1)
            if name[0].isupper():
                results.append(name)
        for match in SEQUENCE_ARROW_PATTERN.finditer(block):
            for group_idx in range(1, 4):
                name = match.group(group_idx)
                if name and name[0].isupper():
                    results.append(name)
        return results

    def _extract_suffix_matches(self, block: str) -> list[str]:
        """Extract PascalCase names matching known class suffixes."""
        return CLASS_SUFFIX_PATTERN.findall(block)

    def extract_python_symbols(self, source_path: Path) -> set[str]:
        """Extract class and function names from Python source."""
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read %s: %s", source_path, exc)
            return set()
        return self.extract_python_symbols_from_text(source_path, source_text)

    def extract_python_symbols_from_text(self, source_path: Path, source_text: str) -> set[str]:
        """Extract Python symbols from text that the caller already read.

        Why:
            The lint run reuses source text and does not read a file twice.
        """
        try:
            source_table = symtable.symtable(source_text, str(source_path), "exec")
        except SyntaxError as exc:
            logger.error("Failed to parse %s: %s", source_path, exc)
            return set()
        return self._extract_symbol_table_names(source_table, source_text)

    def _extract_symbol_table_names(self, source_table: symtable.SymbolTable, source_text: str) -> set[str]:
        """Extract class and synchronous function names from a symbol table.

        Why:
            The symbol table parser uses less memory than a full AST walk.
        """
        symbols: set[str] = set()
        source_lines = source_text.splitlines()
        pending_tables = list(source_table.get_children())
        while pending_tables:
            child_table = pending_tables.pop()
            pending_tables.extend(child_table.get_children())
            if child_table.get_type() == "class":
                symbols.add(child_table.get_name())
            elif self._is_sync_function_table(child_table, source_lines):
                symbols.add(child_table.get_name())
        return symbols

    def _is_sync_function_table(self, child_table: symtable.SymbolTable, source_lines: list[str]) -> bool:
        """Return true when a symbol table names a synchronous function.

        Why:
            The old AST path skipped `async def` names.
        """
        if child_table.get_type() != "function":
            return False
        line_index = child_table.get_lineno() - 1
        if line_index < 0 or line_index >= len(source_lines):
            return False
        line = source_lines[line_index].lstrip()
        return line.startswith("def ")

    def find_closest_match(self, name: str) -> str | None:
        """Find closest Python symbol by edit distance."""
        if not self.python_symbols:
            return None
        best_match = min(
            self.python_symbols,
            key=lambda s: self._edit_distance(name.lower(), s.lower()),
        )
        distance = self._edit_distance(name.lower(), best_match.lower())
        if distance <= len(name) // 2:
            return f"{best_match} (edit distance: {distance})"
        return None

    def _edit_distance(self, first: str, second: str) -> int:
        """Levenshtein edit distance between two strings."""
        if len(first) < len(second):
            return self._edit_distance(second, first)
        if not second:
            return len(first)
        prev_row = list(range(len(second) + 1))
        for i, char_a in enumerate(first):
            curr_row = [i + 1]
            for j, char_b in enumerate(second):
                cost = 0 if char_a == char_b else 1
                curr_row.append(
                    min(
                        curr_row[j] + 1,
                        prev_row[j + 1] + 1,
                        prev_row[j] + cost,
                    )
                )
            prev_row = curr_row
        return prev_row[-1]

    def validate_file(self, filepath: Path, verbose: bool = False) -> int:
        """Validate all Mermaid references in a single markdown file."""
        try:
            content = self.markdown_cache.get(filepath)
            if content is None:
                content = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Cannot read %s: %s", filepath, exc)
            return 0

        blocks = self.diagram_blocks.get(filepath)
        if blocks is None:
            blocks = self.extract_mermaid_blocks(content)
        if not blocks:
            return 0

        self.files_scanned += 1
        lines = content.split("\n")
        file_stale = 0

        for block in blocks:
            identifiers = self.extract_identifiers(block)
            for name in identifiers:
                if name in self.allowlist:
                    continue
                self.total_checked += 1
                if name in self.python_symbols:
                    if verbose:
                        logger.info("  OK: %s:%s", filepath, name)
                    continue
                line_num = self._find_line_number(lines, name)
                closest = self.find_closest_match(name)
                self.stale_references.append(
                    {
                        "file": str(filepath),
                        "line": line_num,
                        "name": name,
                        "closest": closest,
                    }
                )
                file_stale += 1
        return file_stale

    def _find_line_number(self, lines: list[str], name: str) -> int:
        """Find the 1-based line number where name first appears."""
        for idx, line in enumerate(lines, start=1):
            if name in line:
                return idx
        return 0

    def run(self, config: argparse.Namespace) -> int:
        """Execute full validation pipeline. Returns exit code."""
        source_files = self._collect_source_files(config.source_files)
        if source_files is None:
            return 2

        markdown_files = self._collect_markdown_files(config)
        if not markdown_files:
            logger.error("No markdown files found")
            return 2

        needed_symbols = self._collect_needed_symbols(markdown_files)
        self._extract_needed_python_symbols(source_files, needed_symbols)
        if needed_symbols and not self.python_symbols:
            self._complete_python_symbols(source_files)
        if needed_symbols and not self.python_symbols:
            logger.error("No Python symbols extracted")
            return 2

        for md_file in markdown_files:
            self.validate_file(md_file, verbose=config.verbose)

        if self.stale_references:
            self._complete_python_symbols(source_files)

        return self._report_results()

    def _collect_source_files(self, source_files: list[str]) -> list[Path] | None:
        """Collect the Python files that can define diagram references.

        Why:
            The lint run must walk each source root once.
        """
        files: list[Path] = []
        for source_path in source_files:
            path = Path(source_path)
            if path.is_dir():
                files.extend(path.rglob("*.py"))
            elif path.exists():
                files.append(path)
            else:
                logger.error("Source file not found: %s", path)
                return None
        return files

    def _collect_needed_symbols(self, markdown_files: list[Path]) -> set[str]:
        """Read diagram files and return the references that need source symbols.

        Why:
            The lint run can stop reading Python files after it resolves each reference.
        """
        needed_symbols: set[str] = set()
        for markdown_file in markdown_files:
            content = markdown_file.read_text(encoding="utf-8")
            blocks = self.extract_mermaid_blocks(content)
            self.markdown_cache[markdown_file] = content
            self.diagram_blocks[markdown_file] = blocks
            for block in blocks:
                needed_symbols.update(name for name in self.extract_identifiers(block) if name not in self.allowlist)
        return needed_symbols

    def _extract_needed_python_symbols(self, source_files: list[Path], needed_symbols: set[str]) -> None:
        """Extract only symbols that can resolve the current diagram set.

        Why:
            Most Python files cannot contain a requested diagram reference.
        """
        pending_symbols = set(needed_symbols)
        for source_file in source_files:
            if not pending_symbols:
                break
            source_text = source_file.read_text(encoding="utf-8")
            self.source_cache[source_file] = source_text
            if not self._text_has_symbol(source_text, pending_symbols):
                continue
            symbols = self.extract_python_symbols_from_text(source_file, source_text)
            self.python_symbols.update(symbols)
            pending_symbols.difference_update(symbols)

    def _complete_python_symbols(self, source_files: list[Path]) -> None:
        """Parse each source file when a stale reference needs a closest match.

        Why:
            The failure report must use the same source symbol set as the old run.
        """
        for source_file in source_files:
            source_text = self.source_cache.get(source_file)
            if source_text is None:
                source_text = source_file.read_text(encoding="utf-8")
                self.source_cache[source_file] = source_text
            symbols = self.extract_python_symbols_from_text(source_file, source_text)
            self.python_symbols.update(symbols)
        for reference in self.stale_references:
            reference["closest"] = self.find_closest_match(reference["name"])

    def _text_has_symbol(self, source_text: str, symbols: set[str]) -> bool:
        """Return true when source text can define a needed symbol.

        Why:
            The AST parser is slower than a text membership check.
        """
        return any(symbol in source_text for symbol in symbols)

    def _collect_markdown_files(self, config: argparse.Namespace) -> list[Path]:
        """Collect all markdown files to scan."""
        files: list[Path] = []
        docs_dir = Path(config.docs_dir)
        if docs_dir.exists():
            files.extend(docs_dir.rglob("*.md"))

        for extra in config.extra_files:
            path = Path(extra)
            if path.exists():
                files.append(path)
        return sorted(set(files))

    def _report_results(self) -> int:
        """Print results and return exit code."""
        if self.stale_references:
            for ref in self.stale_references:
                msg = f'STALE: {ref["file"]}:{ref["line"]} "{ref["name"]}" not found in codebase'
                logger.warning(msg)
                if ref["closest"]:
                    logger.warning("  Closest match: %s", ref["closest"])
            logger.warning(
                "\nFAILED: %d stale references found across %d diagram files",
                len(self.stale_references),
                self.files_scanned,
            )
            return 1

        logger.info(
            "OK: %d references validated across %d diagram files",
            self.total_checked,
            self.files_scanned,
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="diagram-refs",
        description="Validate Mermaid diagram references against Python symbols.",
    )
    parser.add_argument(
        "--docs-dir",
        default=DEFAULT_DOCS_DIR,
        help="Directory containing diagram markdown files",
    )
    parser.add_argument(
        "--extra-files",
        nargs="*",
        default=["README.md"],
        help="Additional markdown files with inline diagrams",
    )
    parser.add_argument(
        "--source-files",
        nargs="*",
        default=DEFAULT_SOURCE_FILES,
        help="Python source files or directories to extract symbols from",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Identifier to skip. Repeat this option for more product terms.",
    )
    parser.add_argument(
        "--allowlist-file",
        "--allowlist",
        dest="allowlist_file",
        default=None,
        help="File of identifiers to skip (one per line)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all checked references",
    )
    return parser


def _extra_allowlist(path: Path | None) -> frozenset[str]:
    """Return caller-owned allowlist words from a file."""
    if path is None:  # No file means no extra words.
        return frozenset()
    logger.info("Reading the diagram allowlist file %s", path)  # Log before reading caller data.
    if not path.exists():  # A named file must exist, or the lint result is incomplete.
        raise FileNotFoundError(f"Allowlist file not found: {path}")
    words = frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )  # Skip comments and blanks, as the old file option did.
    logger.debug("Read %d allowlist words from %s", len(words), path)  # Log after reading caller data.
    return words


def main(argv: list[str] | None = None) -> int:
    """Entry point for the lint script."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Keep the old concise CI log format.
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        extra = _extra_allowlist(Path(args.allowlist_file) if args.allowlist_file else None)
    except OSError as error:
        logger.error("%s", error)
        return 2
    allow_words = frozenset(str(word) for word in args.allow)
    logger.info("Starting the diagram reference check")  # Log before the scan.
    validator = DiagramReferenceValidator(BUILT_IN_ALLOWLIST | extra | allow_words)
    result = validator.run(args)
    logger.info("Finished the diagram reference check with status %d", result)  # Log after the scan.
    return result


if __name__ == "__main__":
    sys.exit(main())
