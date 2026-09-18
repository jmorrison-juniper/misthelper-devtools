"""Assemble level 1 Juniper domain skill packages from document topic trees."""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

LIFECYCLE_TAGS = ("day0", "day1", "day2", "day2plus")
SKILL_HARD_LIMIT = 12_288
INDEX_HARD_LIMIT = 40_960
SOURCE_HARD_LIMIT = 40_960
SKILL_SOFT_LIMIT = 8_192
INDEX_SOFT_LIMIT = 20_480
SOURCE_SOFT_LIMIT = 20_480


@dataclass(frozen=True)
class DocumentPackageInput:
    """One converted document tree that belongs in a domain skill."""

    slug: str
    title: str
    category: str
    pages: int
    document_dir: Path
    markdown_paths: tuple[Path, ...]
    pdf_path: Path
    public_origin: str
    author: str = "Unknown"
    converted: str = ""


@dataclass(frozen=True)
class TopicRoute:
    """One topic route row for level 1 and level 2 indexes."""

    title: str
    subject: str
    lifecycle: tuple[str, ...]
    citation: str
    source_range: str
    relative_path: Path


@dataclass(frozen=True)
class RouteCluster:
    """One user-language route row for SKILL.md."""

    subject: str
    destination: str
    rank: int


@dataclass(frozen=True)
class PackageAssemblyResult:
    """Measured output from one domain assembly run."""

    package_dir: Path
    skill_size: int
    index_size: int
    sources_size: int
    topic_count: int
    document_count: int
    coverage: dict[str, int]


@dataclass(frozen=True)
class ValidationFinding:
    """One validator finding with the measured value that failed."""

    file: Path
    message: str
    measured: str
    severity: str = "error"


@dataclass(frozen=True)
class ValidationResult:
    """The full package validation result."""

    files_checked: int
    errors: tuple[ValidationFinding, ...]
    warnings: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """Run the passed operation."""
        return not self.errors and self.files_checked > 0


class CitationKeyAllocator:
    """Allocate stable, collision-free source keys inside one domain."""

    STOP_WORDS = {
        "A",
        "AN",
        "AND",
        "DAY",
        "FOR",
        "GUIDE",
        "LEARNING",
        "OF",
        "ONE",
        "THE",
        "TO",
        "USER",
    }

    def __init__(self, database_path: Path) -> None:
        """Initialize the CitationKeyAllocator instance."""
        self.database_path = database_path

    def allocate(self, domain: str, document_slug: str, title: str) -> str:
        """Run the allocate operation."""
        logger.info("Allocating a citation key for %s", document_slug)
        self._ensure_schema()
        existing = self._existing_key(domain, document_slug)
        if existing:
            logger.debug("Reused citation key %s for %s", existing, document_slug)
            return existing
        key = self._next_key(domain, title)
        self._insert_key(domain, document_slug, title, key)
        logger.debug("Allocated citation key %s for %s", key, document_slug)
        return key

    def _connect(self) -> sqlite3.Connection:
        logger.info("Opening the skill factory database")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        logger.debug("Opened the skill factory database at %s", self.database_path)
        return connection

    def _ensure_schema(self) -> None:
        logger.info("Ensuring the citation key table exists")
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS citation_keys ("
                "domain TEXT NOT NULL, document_slug TEXT NOT NULL, title TEXT NOT NULL, "
                "key TEXT NOT NULL, created_at TEXT NOT NULL, "
                "PRIMARY KEY (domain, document_slug), UNIQUE (domain, key))"
            )
        logger.debug("Citation key table is ready")

    def _existing_key(self, domain: str, document_slug: str) -> str:
        logger.info("Reading an existing citation key for %s", document_slug)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT key FROM citation_keys WHERE domain = ? AND document_slug = ?",
                (domain, document_slug),
            ).fetchone()
        key = str(row[0]) if row else ""
        logger.debug("Existing citation key lookup returned %s", bool(key))
        return key

    def _insert_key(self, domain: str, document_slug: str, title: str, key: str) -> None:
        logger.info("Persisting citation key %s for %s", key, document_slug)
        built = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO citation_keys (domain, document_slug, title, key, created_at) VALUES (?, ?, ?, ?, ?)",
                (domain, document_slug, title, key, built),
            )
        logger.debug("Persisted citation key %s for %s", key, document_slug)

    def _next_key(self, domain: str, title: str) -> str:
        logger.info("Building a candidate citation key for %s", title)
        candidate = self._candidate(title)
        used = self._used_keys(domain)
        key = self._dedupe(candidate, used)
        logger.debug("Selected citation key %s from candidate %s", key, candidate)
        return key

    def _candidate(self, title: str) -> str:
        words = re.findall(r"[A-Za-z0-9]+", title.upper())
        product = self._product_word(words)
        initials = "".join(word[0] for word in words if word not in self.STOP_WORDS and word != product)
        candidate = (product + initials)[:12]
        return candidate if len(candidate) >= 3 else (candidate + "DOC")[:3]

    def _product_word(self, words: list[str]) -> str:
        products = ("JUNOS", "MIST", "SRX", "APSTRA", "EX", "QFX", "MX", "PTX")
        for word in words:
            if word in products:
                return word
        return words[0][:6] if words else "DOC"

    def _used_keys(self, domain: str) -> set[str]:
        logger.info("Reading allocated citation keys for %s", domain)
        with self._connect() as connection:
            rows = connection.execute("SELECT key FROM citation_keys WHERE domain = ?", (domain,)).fetchall()
        keys = {str(row[0]) for row in rows}
        logger.debug("Read %s allocated citation keys for %s", len(keys), domain)
        return keys

    def _dedupe(self, candidate: str, used: set[str]) -> str:
        if candidate not in used:
            return candidate
        for number in range(2, 1000):
            suffix = str(number)
            key = f"{candidate[: 12 - len(suffix)]}{suffix}"
            if key not in used:
                return key
        raise RuntimeError("citation key allocation exhausted")


class SkillPackageAssembler:
    """Build only the level 1 files for an existing document tree."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the SkillPackageAssembler instance."""
        self.allocator = CitationKeyAllocator(database_path)

    def assemble(
        self, domain: str, output_root: Path, documents: tuple[DocumentPackageInput, ...], taxonomy_path: Path
    ) -> PackageAssemblyResult:
        """Run the assemble operation."""
        logger.info("Assembling domain skill package for %s", domain)
        package_dir = output_root / f"juniper-{domain}"
        package_dir.mkdir(parents=True, exist_ok=True)
        keys = {document.slug: self.allocator.allocate(domain, document.slug, document.title) for document in documents}
        routes = self._read_documents(package_dir, documents, keys)
        coverage = self._coverage(routes)
        self._write_level_one(package_dir, domain, documents, routes, taxonomy_path)
        result = self._result(package_dir, routes, documents, coverage)
        logger.debug("Assembled %s with %s topics", package_dir, result.topic_count)
        return result

    def _read_documents(
        self, package_dir: Path, documents: tuple[DocumentPackageInput, ...], keys: dict[str, str]
    ) -> list[TopicRoute]:
        routes: list[TopicRoute] = []
        for document in documents:
            routes.extend(self._read_document(package_dir, document, keys[document.slug]))
        return routes

    def _read_document(self, package_dir: Path, document: DocumentPackageInput, key: str) -> list[TopicRoute]:
        logger.info("Reading existing document tree for %s", document.slug)
        if not (document.document_dir / "INDEX.md").exists():
            raise ValueError(f"document index is missing for {document.slug}")
        routes = self._read_topics(package_dir, document, key)
        logger.debug("Read existing document tree for %s with %s topics", document.slug, len(routes))
        return routes

    def _read_topics(self, package_dir: Path, document: DocumentPackageInput, key: str) -> list[TopicRoute]:
        logger.info("Reading existing topic files for %s", document.slug)
        source_files = self._topic_files(document.document_dir)
        subjects = SegmentIndexReader().subjects(document.document_dir / "INDEX.md")
        routes = [self._read_topic(package_dir, document.slug, key, path, subjects) for path in source_files]
        logger.debug("Read %s existing topic files for %s", len(routes), document.slug)
        return routes

    def _topic_files(self, document_dir: Path) -> list[Path]:
        logger.info("Reading topic files from %s", document_dir)
        files = sorted(path for path in document_dir.glob("*.md") if path.name != "INDEX.md")
        if not files:
            raise ValueError(f"document folder has no topic files: {document_dir}")
        logger.debug("Read %s topic files from %s", len(files), document_dir)
        return files

    def _read_topic(
        self,
        package_dir: Path,
        slug: str,
        key: str,
        source_path: Path,
        subjects: dict[str, str],
    ) -> TopicRoute:
        logger.info("Reading existing topic file %s", source_path.name)
        source = source_path.read_text(encoding="utf-8")
        frontmatter, body = FrontMatterParser().parse(source)
        title = str(frontmatter.get("topic") or self._title_from_name(source_path))
        lifecycle = self._lifecycle(frontmatter)
        source_range = self._source_range(frontmatter, key)
        route = self._route(package_dir, title, slug, source_range, source_path, lifecycle, body, subjects)
        logger.debug("Read existing topic file %s with %s bytes", source_path.name, source_path.stat().st_size)
        return route

    def _route(
        self,
        package_dir: Path,
        title: str,
        slug: str,
        citation: str,
        path: Path,
        lifecycle: tuple[str, ...],
        body: str,
        subjects: dict[str, str],
    ) -> TopicRoute:
        subject = subjects.get(title) or self._subject(title, body)
        source_range = citation.split(" ", 1)[1] if " " in citation else "p.0-0"
        relative = self._relative_topic_path(package_dir, slug, path)
        return TopicRoute(title, subject, lifecycle, citation, source_range, relative)

    def _relative_topic_path(self, package_dir: Path, slug: str, path: Path) -> Path:
        try:
            return path.relative_to(package_dir)
        except ValueError:
            return Path("documents") / slug / path.name

    def _subject(self, title: str, body: str) -> str:
        first_card = next((line for line in body.splitlines() if re.match(r"^- (MUST|SHOULD|INFO): ", line)), "")
        cleaned = re.sub(r"^- (?:MUST|SHOULD|INFO):\s*", "", first_card).split("[", 1)[0].strip()
        return cleaned if cleaned else f"Read about {title}."

    def _lifecycle(self, frontmatter: dict[str, Any]) -> tuple[str, ...]:
        values = frontmatter.get("lifecycle") or ["day0"]
        tags = tuple(tag for tag in values if tag in LIFECYCLE_TAGS) if isinstance(values, list) else ("day0",)
        return tags or ("day0",)

    def _source_range(self, frontmatter: dict[str, Any], key: str) -> str:
        sources = frontmatter.get("sources") or [f"{key} p.0-0"]
        first = str(sources[0]) if isinstance(sources, list) and sources else f"{key} p.0-0"
        first = first.strip().strip('"').strip("'")
        citation = re.search(
            r"^[A-Z0-9-]{3,16} (?:p\.\d+(?:-\d+)?|sec\.[a-z0-9-]+|table\.[a-z0-9-]+|fig\.[a-z0-9-]+)",
            first,
        )
        if citation:
            return citation.group(0)
        match = re.search(r"(?:p\.\d+(?:-\d+)?|sec\.[a-z0-9-]+|table\.[a-z0-9-]+|fig\.[a-z0-9-]+)", first)
        return f"{key} {match.group(0) if match else 'p.0-0'}"

    def _write_document_index(
        self, target_dir: Path, document: DocumentPackageInput, key: str, routes: list[TopicRoute]
    ) -> None:
        logger.info("Writing level 2 index for %s", document.slug)
        text = DocumentIndexRenderer(document, key, routes).render()
        (target_dir / "INDEX.md").write_text(text, encoding="utf-8")
        logger.debug("Wrote level 2 index for %s with %s rows", document.slug, len(routes))

    def _write_level_one(
        self,
        package_dir: Path,
        domain: str,
        documents: tuple[DocumentPackageInput, ...],
        routes: list[TopicRoute],
        taxonomy_path: Path,
    ) -> None:
        logger.info("Writing level 1 files for %s", domain)
        keywords = TaxonomyReader(taxonomy_path).keywords_for(f"juniper-{domain}")
        coverage = self._coverage(routes)
        (package_dir / "SKILL.md").write_text(
            SkillRenderer(domain, documents, routes, keywords, coverage).render(), "utf-8"
        )
        (package_dir / "INDEX.md").write_text(IndexRenderer(domain, documents, routes, coverage).render(), "utf-8")
        sources_text = SourcesRenderer(documents, self.allocator, routes).render(domain)
        (package_dir / "sources.md").write_text(sources_text, "utf-8")
        logger.debug("Wrote level 1 files for %s", domain)

    def _coverage(self, routes: list[TopicRoute]) -> dict[str, int]:
        logger.info("Counting life cycle coverage for the domain")
        coverage = {tag: sum(1 for route in routes if tag in route.lifecycle) for tag in LIFECYCLE_TAGS}
        logger.debug("Counted life cycle coverage as %s", coverage)
        return coverage

    def _result(
        self,
        package_dir: Path,
        routes: list[TopicRoute],
        documents: tuple[DocumentPackageInput, ...],
        coverage: dict[str, int],
    ) -> PackageAssemblyResult:
        logger.info("Measuring generated level 1 file sizes")
        result = PackageAssemblyResult(
            package_dir,
            (package_dir / "SKILL.md").stat().st_size,
            (package_dir / "INDEX.md").stat().st_size,
            (package_dir / "sources.md").stat().st_size,
            len(routes),
            len(documents),
            coverage,
        )
        logger.debug("Measured level 1 files in %s", package_dir)
        return result

    def _title_from_name(self, path: Path) -> str:
        return (
            path.stem[3:].replace("-", " ").title() if path.stem[:2].isdigit() else path.stem.replace("-", " ").title()
        )

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug[:60].rsplit("-", 1)[0] or "overview"


@dataclass(frozen=True)
class SmallDocumentPlan:
    """State how the assembler handles large and small source documents."""

    large_documents: tuple[DocumentPackageInput, ...]  # Build one package for each full source document.
    collection_documents: tuple[DocumentPackageInput, ...]  # Group short source documents in one collection package.
    attached_documents: tuple[DocumentPackageInput, ...] = ()  # Reserve the attachment path for future nearest matches.


class SmallDocumentPlanner:
    """Choose package granularity from the source page count."""

    MIN_DOCUMENT_PACKAGE_PAGES = 20  # Keep short flyers out of the registered skill list.

    def plan(self, documents: tuple[DocumentPackageInput, ...]) -> SmallDocumentPlan:
        """Run the plan operation."""
        logger.info("Planning document package granularity")  # Record the classification start.
        large = self._large_documents(documents)  # Keep long documents as standalone packages.
        small = self._small_documents(documents)  # Move short documents into a collection package.
        plan = SmallDocumentPlan(large, small)  # Return one immutable decision record.
        logger.debug(  # Report the measured decision counts for the proof section.
            "Planned %s large documents, %s collection documents, and %s attached documents",
            len(plan.large_documents),
            len(plan.collection_documents),
            len(plan.attached_documents),
        )
        return plan  # Return the rule result to the caller.

    def _large_documents(self, documents: tuple[DocumentPackageInput, ...]) -> tuple[DocumentPackageInput, ...]:
        logger.info("Selecting documents with at least %s pages", self.MIN_DOCUMENT_PACKAGE_PAGES)  # Log rule use.
        selected = tuple(document for document in documents if document.pages >= self.MIN_DOCUMENT_PACKAGE_PAGES)
        logger.debug("Selected %s large documents", len(selected))  # Report the large path count.
        return selected  # Return documents that qualify for standalone packages.

    def _small_documents(self, documents: tuple[DocumentPackageInput, ...]) -> tuple[DocumentPackageInput, ...]:
        logger.info("Selecting documents with fewer than %s pages", self.MIN_DOCUMENT_PACKAGE_PAGES)  # Log rule use.
        selected = tuple(document for document in documents if document.pages < self.MIN_DOCUMENT_PACKAGE_PAGES)
        logger.debug("Selected %s small documents", len(selected))  # Report the collection path count.
        return selected  # Return documents that must use the collection path.


class DocumentSkillPackageAssembler:
    """Build one standalone skill package from one existing topic tree."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the DocumentSkillPackageAssembler instance."""
        self.allocator = CitationKeyAllocator(database_path)  # Reuse stable citation keys across reruns.

    def assemble(
        self, domain: str, output_root: Path, document: DocumentPackageInput, taxonomy_path: Path
    ) -> PackageAssemblyResult:
        """Run the assemble operation."""
        logger.info("Assembling document skill package for %s", document.slug)  # Log the package start.
        package_dir = output_root / self.package_name(domain, document)  # Use the required stable skill name.
        self._require_existing_tree(package_dir, document)  # Refuse to create or replace topic files.
        key = self.allocator.allocate(domain, document.slug, document.title)  # Get one stable source key.
        routes = SkillPackageAssembler(self.allocator.database_path)._read_document(package_dir, document, key)
        coverage = self._coverage(routes)  # Count stage coverage from the existing topic metadata.
        self._write_level_one(package_dir, domain, document, routes, taxonomy_path)  # Write only root package files.
        result = self._result(package_dir, routes, document, coverage)  # Measure the emitted package files.
        logger.debug("Assembled document package %s with %s topics", package_dir.name, result.topic_count)
        return result  # Return the measured package result.

    def package_name(self, domain: str, document: DocumentPackageInput) -> str:
        """Run the package name operation."""
        logger.info("Building the document skill package name for %s", document.slug)  # Log naming.
        name = f"juniper-{domain}-{document.slug}"  # Include the domain and document slug for collision safety.
        logger.debug("Built document skill package name %s", name)  # Record the stable package name.
        return name  # Return the package directory and SKILL.md name.

    def _require_existing_tree(self, package_dir: Path, document: DocumentPackageInput) -> None:
        logger.info("Checking existing topic tree for %s", document.slug)  # Log the no-write guard.
        expected = package_dir / "documents" / document.slug  # Require topics inside the standalone package.
        if document.document_dir.resolve() != expected.resolve():  # Prevent hidden copies from another package.
            raise ValueError("document topic tree must already be inside the document skill package")
        if not (expected / "INDEX.md").exists():  # Require the existing document index before root writes.
            raise ValueError("document topic tree must include INDEX.md before assembly")
        logger.debug("Existing topic tree passed for %s", document.slug)  # Record the positive guard result.

    def _write_level_one(
        self,
        package_dir: Path,
        domain: str,
        document: DocumentPackageInput,
        routes: list[TopicRoute],
        taxonomy_path: Path,
    ) -> None:
        logger.info("Writing root files for document package %s", package_dir.name)  # Log root writes.
        keywords = TaxonomyReader(taxonomy_path).keywords_for(f"juniper-{domain}")  # Read the shared domain catalog.
        coverage = self._coverage(routes)  # Recompute coverage close to rendering for consistency.
        package_dir.mkdir(parents=True, exist_ok=True)  # Create only the package root if it is absent.
        skill = DocumentSkillRenderer(domain, document, routes, keywords, coverage).render()  # Render SKILL.md.
        (package_dir / "SKILL.md").write_text(skill, encoding="utf-8")  # Write the registered skill router.
        index = IndexRenderer(domain, (document,), routes, coverage).render()  # Render the package index.
        (package_dir / "INDEX.md").write_text(index, encoding="utf-8")  # Write the full route index.
        sources = SourcesRenderer((document,), self.allocator, routes).render(domain)  # Render source attribution.
        (package_dir / "sources.md").write_text(sources, encoding="utf-8")  # Write the source table.
        logger.debug("Wrote root files for document package %s", package_dir.name)  # Record root write completion.

    def _coverage(self, routes: list[TopicRoute]) -> dict[str, int]:
        logger.info("Counting life cycle coverage for the document package")  # Log stage coverage measurement.
        coverage = {tag: sum(1 for route in routes if tag in route.lifecycle) for tag in LIFECYCLE_TAGS}
        logger.debug("Counted document life cycle coverage as %s", coverage)  # Report measured stage counts.
        return coverage  # Return coverage for SKILL.md and INDEX.md.

    def _result(
        self, package_dir: Path, routes: list[TopicRoute], document: DocumentPackageInput, coverage: dict[str, int]
    ) -> PackageAssemblyResult:
        logger.info("Measuring document package root file sizes")  # Log before reading file sizes.
        result = PackageAssemblyResult(  # Build one measured outcome for proof and tests.
            package_dir,
            (package_dir / "SKILL.md").stat().st_size,
            (package_dir / "INDEX.md").stat().st_size,
            (package_dir / "sources.md").stat().st_size,
            len(routes),
            1,
            coverage,
        )
        logger.debug("Measured document package for %s", document.slug)  # Record measurement completion.
        return result  # Return measured package data.


class CollectionSkillPackageAssembler:
    """Build one collection skill package for short documents in one domain."""

    def __init__(self, database_path: Path) -> None:
        """Initialize the CollectionSkillPackageAssembler instance."""
        self.assembler = SkillPackageAssembler(database_path)  # Reuse the multi-document domain assembler safely.

    def assemble(
        self, domain: str, output_root: Path, documents: tuple[DocumentPackageInput, ...], taxonomy_path: Path
    ) -> PackageAssemblyResult:
        """Run the assemble operation."""
        logger.info("Assembling a small-document collection for %s", domain)  # Log collection package start.
        name = self.package_name(domain)  # Use one stable collection package for this domain.
        result = self.assembler.assemble(f"{domain}-small-documents", output_root, documents, taxonomy_path)
        logger.debug("Assembled small-document collection %s with %s documents", name, result.document_count)
        return result  # Return the measured package output.

    def package_name(self, domain: str) -> str:
        """Run the package name operation."""
        logger.info("Building the small-document collection package name for %s", domain)  # Log naming.
        name = f"juniper-{domain}-small-documents"  # Keep the collection visible but not document-specific.
        logger.debug("Built small-document collection package name %s", name)  # Record the package name.
        return name  # Return the package name for router rows.


class SkillCatalogIndex:
    """Read generated catalog rows so domain routers do not invent package lists."""

    def __init__(self, catalog_path: Path) -> None:
        """Initialize the SkillCatalogIndex instance."""
        self.catalog_path = catalog_path  # Store the generated installer catalog path.

    def package_names(self, domain: str) -> tuple[str, ...]:
        """Run the package names operation."""
        logger.info("Reading catalog package names for %s", domain)  # Log catalog access.
        rows = self._rows()  # Read the generated catalog table or list.
        prefix = f"juniper-{domain}-"  # Select document packages for this domain.
        names = tuple(row for row in rows if row == f"juniper-{domain}" or row.startswith(prefix))
        logger.debug("Read %s catalog package names for %s", len(names), domain)  # Report catalog coverage.
        return names  # Return package names that the domain router can cite.

    def _rows(self) -> tuple[str, ...]:
        logger.info("Reading skill catalog text from %s", self.catalog_path)  # Log before file read.
        if not self.catalog_path.exists():  # Permit early factory runs before the installer writes a catalog.
            logger.debug("Skill catalog does not exist at %s", self.catalog_path)  # State the missing file path.
            return ()  # Return an empty catalog so explicit package inputs can still render.
        text = self.catalog_path.read_text(encoding="utf-8")  # Read the installer-owned catalog.
        names = tuple(dict.fromkeys(re.findall(r"`(juniper-[a-z0-9-]+)`", text)))  # Keep first seen order.
        logger.debug("Read %s package names from the skill catalog", len(names))  # Report parsed row count.
        return names  # Return unique package names from the catalog.


class DomainRouterPackageAssembler:
    """Build the retained domain router that points to document packages."""

    def __init__(self, catalog: SkillCatalogIndex) -> None:
        """Initialize the DomainRouterPackageAssembler instance."""
        self.catalog = catalog  # Use the installer catalog as the package authority.

    def assemble(
        self, domain: str, output_root: Path, packages: tuple[str, ...], taxonomy_path: Path
    ) -> PackageAssemblyResult:
        """Run the assemble operation."""
        logger.info("Assembling domain router package for %s", domain)  # Log domain router start.
        package_dir = output_root / f"juniper-{domain}"  # Keep the domain routing tier package name.
        package_dir.mkdir(parents=True, exist_ok=True)  # Create the domain router folder.
        names = self._package_names(domain, packages)  # Merge catalog rows with unregistered package names.
        keywords = TaxonomyReader(taxonomy_path).keywords_for(f"juniper-{domain}")  # Read domain keywords.
        DomainRouterRenderer(domain, names, keywords).write(package_dir)  # Write the three router files.
        result = self._result(package_dir, names)  # Measure the router package.
        logger.debug("Assembled domain router %s with %s package links", domain, len(names))
        return result  # Return measured router output.

    def _package_names(self, domain: str, packages: tuple[str, ...]) -> tuple[str, ...]:
        logger.info("Combining catalog packages with explicit domain packages")  # Log package source merge.
        names = tuple(dict.fromkeys(self.catalog.package_names(domain) + packages))  # Preserve catalog order.
        logger.debug("Combined %s domain package names", len(names))  # Report the package link count.
        return names  # Return package names for the domain router.

    def _result(self, package_dir: Path, packages: tuple[str, ...]) -> PackageAssemblyResult:
        logger.info("Measuring domain router package root file sizes")  # Log before measurement.
        result = PackageAssemblyResult(  # Domain routers hold no source topics.
            package_dir,
            (package_dir / "SKILL.md").stat().st_size,
            (package_dir / "INDEX.md").stat().st_size,
            (package_dir / "sources.md").stat().st_size,
            0,
            len(packages),
            {tag: 0 for tag in LIFECYCLE_TAGS},
        )
        logger.debug("Measured domain router package %s", package_dir.name)  # Record measurement completion.
        return result  # Return the measured router result.


class DocumentSkillRenderer:
    """Render SKILL.md for one source document package."""

    def __init__(
        self,
        domain: str,
        document: DocumentPackageInput,
        routes: list[TopicRoute],
        keywords: list[str],
        coverage: dict[str, int],
    ) -> None:
        """Initialize the DocumentSkillRenderer instance."""
        self.domain = domain  # Store the domain for the stable skill name.
        self.document = document  # Store the source document metadata.
        self.routes = routes  # Store topic routes that already exist on disk.
        self.keywords = keywords  # Store domain keywords from the catalog.
        self.coverage = coverage  # Store life cycle coverage for the package.

    def render(self) -> str:
        """Run the render operation."""
        logger.info("Rendering document SKILL.md for %s", self.document.slug)  # Log rendering start.
        route_rows = RouteTableBuilder((self.document,), self.routes).rows(SKILL_HARD_LIMIT)  # Build user routes.
        text = self._frontmatter() + self._body(route_rows)  # Combine metadata and router guidance.
        logger.debug("Rendered document SKILL.md with %s bytes", len(text.encode("utf-8")))  # Report size.
        return text  # Return complete SKILL.md text.

    def _frontmatter(self) -> str:
        logger.info("Rendering document SKILL.md frontmatter")  # Log frontmatter generation.
        built = datetime.now(UTC).isoformat(timespec="seconds")  # Record deterministic metadata precision.
        description = self._description()  # Build a complete trigger description for agent routing.
        text = (
            f"---\nname: juniper-{self.domain}-{self.document.slug}\ndescription: >-\n  {description}\n"
            "license: The topics restate Juniper Networks documentation. Juniper Networks holds the copyright of the "
            "source document. This skill stores no source prose.\nmetadata:\n"
            "  feature: 2925-juniper-skill-factory\n"
            f"  domain: {self.domain}\n  document: {self.document.slug}\n  documents: 1\n"
            f"  topics: {len(self.routes)}\n  source_pages: {self.document.pages}\n  built: {built}\n---\n\n"
        )
        logger.debug("Rendered frontmatter for %s", self.document.slug)  # Record frontmatter completion.
        return text  # Return YAML frontmatter text.

    def _description(self) -> str:
        logger.info("Building document skill description for %s", self.document.slug)  # Log trigger text build.
        text = " ".join(self._description_parts())  # Combine dense routing signals for skill selection.
        logger.debug("Built description with %s characters", len(text))  # Report description size.
        return text  # Return one paragraph for skill registration.

    def _description_parts(self) -> list[str]:
        logger.info("Building discriminating description parts")  # Log description part creation.
        parts = [self._title_anchor(), self._subject_anchor(), self._task_anchor(), self._symptom_anchor()]
        version = self._version_anchor()  # Add a release or version only when the title carries one.
        if version:  # Avoid empty filler for documents with no version marker.
            parts.append(version)  # Preserve version specificity for sibling documents.
        logger.debug("Built %s description parts", len(parts))  # Report selected part count.
        return parts  # Return parts in a stable order for reproducible descriptions.

    def _title_anchor(self) -> str:
        logger.info("Building the title anchor for the description")  # Log title anchor creation.
        title = self._clip_phrase(self.document.title, 92)  # Keep long sibling titles inside the token budget.
        anchor = f"Juniper {title}."  # Lead with the source document title for sibling disambiguation.
        logger.debug("Built title anchor with %s characters", len(anchor))  # Report anchor length.
        return anchor  # Return the product title sentence.

    def _subject_anchor(self) -> str:
        logger.info("Building the product and protocol anchor")  # Log subject anchor creation.
        subjects = self._subjects()  # Extract products, platforms, protocols, and features.
        anchor = f"Product, platform, protocol, feature: {subjects}."  # Use direct labels for routing terms.
        logger.debug("Built subject anchor with %s characters", len(anchor))  # Report anchor length.
        return anchor  # Return the searchable subject sentence.

    def _task_anchor(self) -> str:
        return "Tasks: configure, verify, troubleshoot, install, upgrade, design, automate."  # Add user task verbs.

    def _symptom_anchor(self) -> str:
        logger.info("Building the symptom anchor")  # Log symptom selection.
        symptoms = ", ".join(self._symptoms())  # Select domain-specific symptom words.
        anchor = f"Symptoms: {symptoms}."  # Use words that an operator types during incidents.
        logger.debug("Built symptom anchor with %s characters", len(anchor))  # Report anchor length.
        return anchor  # Return the symptom sentence.

    def _version_anchor(self) -> str:
        logger.info("Searching document title for a release or version marker")  # Log version extraction.
        haystack = " ".join((self.document.title, self.document.slug, self.document.category))  # Search metadata only.
        match = re.search(r"\b(?:\d{2}\.\dR\d|\d{4}-\d{2}-\d{2}|[A-Z]?\d+\.\d+(?:R\d+)?)\b", haystack)
        anchor = f"Release or version: {match.group(0)}." if match else ""  # Include version only when present.
        logger.debug("Version marker found: %s", bool(anchor))  # Report whether the anchor exists.
        return anchor  # Return empty text for non-versioned documents.

    def _subjects(self) -> str:
        logger.info("Extracting document subject words for the skill description")  # Log subject extraction.
        route_text = " ".join(route.title + " " + route.subject for route in self.routes[:12])  # Sample topic terms.
        raw = " ".join([self.document.category, self.document.title, route_text, *self.keywords[:12]])
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]+", raw)  # Keep product and protocol tokens.
        chosen = tuple(dict.fromkeys(word for word in words if len(word) > 2))[:12]  # Keep stable order.
        text = ", ".join(chosen) if chosen else self.domain.replace("-", " ")  # Fall back to the domain name.
        logger.debug("Extracted %s document subject words", len(chosen))  # Report trigger token count.
        return text  # Return compact subject text.

    def _symptoms(self) -> tuple[str, ...]:
        logger.info("Selecting symptom words for the document domain")  # Log symptom selection.
        context = " ".join((self.domain, self.document.title, self.document.slug, self._subjects())).lower()
        choices = self._domain_symptoms(context)  # Pick protocol-specific terms when they match.
        logger.debug("Selected %s symptom words", len(choices))  # Report selected symptom count.
        return choices  # Return symptoms for the description.

    def _domain_symptoms(self, context: str) -> tuple[str, ...]:
        if "evpn" in context or "vxlan" in context:  # Match data-center overlay questions.
            return ("missing MAC address", "VNI mismatch", "BGP neighbor down", "traffic blackhole", "commit fails")
        if "mpls" in context or "ldp" in context:  # Match label-switched path questions.
            return ("LSP down", "label missing", "LDP neighbor down", "packet loss", "high latency")
        if "bgp" in context:  # Match route-exchange questions.
            return ("neighbor down", "route missing", "prefix rejected", "flapping", "commit fails")
        if "ospf" in context:  # Match link-state routing questions.
            return ("adjacency down", "route missing", "flapping", "packet loss", "high latency")
        if "sd-wan" in context or "session smart" in context:  # Match WAN path questions.
            return ("tunnel problem", "application path loss", "high latency", "failover failed", "site down")
        return ("will not boot", "commit fails", "alarm", "packet loss", "reachability loss")  # Use safe defaults.

    def _clip_phrase(self, value: str, limit: int) -> str:
        text = value.strip().rstrip(".")  # Normalize source titles before the budget cut.
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."  # Keep long titles bounded.

    def _body(self, rows: str) -> str:
        logger.info("Rendering document SKILL.md body")  # Log body generation.
        text = (
            f"# {self.document.title}\n\nThis skill answers questions from one Juniper source document. "
            "Read the topic that holds the subject before you answer.\n\n"
            "## Route the question to a topic\n\n| Ask about | Read this topic |\n| - | - |\n"
            f"{rows}\nRead `INDEX.md` for the full document route.\n\n"
            "## Life cycle coverage\n\n| Stage | Status |\n| - | - |\n"
            f"{self._coverage_rows()}\n## Answer rules\n\n"
            "1. Read the needed topic before you answer.\n2. Give a citation with each rule.\n"
            "3. Say that the skill does not hold the fact when no topic states it.\n"
            "4. Keep commands exact, and restate explanatory text in new words.\n\n"
            "Warning: never copy Juniper source prose into an answer. Juniper Networks holds the copyright.\n\n"
            "## Scope\n\nThis skill answers questions from this document only. It does not change a network device.\n"
        )
        logger.debug("Rendered document SKILL.md body with %s bytes", len(text.encode("utf-8")))  # Report size.
        return text  # Return the document router body.

    def _coverage_rows(self) -> str:
        logger.info("Rendering life cycle coverage rows")  # Log coverage row rendering.
        rows = "".join(f"| {tag} | {self._coverage_status(tag)} |\n" for tag in LIFECYCLE_TAGS)
        logger.debug("Rendered life cycle coverage rows for %s stages", len(LIFECYCLE_TAGS))  # Report row count.
        return rows  # Return stage coverage rows.

    def _coverage_status(self, tag: str) -> str:
        count = self.coverage.get(tag, 0)  # Read zero for absent stages.
        return f"present with {count} topics" if count else "absent in this document"  # State each gap plainly.


class DomainRouterRenderer:
    """Render the retained domain router files."""

    def __init__(self, domain: str, packages: tuple[str, ...], keywords: list[str]) -> None:
        """Initialize the DomainRouterRenderer instance."""
        self.domain = domain  # Store the domain slug for headings and metadata.
        self.packages = packages  # Store package names from the catalog and explicit inputs.
        self.keywords = keywords  # Store subject words from the domain taxonomy.

    def write(self, package_dir: Path) -> None:
        """Run the write operation."""
        logger.info("Writing domain router files for %s", self.domain)  # Log router file writes.
        (package_dir / "SKILL.md").write_text(self._skill_text(), encoding="utf-8")  # Write the router skill.
        (package_dir / "INDEX.md").write_text(self._index_text(), encoding="utf-8")  # Write package targets.
        (package_dir / "sources.md").write_text(self._sources_text(), encoding="utf-8")  # State no source prose.
        logger.debug("Wrote domain router files for %s", self.domain)  # Record router write completion.

    def _skill_text(self) -> str:
        logger.info("Rendering domain router SKILL.md")  # Log SKILL text build.
        built = datetime.now(UTC).isoformat(timespec="seconds")  # Record the build time.
        text = (
            f"---\nname: juniper-{self.domain}\ndescription: >-\n  {self._description()}\n"
            "license: This router stores package names only. Juniper Networks holds the source document copyright.\n"
            "metadata:\n  feature: 2925-juniper-skill-factory\n"
            f"  domain: {self.domain}\n  documents: {len(self.packages)}\n  topics: 0\n"
            f"  source_pages: 0\n  built: {built}\n---\n\n# {self._title()}\n\n"
            "Use this router when the question names a domain but not a document package.\n\n"
            "## Route by package\n\n| Ask about | Read this package |\n| - | - |\n"
            f"{self._route_rows()}\nRead the named package skill next.\n"
        )
        logger.debug("Rendered domain router SKILL.md with %s bytes", len(text.encode("utf-8")))  # Report size.
        return text  # Return the domain router skill text.

    def _description(self) -> str:
        logger.info("Building domain router description")  # Log description build.
        subjects = ", ".join(self.keywords[:24]) or self.domain.replace("-", " ")  # Keep taxonomy wording.
        text = (
            f"Use this skill to choose the correct Juniper {subjects} document skill. "
            "Use it when a question names the domain but needs a specific manual, guide, release note, "
            "troubleshooting guide, command reference, configuration task, alarm, or protocol symptom."
        )
        logger.debug("Built domain router description with %s characters", len(text))  # Report length.
        return text  # Return the one-paragraph trigger.

    def _route_rows(self) -> str:
        logger.info("Rendering domain router package rows")  # Log route row rendering.
        rows = "".join(f"| {self._subject(name)} | `{name}` |\n" for name in self.packages)
        logger.debug("Rendered %s domain router package rows", len(self.packages))  # Report row count.
        return rows or "| This domain has no built document package yet | `INDEX.md` |\n"  # Keep router useful.

    def _subject(self, package: str) -> str:
        words = package.removeprefix(f"juniper-{self.domain}-").replace("-", " ")  # Convert slug to user text.
        return f"Questions about {words}"  # Give a user-vocabulary route, not a file instruction.

    def _index_text(self) -> str:
        logger.info("Rendering domain router INDEX.md")  # Log index generation.
        rows = "".join(f"| {package} | {package} |\n" for package in self.packages)  # List package targets.
        logger.debug("Rendered domain router INDEX.md with %s rows", len(self.packages))  # Report row count.
        return f"# {self._title()} package index\n\n| Subject package | Skill name |\n| - | - |\n{rows}"

    def _sources_text(self) -> str:
        logger.info("Rendering domain router sources.md")  # Log source text generation.
        text = "# Sources\n\nThis router stores no source document prose.\n"  # State that source data lives below.
        logger.debug("Rendered domain router sources.md")  # Record completion.
        return text  # Return the source note.

    def _title(self) -> str:
        return " ".join(part.capitalize() for part in self.domain.split("-"))  # Build a readable heading.


class FrontMatterParser:
    """Parse small YAML frontmatter blocks from generated Markdown."""

    def parse(self, text: str) -> tuple[dict[str, Any], str]:
        """Run the parse operation."""
        logger.info("Parsing Markdown frontmatter")
        if not text.startswith("---\n"):
            logger.debug("Markdown file has no frontmatter")
            return {}, text
        end = text.find("\n---", 4)
        if end == -1:
            logger.debug("Markdown file has an open frontmatter block")
            return {}, text
        try:
            data = yaml.safe_load(text[4:end]) or {}
        except yaml.YAMLError:
            data = self._fallback(text[4:end])
        body = text[end + 4 :].lstrip()
        logger.debug("Parsed frontmatter with %s keys", len(data))
        return data if isinstance(data, dict) else {}, body

    def _fallback(self, text: str) -> dict[str, Any]:
        logger.info("Parsing frontmatter with the fault-tolerant reader")
        data: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line:
                self._fallback_line(data, line)
        logger.debug("Fault-tolerant reader parsed %s keys", len(data))
        return data

    def _fallback_line(self, data: dict[str, Any], line: str) -> None:
        key, value = line.split(":", 1)
        value = value.strip()
        data[key.strip()] = self._fallback_value(value)

    def _fallback_value(self, value: str) -> object:
        if value.startswith("[") and value.endswith("]"):
            return [part.strip() for part in value[1:-1].split(",") if part.strip()]
        return value


class SegmentIndexReader:
    """Read task-language subjects from the segmenter level 2 index."""

    def subjects(self, index_path: Path) -> dict[str, str]:
        """Run the subjects operation."""
        logger.info("Reading segmenter subjects from %s", index_path)
        if not index_path.exists():
            logger.debug("Segmenter index does not exist at %s", index_path)
            return {}
        rows = self._rows(index_path.read_text(encoding="utf-8"))
        subjects = {row[0]: row[3] for row in rows if len(row) >= 4}
        logger.debug("Read %s segmenter subjects from %s", len(subjects), index_path)
        return subjects

    def _rows(self, text: str) -> list[list[str]]:
        rows = [line.strip("|").split("|") for line in text.splitlines() if line.startswith("| ")]
        clean = [[cell.strip() for cell in row] for row in rows]
        return [row for row in clean if row and row[0] not in {"Topic", "-"}]


class TaxonomyReader:
    """Read routing keywords for one domain from the locked taxonomy table."""

    def __init__(self, taxonomy_path: Path) -> None:
        """Initialize the TaxonomyReader instance."""
        self.taxonomy_path = taxonomy_path

    def keywords_for(self, skill_name: str) -> list[str]:
        """Run the keywords for operation."""
        logger.info("Reading taxonomy keywords for %s", skill_name)
        text = self.taxonomy_path.read_text(encoding="utf-8")
        pattern = rf"\|\s*\d+\s*\|\s*`{re.escape(skill_name)}`\s*\|\s*(.*?)\s*\|"
        match = re.search(pattern, text)
        keywords = self._split_keywords(match.group(1)) if match else [skill_name.replace("juniper-", "Juniper ")]
        logger.debug("Read %s taxonomy keywords for %s", len(keywords), skill_name)
        return keywords

    def _split_keywords(self, text: str) -> list[str]:
        clean = text.replace("`", "")
        parts = re.split(r",\s*|\s+or\s+", clean)
        return [part.strip(". ") for part in parts if part.strip(". ")]


class SkillRenderer:
    """Render the domain router file."""

    def __init__(
        self,
        domain: str,
        documents: tuple[DocumentPackageInput, ...],
        routes: list[TopicRoute],
        keywords: list[str],
        coverage: dict[str, int],
    ) -> None:
        """Initialize the SkillRenderer instance."""
        self.domain = domain
        self.documents = documents
        self.routes = routes
        self.keywords = keywords
        self.coverage = coverage

    def render(self) -> str:
        """Run the render operation."""
        logger.info("Rendering SKILL.md for %s", self.domain)
        frontmatter = self._frontmatter()
        overhead = len(frontmatter.encode("utf-8")) + len(self._body("").encode("utf-8"))
        route_rows = RouteTableBuilder(self.documents, self.routes).rows(SKILL_HARD_LIMIT - overhead)
        text = frontmatter + self._body(route_rows)
        logger.debug("Rendered SKILL.md for %s with %s bytes", self.domain, len(text.encode("utf-8")))
        return text

    def _frontmatter(self) -> str:
        built = datetime.now(UTC).isoformat(timespec="seconds")
        description = self._description()
        return (
            f"---\nname: juniper-{self.domain}\ndescription: >-\n  {description}\n"
            "license: The topics restate Juniper Networks documentation. Juniper Networks holds the copyright of the "
            "source documents. This skill stores no source prose.\nmetadata:\n"
            f"  feature: 2925-juniper-skill-factory\n  domain: {self.domain}\n"
            f"  documents: {len(self.documents)}\n  topics: {len(self.routes)}\n"
            f"  source_pages: {sum(document.pages for document in self.documents)}\n  built: {built}\n---\n\n"
        )

    def _description(self) -> str:
        subjects = ", ".join(self.keywords[:30])
        return (
            f"Use this skill for Juniper {subjects}. The skill holds a document tree with source documents, "
            "topic routes, life cycle tags, citation keys, and copyright-safe knowledge cards. Use it when a question "
            "asks how to select, deploy, configure, verify, troubleshoot, upgrade, migrate, or automate the subjects "
            "that this domain owns."
        )

    def _body(self, rows: str) -> str:
        return (
            f"# {self._title()}\n\nRead the topic that holds the subject before you answer. "
            "Do not answer a Juniper documentation question from memory.\n\n"
            "## Route by subject\n\n| Ask about | Read this topic |\n| - | - |\n"
            f"{rows}\nRead `INDEX.md` for the full domain route.\n\n"
            "## Class marks\n\n| Mark | Meaning |\n| - | - |\n| MUST | The source states a required practice. |\n"
            "| SHOULD | The source recommends the practice and permits a judged exception. |\n"
            "| INFO | The card states a fact that sets no limit. |\n\n"
            "## Answer rules\n\n1. Read the needed topic before you answer.\n2. Give a citation with each rule.\n"
            "3. Say that the skill does not hold the fact when no topic states it.\n"
            "4. State whether a rule is a requirement, a recommendation, or a fact.\n"
            "5. Point to `sources.md` when the reader needs the public source.\n\n"
            "## Copyright rule\n\nWarning: never copy Juniper source prose into an answer. "
            "Juniper Networks holds the copyright. Restate the fact in new words.\n\n"
            "## Precedence\n\nThe `ste-writing` skill controls the answer form. "
            "This skill controls the answer content.\n\n"
            "## Scope\n\nThis skill answers questions about this Juniper documentation domain. "
            "This skill does not change a network device or a service.\n"
        )

    def _title(self) -> str:
        return " ".join(part.capitalize() for part in self.domain.split("-"))


class RouteTableBuilder:
    """Build concrete SKILL.md route rows from the real topic set."""

    def __init__(self, documents: tuple[DocumentPackageInput, ...], routes: list[TopicRoute]) -> None:
        """Initialize the RouteTableBuilder instance."""
        self.documents = documents
        self.routes = routes

    def rows(self, byte_budget: int) -> str:
        """Run the rows operation."""
        logger.info("Building SKILL.md route rows with concrete destinations")
        candidates = [self._cluster_rows(), self._lifecycle_rows(), self._document_rows(), self._domain_rows()]
        selected = next((rows for rows in candidates if 0 < len(rows.encode("utf-8")) <= byte_budget), candidates[-1])
        logger.debug("Selected SKILL.md route rows with %s bytes", len(selected.encode("utf-8")))
        return selected

    def _cluster_rows(self) -> str:
        clusters = SubjectClusterBuilder(self.routes).clusters()
        return "".join(f"| {cluster.subject} | `{cluster.destination}` |\n" for cluster in clusters[:20])

    def _lifecycle_rows(self) -> str:
        rows: list[str] = []
        for document in self.documents:
            rows.extend(self._document_lifecycle_rows(document))
        return "".join(rows)

    def _document_lifecycle_rows(self, document: DocumentPackageInput) -> list[str]:
        rows: list[str] = []
        document_routes = [route for route in self.routes if route.relative_path.parts[1] == document.slug]
        for tag in LIFECYCLE_TAGS:
            tag_routes = [route for route in document_routes if tag in route.lifecycle]
            if tag_routes:
                rows.append(self._group_row(document, tag, tag_routes))
        return rows

    def _group_row(self, document: DocumentPackageInput, tag: str, routes: list[TopicRoute]) -> str:
        destination = routes[0].relative_path.as_posix() if len(routes) == 1 else f"documents/{document.slug}/INDEX.md"
        subject = f"{self._stage(tag)} in {document.title}"
        return f"| {subject} | `{destination}` |\n"

    def _document_rows(self) -> str:
        return "".join(
            f"| {document.title} topics | `documents/{document.slug}/INDEX.md` |\n" for document in self.documents
        )

    def _domain_rows(self) -> str:
        if len(self.documents) > 1:
            return "| The subject spans more than one source document | `INDEX.md` |\n"
        return self._document_rows()

    def _stage(self, tag: str) -> str:
        names = {
            "day0": "Design, selection, and requirements",
            "day1": "Setup, access, and first configuration",
            "day2": "Verification, monitoring, and troubleshooting",
            "day2plus": "Change, recovery, upgrade, and automation",
        }
        return names[tag]


class SubjectClusterBuilder:
    """Compress topic routes into user-language subject clusters."""

    def __init__(self, routes: list[TopicRoute]) -> None:
        """Initialize the SubjectClusterBuilder instance."""
        self.routes = routes

    def clusters(self) -> list[RouteCluster]:
        """Run the clusters operation."""
        logger.info("Building user-language route clusters")
        clusters = self._known_clusters()
        ordered = sorted(self._unique(clusters), key=lambda cluster: cluster.rank)
        logger.debug("Built %s user-language route clusters", len(ordered))
        return ordered

    def _known_clusters(self) -> list[RouteCluster]:
        return [
            self._one("filtering output", "Filtering or searching command output, match, except, count, or last", 10),
            self._one("cli help", "Finding a command, its syntax, or its options", 20),
            self._one("comparing", "Undoing a change, rolling back, or recovering a broken configuration", 30),
            self._one("comparing", "Making a change safely on a remote device", 40),
            self._one("management port", "First console access, out-of-band management, or the management port", 50),
            self._one("mx series", "Choosing a platform for a branch, a campus, or an edge role", 60),
            self._one("alarms", "A device that will not boot, an alarm, or a hardware fault", 70),
            self._one("basic networking tools", "Capturing packets or testing reachability", 80),
            self._many("routing", "Writing or debugging a routing policy", 90),
            self._many("firewall filter", "Writing or debugging a firewall filter", 100),
        ]

    def _one(self, needle: str, subject: str, rank: int) -> RouteCluster:
        route = self._find(needle)
        destination = route.relative_path.as_posix() if route else self._first_document_index()
        return RouteCluster(subject, destination, rank)

    def _many(self, needle: str, subject: str, rank: int) -> RouteCluster:
        matches = [route for route in self.routes if needle in self._context(route)]
        destination = self._span_destination(matches)
        return RouteCluster(subject, destination, rank)

    def _unique(self, clusters: list[RouteCluster]) -> list[RouteCluster]:
        seen: set[tuple[str, str]] = set()
        output: list[RouteCluster] = []
        for cluster in clusters:
            key = (cluster.subject, cluster.destination)
            if key not in seen:
                output.append(cluster)
                seen.add(key)
        return output

    def _find(self, needle: str) -> TopicRoute | None:
        return next((route for route in self.routes if needle in self._context(route)), None)

    def _context(self, route: TopicRoute) -> str:
        return f"{route.title} {route.subject} {route.relative_path.name}".lower()

    def _span_destination(self, routes: list[TopicRoute]) -> str:
        if len(routes) == 1:
            return routes[0].relative_path.as_posix()
        return self._document_index(routes) if routes else self._first_document_index()

    def _document_index(self, routes: list[TopicRoute]) -> str:
        parts = routes[0].relative_path.parts
        return f"documents/{parts[1]}/INDEX.md"

    def _first_document_index(self) -> str:
        if not self.routes:
            return "INDEX.md"
        return self._document_index([self.routes[0]])


class IndexRenderer:
    """Render the level 1 domain index with graceful size degradation."""

    def __init__(
        self,
        domain: str,
        documents: tuple[DocumentPackageInput, ...],
        routes: list[TopicRoute],
        coverage: dict[str, int],
    ) -> None:
        """Initialize the IndexRenderer instance."""
        self.domain = domain
        self.documents = documents
        self.routes = routes
        self.coverage = coverage

    def render(self) -> str:
        """Run the render operation."""
        logger.info("Rendering level 1 INDEX.md for %s", self.domain)
        text = self._text(self._topic_rows())
        if len(text.encode("utf-8")) > INDEX_HARD_LIMIT:
            text = self._text(self._summary_rows())
        logger.debug("Rendered level 1 INDEX.md with %s bytes", len(text.encode("utf-8")))
        return text

    def _text(self, route_rows: str) -> str:
        return (
            f"# {self._title()} index\n\n## Route by subject\n\n"
            "| Ask about | Read | Life cycle |\n| - | - | - |\n"
            f"{route_rows}\n## Route by source document\n\n"
            "| Document | Slug | Pages | Read |\n| - | - | -: | - |\n"
            f"{self._document_rows()}\n## Life cycle coverage\n\n"
            "| Stage | Topics | Status |\n| - | -: | - |\n"
            f"{self._coverage_rows()}\n## Coverage gaps\n\n{self._gap_text()}\n"
        )

    def _topic_rows(self) -> str:
        return "".join(
            f"| {route.subject} | {route.relative_path.as_posix()} | {', '.join(route.lifecycle)} |\n"
            for route in self.routes
        )

    def _summary_rows(self) -> str:
        return "".join(
            f"| {document.title} topics. Detail lives in the document index. | "
            f"documents/{document.slug}/INDEX.md | document-summary |\n"
            for document in self.documents
        )

    def _document_rows(self) -> str:
        return "".join(
            f"| {document.title} | {document.slug} | {document.pages} | documents/{document.slug}/INDEX.md |\n"
            for document in self.documents
        )

    def _coverage_rows(self) -> str:
        return "".join(f"| {tag} | {self.coverage[tag]} | {self._status(tag)} |\n" for tag in LIFECYCLE_TAGS)

    def _gap_text(self) -> str:
        gaps = [tag for tag in LIFECYCLE_TAGS if self.coverage[tag] == 0]
        if not gaps:
            return "No gaps found.\n"
        return "".join(f"- {tag} has no topic.\n" for tag in gaps)

    def _status(self, tag: str) -> str:
        return "covered" if self.coverage[tag] else "gap"

    def _title(self) -> str:
        return " ".join(part.capitalize() for part in self.domain.split("-"))


class SourcesRenderer:
    """Render the domain attribution table."""

    def __init__(
        self, documents: tuple[DocumentPackageInput, ...], allocator: CitationKeyAllocator, routes: list[TopicRoute]
    ) -> None:
        """Initialize the SourcesRenderer instance."""
        self.documents = documents
        self.allocator = allocator
        self.routes = routes

    def render(self, domain: str) -> str:
        """Run the render operation."""
        logger.info("Rendering sources.md for %s", domain)
        rows = "".join(self._row(domain, document) for document in self.documents)
        text = "# Sources\n\n| Key | Title | Author | Category | Pages | Converted | Origin | Markdown | PDF |\n"
        text += "| - | - | - | - | -: | - | - | - | - |\n" + rows
        logger.debug("Rendered sources.md for %s with %s rows", domain, len(self.documents))
        return text

    def _row(self, domain: str, document: DocumentPackageInput) -> str:
        key = self._key(domain, document)
        metadata = SourceMetadataReader().read(document)
        markdown = "<br>".join(self._display_path(path) for path in document.markdown_paths)
        title = str(metadata.get("title") or document.title)
        pages = int(str(metadata.get("pages") or document.pages))  # Convert source metadata through text for mypy.
        author = str(metadata.get("author") or document.author)
        source_date = metadata.get("modDate") or metadata.get("creationDate") or datetime.now(UTC).date()
        converted = str(document.converted or source_date)
        return (
            f"| {key} | {title} | {author} | {document.category} | {pages} | "
            f"{converted} | {document.public_origin} | {markdown} | {document.pdf_path.as_posix()} |\n"
        )

    def _key(self, domain: str, document: DocumentPackageInput) -> str:
        route_key = self._route_key(document.slug)
        if route_key:
            return route_key
        return self.allocator.allocate(domain, document.slug, document.title)

    def _route_key(self, slug: str) -> str:
        route = next((item for item in self.routes if item.relative_path.parts[1] == slug), None)
        return route.citation.split(" ", 1)[0].strip().strip('"').strip("'") if route else ""

    def _display_path(self, path: Path) -> str:
        parts = path.parts
        if "juniper-harvest-md" in parts:
            index = parts.index("juniper-harvest-md")
            return Path(*parts[index + 1 :]).as_posix()
        return path.as_posix()


class SourceMetadataReader:
    """Read attribution fields from converted source frontmatter."""

    def read(self, document: DocumentPackageInput) -> dict[str, object]:
        """Run the read operation."""
        logger.info("Reading source metadata for %s", document.slug)
        source_path = next((path for path in document.markdown_paths if path.exists()), None)
        metadata = self._read_path(source_path) if source_path else {}
        logger.debug("Read %s source metadata fields for %s", len(metadata), document.slug)
        return metadata

    def _read_path(self, path: Path | None) -> dict[str, object]:
        if path is None:
            return {}
        text = path.read_text(encoding="utf-8")
        frontmatter, _ = FrontMatterParser().parse(text)
        return frontmatter


class DocumentIndexRenderer:
    """Render a contract-shaped level 2 document index."""

    def __init__(self, document: DocumentPackageInput, key: str, routes: list[TopicRoute]) -> None:
        """Initialize the DocumentIndexRenderer instance."""
        self.document = document
        self.key = key
        self.routes = routes

    def render(self) -> str:
        """Run the render operation."""
        logger.info("Rendering level 2 index for %s", self.document.slug)
        text = self._text(self._topic_rows())
        if len(text.encode("utf-8")) > 10_240:
            text = self._text(self._compact_topic_rows())
        logger.debug("Rendered level 2 index for %s with %s bytes", self.document.slug, len(text.encode("utf-8")))
        return text

    def _text(self, rows: str) -> str:
        return (
            f"# {self.document.title} index\n\n## Source\n\n"
            f"Title: {self.document.title}.\n\nCategory: {self.document.category}.\n\nPages: {self.document.pages}.\n\n"
            f"Source file path: {self._markdown_paths()}.\n\nSource PDF path: {self.document.pdf_path.as_posix()}.\n\n"
            f"Split part count: {len(self.document.markdown_paths)}.\n\n## Topic route\n\n"
            "| Ask about | Topic | Read | Life cycle |\n| - | - | - | - |\n"
            f"{rows}\n## Life cycle map\n\n| Stage | Topics | Status |\n| - | -: | - |\n"
            f"{self._coverage_rows()}\n## Citation keys\n\n| Key | Source range | Topic |\n| - | - | - |\n"
            f"{self._citation_rows()}"
        )

    def _markdown_paths(self) -> str:
        return ", ".join(path.as_posix() for path in self.document.markdown_paths)

    def _topic_rows(self) -> str:
        return "".join(
            f"| {self._cell(route.subject, 95)} | {route.title} | {route.relative_path.name} | "
            f"{', '.join(route.lifecycle)} |\n"
            for route in self.routes
        )

    def _compact_topic_rows(self) -> str:
        return "".join(
            f"| {self._cell(route.subject, 42)} | {self._cell(route.title, 28)} | {route.relative_path.name} | "
            f"{', '.join(route.lifecycle)} |\n"
            for route in self.routes
        )

    def _cell(self, value: str, limit: int) -> str:
        text = value.strip().rstrip(".")
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

    def _coverage_rows(self) -> str:
        coverage = {tag: sum(1 for route in self.routes if tag in route.lifecycle) for tag in LIFECYCLE_TAGS}
        return "".join(
            f"| {tag} | {coverage[tag]} | {'covered' if coverage[tag] else 'gap'} |\n" for tag in LIFECYCLE_TAGS
        )

    def _citation_rows(self) -> str:
        return "".join(f"| {self.key} | {route.source_range} | {route.relative_path.name} |\n" for route in self.routes)


class SkillPackageValidator:
    """Validate a finished domain skill package against the locked schema."""

    FILE_LIMITS = {"SKILL.md": (SKILL_SOFT_LIMIT, SKILL_HARD_LIMIT), "INDEX.md": (INDEX_SOFT_LIMIT, INDEX_HARD_LIMIT)}

    def validate(self, package_dir: Path) -> ValidationResult:
        """Run the validate operation."""
        logger.info("Validating skill package %s", package_dir)
        errors: list[ValidationFinding] = []
        warnings: list[ValidationFinding] = []
        markdown_files = self._markdown_files(package_dir)
        self._required_files(package_dir, errors)
        self._layout(package_dir, markdown_files, errors)
        self._sizes(package_dir, markdown_files, errors, warnings)
        sources = self._sources(package_dir, errors)
        topic_names = self._topics(package_dir, sources, errors)
        self._skill_frontmatter(package_dir, errors)
        self._route_vocabulary(package_dir, errors)
        self._links(package_dir, markdown_files, errors)
        self._orphans(package_dir, markdown_files, errors)
        self._duplicates(package_dir, topic_names, errors)
        self._zero(markdown_files, errors, package_dir)
        logger.debug("Validated %s files with %s errors", len(markdown_files), len(errors))
        return ValidationResult(len(markdown_files), tuple(errors), tuple(warnings))

    def _markdown_files(self, package_dir: Path) -> list[Path]:
        logger.info("Listing Markdown files in %s", package_dir)
        files = sorted(package_dir.rglob("*.md")) if package_dir.exists() else []
        logger.debug("Found %s Markdown files in %s", len(files), package_dir)
        return files

    def _required_files(self, package_dir: Path, errors: list[ValidationFinding]) -> None:
        logger.info("Checking required level 1 files")
        for name in ("SKILL.md", "INDEX.md", "sources.md"):
            if not (package_dir / name).exists():
                errors.append(ValidationFinding(package_dir / name, "required file is missing", "missing"))
        logger.debug("Required level 1 file check produced %s errors", len(errors))

    def _layout(self, package_dir: Path, files: list[Path], errors: list[ValidationFinding]) -> None:
        logger.info("Checking package path layout")
        if not re.match(r"^juniper-[a-z0-9]+(-[a-z0-9]+)*$", package_dir.name):
            errors.append(ValidationFinding(package_dir, "package directory name is invalid", package_dir.name))
        for path in files:
            self._layout_path(package_dir, path, errors)
        self._overview_files(package_dir, errors)
        logger.debug("Checked package path layout for %s files", len(files))

    def _layout_path(self, package_dir: Path, path: Path, errors: list[ValidationFinding]) -> None:
        relative = path.relative_to(package_dir)
        parts = relative.parts
        if len(parts) == 1 and path.name in {"SKILL.md", "INDEX.md", "sources.md"}:
            return
        if len(parts) == 3 and parts[0] == "documents":
            self._document_path(path, parts, errors)
            return
        errors.append(ValidationFinding(path, "Markdown file path is not allowed by the package layout", str(relative)))

    def _document_path(self, path: Path, parts: tuple[str, ...], errors: list[ValidationFinding]) -> None:
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", parts[1]):
            errors.append(ValidationFinding(path, "document directory name is invalid", parts[1]))
        valid_topic = re.match(r"^[0-9][0-9]-[a-z0-9]+(-[a-z0-9]+)*\.md$", parts[2])
        if parts[2] != "INDEX.md" and not valid_topic:
            errors.append(ValidationFinding(path, "topic file name is invalid", parts[2]))

    def _overview_files(self, package_dir: Path, errors: list[ValidationFinding]) -> None:
        documents_dir = package_dir / "documents"
        if not documents_dir.exists():
            return
        for document_dir in documents_dir.iterdir():
            if document_dir.is_dir() and not (document_dir / "00-overview.md").exists():
                errors.append(ValidationFinding(document_dir, "00-overview.md is missing", "missing"))

    def _sizes(
        self, package_dir: Path, files: list[Path], errors: list[ValidationFinding], warnings: list[ValidationFinding]
    ) -> None:
        logger.info("Checking Markdown file size limits")
        for path in files:
            self._size(package_dir, path, errors, warnings)
        logger.debug("Checked size limits for %s files", len(files))

    def _size(
        self, package_dir: Path, path: Path, errors: list[ValidationFinding], warnings: list[ValidationFinding]
    ) -> None:
        size = path.stat().st_size
        soft, hard = self._limits(package_dir, path)
        if size > hard:
            errors.append(ValidationFinding(path, "file exceeds the hard size limit", str(size)))
        elif size > soft:
            warnings.append(ValidationFinding(path, "file exceeds the soft size limit", str(size), "warning"))

    def _limits(self, package_dir: Path, path: Path) -> tuple[int, int]:
        if path.name == "sources.md":
            return SOURCE_SOFT_LIMIT, SOURCE_HARD_LIMIT
        if path.parent == package_dir:
            return self.FILE_LIMITS.get(path.name, (INDEX_SOFT_LIMIT, INDEX_HARD_LIMIT))
        if path.name == "INDEX.md":
            return 6_144, 10_240
        return 12_288, 20_480

    def _sources(self, package_dir: Path, errors: list[ValidationFinding]) -> set[str]:
        logger.info("Reading citation keys from sources.md")
        path = package_dir / "sources.md"
        if not path.exists():
            logger.debug("No sources.md exists for citation validation")
            return set()
        rows = self._table_rows(path)
        keys = {row[0] for row in rows if row}
        self._duplicate_source_keys(path, rows, errors)
        logger.debug("Read %s citation keys from sources.md", len(keys))
        return keys

    def _table_rows(self, path: Path) -> list[list[str]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [line.strip("|").split("|") for line in lines if line.startswith("| ") and " - " not in line]
        return [[cell.strip() for cell in row] for row in rows[1:]]

    def _duplicate_source_keys(self, path: Path, rows: list[list[str]], errors: list[ValidationFinding]) -> None:
        keys = [row[0] for row in rows if row]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        for key in duplicates:
            errors.append(ValidationFinding(path, "duplicate citation key", key))

    def _topics(self, package_dir: Path, sources: set[str], errors: list[ValidationFinding]) -> list[str]:
        logger.info("Validating topic files")
        names: list[str] = []
        for path in self._topic_paths(package_dir):
            names.append(self._topic(path, sources, errors))
        logger.debug("Validated %s topic files", len(names))
        return names

    def _topic_paths(self, package_dir: Path) -> list[Path]:
        return sorted((package_dir / "documents").glob("*/*.md")) if (package_dir / "documents").exists() else []

    def _topic(self, path: Path, sources: set[str], errors: list[ValidationFinding]) -> str:
        if path.name == "INDEX.md":
            return ""
        frontmatter, body = FrontMatterParser().parse(path.read_text(encoding="utf-8"))
        self._topic_frontmatter(path, frontmatter, sources, errors)
        self._cards(path, body, sources, errors)
        return str(frontmatter.get("topic", ""))

    def _topic_frontmatter(
        self, path: Path, frontmatter: dict[str, Any], sources: set[str], errors: list[ValidationFinding]
    ) -> None:
        missing = [name for name in ("topic", "domain", "document", "lifecycle", "sources") if name not in frontmatter]
        for name in missing:
            errors.append(ValidationFinding(path, "topic frontmatter field is missing", name))
        self._lifecycle_tags(path, frontmatter.get("lifecycle"), errors)
        self._source_tags(path, frontmatter.get("sources"), sources, errors)

    def _lifecycle_tags(self, path: Path, value: object, errors: list[ValidationFinding]) -> None:
        if not isinstance(value, list) or not value:
            errors.append(ValidationFinding(path, "topic has no life cycle tag", str(value)))
            return
        for tag in value:
            if tag not in LIFECYCLE_TAGS:
                errors.append(ValidationFinding(path, "topic has an invalid life cycle tag", str(tag)))

    def _source_tags(self, path: Path, value: object, sources: set[str], errors: list[ValidationFinding]) -> None:
        if not isinstance(value, list) or not value:
            errors.append(ValidationFinding(path, "topic has no citation key", str(value)))
            return
        for citation in value:
            self._citation_resolves(path, str(citation), sources, errors)

    def _cards(self, path: Path, body: str, sources: set[str], errors: list[ValidationFinding]) -> None:
        logger.info("Validating cards in %s", path.name)
        cards = [line for line in body.splitlines() if line.startswith("- ")]
        for line in cards:
            self._card(path, line, sources, errors)
        logger.debug("Validated %s cards in %s", len(cards), path.name)

    def _card(self, path: Path, line: str, sources: set[str], errors: list[ValidationFinding]) -> None:
        if not re.match(r"^- (MUST|SHOULD|INFO): ", line):
            errors.append(ValidationFinding(path, "card lacks a class mark", line[:80]))
        match = re.search(r"\[([A-Z0-9-]{3,16} .+?)\]$", line)
        if not match:
            errors.append(ValidationFinding(path, "card lacks a citation key", line[:80]))
            return
        self._citation_resolves(path, match.group(1), sources, errors)

    def _citation_resolves(self, path: Path, citation: str, sources: set[str], errors: list[ValidationFinding]) -> None:
        key = citation.split(" ", 1)[0].strip().strip('"').strip("'")
        if key not in sources:
            errors.append(ValidationFinding(path, "citation key does not resolve to sources.md", citation))

    def _skill_frontmatter(self, package_dir: Path, errors: list[ValidationFinding]) -> None:
        logger.info("Validating SKILL.md frontmatter")
        path = package_dir / "SKILL.md"
        if not path.exists():
            return
        frontmatter, _ = FrontMatterParser().parse(path.read_text(encoding="utf-8"))
        for name in ("name", "description", "license", "metadata"):
            if name not in frontmatter:
                errors.append(ValidationFinding(path, "SKILL.md frontmatter field is missing", name))
        self._skill_name(path, frontmatter.get("name"), package_dir.name, errors)
        self._skill_description(path, frontmatter.get("description"), errors)
        self._skill_metadata(path, frontmatter.get("metadata"), errors)
        logger.debug("Validated SKILL.md frontmatter")

    def _skill_name(self, path: Path, value: object, package_name: str, errors: list[ValidationFinding]) -> None:
        if value != package_name:
            errors.append(ValidationFinding(path, "SKILL.md name does not match the package directory", str(value)))

    def _skill_description(self, path: Path, value: object, errors: list[ValidationFinding]) -> None:
        if not isinstance(value, str):
            errors.append(ValidationFinding(path, "SKILL.md description is not text", str(type(value))))
            return
        if not 250 <= len(value) <= 1200:
            errors.append(ValidationFinding(path, "SKILL.md description length is invalid", str(len(value))))

    def _skill_metadata(self, path: Path, value: object, errors: list[ValidationFinding]) -> None:
        if not isinstance(value, dict):
            errors.append(ValidationFinding(path, "SKILL.md metadata is not a map", str(type(value))))
            return
        for name in ("feature", "domain", "documents", "topics", "source_pages", "built"):
            if name not in value:
                errors.append(ValidationFinding(path, "SKILL.md metadata field is missing", name))
        self._metadata_types(path, value, errors)

    def _route_vocabulary(self, package_dir: Path, errors: list[ValidationFinding]) -> None:
        logger.info("Validating SKILL.md route vocabulary")
        path = package_dir / "SKILL.md"
        if not path.exists():
            logger.debug("Skipped route vocabulary validation because SKILL.md is absent")
            return
        rows = self._table_rows(path)
        for row in rows:
            self._route_row_vocabulary(path, row, errors)
        logger.debug("Validated route vocabulary for %s rows", len(rows))

    def _route_row_vocabulary(self, path: Path, row: list[str], errors: list[ValidationFinding]) -> None:
        if len(row) < 2:
            return
        subject = row[0].strip()
        destination = row[1].strip("` ")
        if self._restates_destination(subject, destination):
            errors.append(ValidationFinding(path, "route row restates the destination file name", subject))
        if subject.lower().startswith("read about "):
            errors.append(ValidationFinding(path, "route row must use user vocabulary", subject))

    def _restates_destination(self, subject: str, destination: str) -> bool:
        stem = Path(destination).stem  # Read the topic slug that the row points to.
        readable = re.sub(r"^\d+-", "", stem).replace("-", " ").strip()  # Remove sort prefixes from file names.
        normalized_subject = self._route_words(subject)  # Normalize the user-facing route phrase.
        normalized_target = self._route_words(readable)  # Normalize the destination-derived phrase.
        return bool(normalized_target and normalized_subject == normalized_target)  # Flag file-name restatement.

    def _route_words(self, value: str) -> str:
        text = re.sub(r"^read about\s+", "", value.lower()).strip()  # Remove the known stale stub prefix.
        return re.sub(r"[^a-z0-9]+", " ", text).strip()  # Compare only words so punctuation cannot hide a stub.

    def _metadata_types(self, path: Path, value: dict[str, object], errors: list[ValidationFinding]) -> None:
        integer_fields = ("documents", "topics", "source_pages")
        for name in integer_fields:
            if name in value and not isinstance(value[name], int):
                errors.append(ValidationFinding(path, "SKILL.md metadata field is not an integer", name))

    def _links(self, package_dir: Path, files: list[Path], errors: list[ValidationFinding]) -> None:
        logger.info("Checking internal Markdown links")
        for path in files:
            self._file_links(package_dir, path, errors)
        logger.debug("Checked internal links for %s files", len(files))

    def _file_links(self, package_dir: Path, path: Path, errors: list[ValidationFinding]) -> None:
        text = path.read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^):#]+(?:\.md)?)\)|`([^`]+\.md)`", text)
        for first, second in links:
            self._link(package_dir, path, first or second, errors)

    def _link(self, package_dir: Path, path: Path, target: str, errors: list[ValidationFinding]) -> None:
        if target.startswith("http") or target == "INDEX.md":
            return
        resolved = (
            (path.parent / target).resolve()
            if not target.startswith("documents/")
            else (package_dir / target).resolve()
        )
        if not resolved.exists():
            errors.append(ValidationFinding(path, "internal link is broken", target))

    def _orphans(self, package_dir: Path, files: list[Path], errors: list[ValidationFinding]) -> None:
        logger.info("Checking for orphan topic files")
        index_text = "\n".join(path.read_text("utf-8") for path in files if path.name == "INDEX.md")
        for topic in self._topic_paths(package_dir):
            if topic.name != "INDEX.md" and topic.name not in index_text:
                errors.append(ValidationFinding(topic, "topic file is absent from the index", topic.name))
        logger.debug("Checked orphan status for topic files")

    def _duplicates(self, package_dir: Path, names: list[str], errors: list[ValidationFinding]) -> None:
        logger.info("Checking for duplicate topic names")
        values = [name for name in names if name]
        for name in sorted({name for name in values if values.count(name) > 1}):
            errors.append(ValidationFinding(package_dir, "duplicate topic name", name))
        logger.debug("Checked duplicate topic names")

    def _zero(self, files: list[Path], errors: list[ValidationFinding], package_dir: Path) -> None:
        if files:
            return
        errors.append(ValidationFinding(package_dir, "validator checked zero files", "0"))
