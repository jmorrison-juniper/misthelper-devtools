"""Tests for the column-aware dictionary extractor and the quality harness."""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import json  # Reads back written output and builds temporary files.
import pathlib  # Builds temporary paths.

import pytest  # Provides the monkeypatch and capture fixtures.

from tools.ste_linter.dictionary import extract as extract_module  # The extractor module for the glue tests.
from tools.ste_linter.dictionary import quality as quality_module  # The harness module for the glue tests.
from tools.ste_linter.dictionary.extract import (  # The extractor parts under test.
    DictionaryExtractor,
    PositionedWord,
    RawEntry,
)
from tools.ste_linter.dictionary.quality import QualityHarness  # The harness under test.


def _word(text: str, x0: float, top: float) -> PositionedWord:
    """Return a positioned word on page 149 for a test row."""
    return PositionedWord(text=text, x0=x0, top=top, page=149)  # A single test word.


def _row(column1: str, column2: str, top: float) -> list[PositionedWord]:
    """Return the positioned words for one printed row.

    Column 1 words start near x0 80. Column 2 words start near x0 160. An example
    word past x0 270 is added so the tests confirm the example column is ignored.
    """
    words: list[PositionedWord] = []  # Holds the row words.
    for offset, token in enumerate(column1.split()):  # Place column-1 tokens.
        words.append(_word(token, 80 + offset * 12, top))  # A word in the word column.
    for offset, token in enumerate(column2.split()):  # Place column-2 tokens.
        words.append(_word(token, 160 + offset * 12, top))  # A word in the meaning column.
    words.append(_word("EXAMPLE", 320, top))  # An example-column word that must be ignored.
    return words  # Return the row words.


# --- Column classification -------------------------------------------------


def test_column_of_word_column() -> None:
    """A small left coordinate is the word column."""
    assert DictionaryExtractor()._column_of(80) == 0  # Column 1.


def test_column_of_meaning_column() -> None:
    """A middle left coordinate is the meaning column."""
    assert DictionaryExtractor()._column_of(160) == 1  # Column 2.


def test_column_of_example_column() -> None:
    """A large left coordinate is an ignored example column."""
    assert DictionaryExtractor()._column_of(320) == -1  # Ignored.


# --- Header detection (three patterns) -------------------------------------


def test_detect_header_pos_in_column1() -> None:
    """A part of speech in column 1 starts an entry."""
    result = DictionaryExtractor()._detect_header("ACTIVE (adj)", "a state of action", "")
    assert result == ("ACTIVE", "adj", "a state of action", False)  # Pattern 1.


def test_detect_header_pos_in_column2() -> None:
    """A part of speech at the start of column 2 starts an entry."""
    result = DictionaryExtractor()._detect_header("abnormality", "(n) UNUSUAL (adj)", "")
    assert result == ("abnormality", "n", "UNUSUAL (adj)", False)  # Pattern 2.


def test_detect_header_pos_wrapped_to_next_row() -> None:
    """A part of speech on the next column-1 row starts an entry and consumes the row."""
    result = DictionaryExtractor()._detect_header("according to", "REFER (v)", "(prep)")
    assert result == ("according to", "prep", "REFER (v)", True)  # Pattern 3.


def test_detect_header_rejects_column_title() -> None:
    """The column title 'Word' does not start an entry."""
    assert DictionaryExtractor()._detect_header("Word", "Approved meaning", "") is None  # Not an entry.


def test_detect_header_rejects_long_fragment() -> None:
    """A long bare fragment does not start an entry."""
    assert DictionaryExtractor()._detect_header("one two three four five", "(n)", "") is None  # Too long.


# --- Alternative extraction ------------------------------------------------


def test_extract_alternatives_single() -> None:
    """A single alternative is extracted from the meaning column."""
    assert DictionaryExtractor()._extract_alternatives("PRECISION (n)") == ["precision"]  # One alternative.


def test_extract_alternatives_multiple() -> None:
    """Several alternatives are extracted in order."""
    text = "TASK (n) PROCEDURE (n) WORK (n)"  # Three alternatives.
    assert DictionaryExtractor()._extract_alternatives(text) == ["task", "procedure", "work"]  # In order.


def test_extract_alternatives_ignores_lowercase_words() -> None:
    """Lowercase meaning text before an alternative is not captured."""
    text = "some lowercase meaning PERMITTED (adj)"  # Meaning text then a real alternative.
    assert DictionaryExtractor()._extract_alternatives(text) == ["permitted"]  # Only the real one.


# --- Record building -------------------------------------------------------


def test_to_record_approved_keeps_meaning() -> None:
    """An approved word keeps its meaning and has no alternatives."""
    entry = RawEntry(keyword="ACTIVE", part_of_speech="adj", approved=True, page=0, column2_lines=["a state of action"])
    record = DictionaryExtractor()._to_record(entry)  # Build the record.
    assert record is not None  # The record was built.
    assert record["approved"] is True  # The word is approved.
    assert record["alternatives"] == []  # An approved word has no alternatives.
    assert record["approved_meaning"] == "a state of action"  # The meaning is kept.


def test_to_record_not_approved_keeps_alternatives() -> None:
    """A not-approved word keeps its alternatives and no meaning."""
    entry = RawEntry(keyword="accuracy", part_of_speech="n", approved=False, page=0, column2_lines=["PRECISION (n)"])
    record = DictionaryExtractor()._to_record(entry)  # Build the record.
    assert record is not None  # The record was built.
    assert record["approved"] is False  # The word is not approved.
    assert record["alternatives"] == ["precision"]  # The alternative is kept.
    assert record["approved_meaning"] == ""  # A not-approved word has no meaning.


def test_to_record_rejects_long_keyword() -> None:
    """A keyword longer than four words is rejected."""
    entry = RawEntry(keyword="one two three four five", part_of_speech="n", approved=False, page=0)
    assert DictionaryExtractor()._to_record(entry) is None  # The keyword is a fragment.


def test_to_record_lowercases_keyword() -> None:
    """The record keyword is lower case."""
    entry = RawEntry(keyword="ACTIVE", part_of_speech="adj", approved=True, page=0, column2_lines=[])
    record = DictionaryExtractor()._to_record(entry)  # Build the record.
    assert record is not None and record["keyword"] == "active"  # The keyword is lower case.


# --- Deduplication ---------------------------------------------------------


def test_deduplicate_keeps_distinct_parts_of_speech() -> None:
    """The same word with different parts of speech is kept twice."""
    records = [
        {"keyword": "advance", "part_of_speech": "n", "approved": False, "alternatives": [], "approved_meaning": ""},
        {"keyword": "advance", "part_of_speech": "v", "approved": False, "alternatives": [], "approved_meaning": ""},
    ]  # Two records for one word.
    assert len(DictionaryExtractor()._deduplicate(records)) == 2  # Both kept.


def test_deduplicate_drops_repeat_pairs() -> None:
    """A repeated keyword and part-of-speech pair is dropped."""
    records = [
        {"keyword": "aid", "part_of_speech": "n", "approved": True, "alternatives": [], "approved_meaning": ""},
        {"keyword": "aid", "part_of_speech": "n", "approved": True, "alternatives": [], "approved_meaning": ""},
    ]  # A duplicate pair.
    assert len(DictionaryExtractor()._deduplicate(records)) == 1  # Only one kept.


# --- Full parse over synthetic rows ----------------------------------------


def test_parse_entries_over_synthetic_rows() -> None:
    """The parser builds records from a small synthetic page."""
    words: list[PositionedWord] = []  # Holds every synthetic word.
    words += _row("ACTIVE (adj)", "a state of action", 100)  # An approved entry.
    words += _row("accuracy (n)", "PRECISION (n)", 130)  # A not-approved entry.
    words += _row("abnormality", "(n) UNUSUAL (adj)", 160)  # Part of speech in column 2.
    records = DictionaryExtractor()._parse_entries(words)  # Parse the rows into raw entries.
    keywords = {entry.keyword for entry in records}  # The keywords found.
    assert keywords == {"ACTIVE", "accuracy", "abnormality"}  # All three entries were found.


def test_extract_alternatives_rejects_bleed_by_column() -> None:
    """An example word past the column boundary does not become an alternative."""
    words = _row("acceptable", "(adj) PERMITTED (adj)", 100)  # A real entry with one alternative.
    words.append(_word("REPLACE", 288, 112))  # An example word just past the boundary, next row.
    records = DictionaryExtractor()._parse_entries(words)  # Parse the rows.
    built = DictionaryExtractor()._to_record(records[0])  # Build the first record.
    assert built is not None and built["alternatives"] == ["permitted"]  # The bleed word is excluded.


# --- Quality harness -------------------------------------------------------


def _dictionary() -> list[dict]:
    """Return a small extracted dictionary for the harness tests."""
    return [
        {"keyword": "accuracy", "part_of_speech": "n", "approved": False, "alternatives": ["precision"]},
        {"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": []},
    ]  # Two records.


def test_harness_perfect_match() -> None:
    """A dictionary that matches the golden set scores one."""
    golden = _dictionary()  # The golden set equals the dictionary.
    report = QualityHarness().evaluate(_dictionary(), golden)  # Score the dictionary.
    assert report.field_accuracy == 1.0  # A perfect match.
    assert report.mismatches == []  # No mismatches.


def test_harness_reports_alternative_mismatch() -> None:
    """A wrong alternatives list is reported as a mismatch."""
    dictionary = [
        {"keyword": "accuracy", "part_of_speech": "n", "approved": False, "alternatives": ["wrong"]},
    ]  # A wrong alternative.
    golden = [
        {"keyword": "accuracy", "part_of_speech": "n", "approved": False, "alternatives": ["precision"]},
    ]  # The correct alternative.
    report = QualityHarness().evaluate(dictionary, golden)  # Score the dictionary.
    assert report.alternatives_correct == 0  # The alternatives do not match.
    assert any("alternatives" in line for line in report.mismatches)  # The mismatch is reported.


def test_harness_reports_missing_keyword() -> None:
    """A golden keyword missing from the dictionary is reported."""
    golden = [
        {"keyword": "missing", "part_of_speech": "n", "approved": False, "alternatives": []},
    ]  # A word not in the dictionary.
    report = QualityHarness().evaluate(_dictionary(), golden)  # Score the dictionary.
    assert report.keyword_found == 0  # The keyword was not found.
    assert any("missing" in line for line in report.mismatches)  # The miss is reported.


# --- Glue: output, import, and command-line entries ------------------------


def test_summarize_counts() -> None:
    """The summary reports the approved and not-approved counts."""
    records = [
        {"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": [], "approved_meaning": ""},
        {
            "keyword": "accuracy",
            "part_of_speech": "n",
            "approved": False,
            "alternatives": ["precision"],
            "approved_meaning": "",
        },
    ]  # One approved and one not-approved record.
    summary = extract_module._summarize(records)  # Build the summary line.
    assert "2 entries" in summary and "1 approved" in summary  # The counts are shown.


def test_write_output_round_trip(tmp_path: pathlib.Path) -> None:
    """The written output reads back as the same records."""
    records = [
        {"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": [], "approved_meaning": ""},
    ]  # One record to write.
    output = tmp_path / "sub" / "dict.json"  # A path with a missing parent folder.
    extract_module._write_output(records, str(output))  # Write the dictionary.
    data = json.loads(output.read_text(encoding="utf-8"))  # Read the file back.
    assert data["version"] == "1.0" and data["entries"] == records  # The content matches.


def test_import_pdfplumber_returns_module() -> None:
    """The pdfplumber import returns a module when it is installed."""
    pytest.importorskip("pdfplumber")  # Skip when the optional extraction library is absent.
    module = DictionaryExtractor()._import_pdfplumber()  # Import pdfplumber.
    assert hasattr(module, "open")  # The module exposes the open function.


def test_extract_main_writes_output(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The extractor command writes the output without reading a real PDF."""
    records = [
        {"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": [], "approved_meaning": ""},
    ]  # The records the fake extractor returns.
    monkeypatch.setattr(DictionaryExtractor, "extract", lambda self, pdf, start_page=149: records)  # Skip the PDF.
    output = tmp_path / "dict.json"  # The output path.
    code = extract_module.main(["fake.pdf", "--output", str(output)])  # Run the command.
    assert code == 0 and output.exists()  # The command wrote the file.


def test_quality_load_entries_both_shapes(tmp_path: pathlib.Path) -> None:
    """The harness loader reads a wrapped object and a bare list."""
    wrapped = tmp_path / "wrapped.json"  # A file with an entries object.
    wrapped.write_text(json.dumps({"entries": [{"keyword": "a"}]}), encoding="utf-8")  # Wrapped shape.
    bare = tmp_path / "bare.json"  # A file with a bare list.
    bare.write_text(json.dumps([{"keyword": "b"}]), encoding="utf-8")  # Bare shape.
    assert quality_module._load_entries(str(wrapped)) == [{"keyword": "a"}]  # Wrapped read.
    assert quality_module._load_entries(str(bare)) == [{"keyword": "b"}]  # Bare read.


def test_quality_main_pass_and_fail(tmp_path: pathlib.Path) -> None:
    """The harness command returns zero on a match and one on a mismatch."""
    golden = tmp_path / "golden.json"  # The golden file.
    golden.write_text(
        json.dumps({"entries": [{"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": []}]}),
        encoding="utf-8",
    )  # One golden entry.
    good = tmp_path / "good.json"  # A matching dictionary.
    good.write_text(
        json.dumps({"entries": [{"keyword": "active", "part_of_speech": "adj", "approved": True, "alternatives": []}]}),
        encoding="utf-8",
    )  # The dictionary matches the golden entry.
    bad = tmp_path / "bad.json"  # A non-matching dictionary.
    bad.write_text(json.dumps({"entries": []}), encoding="utf-8")  # An empty dictionary.
    assert quality_module.main([str(good), str(golden)]) == 0  # A full match passes.
    assert quality_module.main([str(bad), str(golden)]) == 1  # A miss fails.
