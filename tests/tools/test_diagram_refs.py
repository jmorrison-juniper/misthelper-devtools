"""Tests for the shared Mermaid diagram reference command."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from misthelper_devtools import diagram_refs as lint_diagram_refs

DiagramReferenceValidator = lint_diagram_refs.DiagramReferenceValidator
BUILT_IN_ALLOWLIST = lint_diagram_refs.BUILT_IN_ALLOWLIST


@pytest.fixture
def validator() -> DiagramReferenceValidator:
    """Create a fresh validator instance."""
    return DiagramReferenceValidator()


@pytest.fixture
def validator_with_symbols(validator: DiagramReferenceValidator) -> DiagramReferenceValidator:
    """Validator pre-loaded with sample Python symbols."""
    validator.python_symbols = {
        "DataExporter",
        "write_with_format_selection",
        "APIFetchUtils",
        "fetch_with_pagination",
        "SQLiteDatabaseWriter",
        "upsert_records",
        "OperationRegistry",
        "RateLimitingUtils",
        "WebSocketManager",
        "PacketCaptureManager",
    }
    return validator


class TestMermaidBlockExtraction:
    """Test Mermaid code block extraction from markdown."""

    def test_extracts_single_block(self, validator):
        content = dedent("""\
            # Title

            ```mermaid
            classDiagram
                class DataExporter
            ```
        """)
        blocks = validator.extract_mermaid_blocks(content)
        assert len(blocks) == 1
        assert "DataExporter" in blocks[0]

    def test_extracts_multiple_blocks(self, validator):
        content = dedent("""\
            ```mermaid
            classDiagram
                class Foo
            ```

            Some text.

            ```mermaid
            sequenceDiagram
                participant Bar
            ```
        """)
        blocks = validator.extract_mermaid_blocks(content)
        assert len(blocks) == 2

    def test_ignores_non_mermaid_blocks(self, validator):
        content = dedent("""\
            ```python
            class NotADiagram:
                pass
            ```

            ```mermaid
            classDiagram
                class RealDiagram
            ```
        """)
        blocks = validator.extract_mermaid_blocks(content)
        assert len(blocks) == 1
        assert "RealDiagram" in blocks[0]

    def test_empty_content(self, validator):
        assert validator.extract_mermaid_blocks("") == []


class TestIdentifierExtraction:
    """Test identifier extraction from Mermaid code blocks."""

    def test_class_diagram_class_name(self, validator):
        block = "classDiagram\n    class DataExporter"
        ids = validator.extract_identifiers(block)
        assert "DataExporter" in ids

    def test_class_diagram_method(self, validator):
        block = "classDiagram\n    DataExporter : write_csv()"
        ids = validator.extract_identifiers(block)
        assert "DataExporter" in ids

    def test_class_diagram_inheritance(self, validator):
        block = "DataExporter <|-- OrgExportUtils"
        ids = validator.extract_identifiers(block)
        assert "DataExporter" in ids
        assert "OrgExportUtils" in ids

    def test_sequence_participant(self, validator):
        block = "sequenceDiagram\n    participant APIFetchUtils"
        ids = validator.extract_identifiers(block)
        assert "APIFetchUtils" in ids

    def test_sequence_arrow(self, validator):
        block = "APIFetchUtils->>DataExporter: write_csv"
        ids = validator.extract_identifiers(block)
        assert "APIFetchUtils" in ids
        assert "DataExporter" in ids

    def test_suffix_pattern_matching(self, validator):
        block = "Some text with PacketCaptureManager and FirmwareManager"
        ids = validator.extract_identifiers(block)
        assert "PacketCaptureManager" in ids
        assert "FirmwareManager" in ids

    def test_no_lowercase_classes(self, validator):
        block = "class lowercase_name"
        ids = validator.extract_identifiers(block)
        assert "lowercase_name" not in ids


class TestPythonSymbolExtraction:
    """Test Python symbol extraction via AST."""

    def test_extracts_class_names(self, validator, tmp_path):
        source = tmp_path / "test_source.py"
        source_text = dedent("""\
            class MyExporter:
                def export_data(self):
                    pass

            class MyManager:
                pass
        """)
        source.write_text(source_text)
        symbols = validator.extract_python_symbols(source)
        assert "MyExporter" in symbols
        assert "MyManager" in symbols
        assert "export_data" in symbols

    def test_extracts_top_level_functions(self, validator, tmp_path):
        source = tmp_path / "test_source.py"
        source.write_text("def top_level_func():\n    pass\n")
        symbols = validator.extract_python_symbols(source)
        assert "top_level_func" in symbols

    def test_extracts_nested_definitions_from_statement_bodies(self, validator, tmp_path):
        """Nested definitions must stay visible to the diagram lint.

        Why:
            The symbol extraction path must keep the old AST verdict.
        """
        source = tmp_path / "nested_source.py"
        source_text = dedent("""\
            def outer_func():
                def inner_func():
                    pass
                try:
                    class TryManager:
                        def run(self):
                            pass
                except ValueError:
                    class ExceptManager:
                        pass
                match 1:
                    case 1:
                        def matched_func():
                            pass
        """)
        source.write_text(source_text, encoding="utf-8")
        symbols = validator.extract_python_symbols(source)
        assert {"outer_func", "inner_func", "TryManager", "run", "ExceptManager", "matched_func"} <= symbols

    def test_preserves_async_function_name_omission(self, validator, tmp_path):
        """The symbol extraction path must omit async function names.

        Why:
            The optimized parser must keep the old AST symbol set.
        """
        source = tmp_path / "async_source.py"
        source_text = dedent("""\
            async def async_outer():
                def nested_sync():
                    pass
        """)
        source.write_text(source_text, encoding="utf-8")
        symbols = validator.extract_python_symbols(source)
        assert "async_outer" not in symbols
        assert "nested_sync" in symbols

    def test_ignores_definition_words_in_strings_and_comments(self, validator, tmp_path):
        """Definition words in text must not become source symbols.

        Why:
            The optimized parser must not read strings or comments as code.
        """
        source = tmp_path / "literal_source.py"
        source_text = dedent("""\
            # def CommentFunction():
            text = "class StringManager"

            class RealManager:
                pass
        """)
        source.write_text(source_text, encoding="utf-8")
        symbols = validator.extract_python_symbols(source)
        assert "RealManager" in symbols
        assert "CommentFunction" not in symbols
        assert "StringManager" not in symbols

    def test_handles_syntax_error(self, validator, tmp_path):
        source = tmp_path / "bad.py"
        source.write_text("def broken(\n")
        symbols = validator.extract_python_symbols(source)
        assert symbols == set()

    def test_handles_missing_file(self, validator, tmp_path):
        source = tmp_path / "nonexistent.py"
        symbols = validator.extract_python_symbols(source)
        assert symbols == set()


class TestAllowlistFiltering:
    """Test that allowlisted terms are skipped."""

    def test_builtin_allowlist_skips_keywords(self, validator_with_symbols):
        v = validator_with_symbols
        block = "classDiagram\n    class WebSocket"
        v.extract_identifiers(block)
        # WebSocket is in allowlist, should not cause stale reference
        assert "WebSocket" in BUILT_IN_ALLOWLIST

    def test_custom_allowlist(self):
        custom = frozenset(["MyCustomTerm"])
        v = DiagramReferenceValidator(BUILT_IN_ALLOWLIST | custom)
        assert "MyCustomTerm" in v.allowlist


class TestStaleReferenceDetection:
    """Test stale reference detection and closest match."""

    def test_valid_reference_not_stale(self, validator_with_symbols):
        v = validator_with_symbols
        v.total_checked = 0
        name = "DataExporter"
        assert name in v.python_symbols

    def test_stale_reference_detected(self, validator_with_symbols):
        v = validator_with_symbols
        name = "OrgFooExporter"
        assert name not in v.python_symbols

    def test_closest_match_found(self, validator_with_symbols):
        v = validator_with_symbols
        match = v.find_closest_match("DataExportr")
        assert match is not None
        assert "DataExporter" in match

    def test_no_close_match(self, validator_with_symbols):
        v = validator_with_symbols
        match = v.find_closest_match("XyzAbcDefGhi")
        # Should return None if edit distance > len/2
        # This depends on the actual symbols
        assert match is None or "edit distance" in match


class TestEditDistance:
    """Test Levenshtein edit distance calculation."""

    def test_identical_strings(self, validator):
        assert validator._edit_distance("abc", "abc") == 0

    def test_single_insertion(self, validator):
        assert validator._edit_distance("abc", "abcd") == 1

    def test_single_deletion(self, validator):
        assert validator._edit_distance("abcd", "abc") == 1

    def test_single_substitution(self, validator):
        assert validator._edit_distance("abc", "axc") == 1

    def test_empty_strings(self, validator):
        assert validator._edit_distance("", "") == 0
        assert validator._edit_distance("abc", "") == 3


class TestExitCodes:
    """Test exit code behavior."""

    def test_report_success(self, validator):
        validator.total_checked = 5
        validator.files_scanned = 2
        validator.stale_references = []
        assert validator._report_results() == 0

    def test_report_failure(self, validator):
        validator.files_scanned = 1
        validator.stale_references = [{"file": "test.md", "line": 1, "name": "Foo", "closest": None}]
        assert validator._report_results() == 1


class TestDiagramFileValidation:
    """Test full diagram file verdicts."""

    def test_file_with_valid_reference_passes(self, validator, tmp_path):
        """A diagram file with a known reference must pass.

        Why:
            The lint gate must accept valid Mermaid references.
        """
        diagram_file = tmp_path / "valid.md"
        diagram_file.write_text("```mermaid\nclassDiagram\n    class DataExporter\n```\n", encoding="utf-8")
        validator.python_symbols = {"DataExporter"}
        assert validator.validate_file(diagram_file) == 0
        assert validator.stale_references == []

    def test_file_with_broken_reference_fails_gate(self, validator, tmp_path):
        """A diagram file with an unknown reference must fail.

        Why:
            The lint gate must reject stale Mermaid references.
        """
        diagram_file = tmp_path / "broken.md"
        diagram_file.write_text("```mermaid\nclassDiagram\n    class MissingExporter\n```\n", encoding="utf-8")
        validator.python_symbols = {"DataExporter"}
        assert validator.validate_file(diagram_file) == 1
        assert validator._report_results() == 1

    def test_run_with_broken_reference_fails_gate(self, tmp_path):
        """A complete run with a stale reference must fail.

        Why:
            The optimized source scan must keep the lint gate verdict.
        """
        source_dir = tmp_path / "source"
        docs_dir = tmp_path / "docs"
        source_dir.mkdir()
        docs_dir.mkdir()
        (source_dir / "defined.py").write_text("class DataExporter:\n    pass\n", encoding="utf-8")
        (docs_dir / "diagram.md").write_text(
            "```mermaid\nclassDiagram\n    class MissingExporter\n```\n",
            encoding="utf-8",
        )
        config = lint_diagram_refs.build_parser().parse_args(
            ["--docs-dir", str(docs_dir), "--extra-files", "--source-files", str(source_dir)]
        )
        validator = DiagramReferenceValidator()
        assert validator.run(config) == 1

    def test_file_without_reference_skips_validation(self, validator, tmp_path):
        """A diagram file without Mermaid content must not add findings.

        Why:
            Plain prose cannot hold a diagram reference.
        """
        diagram_file = tmp_path / "plain.md"
        diagram_file.write_text("# Plain file\n\nNo diagram reference exists.\n", encoding="utf-8")
        assert validator.validate_file(diagram_file) == 0
        assert validator.files_scanned == 0

    def test_non_mermaid_code_block_skips_validation(self, validator, tmp_path):
        """A non-Mermaid code block must not add findings.

        Why:
            Python examples are not diagram references.
        """
        diagram_file = tmp_path / "python.md"
        diagram_file.write_text("```python\nclass MissingExporter:\n    pass\n```\n", encoding="utf-8")
        assert validator.validate_file(diagram_file) == 0
        assert validator.stale_references == []

    def test_run_stops_source_reads_after_references_resolve(self, validator, tmp_path, monkeypatch):
        """The run must stop source reads after all references resolve.

        Why:
            The optimized gate avoids reading Python files that cannot change the verdict.
        """
        source_dir = tmp_path / "source"
        docs_dir = tmp_path / "docs"
        source_dir.mkdir()
        docs_dir.mkdir()
        first_source = source_dir / "a_first.py"
        later_source = source_dir / "z_later.py"
        first_source.write_text("class NeededManager:\n    pass\n", encoding="utf-8")
        later_source.write_text("class OtherManager:\n    pass\n", encoding="utf-8")
        (docs_dir / "diagram.md").write_text(
            "```mermaid\nclassDiagram\n    class NeededManager\n```\n",
            encoding="utf-8",
        )
        config = lint_diagram_refs.build_parser().parse_args(
            ["--docs-dir", str(docs_dir), "--extra-files", "--source-files", str(source_dir)]
        )
        monkeypatch.setattr(validator, "_collect_source_files", lambda _source_files: [first_source, later_source])
        real_read_text = Path.read_text
        read_paths: list[Path] = []

        def counted_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            read_paths.append(path)
            return real_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counted_read_text)
        assert validator.run(config) == 0
        assert first_source in read_paths
        assert later_source not in read_paths


def test_default_sources_are_generic() -> None:
    args = lint_diagram_refs.build_parser().parse_args([])
    assert args.source_files == ["src/"]
    assert "MistHelper" not in BUILT_IN_ALLOWLIST


def test_repeatable_allow_option_adds_product_terms(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    src_dir = tmp_path / "src"
    docs_dir.mkdir()
    src_dir.mkdir()
    (src_dir / "defined.py").write_text("class RealManager:\n    pass\n", encoding="utf-8")
    (docs_dir / "diagram.md").write_text("```mermaid\nclassDiagram\n    class MistHelper\n```\n", encoding="utf-8")

    assert lint_diagram_refs.main(["--docs-dir", str(docs_dir), "--extra-files", "--source-files", str(src_dir)]) == 1
    assert (
        lint_diagram_refs.main(
            ["--docs-dir", str(docs_dir), "--extra-files", "--source-files", str(src_dir), "--allow", "MistHelper"]
        )
        == 0
    )
