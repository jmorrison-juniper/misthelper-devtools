"""Dictionary extraction from the licensed PDF.

Reads a licensed ASD-STE100 PDF and writes a JSON dictionary that the linter
loads. The parser is a best effort. It finds "word (part of speech)" entries and
marks a word as approved when the word is in upper case, which is the convention
in the standard. The output goes to a git-ignored path and is never committed.

Run it with:

    python -m tools.ste_linter.dictionary.extract path/to/ASD-STE100.pdf
"""

from __future__ import annotations  # Postponed annotations keep the type hints light.

import argparse  # Parses the command-line arguments.
import json  # Writes the dictionary file.
import logging  # Records the extraction stages.
import os  # Builds the output path and folders.
import re  # Drives the entry search.

# The logger for the extraction tool. The main function configures the handler.
_LOG = logging.getLogger("ste_linter.dictionary.extract")

# Matches a dictionary entry header: a word and its part of speech in parentheses.
_ENTRY = re.compile(r"([A-Za-z][A-Za-z '\-]*?)\s*\((n|v|adj|adv|prep|pro|conj|art|det|aux|TN|TV)\)")

# The default output path. The path is ignored by git so no copyrighted data is
# committed.
_DEFAULT_OUTPUT = os.path.join("data", "ste_dictionary.json")


class DictionaryExtractor:
    """Extracts a best-effort STE dictionary from a licensed PDF."""

    def extract(self, pdf_path: str) -> list[dict[str, object]]:
        """Return the dictionary records found in the PDF at ``pdf_path``."""
        _LOG.info("Reading PDF %s", pdf_path)  # Log before the read.
        text = self._read_pdf(pdf_path)  # Read all page text from the PDF.
        records = self._parse(text)  # Parse the text into records.
        _LOG.debug("Extracted %d dictionary records", len(records))  # Log the result.
        return records  # Return the records.

    def _read_pdf(self, pdf_path: str) -> str:
        """Return the joined text of every page in the PDF."""
        try:  # pypdf is needed only for extraction, so import it here.
            import importlib  # Loads pypdf by name.

            pypdf = importlib.import_module("pypdf")  # The PDF reader library.
        except ImportError as error:  # pypdf is not installed.
            raise SystemExit("The 'pypdf' package is needed. Install it first.") from error  # Stop clearly.
        reader = pypdf.PdfReader(pdf_path)  # Open the PDF.
        parts = [page.extract_text() or "" for page in reader.pages]  # Read every page.
        return "\n".join(parts)  # Join the pages into one text.

    def _parse(self, text: str) -> list[dict[str, object]]:
        """Return dictionary records parsed from the PDF text."""
        matches = list(_ENTRY.finditer(text))  # Find every entry header.
        records: list[dict[str, object]] = []  # Holds the finished records.
        for index, match in enumerate(matches):  # Walk each entry header.
            keyword = match.group(1).strip()  # The word as written.
            pos = match.group(2)  # The part of speech.
            approved = keyword.isupper()  # The standard shows approved words in upper case.
            alternatives = self._alternatives(matches, index, approved)  # Find any alternatives.
            records.append(
                {
                    "keyword": keyword.lower(),  # The word in lower case.
                    "part_of_speech": self._normalize_pos(pos),  # The normalized part of speech.
                    "approved": approved,  # The approved flag.
                    "alternatives": alternatives,  # The approved alternatives.
                    "approved_meaning": "",  # The meaning is left empty in this best-effort parse.
                }
            )  # Add the record.
        return self._deduplicate(records)  # Remove duplicate words and return.

    def _alternatives(self, matches: list[re.Match[str]], index: int, approved: bool) -> list[str]:
        """Return the alternative word for an unapproved entry, when one is near."""
        if approved:  # An approved word has no alternatives.
            return []  # Return an empty list.
        following = matches[index + 1] if index + 1 < len(matches) else None  # The next entry header.
        if following and following.group(1).strip().isupper():  # The next word is an approved word.
            return [following.group(1).strip().lower()]  # Use it as the alternative.
        return []  # No alternative was found nearby.

    def _normalize_pos(self, pos: str) -> str:
        """Return a short, lower-case part-of-speech label."""
        return pos.lower()  # The labels are already short, so lower case is enough.

    def _deduplicate(self, records: list[dict[str, object]]) -> list[dict[str, object]]:
        """Return the records with duplicate words removed, keeping the first."""
        seen: set[str] = set()  # Tracks the words already kept.
        unique: list[dict[str, object]] = []  # Holds the kept records.
        for record in records:  # Walk each record.
            keyword = str(record["keyword"])  # The word key.
            if keyword not in seen:  # The word was not kept yet.
                seen.add(keyword)  # Mark the word as kept.
                unique.append(record)  # Keep the record.
        return unique  # Return the unique records.


def main(argv: list[str] | None = None) -> int:
    """Run the extraction tool from the command line."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # Show progress messages.
    parser = argparse.ArgumentParser(description="Build the STE dictionary from a licensed PDF.")  # The parser.
    parser.add_argument("pdf", help="Path to the licensed ASD-STE100 PDF.")  # The input PDF.
    parser.add_argument("--output", default=_DEFAULT_OUTPUT, help="Output JSON path.")  # The output path.
    args = parser.parse_args(argv)  # Parse the arguments.
    records = DictionaryExtractor().extract(args.pdf)  # Extract the records.
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)  # Make the output folder.
    _LOG.info("Writing %d words to %s", len(records), args.output)  # Log before the write.
    with open(args.output, "w", encoding="utf-8") as handle:  # Open the output file.
        json.dump({"version": "1.0", "entries": records}, handle, indent=2)  # Write the dictionary.
    _LOG.info("Done. Remember: this file is git-ignored and must not be committed.")  # Final note.
    return 0  # Report success.


if __name__ == "__main__":  # Allow "python -m tools.ste_linter.dictionary.extract".
    raise SystemExit(main())  # Run the tool and use its exit code.
