"""Dictionary extraction from the licensed PDF.

Reads a licensed ASD-STE100 PDF by word position and writes a JSON dictionary that
the linter loads. The dictionary is a four-column table. This tool reads the two
left columns: the word column and the meaning-or-alternatives column. It classifies
a word as approved when the word is printed in capital letters, and it extracts
alternatives that are printed as an uppercase word with a part of speech. The output
goes to a git-ignored path and is never committed.

Run it with:

    python -m tools.ste_linter.dictionary.extract path/to/ASD-STE100.pdf

See ``specs/1027-ste-dict-extractor/`` for the design.
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import argparse  # Parses the command-line arguments.
import importlib  # Loads pdfplumber by name so the import stays optional.
import json  # Writes the dictionary file.
import logging  # Records the extraction stages.
import os  # Builds the output path and folders.
import re  # Drives the header and alternative patterns.
from dataclasses import dataclass, field  # Declares the small parser value types.
from typing import Any  # Types the pdfplumber page objects, which have no stubs.

# The logger for the extraction tool. The main function configures the handler.
_LOG = logging.getLogger("ste_linter.dictionary.extract")

# The default output path. Git ignores this path so no copyrighted data is committed.
_DEFAULT_OUTPUT = os.path.join("data", "ste_dictionary.json")

# The first page that holds real alphabetical entries. In ASD-STE100 Issue 9 the
# dictionary body is section 2-1, and its first page (footer "Page 2-1-A1") is PDF
# page 149. Earlier pages hold the intro and example tables (section 2-0), which
# must not become entries.
_DEFAULT_START_PAGE = 149

# The left boundary of the word column. A word with a smaller left coordinate is in
# the word column. A word between this and the meaning boundary is in column 2.
_COLUMN1_MAX_X = 130.0

# The right boundary of the meaning column. Words past this are the STE example
# column and must not enter the meaning text. The real alternatives sit near the
# left of the meaning column (about x0 158) and their part-of-speech markers end by
# about x0 246. The example column starts near x0 288. A boundary of 270 keeps the
# alternatives and rejects the example words that would otherwise bleed in.
_COLUMN2_MAX_X = 270.0

# The vertical bucket size that merges words on the same printed line into one row.
_ROW_BUCKET = 3.0

# The parts of speech that a real headword uses in column 1.
_HEADWORD_POS = "n|v|adj|adv|prep|pron|conj|art"

# The parts of speech that an alternative uses in column 2. The technical-noun and
# technical-verb markers appear only as alternatives.
_ALTERNATIVE_POS = "n|v|adj|adv|prep|pron|conj|art|TN|TV"

# Matches a headword and its part of speech at the start of the word column, for
# example "ACTIVE (adj)" or "activity (n)" or "AFT OF (prep)".
_HEADWORD = re.compile(rf"^([A-Za-z][A-Za-z '\-]{{0,40}}?)\s*\(({_HEADWORD_POS})\)")

# Matches an alternative in column 2: an uppercase word, or a short uppercase phrase
# of up to three words, that is followed by a part of speech in parentheses. The
# part-of-speech suffix separates a real alternative from an example word that leaked
# in from the example column. The three-word cap bounds any leaked run while it still
# allows a real technical-noun phrase such as "THE TWO".
_ALTERNATIVE = re.compile(rf"\b([A-Z][A-Z\-]*(?:\s[A-Z][A-Z\-]*){{0,2}})\s*\(({_ALTERNATIVE_POS})\)")

# Matches a bare headword in column 1 when the part of speech is not on the same row.
# The part of speech may sit at the start of column 2, or wrap to the next row.
_BARE_WORD = re.compile(r"^([A-Za-z][A-Za-z '\-]{0,40}?),?$")

# Matches a part of speech at the very start of column 2, for example "(n)".
_POS_PREFIX = re.compile(rf"^\(({_HEADWORD_POS})\)")

# Matches a whole column-1 cell that is only a wrapped part of speech, for example
# "(adj)" on the line below a headword.
_BARE_POS = re.compile(rf"^\(({_HEADWORD_POS})\),?$")

# Text fragments that mark a page header, a footer, or a column title. A row that
# holds one of these is not a dictionary entry.
_NOISE_MARKERS = (
    "ASD-STE100",
    "Simplified Technical",
    "Issue 9",
    "Part 2",
    "Dictionary",
    "Page 2-",
    "2025-01-15",
    "Blank Page",
    "part of speech",
    "Approved meaning",
    "ALTERNATIVES",
    "STE EXAMPLE",
    "Non-STE",
)

# The largest number of words a real keyword can hold. A longer value is a parse
# error, for example a captured sentence.
_MAX_KEYWORD_WORDS = 4


@dataclass(frozen=True)
class PositionedWord:
    """One word read from the PDF with its position."""

    text: str  # The word as printed.
    x0: float  # The left coordinate on the page.
    top: float  # The top coordinate on the page.
    page: int  # The 1-based page number.


@dataclass
class RawEntry:
    """One entry as the parser builds it, before it becomes a record."""

    keyword: str  # The headword as printed.
    part_of_speech: str  # The part of speech from the word column.
    approved: bool  # True when the headword is printed in capitals.
    page: int  # The page where the entry starts.
    column2_lines: list[str] = field(default_factory=list)  # The joined column-2 text.


class DictionaryExtractor:
    """Extracts the STE dictionary from a licensed PDF by word position."""

    def extract(self, pdf_path: str, start_page: int = _DEFAULT_START_PAGE) -> list[dict[str, Any]]:
        """Return the dictionary records found in the PDF at ``pdf_path``."""
        _LOG.info("Reading PDF %s from page %d", pdf_path, start_page)  # Log before the read.
        words = self._read_words(pdf_path, start_page)  # Read positioned words from the entry pages.
        _LOG.debug("Read %d positioned words", len(words))  # Log the word count after the read.
        raw_entries = self._parse_entries(words)  # Build raw entries from the words.
        _LOG.debug("Parsed %d raw entries", len(raw_entries))  # Log the raw entry count.
        built = [self._to_record(entry) for entry in raw_entries]  # Turn raw entries into records.
        records = [record for record in built if record is not None]  # Drop rejected records.
        deduped = self._deduplicate(records)  # Remove duplicate keyword and part-of-speech pairs.
        _LOG.debug("Produced %d dictionary records", len(deduped))  # Log the final record count.
        return deduped  # Return the records.

    def _read_words(self, pdf_path: str, start_page: int) -> list[PositionedWord]:
        """Return the positioned words from the entry pages of the PDF."""
        pdfplumber = self._import_pdfplumber()  # Import the optional PDF library.
        words: list[PositionedWord] = []  # Holds every positioned word.
        with pdfplumber.open(pdf_path) as pdf:  # Open the licensed PDF.
            for page_index in range(start_page - 1, len(pdf.pages)):  # Scan from the start page.
                page_number = page_index + 1  # The 1-based page number.
                for word in pdf.pages[page_index].extract_words():  # Each word with its position.
                    words.append(
                        PositionedWord(
                            text=word["text"],  # The word text.
                            x0=float(word["x0"]),  # The left coordinate.
                            top=float(word["top"]),  # The top coordinate.
                            page=page_number,  # The page number.
                        )
                    )  # Record the positioned word.
        return words  # Return every positioned word.

    def _import_pdfplumber(self) -> Any:
        """Return the pdfplumber module, or stop with a clear message."""
        try:  # pdfplumber is needed only for extraction, so import it here.
            return importlib.import_module("pdfplumber")  # Load the PDF library by name.
        except ImportError as error:  # pdfplumber is not installed.
            raise SystemExit(
                "The 'pdfplumber' package is needed. Install it with: uv pip install \".[ste-linter]\""
            ) from error  # Stop with install guidance.

    def _parse_entries(self, words: list[PositionedWord]) -> list[RawEntry]:
        """Return the raw entries built from the positioned words."""
        rows = self._rows(words)  # Build the (column1, column2) rows in reading order.
        entries: list[RawEntry] = []  # Holds the finished raw entries.
        skip_next = False  # True when the previous row consumed a wrapped part-of-speech row.
        for index, (column1, column2) in enumerate(rows):  # Walk each row with its index.
            if skip_next:  # The previous entry already used this wrapped part-of-speech row.
                skip_next = False  # Reset the flag for the next row.
                continue  # Do not process the consumed row again.
            if self._is_noise(column1) or self._is_noise(column2):  # Skip a header or footer row.
                continue  # Move to the next row.
            next_column1 = rows[index + 1][0] if index + 1 < len(rows) else ""  # Peek at the next row.
            header = self._detect_header(column1, column2, next_column1)  # Try to start an entry.
            if header is not None:  # A new headword starts an entry.
                keyword, part_of_speech, remaining, consumed = header  # Unpack the detection result.
                entries.append(self._start_entry(keyword, part_of_speech, remaining))  # Begin the entry.
                skip_next = consumed  # Skip the next row when the part of speech came from it.
            elif entries and column2:  # A continuation row extends the current entry.
                entries[-1].column2_lines.append(column2)  # Add the column-2 text.
        return entries  # Return every raw entry.

    def _detect_header(self, column1: str, column2: str, next_column1: str) -> tuple[str, str, str, bool] | None:
        """Return (keyword, part of speech, remaining column 2, consumed next row) or None.

        The dictionary prints a headword in three ways: the part of speech on the same
        row, at the start of the meaning column, or wrapped to the next row.
        """
        same_row = _HEADWORD.match(column1)  # Pattern 1: the part of speech is in column 1.
        if same_row:  # The headword and part of speech share the row.
            return same_row.group(1).strip(), same_row.group(2), column2, False  # Column 2 is the meaning.
        bare = _BARE_WORD.match(column1)  # The headword may stand alone with no part of speech.
        if not bare or column1 == "Word":  # Not a bare headword, or the column title.
            return None  # This row does not start an entry.
        keyword = bare.group(1).strip()  # The bare headword.
        if len(keyword.split()) > _MAX_KEYWORD_WORDS:  # Reject a long fragment, not a real headword.
            return None  # This row does not start an entry.
        prefix = _POS_PREFIX.match(column2)  # Pattern 2: the part of speech starts column 2.
        if prefix:  # The part of speech leads the meaning column.
            return keyword, prefix.group(1), column2[prefix.end() :].strip(), False  # Strip the prefix.
        wrapped = _BARE_POS.match(next_column1)  # Pattern 3: the part of speech wrapped to the next row.
        if wrapped:  # The next column-1 cell holds only the part of speech.
            return keyword, wrapped.group(1), column2, True  # Consume the next row.
        return None  # No headword pattern matched.

    def _start_entry(self, keyword: str, part_of_speech: str, column2: str) -> RawEntry:
        """Return a new raw entry from a headword, its part of speech, and its column-2 text."""
        return RawEntry(
            keyword=keyword,  # The headword.
            part_of_speech=part_of_speech,  # The part of speech.
            approved=keyword.isupper(),  # Approved words are printed in capitals.
            page=0,  # The page is not needed once the entry is built.
            column2_lines=[column2] if column2 else [],  # The first column-2 line, when present.
        )  # Return the started entry.

    def _rows(self, words: list[PositionedWord]) -> list[tuple[str, str]]:
        """Return each printed row as a (column1 text, column2 text) pair."""
        buckets: dict[tuple[int, int], dict[int, list[PositionedWord]]] = {}  # (page, top) -> col -> words
        for word in words:  # Assign each word to a row bucket and a column.
            column = self._column_of(word.x0)  # The column index, or -1 for example columns.
            if column < 0:  # The word is in an example column.
                continue  # Ignore example columns.
            top_key = int(word.top / _ROW_BUCKET)  # Bucket the vertical position into a row.
            row_key = (word.page, top_key)  # The row identity across the page.
            buckets.setdefault(row_key, {}).setdefault(column, []).append(word)  # Group the word.
        return [self._row_text(buckets[key]) for key in sorted(buckets)]  # Order rows top to bottom.

    def _row_text(self, columns: dict[int, list[PositionedWord]]) -> tuple[str, str]:
        """Return the joined column-1 and column-2 text for one row."""
        column1 = " ".join(word.text for word in columns.get(0, []))  # Column 1 text.
        column2 = " ".join(word.text for word in columns.get(1, []))  # Column 2 text.
        return column1.strip(), column2.strip()  # Return the trimmed column strings.

    def _column_of(self, x0: float) -> int:
        """Return 0 for the word column, 1 for the meaning column, or -1 for examples."""
        if x0 < _COLUMN1_MAX_X:  # The word sits in the word column.
            return 0  # Column 1.
        if x0 < _COLUMN2_MAX_X:  # The word sits in the meaning or alternatives column.
            return 1  # Column 2.
        return -1  # An example column the tool ignores.

    def _is_noise(self, text: str) -> bool:
        """Return True when a row is a page header, footer, or column title."""
        return any(marker in text for marker in _NOISE_MARKERS)  # Match a known noise fragment.

    def _to_record(self, entry: RawEntry) -> dict[str, Any] | None:
        """Return a dictionary record for a raw entry, or None when the keyword is bad."""
        keyword = entry.keyword.lower()  # The keyword in lower case.
        if not keyword or len(keyword.split()) > _MAX_KEYWORD_WORDS:  # Reject a sentence-length keyword.
            return None  # Drop the bad entry.
        column2 = " ".join(entry.column2_lines).strip()  # The joined column-2 text.
        alternatives = self._extract_alternatives(column2)  # The approved alternatives.
        meaning = ""  # The approved meaning, filled only for an approved word.
        if entry.approved:  # An approved word carries a meaning, not alternatives.
            meaning = column2 if not alternatives else ""  # Keep the meaning text when there is no alternative.
            alternatives = []  # An approved word has no alternatives.
        return {
            "keyword": keyword,  # The word in lower case.
            "part_of_speech": entry.part_of_speech.lower(),  # The normalized part of speech.
            "approved": entry.approved,  # The approved flag.
            "alternatives": alternatives,  # The approved alternatives.
            "approved_meaning": meaning,  # The approved meaning, or empty.
        }  # Return the record.

    def _extract_alternatives(self, column2: str) -> list[str]:
        """Return the approved alternatives found in the column-2 text."""
        found: list[str] = []  # Holds the alternatives in order.
        for match in _ALTERNATIVE.finditer(column2):  # Each uppercase word with a part of speech.
            alternative = match.group(1).strip().lower()  # The alternative in lower case.
            if alternative and alternative not in found:  # Keep each alternative once.
                found.append(alternative)  # Record the alternative.
        return found  # Return the alternatives.

    def _deduplicate(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the records with duplicate keyword and part-of-speech pairs removed."""
        seen: set[tuple[str, str]] = set()  # Tracks the pairs already kept.
        unique: list[dict[str, Any]] = []  # Holds the kept records.
        for record in records:  # Walk each record.
            key = (str(record["keyword"]), str(record["part_of_speech"]))  # The keyword and part of speech.
            if key not in seen:  # The pair was not kept yet.
                seen.add(key)  # Mark the pair as kept.
                unique.append(record)  # Keep the record.
        return unique  # Return the unique records.


def _write_output(records: list[dict[str, Any]], output_path: str) -> None:
    """Write the records to the output path as a JSON dictionary."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)  # Make the output folder.
    _LOG.info("Writing %d words to %s", len(records), output_path)  # Log before the write.
    with open(output_path, "w", encoding="utf-8") as handle:  # Open the output file.
        json.dump({"version": "1.0", "entries": records}, handle, indent=2)  # Write the dictionary.


def _summarize(records: list[dict[str, Any]]) -> str:
    """Return a one-line summary of the record counts."""
    approved = sum(1 for record in records if record["approved"])  # Count approved records.
    return f"{len(records)} entries ({approved} approved, {len(records) - approved} not approved)"  # Summary.


def main(argv: list[str] | None = None) -> int:
    """Run the extraction tool from the command line."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Show progress messages.
    parser = argparse.ArgumentParser(description="Build the STE dictionary from a licensed PDF.")  # The parser.
    parser.add_argument("pdf", help="Path to the licensed ASD-STE100 PDF.")  # The input PDF.
    parser.add_argument("--output", default=_DEFAULT_OUTPUT, help="Output JSON path.")  # The output path.
    parser.add_argument(
        "--start-page", type=int, default=_DEFAULT_START_PAGE, help="First page to scan for entries."
    )  # The start page.
    args = parser.parse_args(argv)  # Parse the arguments.
    records = DictionaryExtractor().extract(args.pdf, start_page=args.start_page)  # Extract the records.
    _write_output(records, args.output)  # Write the dictionary file.
    _LOG.info("Done: %s", _summarize(records))  # Report the summary.
    _LOG.info("Remember: this file is git-ignored and must not be committed.")  # Copyright reminder.
    return 0  # Report success.


if __name__ == "__main__":  # Allow "python -m tools.ste_linter.dictionary.extract".
    raise SystemExit(main())  # Run the tool and use its exit code.
