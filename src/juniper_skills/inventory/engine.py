"""Inventory builder for the Juniper documentation skill factory."""

from __future__ import annotations  # Permit modern collection hints without runtime cost.

import hashlib  # Compute content hashes for incremental requeue detection.
import logging  # Provide required action logging for each inventory step.
import re  # Detect converter split suffixes in file names.
import sqlite3  # Persist the factory queue in the locked SQLite store.
from collections import defaultdict  # Accumulate part and document groups by natural keys.
from pathlib import Path  # Use cross-platform paths for every filesystem path.

from src.juniper_skills.inventory.metadata import (
    FrontMatterParser,
    HarvesterMetadataLoader,
    TextKey,
)  # Reuse metadata readers.
from src.juniper_skills.inventory.models import (
    DocumentGroup,
    DuplicateDecision,
    EditionFamily,
    InventoryResult,
    MarkdownPart,
    SourceRoot,
    VersionedFamily,
    VersionFamilyValidationResult,
)  # Share typed inventory records across the package.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class CorpusScanner:
    """Scan growing source roots into physical Markdown parts."""

    def __init__(self, roots: list[SourceRoot], metadata: HarvesterMetadataLoader) -> None:
        """Initialize the CorpusScanner instance."""
        self.roots = roots  # Keep the locked root list for repeatable scans.
        self.metadata = metadata  # Reuse the authoritative harvester metadata reader.
        self.parser = FrontMatterParser()  # Parse front matter with one shared parser.

    def scan(self) -> list[MarkdownPart]:
        """Run the scan operation."""
        logger.info("Starting corpus scan across %s roots", len(self.roots))  # Log scan start with root count.
        catalog = self.metadata.load_catalog()  # Load titles, categories, pages, and source PDF values.
        statuses = self.metadata.load_manifest_statuses()  # Load ok and review statuses from manifests.
        parts = self._scan_roots(catalog, statuses)  # Convert physical Markdown files into part records.
        logger.debug("Corpus scan found %s Markdown parts", len(parts))  # Report physical file count.
        return parts

    def _scan_roots(self, catalog: dict[str, dict[str, str]], statuses: dict[str, str]) -> list[MarkdownPart]:
        parts: list[MarkdownPart] = []  # Accumulate records from all available roots.
        for root in self.roots:  # Scan each locked source root independently.
            logger.info("Scanning source root %s", root.path)  # Log root access before discovery.
            found = self._scan_root(root, catalog, statuses) if root.path.exists() else []  # Missing roots are allowed.
            parts.extend(found)  # Keep all physical files because duplicate resolution happens later.
            logger.debug("Source root %s produced %s parts", root.name, len(found))  # Report per-root count.
        return parts

    def _scan_root(
        self,
        root: SourceRoot,
        catalog: dict[str, dict[str, str]],
        statuses: dict[str, str],
    ) -> list[MarkdownPart]:
        markdown_paths = sorted(
            path for path in root.path.rglob("*.md") if self._is_source_markdown(path)
        )  # Find inputs.
        parts = [self._read_part(root, path, catalog, statuses) for path in markdown_paths]  # Read metadata and hash.
        logger.debug("Read %s source Markdown files from %s", len(parts), root.name)  # Report root inventory count.
        return parts

    def _is_source_markdown(self, path: Path) -> bool:
        is_report = path.name.startswith("_")  # Exclude harvester reports and manifests from source parts.
        return not is_report  # Only document Markdown files enter the factory inventory.

    def _read_part(
        self,
        root: SourceRoot,
        path: Path,
        catalog: dict[str, dict[str, str]],
        statuses: dict[str, str],
    ) -> MarkdownPart:
        logger.info("Reading Markdown part %s", path)  # Log each file read before disk access.
        raw = path.read_bytes()  # Read bytes once so the content hash sees exact converter output.
        text = raw.decode("utf-8", errors="ignore")  # Decode with replacement so inventory survives bad glyphs.
        front_matter = self.parser.parse_text(text)  # Extract authoritative converter metadata when it exists.
        relative_path = path.relative_to(root.path).as_posix()  # Store a stable path independent of Windows separators.
        catalog_row = self._catalog_for(relative_path, front_matter, catalog)  # Attach harvester metadata.
        status = self._status_for(front_matter, catalog_row, statuses)  # Attach manifest review or ok status.
        body = self.parser.body_text(text)  # Measure skill input yield without front matter.
        part_key = f"{root.name}:{relative_path}"  # Use root and path as a natural physical key.
        content_hash = hashlib.sha256(raw).hexdigest()  # Detect reconverted files by exact content hash.
        part = MarkdownPart(
            part_key, root, path, relative_path, content_hash, len(raw), len(body), front_matter, catalog_row, status
        )  # Build the immutable record.
        logger.debug("Read part %s with %s bytes", part.part_key, part.size_bytes)  # Report the file size.
        return part

    def _catalog_for(
        self,
        relative_path: str,
        front_matter: dict[str, str],
        catalog: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        keys = [relative_path, front_matter.get("source_file", ""), Path(relative_path).stem]  # Try precise keys first.
        for key in keys:  # Return the first catalog row that matches the part.
            row = catalog.get(TextKey.normalize(key)) if key else None  # Normalize the candidate key.
            if row:  # Stop when authoritative metadata exists.
                return row
        return {}

    def _status_for(self, front_matter: dict[str, str], catalog_row: dict[str, str], statuses: dict[str, str]) -> str:
        keys = [catalog_row.get("source_pdf", ""), front_matter.get("source_file", "")]  # Match manifest source keys.
        for key in keys:  # Prefer catalog source because it is authored by the harvester.
            status = statuses.get(TextKey.normalize(key)) if key else None  # Look up normalized manifest status.
            if status:  # Return the authoritative manifest status.
                return status
        return "unknown"


class PartSetGrouper:
    """Group physical parts that belong to one source document."""

    _hash_suffix = re.compile(r"-[0-9a-f]{8,16}(?:-\d+)?$", re.IGNORECASE)  # Detect converter hash suffixes.
    _number_suffix = re.compile(r"-\d+$")  # Detect numbered split suffixes.
    _part_name = re.compile(r"^part-\d+$", re.IGNORECASE)  # Detect part folders from large PDFs.

    def group(self, parts: list[MarkdownPart]) -> list[DocumentGroup]:
        """Run the group operation."""
        logger.info("Grouping %s Markdown parts into documents", len(parts))  # Log grouping before computation.
        buckets: dict[str, list[MarkdownPart]] = defaultdict(list)  # Store part sets by measured grouping key.
        methods: dict[str, str] = {}  # Preserve the evidence type used for each group.
        for part in parts:  # Assign each part with the contract priority order.
            key, method = self._initial_part_key(part)  # First use exact source_file evidence.
            buckets[key].append(part)  # Add the physical part to its logical document bucket.
            methods[key] = method  # Record why this part joined the bucket.
        groups = self._merge_split_groups(buckets, methods)  # Merge measured split sets after exact grouping.
        logger.debug(
            "Grouped parts into %s document candidates", len(groups)
        )  # Report logical count before duplicates.
        return groups

    def _initial_part_key(self, part: MarkdownPart) -> tuple[str, str]:
        source_file = part.front_matter.get(
            "source_file", ""
        )  # Contract says shared source_file is strongest evidence.
        if source_file:  # Use exact source_file before weaker title or filename evidence.
            return f"{part.root.name}:source:{TextKey.normalize(source_file)}", "source_file"
        title = part.front_matter.get("title", "")  # Title is the second contract signal for split outputs.
        if title:  # Use title when no source file exists.
            return f"{part.root.name}:title:{TextKey.normalize(title)}", "title"
        stem = self._similar_stem(part.path)  # Fall back to suffix-aware filename similarity.
        return f"{part.root.name}:stem:{part.path.parent.as_posix()}:{stem}", "filename"

    def _merge_split_groups(
        self, buckets: dict[str, list[MarkdownPart]], methods: dict[str, str]
    ) -> list[DocumentGroup]:
        groups = [self._make_group(key, value, methods[key]) for key, value in buckets.items()]  # Build exact groups.
        split_keys = self._split_keys(groups)  # Find merge keys with measured split evidence.
        merged: dict[str, list[MarkdownPart]] = defaultdict(list)  # Store merged split part lists.
        group_methods: dict[str, str] = {}  # Preserve grouping evidence for merged groups.
        for group in groups:  # Merge only groups with measured split evidence.
            key = self._split_merge_key(group) if self._split_merge_key(group) in split_keys else group.group_key
            merged[key].extend(group.parts)  # Add exact group parts to the final group.
            group_methods[key] = "title" if key in split_keys else group.group_method  # State split merge evidence.
        return [self._make_group(key, value, group_methods[key]) for key, value in merged.items()]

    def _split_keys(self, groups: list[DocumentGroup]) -> set[str]:
        counts: dict[str, int] = defaultdict(int)  # Count exact groups that share a split merge key.
        candidates: set[str] = set()  # Track keys that have explicit split evidence.
        for group in groups:  # Inspect all exact groups before a possible merge.
            key = self._split_merge_key(group)  # Build the same merge key for candidates and base files.
            counts[key] += 1  # Count all exact groups so a base file can join a numbered part.
            if self._is_split_candidate(group):  # Use only explicit split evidence to form merge sets.
                candidates.add(key)  # Mark the key eligible only when a measured split signal exists.
        return {key for key in candidates if counts[key] > 1}  # Merge only when more than one group participates.

    def _split_merge_key(self, group: DocumentGroup) -> str:
        first = group.parts[0]  # Use the first part because exact groups already share source evidence.
        title = TextKey.normalize(group.title)  # Title is the contract's second split signal.
        source = first.front_matter.get("source_file") or first.relative_path  # Use source name when available.
        stem = self._number_stem(Path(source))  # Remove only numeric part suffixes across source_file groups.
        return (
            f"{group.root.name}:split:{title}:{group.pages}:{stem}"  # Keep split merges inside one root and page count.
        )

    def _is_split_candidate(self, group: DocumentGroup) -> bool:
        return any(self._part_has_split_signal(part) for part in group.parts)  # Merge only measured split candidates.

    def _part_has_split_signal(self, part: MarkdownPart) -> bool:
        source = part.front_matter.get("source_file", "")  # Read the converter source file signal.
        has_part_field = bool(part.front_matter.get("part"))  # Part front matter is direct split evidence.
        return has_part_field or self._has_number_suffix(source) or self._has_number_suffix(part.relative_path)

    def _make_group(self, key: str, parts: list[MarkdownPart], method: str) -> DocumentGroup:
        first = parts[0]  # Use the first part as a deterministic source for shared metadata.
        title = self._title_for(parts)  # Prefer front matter title, then catalog title, then file stem.
        category = first.catalog.get("category") or self._category_for(first)  # Use catalog category when available.
        source_pdf = self._source_pdf_for(parts)  # Preserve source PDF evidence for duplicate detection.
        pages = self._pages_for(parts)  # Sum or read page count from authoritative metadata.
        status = self._status_for(parts)  # Keep review status if any part requires review.
        return DocumentGroup(key, title, category, source_pdf, first.root, parts, pages, status, method)

    def _title_for(self, parts: list[MarkdownPart]) -> str:
        for part in parts:  # Prefer a converter title because it ties split parts together.
            title = part.front_matter.get("title") or part.catalog.get(
                "title"
            )  # Read both authoritative metadata sources.
            if title:  # Return the first useful title.
                return title
        return parts[0].path.stem.replace("-", " ").title()  # Fall back to a readable file stem.

    def _category_for(self, part: MarkdownPart) -> str:
        return (
            Path(part.relative_path).parts[0] if Path(part.relative_path).parts else "uncategorized"
        )  # Use first folder.

    def _source_pdf_for(self, parts: list[MarkdownPart]) -> str:
        for part in parts:  # Prefer catalog source PDF for the document.
            source_pdf = part.catalog.get("source_pdf") or part.front_matter.get(
                "source_file"
            )  # Use measured metadata.
            if source_pdf:  # Return the first available source name.
                return source_pdf
        return parts[0].relative_path  # Use the Markdown path when no PDF evidence exists.

    def _pages_for(self, parts: list[MarkdownPart]) -> int:
        values = [
            self._int_value(part.catalog.get("pages") or part.front_matter.get("pages")) for part in parts
        ]  # Read pages.
        return max(values) if values else 0  # Split parts often repeat the source page count.

    def _status_for(self, parts: list[MarkdownPart]) -> str:
        return "review" if any(part.manifest_status == "review" for part in parts) else "ok"  # Review wins over ok.

    def _int_value(self, value: str | None) -> int:
        try:
            return int(value or "0")  # Convert missing metadata into zero pages.
        except ValueError:
            return 0  # Keep inventory running when a converter writes a bad page value.

    def _similar_stem(self, path: Path) -> str:
        stem = path.stem  # Start with the file name without extension.
        stem = self._hash_suffix.sub("", stem)  # Remove converter hash suffixes that identify split parts.
        stem = self._number_suffix.sub("", stem)  # Remove numeric suffixes such as "-2".
        return "part-set" if self._part_name.match(path.stem) else TextKey.normalize(stem)  # Normalize the final key.

    def _number_stem(self, path: Path) -> str:
        stem = self._number_suffix.sub("", path.stem)  # Remove only numeric suffixes from exact source_file groups.
        return TextKey.normalize(stem)  # Keep hash suffixes so editions do not merge as parts.

    def _has_split_suffix(self, value: str) -> bool:
        stem = Path(value).stem  # Inspect only the source file name.
        return bool(self._hash_suffix.search(stem) or self._number_suffix.search(stem))  # Detect a split-like suffix.

    def _has_number_suffix(self, value: str) -> bool:
        stem = Path(value).stem  # Inspect only the source or Markdown file name.
        return bool(self._number_suffix.search(stem))  # Numeric suffixes are strong split evidence.


class DuplicateResolver:
    """Select one winner when several roots hold the same logical document."""

    def resolve(self, groups: list[DocumentGroup]) -> list[DuplicateDecision]:
        """Run the resolve operation."""
        logger.info("Resolving duplicates across %s document candidates", len(groups))  # Log duplicate pass start.
        buckets: dict[str, list[DocumentGroup]] = defaultdict(list)  # Group documents by natural document key.
        for group in groups:  # Add every candidate to a duplicate bucket.
            buckets[self._canonical_key(group)].append(group)  # Use source PDF or title as duplicate evidence.
        decisions = [self._decide(key, value) for key, value in buckets.items()]  # Pick a winner for each bucket.
        logger.debug("Duplicate resolver produced %s logical documents", len(decisions))  # Report final document count.
        return decisions

    def _canonical_key(self, group: DocumentGroup) -> str:
        source_key = TextKey.normalize(group.source_pdf) if group.source_pdf else ""  # Prefer source PDF across roots.
        title_key = TextKey.normalize(group.title) if group.title else ""  # Use title when the source PDF is absent.
        return source_key or title_key or TextKey.normalize(group.group_key)  # Ensure every group has a stable key.

    def _decide(self, canonical_key: str, groups: list[DocumentGroup]) -> DuplicateDecision:
        logger.info("Selecting duplicate winner for %s", canonical_key)  # Log the natural document key.
        winner = max(groups, key=self._winner_score)  # Apply the stated, testable duplicate winner rule.
        losers = [group for group in groups if group is not winner]  # Keep loser records for audit and re-run safety.
        self._mark_groups(canonical_key, winner, losers)  # Write winner state back to group records.
        logger.debug("Duplicate key %s has %s losers", canonical_key, len(losers))  # Report duplicate loser count.
        return DuplicateDecision(canonical_key, winner, losers)

    def _winner_score(self, group: DocumentGroup) -> tuple[int, int, int]:
        return (
            group.root.rank,
            group.front_matter_parts,
            group.text_chars,
        )  # Prefer newest, metadata, then text yield.

    def _mark_groups(self, canonical_key: str, winner: DocumentGroup, losers: list[DocumentGroup]) -> None:
        winner.group_key = canonical_key  # Store the natural key used by the rest of the factory.
        winner.is_winner = True  # Mark the selected conversion as active.
        for loser in losers:  # Preserve every duplicate loser for the report and database.
            loser.duplicate_of = canonical_key  # Link the loser to the selected document.
            loser.is_winner = False  # Mark the conversion as inactive.


class VersionedFamilyResolver:
    """Mark current and superseded product guide versions."""

    _version_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:Release\s+|Version\s+)?"
        r"(?P<version>\d+(?:\.(?:\d+|x))+(?:[A-Z]\d+)*(?:-[A-Z]\d+)?)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )  # Match full release tokens such as 13.2X51-D20, 21.4R3, and 2.0.x.
    _brand_pattern = re.compile(r"\b(?:Juniper|HPE Networking)\b", re.IGNORECASE)  # Remove brands from product keys.

    def resolve(self, groups: list[DocumentGroup]) -> list[VersionedFamily]:
        """Run the resolve operation."""
        logger.info("Resolving versioned product guide families")  # Log the version pass before mutation.
        buckets: dict[str, list[DocumentGroup]] = defaultdict(list)  # Store versioned documents by product family.
        for group in groups:  # Clear stale state before this resolver writes the only version decision.
            self._mark_unversioned(group)  # Prevent an earlier run from leaving a second current member.
        for group in groups:  # Inspect every active logical document.
            family_key = self._family_key(group.title)  # Remove versions and normalize product names.
            if family_key:  # Ignore documents with no parseable version.
                buckets[family_key].append(group)  # Add the versioned document to its family.
        families = [self._mark_family(key, value) for key, value in buckets.items() if len(value) > 1]  # Mark repeats.
        VersionFamilyInvariantValidator().validate_groups(groups)  # Fail if any marked family lacks one current.
        logger.debug("Resolved %s versioned families", len(families))  # Report family count.
        return sorted(families, key=lambda family: family.superseded_pages, reverse=True)  # Rank by page saving.

    def _family_key(self, title: str) -> str:
        if not self._version_pattern.search(title):  # A family needs a version value in the title.
            return ""
        if "apstra" in title.lower() and "user guide" in title.lower():  # Handle the HPE Apstra rebrand safely.
            return "apstra-user-guide"
        without_version = self._version_pattern.sub("", title)  # Remove every version token from the product name.
        without_brand = self._brand_pattern.sub(
            "", without_version
        )  # Remove vendor brand words that changed by rebrand.
        return TextKey.normalize(without_brand)  # Normalize remaining product words into a family key.

    def _mark_family(self, family_key: str, groups: list[DocumentGroup]) -> VersionedFamily:
        logger.info("Marking version family %s", family_key)  # Log family mutation before status writes.
        current = max(groups, key=self._current_score)  # Use version, recency, pages, and yield to break ties.
        for group in groups:  # Assign a version status to each family member.
            self._mark_group(group, family_key, current)  # Store current or superseded state on the document.
        superseded = [group for group in groups if group is not current]  # Gather older documents for page savings.
        pages = sum(group.pages for group in superseded)  # Measure skipped topic-build pages.
        logger.debug("Version family %s saves %s pages", family_key, pages)  # Report the measured page saving.
        return VersionedFamily(family_key, current.title, current.version_value, len(groups), len(superseded), pages)

    def _mark_group(self, group: DocumentGroup, family_key: str, current: DocumentGroup) -> None:
        group.version_family_key = family_key  # Preserve the product family for reporting and queries.
        group.version_value = self._version_value(group.title)  # Store the newest version named in the title.
        group.version_status = "current" if group is current else "superseded"  # Mark the newest version current.
        group.build_topics = group is current  # Skip topic generation for superseded documents.

    def _mark_unversioned(self, group: DocumentGroup) -> None:
        group.version_family_key = ""  # Clear the family key so stale grouping cannot survive a re-run.
        group.version_value = ""  # Clear the version value because an ungrouped document has no family version.
        group.version_status = "unversioned"  # Restore the default status before the resolver measures families.
        group.build_topics = True  # Keep non-versioned documents eligible for topic generation.

    def _current_score(self, group: DocumentGroup) -> tuple[tuple[int, ...], int, int, int, str]:
        return (
            self._version_tuple(group),
            group.root.rank,
            group.pages,
            group.text_chars,
            TextKey.normalize(group.title),
        )  # Break ties deterministically so one family cannot keep two current members.

    def _version_tuple(self, group: DocumentGroup) -> tuple[int, ...]:
        return self._version_parts(self._version_value(group.title))  # Compare releases with numeric fields only.

    def _version_value(self, title: str) -> str:
        versions = [match.group("version") for match in self._version_pattern.finditer(title)]  # Parse all versions.
        winner = max(versions, key=self._version_parts) if versions else "0"  # A multi-version title uses the newest.
        return winner  # Return a string that is human-readable in the report.

    def _version_parts(self, version: str) -> tuple[int, ...]:
        values = [int(part) for part in re.findall(r"\d+", version)]  # Build numeric fields across R, X, and D tags.
        return tuple(values) if values else (0,)  # Keep malformed input lower than any measured version.


class VersionFamilyInvariantValidator:
    """Validate that each measured version family has exactly one current member."""

    def validate_groups(self, groups: list[DocumentGroup]) -> VersionFamilyValidationResult:
        """Validate the validate groups requirement."""
        logger.info("Validating version family invariants from groups")  # Log the in-memory validation start.
        counts = self._group_counts(groups)  # Count current members for each non-empty version family key.
        candidates = self._candidate_family_count(groups)  # Count families that the parser can derive from titles.
        result = self._validate_counts(len(groups), counts, candidates)  # Raise when candidate families vanish.
        logger.debug("Validated %s version families from groups", result.families_checked)  # Report scope.
        return result

    def validate_database(self, connection: sqlite3.Connection) -> VersionFamilyValidationResult:
        """Validate the validate database requirement."""
        logger.info("Validating version family invariants from the database")  # Log the database validation start.
        document_count = self._database_document_count(connection)  # Measure table scope before family counts.
        candidates = self._database_candidate_family_count(connection)  # Count title-derived version families.
        rows = connection.execute(self._database_query()).fetchall()  # Read one count row for each version family.
        counts = {str(row[0]): int(row[1]) for row in rows}  # Convert SQLite rows into the shared count mapping.
        result = self._validate_counts(document_count, counts, candidates)  # Raise when candidate families vanish.
        logger.debug("Validated %s version families from the database", result.families_checked)  # Report scope.
        return result

    def _group_counts(self, groups: list[DocumentGroup]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)  # Store current counts by measured version family.
        for group in groups:  # Inspect each logical document after version resolution.
            if group.version_family_key:  # Ignore unversioned documents because they are not family members.
                counts[group.version_family_key] += 1 if group.version_status == "current" else 0  # Count current.
        return counts

    def _candidate_family_count(self, groups: list[DocumentGroup]) -> int:
        counts: dict[str, int] = defaultdict(int)  # Count parser-derived family keys before stored markings.
        for group in groups:  # Inspect titles so stale version columns cannot hide candidate families.
            key = VersionedFamilyResolver()._family_key(group.title)  # Reuse the production family parser.
            counts[key] += 1 if key else 0  # Count only documents that carry a recognized version token.
        return sum(1 for value in counts.values() if value > 1)  # A family needs at least two versioned members.

    def _database_query(self) -> str:
        return """
            SELECT version_family_key, SUM(CASE WHEN version_status = 'current' THEN 1 ELSE 0 END)
            FROM source_document
            WHERE version_family_key <> ''
            GROUP BY version_family_key
            """  # Return one current-member count for each persisted family.

    def _database_document_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) FROM source_document").fetchone()  # Fail if the table is missing.
        return int(row[0])  # Return the measured document count for failure messages.

    def _database_candidate_family_count(self, connection: sqlite3.Connection) -> int:
        rows = connection.execute("SELECT title FROM source_document").fetchall()  # Fail if the title column is absent.
        groups = [self._title_group(str(row[0])) for row in rows]  # Reuse in-memory candidate family detection.
        return self._candidate_family_count(groups)  # Return repeated version-family candidates.

    def _title_group(self, title: str) -> DocumentGroup:
        root = SourceRoot("validator", Path(), 0)  # Build a minimal root for title-only validation.
        part = MarkdownPart(title, root, Path(), "", "", 0, 0, {}, {}, "ok")  # Build a minimal part for the model.
        return DocumentGroup(title, title, "", "", root, [part], 0, "", "")  # Return a title-only document group.

    def _validate_counts(
        self, document_count: int, counts: dict[str, int], candidate_family_count: int
    ) -> VersionFamilyValidationResult:
        families_checked = len(counts)  # Measure validation scope for guard proof output.
        multi_current = sum(1 for value in counts.values() if value > 1)  # Count families with contradictory current.
        zero_current = sum(1 for value in counts.values() if value == 0)  # Count families with no buildable current.
        result = VersionFamilyValidationResult(
            document_count, families_checked, multi_current, zero_current
        )  # Build report data.
        no_family_in_populated_set = (
            candidate_family_count > 0 and families_checked == 0
        )  # Candidate families must mark.
        if no_family_in_populated_set or multi_current or zero_current:  # Fail only invalid populated measurements.
            raise ValueError(self._failure_message(result))  # Stop the pipeline before it builds bad topics.
        return result

    def _failure_message(self, result: VersionFamilyValidationResult) -> str:
        return (
            "Version family invariant failed: checked "
            f"{result.documents_checked} documents and {result.families_checked} families, "
            f"found {result.multi_current_families} "
            f"with multiple current members and {result.zero_current_families} with zero current members."
        )  # Report the document count, family count, and both failure modes.


class SourceDocumentVersionUpdater:
    """Refresh version family state for an existing inventory database."""

    def __init__(self, db_path: Path) -> None:
        """Initialize the SourceDocumentVersionUpdater instance."""
        self.db_path = db_path  # Store the live factory database path for in-place updates.

    def refresh(self) -> tuple[list[VersionedFamily], VersionFamilyValidationResult]:
        """Run the refresh operation."""
        logger.info("Refreshing source document version state in %s", self.db_path)  # Log before opening SQLite.
        with sqlite3.connect(self.db_path) as connection:  # Use one transaction for all status changes.
            rows = self._document_rows(connection)  # Read current source document rows without changing schema.
            groups = self._groups(rows)  # Convert rows into resolver input records.
            families = VersionedFamilyResolver().resolve(groups)  # Recompute family status with current parsing.
            self._write_groups(connection, groups)  # Persist only version columns and topic build flags.
            self._ensure_current_index(connection)  # Enforce the one-current invariant at SQLite level.
            result = VersionFamilyInvariantValidator().validate_database(connection)  # Prove the persisted state.
        logger.debug("Refreshed %s version families in %s", len(families), self.db_path)  # Report family count.
        return families, result

    def _document_rows(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        connection.row_factory = sqlite3.Row  # Return named rows so the loader is explicit and stable.
        rows = connection.execute("SELECT * FROM source_document ORDER BY document_key").fetchall()  # Read documents.
        logger.debug("Read %s source document rows for version refresh", len(rows))  # Report refresh input size.
        return rows

    def _groups(self, rows: list[sqlite3.Row]) -> list[DocumentGroup]:
        root = SourceRoot("database", self.db_path.parent, 0)  # Use a synthetic root because rows are already chosen.
        return [self._group(root, row) for row in rows]  # Build resolver records for every persisted document.

    def _group(self, root: SourceRoot, row: sqlite3.Row) -> DocumentGroup:
        part = MarkdownPart(
            str(row["document_key"]), root, root.path, "", "", 0, int(row["text_chars"]), {}, {}, "ok"
        )  # Build one synthetic part so tie-breaking can use persisted text yield.
        return DocumentGroup(
            str(row["document_key"]),
            str(row["title"]),
            str(row["category"]),
            str(row["source_pdf"]),
            root,
            [part],
            int(row["pages"]),
            str(row["status"]),
            str(row["group_method"]),
        )  # Rebuild only fields needed for version resolution and reporting.

    def _write_groups(self, connection: sqlite3.Connection, groups: list[DocumentGroup]) -> None:
        logger.info("Writing refreshed version state for %s documents", len(groups))  # Log before in-place update.
        for group in groups:  # Update all rows so stale version data is removed from ungrouped documents.
            connection.execute(self._update_statement(), self._update_values(group))  # Persist the resolved state.
            self._write_work_item(connection, group)  # Keep the queue aligned with the refreshed topic-build flag.
        logger.debug("Wrote refreshed version state for %s documents", len(groups))  # Report updated row count.

    def _update_statement(self) -> str:
        return """
            UPDATE source_document
            SET version_family_key = ?, version_value = ?, version_status = ?, build_topics = ?
            WHERE document_key = ?
            """  # Limit in-place updates to version resolver output columns.

    def _update_values(self, group: DocumentGroup) -> tuple[object, ...]:
        return (
            group.version_family_key,
            group.version_value,
            group.version_status,
            1 if group.build_topics else 0,
            group.group_key,
        )  # Return ordered SQL values for one document update.

    def _write_work_item(self, connection: sqlite3.Connection, group: DocumentGroup) -> None:
        if not self._has_work_item_table(connection):  # Unit tests can validate source_document without a queue.
            return
        if not group.build_topics:  # Superseded rows must not enter the topic builder queue.
            self._set_work_item(connection, group.group_key, "superseded", "superseded version")  # Block old releases.
            return
        self._restore_superseded_work_item(connection, group.group_key)  # Requeue a row that became current.

    def _has_work_item_table(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'work_item'"
        ).fetchone()  # Check for the optional queue table without mutating the schema.
        return row is not None  # Return whether queue alignment is possible in this database.

    def _set_work_item(self, connection: sqlite3.Connection, key: str, status: str, reason: str) -> None:
        connection.execute(
            "UPDATE work_item SET status = ?, reason = ?, updated_at = CURRENT_TIMESTAMP WHERE document_key = ?",
            (status, reason, key),
        )  # Update the existing queue row while preserving its priority.

    def _restore_superseded_work_item(self, connection: sqlite3.Connection, key: str) -> None:
        connection.execute(
            "UPDATE work_item SET status = 'pending', reason = 'version became current', "
            "updated_at = CURRENT_TIMESTAMP WHERE document_key = ? AND status = 'superseded'",
            (key,),
        )  # Requeue rows that a corrected family decision makes buildable.

    def _ensure_current_index(self, connection: sqlite3.Connection) -> None:
        logger.info("Creating one-current partial index for version families")  # Log before structural guard setup.
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS source_document_one_current_per_version_family
            ON source_document(version_family_key)
            WHERE version_family_key <> '' AND version_status = 'current'
            """)  # Let SQLite reject two current rows in the same family.
        logger.debug("One-current partial index is present")  # Report structural guard completion.


class EditionFamilyAnalyzer:
    """Find same-stem hash families that can be different editions."""

    _hash_suffix = re.compile(r"-[0-9a-f]{8,16}$", re.IGNORECASE)  # Detect source names with converter hashes.

    def find(self, groups: list[DocumentGroup]) -> list[EditionFamily]:
        """Run the find operation."""
        logger.info("Analyzing source file edition families")  # Log the edition check before grouping.
        buckets: dict[str, list[DocumentGroup]] = defaultdict(list)  # Store same-stem hash families.
        for group in groups:  # Inspect each winning logical document.
            key = self._family_key(group)  # Build a same-stem key only for hashed source names.
            if key:  # Ignore unhashable source names because they are not the user-reported pattern.
                buckets[key].append(group)  # Add this document to a possible edition family.
        families = [self._make_family(key, value) for key, value in buckets.items() if len(value) > 1]  # Keep repeats.
        logger.debug("Found %s source file edition families", len(families))  # Report family count.
        return sorted(families, key=lambda item: item.document_count, reverse=True)  # Show the largest families first.

    def _family_key(self, group: DocumentGroup) -> str:
        source_path = Path(group.source_pdf)  # Parse the source file with pathlib for safe path handling.
        stem = source_path.stem  # Compare the file stem, not the extension.
        base = self._hash_suffix.sub("", stem)  # Remove a trailing hash to find same-name families.
        if base == stem:  # Return no key when no hash suffix exists.
            return ""
        parent = source_path.parent.as_posix()  # Keep the source category in the family key.
        return TextKey.normalize(f"{parent}/{base}")  # Normalize the family key for stable reports.

    def _make_family(self, key: str, groups: list[DocumentGroup]) -> EditionFamily:
        titles = {TextKey.normalize(group.title) for group in groups}  # Count distinct titles in the family.
        pages = sorted({group.pages for group in groups})  # Compare page counts to identify likely editions.
        sources = sorted({group.source_pdf for group in groups})  # Preserve the source files that formed the family.
        return EditionFamily(key, len(groups), len(titles), pages, sources)  # Return measured family evidence.


class PriorityScorer:
    """Score documents for conversion priority with tunable weights."""

    CATEGORY_WEIGHTS = {  # Give teaching documents higher queue priority than marketing documents.
        "cli-reference": 400,
        "guides": 320,
        "configuration-guides": 300,
        "api": 280,
        "flyers": 20,
    }

    STATUS_WEIGHTS = {"ok": 80, "review": -120, "unknown": 0}  # Prefer documents that passed harvester review.

    def score(self, group: DocumentGroup) -> int:
        """Run the score operation."""
        logger.info("Scoring priority for %s", group.group_key)  # Log scoring before applying the rule.
        score = (
            self._category_score(group)
            + self._page_score(group)
            + self._status_score(group)
            + self._recency_score(group)
        )
        logger.debug("Priority score for %s is %s", group.group_key, score)  # Report the deterministic score.
        return score

    def _category_score(self, group: DocumentGroup) -> int:
        return self.CATEGORY_WEIGHTS.get(group.category, 100)  # Prefer documents that teach commands and operations.

    def _page_score(self, group: DocumentGroup) -> int:
        return min(group.pages, 2500) // 5  # Reward large teaching documents without letting one PDF dominate.

    def _status_score(self, group: DocumentGroup) -> int:
        return self.STATUS_WEIGHTS.get(group.status, 0)  # Prefer ok output and lower review output.

    def _recency_score(self, group: DocumentGroup) -> int:
        return group.root.rank * 25  # Prefer the newest conversion root as a recency signal.


class InventoryDatabase:
    """Persist the source document inventory and work queue in SQLite."""

    def __init__(self, db_path: Path) -> None:
        """Initialize the InventoryDatabase instance."""
        self.db_path = db_path  # Store the factory database path from the contract.

    def write(self, decisions: list[DuplicateDecision]) -> None:
        """Run the write operation."""
        logger.info("Writing inventory database to %s", self.db_path)  # Log persistence before file operations.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)  # Create the factory data folder if it is missing.
        with sqlite3.connect(self.db_path) as connection:  # Use a transaction so a crash loses at most one commit.
            self._create_schema(connection)  # Ensure the natural-key schema exists before upserts.
            changed = self._changed_parts(connection, decisions)  # Detect reconverted files before replacing hashes.
            self._write_documents(connection, decisions)  # Upsert document and part records.
            self._write_work_items(connection, decisions, changed)  # Queue changed documents for conversion.
            self._enforce_version_invariants(connection)  # Stop bad version state before topic generation can run.
        logger.debug(
            "Inventory database write finished with %s changed parts", len(changed)
        )  # Report requeue signal count.

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        logger.info("Creating inventory database schema")  # Log schema creation before DDL.
        for statement in self._schema_statements():  # Create each table with a bounded statement.
            connection.execute(statement)  # Execute DDL inside the same inventory transaction.
        self._ensure_document_columns(connection)  # Add version columns for an existing incremental database.
        logger.debug("Inventory database schema is ready")  # Report DDL completion.

    def _schema_statements(self) -> list[str]:
        return [
            self._document_schema(),
            self._part_schema(),
            self._work_item_schema(),
        ]  # Return ordered DDL statements.

    def _document_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS source_document (
                document_key TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                source_pdf TEXT NOT NULL, root_name TEXT NOT NULL, pages INTEGER NOT NULL,
                status TEXT NOT NULL, group_method TEXT NOT NULL, part_count INTEGER NOT NULL,
                text_chars INTEGER NOT NULL, priority INTEGER NOT NULL, duplicate_losers INTEGER NOT NULL,
                version_family_key TEXT NOT NULL DEFAULT '', version_value TEXT NOT NULL DEFAULT '',
                version_status TEXT NOT NULL DEFAULT 'unversioned', build_topics INTEGER NOT NULL DEFAULT 1
            )
            """  # Store one row for each logical document after duplicate resolution.

    def _part_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS source_part (
                part_key TEXT PRIMARY KEY, document_key TEXT NOT NULL, root_name TEXT NOT NULL,
                relative_path TEXT NOT NULL, content_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                text_chars INTEGER NOT NULL, has_front_matter INTEGER NOT NULL, is_winner INTEGER NOT NULL,
                duplicate_of TEXT NOT NULL, FOREIGN KEY(document_key) REFERENCES source_document(document_key)
            )
            """  # Store one row for each physical Markdown file.

    def _work_item_schema(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS work_item (
                document_key TEXT PRIMARY KEY, status TEXT NOT NULL, priority INTEGER NOT NULL,
                reason TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(document_key) REFERENCES source_document(document_key)
            )
            """  # Store one queue row for each logical document.

    def _ensure_document_columns(self, connection: sqlite3.Connection) -> None:
        logger.info("Checking source document version columns")  # Log schema migration before inspection.
        rows = connection.execute("PRAGMA table_info(source_document)").fetchall()  # Read the current table columns.
        existing = {str(row[1]) for row in rows}  # Build a set for constant-time migration checks.
        for name, definition in self._document_column_additions().items():  # Add each missing incremental column.
            if name not in existing:  # SQLite needs one ALTER statement for each missing column.
                connection.execute(definition)  # Add the missing column with a safe default.
        logger.debug("Source document table has %s columns", len(existing))  # Report inspected column count.

    def _document_column_additions(self) -> dict[str, str]:
        return {
            "version_family_key": "ALTER TABLE source_document ADD COLUMN version_family_key TEXT NOT NULL DEFAULT ''",
            "version_value": "ALTER TABLE source_document ADD COLUMN version_value TEXT NOT NULL DEFAULT ''",
            "version_status": (
                "ALTER TABLE source_document ADD COLUMN version_status TEXT NOT NULL DEFAULT 'unversioned'"
            ),
            "build_topics": "ALTER TABLE source_document ADD COLUMN build_topics INTEGER NOT NULL DEFAULT 1",
        }  # Keep migration statements near the document schema.

    def _changed_parts(self, connection: sqlite3.Connection, decisions: list[DuplicateDecision]) -> set[str]:
        logger.info("Checking existing part hashes for changes")  # Log incremental detection before reading state.
        rows = connection.execute("SELECT part_key, content_hash FROM source_part").fetchall()  # Read old hashes.
        old_hashes = {str(row[0]): str(row[1]) for row in rows}  # Build a comparison map by natural part key.
        changed = self._changed_document_keys(old_hashes, decisions)  # Convert changed parts to document keys.
        logger.debug("Detected %s changed documents from part hashes", len(changed))  # Report incremental queue count.
        return changed

    def _changed_document_keys(self, old_hashes: dict[str, str], decisions: list[DuplicateDecision]) -> set[str]:
        changed: set[str] = set()  # Store documents that need queue reset.
        for decision in decisions:  # Inspect only active logical documents.
            for part in decision.winner.parts:  # Compare each winning physical part hash.
                old_hash = old_hashes.get(part.part_key)  # Missing hash means a new input part exists.
                if old_hash != part.content_hash:  # Requeue new or changed converter output.
                    changed.add(decision.canonical_key)  # Queue the logical document, not one part.
        return changed

    def _write_documents(self, connection: sqlite3.Connection, decisions: list[DuplicateDecision]) -> None:
        logger.info("Upserting source document and part rows")  # Log data writes before executing SQL.
        for decision in decisions:  # Persist each logical document and its physical parts.
            self._upsert_document(connection, decision)  # Store one source_document row per logical document.
            self._upsert_parts(connection, decision)  # Store winner and loser part rows for audit.
        logger.debug("Upserted %s source documents", len(decisions))  # Report document row count.

    def _upsert_document(self, connection: sqlite3.Connection, decision: DuplicateDecision) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO source_document
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._document_values(decision),
        )  # Upsert by natural document key so repeated scans are incremental.

    def _document_values(self, decision: DuplicateDecision) -> tuple[object, ...]:
        group = decision.winner  # Use the selected duplicate winner as the active source document.
        return (
            decision.canonical_key,
            group.title,
            group.category,
            group.source_pdf,
            group.root.name,
            group.pages,
            group.status,
            group.group_method,
            group.part_count,
            group.text_chars,
            group.priority,
            len(decision.losers),
            group.version_family_key,
            group.version_value,
            group.version_status,
            1 if group.build_topics else 0,
        )  # Return all document columns in schema order.

    def _upsert_parts(self, connection: sqlite3.Connection, decision: DuplicateDecision) -> None:
        groups = [decision.winner, *decision.losers]  # Preserve duplicate losers without activating them.
        for group in groups:  # Write all physical files linked to the winner document.
            for part in group.parts:  # Persist one row for each Markdown file.
                self._upsert_part(connection, decision.canonical_key, group, part)  # Store part metadata and hash.

    def _upsert_part(self, connection: sqlite3.Connection, key: str, group: DocumentGroup, part: MarkdownPart) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO source_part
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                part.part_key,
                key,
                part.root.name,
                part.relative_path,
                part.content_hash,
                part.size_bytes,
                part.text_chars,
                1 if part.has_front_matter else 0,
                1 if group.is_winner else 0,
                group.duplicate_of,
            ),
        )  # Upsert by natural part key so reconverted content updates in place.

    def _write_work_items(
        self,
        connection: sqlite3.Connection,
        decisions: list[DuplicateDecision],
        changed: set[str],
    ) -> None:
        logger.info("Upserting priority work queue")  # Log queue writes before SQL execution.
        for decision in decisions:  # Ensure every logical document has one queue row.
            reason = self._queue_reason(decision, changed)  # Explain why the item is pending or preserved.
            status = self._queue_status(connection, decision, changed)  # Superseded items do not build topic files.
            connection.execute(
                """
                INSERT OR REPLACE INTO work_item (document_key, status, priority, reason, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (decision.canonical_key, status, decision.winner.priority, reason),
            )  # Use document key as the natural queue key.
        logger.debug("Upserted %s work items", len(decisions))  # Report queue size.

    def _existing_status(self, connection: sqlite3.Connection, decision: DuplicateDecision) -> str:
        row = connection.execute(
            "SELECT status FROM work_item WHERE document_key = ?", (decision.canonical_key,)
        ).fetchone()
        return str(row[0]) if row else "pending"  # New documents start pending.

    def _queue_status(self, connection: sqlite3.Connection, decision: DuplicateDecision, changed: set[str]) -> str:
        if not decision.winner.build_topics:  # Superseded documents stay citable but do not build topic files.
            return "superseded"
        if decision.canonical_key in changed:  # Changed source content must run again.
            return "pending"
        return self._existing_status(connection, decision)  # Preserve downstream progress when content did not change.

    def _queue_reason(self, decision: DuplicateDecision, changed: set[str]) -> str:
        if not decision.winner.build_topics:  # State why the pipeline skips this still-citable document.
            return "superseded version"
        return (
            "content changed" if decision.canonical_key in changed else "inventory refreshed"
        )  # State requeue reason.

    def _enforce_version_invariants(self, connection: sqlite3.Connection) -> None:
        logger.info("Enforcing version family invariants")  # Log before the database guard runs.
        SourceDocumentVersionUpdater(self.db_path)._ensure_current_index(connection)  # Add the SQLite uniqueness guard.
        VersionFamilyInvariantValidator().validate_database(connection)  # Fail when a family lacks exactly one current.
        logger.debug("Version family invariants passed")  # Report successful guard completion.


class InventoryReport:
    """Write a measured inventory report for operators."""

    def __init__(self, report_path: Path) -> None:
        """Initialize the InventoryReport instance."""
        self.report_path = report_path  # Store the report path under the factory data directory.

    def write(self, result: InventoryResult) -> None:
        """Run the write operation."""
        logger.info("Writing inventory report to %s", self.report_path)  # Log report write before disk access.
        self.report_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure the report directory exists.
        lines = self._lines(result)  # Build report lines from measured inventory values.
        self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # Persist the report as UTF-8 Markdown.
        logger.debug("Inventory report wrote %s lines", len(lines))  # Report output length.

    def _lines(self, result: InventoryResult) -> list[str]:
        lines = self._summary_lines(result)  # Start with measured top-level counts.
        lines.extend(self._confirmed_structure_lines())  # Add the user supplied full-corpus structure facts.
        lines.extend(self._priority_rule_lines())  # State the rule that builds queue priority.
        lines.extend(self._part_set_lines(result))  # Add measured split part set evidence.
        lines.extend(self._versioned_family_lines(result))  # Add current and superseded version evidence.
        lines.extend(self._edition_family_lines(result))  # Add same-stem edition evidence for hash families.
        lines.extend(self._duplicate_lines(result))  # Add measured duplicate resolution evidence.
        lines.extend(self._category_lines(result))  # Add category totals for planning.
        lines.extend(self._top_document_lines(result))  # Add the top 50 priority records.
        return lines

    def _versioned_family_lines(self, result: InventoryResult) -> list[str]:
        lines = [
            "",
            "## Versioned product families",
            "",
            "| Family | Current version | Current title | Documents | Superseded pages |",
            "| - | - | - | -: | -: |",
        ]  # Create the version family table header.
        for family in result.versioned_families:  # Report each measured version family.
            safe_title = family.current_title.replace("|", "\\|")  # Escape table separators in source titles.
            lines.append(
                f"| {family.family_key} | {family.current_version} | {safe_title} | "
                f"{family.document_count} | {family.superseded_pages} |"
            )
        return lines

    def _summary_lines(self, result: InventoryResult) -> list[str]:
        return [
            "# Juniper corpus inventory report",
            "",
            "## Summary",
            "",
            "| Measure | Value |",
            "| - | -: |",
            f"| Source roots scanned | {result.roots_scanned} |",
            f"| Physical Markdown parts | {result.physical_parts} |",
            f"| Logical documents | {result.logical_documents} |",
            f"| Split part sets | {result.part_sets} |",
            f"| Duplicate conversions removed | {result.duplicate_losers} |",
            f"| Superseded version documents | {result.superseded_documents} |",
            f"| Superseded pages skipped for topic builds | {result.superseded_pages} |",
        ]

    def _confirmed_structure_lines(self) -> list[str]:
        return [
            "",
            "## Confirmed full-corpus structure facts",
            "",
            "| Measure | Value |",
            "| - | -: |",
            "| Total Markdown files | 9931 |",
            "| Files with `part` front matter | 169 |",
            "| Files with fenced code | 8 |",
            "| Distinct `source_file` values | 5787 |",
            "| `source_file` values used more than once | 4009 |",
            "| Files inside repeated `source_file` sets | 8151 |",
        ]  # Record the full scan facts supplied for issue 2925.

    def _priority_rule_lines(self) -> list[str]:
        return [
            "",
            "## Priority rule",
            "",
            "Priority = category weight + page score + status score + recency score.",
            "The rule favors CLI reference, guide, configuration, and API documents.",
            "The rule lowers documents that need review and documents from older roots.",
        ]  # Explain the separate scorer class without repeating code.

    def _part_set_lines(self, result: InventoryResult) -> list[str]:
        lines = ["", "## Split part sets", "", "| Title | Root | Parts | Method |", "| - | - | -: | - |"]
        for group in result.part_set_details:  # Report every measured set and its part count.
            safe_title = group.title.replace("|", "\\|")  # Escape source titles for Markdown tables.
            lines.append(f"| {safe_title} | {group.root.name} | {group.part_count} | {group.group_method} |")
        return lines

    def _edition_family_lines(self, result: InventoryResult) -> list[str]:
        lines = [
            "",
            "## Same-stem hash families",
            "",
            "| Family | Documents | Titles | Page counts | Source files |",
            "| - | -: | -: | - | - |",
        ]  # Create the edition evidence table header.
        for family in result.edition_families[:50]:  # Report largest likely edition families for review.
            pages = ", ".join(str(value) for value in family.page_values)  # Show page evidence for edition decisions.
            sources = "<br>".join(family.source_files[:8])  # Show the source_file values that formed the family.
            lines.append(
                f"| {family.family_key} | {family.document_count} | {family.title_count} | {pages} | {sources} |"
            )
        return lines

    def _duplicate_lines(self, result: InventoryResult) -> list[str]:
        lines = ["", "## Duplicate conversions", "", "| Title | Winner root | Losers |", "| - | - | -: |"]
        for decision in result.duplicate_details:  # Report each logical document that had loser conversions.
            safe_title = decision.winner.title.replace("|", "\\|")  # Escape source titles for Markdown tables.
            lines.append(f"| {safe_title} | {decision.winner.root.name} | {len(decision.losers)} |")
        return lines

    def _category_lines(self, result: InventoryResult) -> list[str]:
        lines = [
            "",
            "## Category totals",
            "",
            "| Category | Documents |",
            "| - | -: |",
        ]  # Create category table header.
        for category, count in sorted(result.category_totals.items()):  # Sort for stable report diffs.
            lines.append(f"| {category} | {count} |")  # Add one measured category total.
        return lines

    def _top_document_lines(self, result: InventoryResult) -> list[str]:
        lines = [
            "",
            "## Top 50 priority documents",
            "",
            "| Rank | Priority | Title | Category | Pages | Parts |",
            "| -: | -: | - | - | -: | -: |",
        ]
        for index, group in enumerate(result.top_documents[:50], start=1):  # Include exactly the top 50 or fewer.
            safe_title = group.title.replace("|", "\\|")  # Escape table separators in source titles.
            lines.append(
                f"| {index} | {group.priority} | {safe_title} | {group.category} | {group.pages} | {group.part_count} |"
            )
        return lines


class InventoryBuilder:
    """Build the inventory database, work queue, and report."""

    def __init__(self, repo_root: Path, download_root: Path | None = None) -> None:
        """Initialize the InventoryBuilder instance."""
        self.repo_root = repo_root  # Store the worktree root that owns the factory database.
        self.download_root = download_root or Path.home() / "Downloads"  # Use the user download folder by default.
        self.roots = self._source_roots()  # Materialize the four locked source roots.
        self.metadata = HarvesterMetadataLoader(self.roots[0].path)  # Read metadata from juniper-harvest-md.
        self.scorer = PriorityScorer()  # Keep priority scoring separate and testable.

    def build(self) -> InventoryResult:
        """Run the build operation."""
        logger.info("Building Juniper corpus inventory")  # Log the high-level build action.
        parts = CorpusScanner(self.roots, self.metadata).scan()  # Scan all physical Markdown roots.
        groups = PartSetGrouper().group(parts)  # Group split files before duplicate detection.
        decisions = DuplicateResolver().resolve(groups)  # Pick one conversion per logical document.
        version_families = VersionedFamilyResolver().resolve(
            [decision.winner for decision in decisions]
        )  # Mark versions.
        self._score(decisions)  # Assign queue priority after duplicate resolution.
        InventoryDatabase(self.repo_root / "data" / "juniper_skills" / "factory.db").write(decisions)  # Persist state.
        result = self._result(parts, decisions, version_families)  # Build measured counts for reports and final output.
        InventoryReport(self.repo_root / "data" / "juniper_skills" / "inventory-report.md").write(
            result
        )  # Write report.
        logger.debug(
            "Inventory build finished with %s logical documents", result.logical_documents
        )  # Report final count.
        return result

    def _source_roots(self) -> list[SourceRoot]:
        archive = self.download_root / "juniper-doc-archives"  # Use the archive folder from the locked contract.
        return [
            SourceRoot("juniper-harvest-md", self.download_root / "juniper-harvest-md", 3),
            SourceRoot("archive-markdown", archive / "markdown", 1),
            SourceRoot("archive-markdown2", archive / "markdown2", 2),
            SourceRoot("juniper-corpus", self.repo_root / "data" / "juniper_corpus", 0),
        ]

    def _score(self, decisions: list[DuplicateDecision]) -> None:
        logger.info("Scoring %s logical documents", len(decisions))  # Log priority scoring start.
        for decision in decisions:  # Score only active winner records.
            decision.winner.priority = self.scorer.score(decision.winner)  # Store the tunable priority value.
        logger.debug("Scored %s logical documents", len(decisions))  # Report scored document count.

    def _result(
        self, parts: list[MarkdownPart], decisions: list[DuplicateDecision], version_families: list[VersionedFamily]
    ) -> InventoryResult:
        winners = [decision.winner for decision in decisions]  # Use winners for logical document totals.
        part_set_details = [group for group in winners if group.part_count > 1]  # Keep part set details for the report.
        superseded = [group for group in winners if group.version_status == "superseded"]  # Gather skipped documents.
        duplicate_details = [
            decision for decision in decisions if decision.losers
        ]  # Keep duplicate detail for the report.
        return InventoryResult(
            len(self.roots),
            len(parts),
            len(winners),
            len(part_set_details),
            sum(len(decision.losers) for decision in decisions),
            self._category_totals(winners),
            self._top_documents(winners),
            part_set_details,
            duplicate_details,
            EditionFamilyAnalyzer().find(winners),
            version_families,
            len(superseded),
            sum(group.pages for group in superseded),
        )

    def _top_documents(self, groups: list[DocumentGroup]) -> list[DocumentGroup]:
        buildable = [
            group for group in groups if group.build_topics
        ]  # Omit superseded documents from topic work ranking.
        return sorted(buildable, key=lambda group: group.priority, reverse=True)[
            :50
        ]  # Keep the highest priority documents.

    def _category_totals(self, groups: list[DocumentGroup]) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)  # Accumulate counts by selected category.
        for group in groups:  # Count each active logical document once.
            totals[group.category] += 1  # Increment the measured category total.
        return dict(totals)  # Return a plain dict for reporting and tests.
