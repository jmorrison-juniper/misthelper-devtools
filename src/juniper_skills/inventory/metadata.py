"""Metadata readers for harvested Juniper documents."""

from __future__ import annotations  # Permit modern type hints on Python 3.13.

import csv  # Read the authoritative harvester catalog.
import json  # Read the authoritative harvester manifests.
import logging  # Provide required action logging for operators.
import re  # Normalize keys and parse scalar front matter fields.
from pathlib import Path  # Use cross-platform paths for all file access.
from typing import Any  # Type JSON manifest values without adding a schema dependency.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class TextKey:
    """Normalize document fields into stable natural keys."""

    _space_pattern = re.compile(r"[^a-z0-9]+")  # Collapse punctuation that differs between roots.

    @classmethod
    def normalize(cls, value: str) -> str:
        """Run the normalize operation."""
        logger.info("Normalizing a document key field")  # Record key normalization for traceability.
        lowered = value.lower().strip()  # Use lower case so equivalent titles share one key.
        normalized = cls._space_pattern.sub("-", lowered).strip("-")  # Remove punctuation that changes by source.
        logger.debug("Normalized key field to %s characters", len(normalized))  # Report key length, not source text.
        return normalized


class FrontMatterParser:
    """Read simple YAML front matter without adding a new dependency."""

    _field_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")  # Match scalar YAML fields only.

    def parse(self, path: Path) -> dict[str, str]:
        """Run the parse operation."""
        logger.info("Reading front matter from %s", path)  # Log the file read before disk access.
        text = path.read_text(
            encoding="utf-8", errors="ignore"
        )  # Read with replacement so one bad glyph does not stop.
        fields = self.parse_text(text)  # Parse the bounded front matter block only.
        logger.debug("Read %s front matter fields from %s", len(fields), path)  # Report parsed field count.
        return fields

    def parse_text(self, text: str) -> dict[str, str]:
        """Run the parse text operation."""
        logger.info("Parsing front matter text")  # Mark the parse step for inventory diagnostics.
        if not text.startswith("---"):  # Return empty when the converter emitted no front matter.
            logger.debug("Front matter text has no opening marker")  # Explain why no fields were returned.
            return {}
        end_index = text.find("\n---", 3)  # Find the closing marker used by the converter.
        if end_index == -1:  # Treat an unclosed block as unusable metadata.
            logger.debug("Front matter text has no closing marker")  # Explain the missing metadata state.
            return {}
        fields = self._parse_lines(text[3:end_index].splitlines())  # Parse only metadata lines.
        logger.debug("Parsed %s fields from front matter text", len(fields))  # Report the result size.
        return fields

    def body_text(self, text: str) -> str:
        """Run the body text operation."""
        logger.info("Removing front matter from Markdown text")  # Log the transform before it runs.
        if not text.startswith("---"):  # Keep the complete file when no metadata block exists.
            logger.debug("Markdown text has no front matter to remove")  # Report that no change occurred.
            return text
        end_index = text.find("\n---", 3)  # Locate the closing metadata marker.
        body = text[end_index + 4 :] if end_index != -1 else text  # Keep content when the marker is valid.
        logger.debug("Markdown body has %s characters", len(body))  # Report the text yield.
        return body

    def _parse_lines(self, lines: list[str]) -> dict[str, str]:
        fields: dict[str, str] = {}  # Store field names exactly as the converter emits them.
        for line in lines:  # Inspect each front matter line for a scalar field.
            match = self._field_pattern.match(line)  # Accept only simple key-value lines.
            if match:  # Skip nested lines because the inventory uses scalar metadata only.
                fields[match.group(1)] = self._clean_value(match.group(2))  # Strip quotes for stable comparisons.
        return fields

    def _clean_value(self, value: str) -> str:
        cleaned = value.strip()  # Remove padding that changes across converters.
        quoted = len(cleaned) >= 2 and cleaned[0] in {"'", '"'} and cleaned[-1] == cleaned[0]  # Detect quoted strings.
        return cleaned[1:-1] if quoted else cleaned  # Return the scalar value without YAML quotes.


class HarvesterMetadataLoader:
    """Load authoritative catalog and manifest metadata from the harvester."""

    def __init__(self, harvest_root: Path) -> None:
        """Initialize the HarvesterMetadataLoader instance."""
        self.harvest_root = harvest_root  # Keep the authoritative metadata root from the contract.

    def load_catalog(self) -> dict[str, dict[str, str]]:
        """Return the load catalog result."""
        logger.info("Loading harvester catalog from %s", self.harvest_root)  # Log the catalog read before disk access.
        catalog_path = self.harvest_root / "_catalog.csv"  # Use the locked catalog file name.
        rows = self._read_catalog_rows(catalog_path) if catalog_path.exists() else []  # Permit incremental first runs.
        catalog = self._index_catalog_rows(rows)  # Build lookups by Markdown path and source PDF.
        logger.debug("Loaded %s catalog keys from %s", len(catalog), catalog_path)  # Report key count.
        return catalog

    def load_manifest_statuses(self) -> dict[str, str]:
        """Return the load manifest statuses result."""
        logger.info("Loading harvester manifests from %s", self.harvest_root)  # Log manifest discovery.
        statuses: dict[str, str] = {}  # Store the latest status for each source PDF.
        for path in sorted(self.harvest_root.glob("_manifest*.json")):  # Read every batch and watch manifest.
            logger.info("Reading manifest %s", path)  # Log each manifest before opening it.
            data = json.loads(path.read_text(encoding="utf-8"))  # Parse the authoritative manifest JSON.
            self._merge_manifest(statuses, data)  # Merge new statuses over older repeated sources.
            logger.debug("Manifest status map has %s entries", len(statuses))  # Report cumulative coverage.
        return statuses

    def _read_catalog_rows(self, catalog_path: Path) -> list[dict[str, str]]:
        logger.info("Reading catalog rows from %s", catalog_path)  # Log CSV access before opening the file.
        with catalog_path.open(newline="", encoding="utf-8-sig") as handle:  # Use BOM-safe UTF-8 for CSV input.
            rows = list(csv.DictReader(handle))  # Materialize rows because the file is small metadata.
        logger.debug("Read %s catalog rows", len(rows))  # Report row count from authoritative catalog.
        return rows

    def _index_catalog_rows(self, rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        catalog: dict[str, dict[str, str]] = {}  # Store duplicated keys for cheap lookup during scans.
        for row in rows:  # Add one or more stable keys for each catalog row.
            self._add_catalog_key(catalog, row.get("markdown_path", ""), row)  # Match current harvest Markdown files.
            self._add_catalog_key(catalog, row.get("source_pdf", ""), row)  # Match front matter source files.
            self._add_catalog_key(catalog, row.get("file", ""), row)  # Match archive stems that lack front matter.
        return catalog

    def _add_catalog_key(self, catalog: dict[str, dict[str, str]], key: str, row: dict[str, str]) -> None:
        if key:  # Skip blank catalog fields because they cannot identify a document.
            catalog[TextKey.normalize(key)] = row  # Normalize the key so path separators and case do not differ.

    def _merge_manifest(self, statuses: dict[str, str], data: dict[str, Any]) -> None:
        for item in data.get("files", []):  # Read the converter's per-source result list.
            source = str(item.get("source", ""))  # Use source PDF path as the manifest natural key.
            status = str(item.get("status", "unknown"))  # Preserve the converter status for review signals.
            if source:  # Ignore malformed manifest rows that do not name a source.
                statuses[TextKey.normalize(source)] = self._status_name(status)  # Store the normalized status name.

    def _status_name(self, status: str) -> str:
        return "ok" if status == "converted" else status  # Convert harvester language into factory language.
