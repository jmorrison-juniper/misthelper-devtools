"""Install generated Juniper skills with Windows directory junctions."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess  # nosec B404 - This module starts fixed local commands without a shell.
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

DEFAULT_TOKEN_WINDOW = 1_000_000  # Use the corrected Opus 5 Max context window for reports.
TOKEN_CHAR_WIDTH = 4  # Use the requested approximate character-to-token ratio.
DESCRIPTION_TOKEN_BUDGET = 100  # Report descriptions that pass the target routing budget.
MIN_REGISTER_ALL_PAGES = 20  # Register the qualifying document set by default.
WARNING_SHARE = 30.0  # Warn operators when the index becomes a large context cost.
FORCE_SHARE = 50.0  # Require force when the projection uses half of the window.
REFUSAL_SHARE = 70.0  # Refuse plans that consume too much working context.
CATALOG_FILE_NAME = "SKILL_CATALOG.json"  # Give routers a stable package catalog file.


@dataclass(frozen=True)
class InstallOutcome:
    """State the result of one install, verify, or uninstall action."""

    target: Path
    action: str
    detail: str


@dataclass(frozen=True)
class SkillMetadata:
    """State the metadata that an agent host reads for one skill."""

    slug: str
    name: str
    description: str
    title: str
    domain: str
    kind: str
    package_path: Path
    page_count: int = 0
    topic_count: int = 0
    lifecycle_tags: tuple[str, ...] = field(default_factory=tuple)
    routing_keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def index_text(self) -> str:
        """Run the index text operation."""
        return f"name: {self.name}\ndescription: {self.description}\n"  # Match the host index shape.


@dataclass(frozen=True)
class CostReport:
    """State the projected cost of one registration plan."""

    skills: tuple[SkillMetadata, ...]
    token_count: int
    window_share: float
    context_window: int
    over_budget_descriptions: tuple[SkillMetadata, ...] = field(default_factory=tuple)
    action: str = "ok"
    message: str = ""

    @property
    def skill_count(self) -> int:
        """Run the skill count operation."""
        return len(self.skills)  # Give callers the measured registration size.


class SkillCatalogIndex:
    """Build and search the complete Juniper skill package catalog."""

    def __init__(self, store_path: Path) -> None:
        """Initialize the SkillCatalogIndex instance."""
        self.store_path = store_path  # Keep one canonical store root for catalog reads and writes.
        self.router_path = self.store_path / "skills"  # Keep routing tier skills in the stable legacy folder.
        self.package_path = self.store_path / "packages"  # Keep document skill packages below their domain.
        self.catalog_path = self.store_path / CATALOG_FILE_NAME  # Give routers one searchable catalog file.

    def rebuild(self) -> list[SkillMetadata]:
        """Run the rebuild operation."""
        logger.info("Building the Juniper skill catalog index")  # Log the full catalog scan.
        skills = self.available_packages()  # Read package metadata from the canonical store.
        records = [self._record(skill) for skill in skills]  # Convert package metadata to stable JSON records.
        self.store_path.mkdir(parents=True, exist_ok=True)  # Ensure the canonical store exists before the write.
        self.catalog_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")  # Write router input.
        logger.debug("Built the Juniper skill catalog index with %d packages", len(records))  # Record package count.
        return skills  # Return the measured metadata to the installer.

    def search(self, keyword: str) -> list[SkillMetadata]:
        """Run the search operation."""
        logger.info("Searching the Juniper skill catalog for %s", keyword)  # Log the operator search request.
        needle = keyword.casefold()  # Normalize the search text for predictable matching.
        skills = self._catalog_or_rebuild()  # Use the catalog when present, but recover from a missing file.
        matches = [skill for skill in skills if self._matches(skill, needle)]  # Find each package that has the term.
        logger.debug("Juniper skill catalog search returned %d packages", len(matches))  # Record match count.
        return matches  # Return structured matches for the CLI.

    def available_routers(self) -> list[SkillMetadata]:
        """Run the available routers operation."""
        logger.info("Reading available Juniper routing skills")  # Log the router inventory read.
        routers = self._skill_dirs(self.router_path, "router")  # Read legacy skill packages as routing tier skills.
        logger.debug("Read %d available Juniper routing skills", len(routers))  # Record router count.
        return routers  # Return router metadata for default registration.

    def available_packages(self) -> list[SkillMetadata]:
        """Run the available packages operation."""
        logger.info("Reading available Juniper document skill packages")  # Log the package inventory read.
        packages = self._package_dirs()  # Read each domain package from the canonical store.
        logger.debug("Read %d available Juniper document skill packages", len(packages))  # Record package count.
        return packages  # Return package metadata for registration and search.

    def available_skills(self) -> list[SkillMetadata]:
        """Run the available skills operation."""
        logger.info("Reading all available Juniper skills")  # Log the combined inventory read.
        skills = self.available_routers() + self.available_packages()  # Combine routers and document packages.
        logger.debug("Read %d total available Juniper skills", len(skills))  # Record combined count.
        return skills  # Return all sources for list and lookup operations.

    def packages_for_domain(self, domain: str) -> list[SkillMetadata]:
        """Run the packages for domain operation."""
        logger.info("Reading Juniper packages for domain %s", domain)  # Log the domain selection.
        packages = [skill for skill in self.available_packages() if skill.domain == domain]  # Select one domain.
        logger.debug("Read %d Juniper packages for domain %s", len(packages), domain)  # Record package count.
        return packages  # Return the selected domain packages.

    def find(self, slug: str) -> SkillMetadata:
        """Run the find operation."""
        logger.info("Finding Juniper skill package %s", slug)  # Log the package lookup.
        for skill in self.available_skills():  # Search all registration sources by slug or skill name.
            if slug in {skill.slug, skill.name}:  # Accept the operator slug and the host skill name.
                logger.debug("Found Juniper skill package %s at %s", slug, skill.package_path)  # Record source path.
                return skill  # Return the matching skill metadata.
        raise FileNotFoundError(f"Canonical skill package not found: {slug}")  # Name the missing package.

    def _catalog_or_rebuild(self) -> list[SkillMetadata]:
        if not self.catalog_path.is_file():  # Rebuild when no searchable catalog exists yet.
            return self.rebuild()  # Create the catalog before returning metadata.
        records = json.loads(self.catalog_path.read_text(encoding="utf-8"))  # Read the existing searchable catalog.
        return [self._metadata_from_record(record) for record in records]  # Return package metadata from JSON.

    def _skill_dirs(self, root: Path, kind: str) -> list[SkillMetadata]:
        if not root.exists():  # Treat a missing source folder as an empty source set.
            return []  # Keep initialization and dry-run modes idempotent.
        paths = sorted(path for path in root.iterdir() if path.is_dir())  # Use directories as installable packages.
        return [self._metadata_from_path(path, kind, "routing") for path in paths]  # Parse each package entry file.

    def _package_dirs(self) -> list[SkillMetadata]:
        if not self.package_path.exists():  # Treat a missing package folder as an empty source set.
            return []  # Keep pre-package stores valid.
        packages: list[SkillMetadata] = []  # Collect every package below every domain.
        for domain_path in sorted(path for path in self.package_path.iterdir() if path.is_dir()):  # Scan domains.
            packages.extend(self._domain_packages(domain_path))  # Add packages from this domain.
        return packages  # Return a stable order for reports.

    def _domain_packages(self, domain_path: Path) -> list[SkillMetadata]:
        paths = sorted(path for path in domain_path.iterdir() if path.is_dir())  # Read document folders in the domain.
        return [self._metadata_from_path(path, "package", domain_path.name) for path in paths]  # Parse package files.

    def _metadata_from_path(self, path: Path, kind: str, domain: str) -> SkillMetadata:
        skill_file = path / "SKILL.md"  # Use the host entry file as the registration source of truth.
        fields = self._front_matter(skill_file)  # Parse the fields that the agent host loads into context.
        text = self._read_text(skill_file)  # Read content for title and keyword extraction.
        title = fields.get("title") or self._title_from_text(text) or fields.get("name", path.name)  # Pick title.
        description = fields.get("description", "")  # Keep the exact registered skill description.
        return SkillMetadata(  # Return one immutable catalog row.
            slug=path.name,
            name=fields.get("name", path.name),
            description=description,
            title=title,
            domain=domain,
            kind=kind,
            package_path=path,
            page_count=self._page_count(path),
            topic_count=self._topic_count(path),
            lifecycle_tags=self._lifecycle_tags(path),
            routing_keywords=self._routing_keywords(path, title, description, domain),
        )

    def _metadata_from_record(self, record: dict[str, object]) -> SkillMetadata:
        path = self.store_path / str(record["relative_path"])  # Restore the package path from catalog JSON.
        life_cycle_tags = cast(list[object], record["life_cycle_tags"])  # Read list values from catalog JSON.
        routing_keywords = cast(list[object], record["routing_keywords"])  # Read keyword values from catalog JSON.
        return SkillMetadata(  # Rebuild the metadata type for search callers.
            slug=str(record["slug"]),
            name=str(record["name"]),
            description=str(record["description"]),
            title=str(record["title"]),
            domain=str(record["domain"]),
            kind=str(record["kind"]),
            package_path=path,
            page_count=int(str(record["page_count"])),
            topic_count=int(str(record["topic_count"])),
            lifecycle_tags=tuple(str(item) for item in life_cycle_tags),
            routing_keywords=tuple(str(item) for item in routing_keywords),
        )

    def _record(self, skill: SkillMetadata) -> dict[str, object]:
        return {  # Persist fields that router skills need for recall.
            "slug": skill.slug,
            "name": skill.name,
            "description": skill.description,
            "title": skill.title,
            "domain": skill.domain,
            "kind": skill.kind,
            "page_count": skill.page_count,
            "topic_count": skill.topic_count,
            "life_cycle_tags": list(skill.lifecycle_tags),
            "routing_keywords": list(skill.routing_keywords),
            "relative_path": str(skill.package_path.relative_to(self.store_path)),
        }

    def _front_matter(self, skill_file: Path) -> dict[str, str]:
        text = self._read_text(skill_file)  # Read the entry file once for simple field parsing.
        fields: dict[str, str] = {}  # Collect scalar YAML front matter values without adding a dependency.
        if not text.startswith("---"):  # Treat a plain Markdown file as a valid but sparse skill file.
            return fields  # Return defaults for packages that lack front matter.
        for line in text.split("---", 2)[1].splitlines():  # Read only the first front matter block.
            if ":" in line:  # Accept simple key and value pairs.
                key, value = line.split(":", 1)  # Split once so descriptions can contain colons.
                fields[key.strip()] = value.strip().strip('"')  # Normalize the scalar value for catalog use.
        return fields  # Return parsed values for metadata creation.

    def _read_text(self, path: Path) -> str:
        if not path.is_file():  # Missing optional files should not stop the complete catalog.
            return ""  # Return empty text so callers can use defaults.
        return path.read_text(encoding="utf-8")  # Read UTF-8 Markdown generated by the skill factory.

    def _title_from_text(self, text: str) -> str:
        for line in text.splitlines():  # Search headings in source order.
            if line.startswith("# "):  # Use the top-level heading as the human title.
                return line.removeprefix("# ").strip()  # Strip Markdown syntax from the title.
        return ""  # Return an empty title when the file has no heading.

    def _page_count(self, path: Path) -> int:
        text = self._read_text(path / "sources.md")  # Prefer the package source record for page counts.
        match = re.search(r"page(?:s|_count)?\D+(\d+)", text, flags=re.IGNORECASE)  # Read common page forms.
        return int(match.group(1)) if match else 0  # Return zero when generated metadata has no page count.

    def _topic_count(self, path: Path) -> int:
        markdown_paths = [item for item in path.rglob("*.md") if item.name not in self._entry_names()]  # Count topics.
        return len(markdown_paths)  # Return the measured topic file count.

    def _lifecycle_tags(self, path: Path) -> tuple[str, ...]:
        text = self._combined_text(path)  # Search generated files for normalized life cycle tags.
        tags = [tag for tag in ("day0", "day1", "day2", "day2plus") if tag in text.casefold()]  # Keep tag order.
        return tuple(tags)  # Return a stable tuple for JSON and tests.

    def _routing_keywords(self, path: Path, title: str, description: str, domain: str) -> tuple[str, ...]:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", " ".join([title, description, domain]))  # Extract terms.
        headings = re.findall(r"^#{1,3}\s+(.+)$", self._combined_text(path), flags=re.MULTILINE)  # Read topic hints.
        keywords = {word.casefold() for word in words}  # Normalize title and description words.
        keywords.update(word.casefold() for heading in headings for word in heading.split())  # Add heading words.
        return tuple(sorted(keyword.strip(".,:()[]") for keyword in keywords if keyword))  # Return searchable terms.

    def _combined_text(self, path: Path) -> str:
        texts = [self._read_text(item) for item in sorted(path.glob("*.md"))]  # Read package-level Markdown files only.
        return "\n".join(texts)  # Join files for simple metadata extraction.

    def _entry_names(self) -> set[str]:
        return {"SKILL.md", "INDEX.md", "sources.md"}  # Exclude package control files from topic counts.

    def _matches(self, skill: SkillMetadata, needle: str) -> bool:
        haystack = " ".join(  # Build one compact searchable string per package.
            [skill.slug, skill.name, skill.title, skill.domain, skill.description, " ".join(skill.routing_keywords)]
        )
        return needle in haystack.casefold()  # Use substring search so partial operator keywords work.


class SkillCostEstimator:
    """Estimate the skill-index context cost for selected registrations."""

    def __init__(self, context_window: int = DEFAULT_TOKEN_WINDOW) -> None:
        """Initialize the SkillCostEstimator instance."""
        self.context_window = context_window  # Store the model context window selected by the operator.

    def estimate(self, skills: list[SkillMetadata], force: bool = False) -> CostReport:
        """Run the estimate operation."""
        logger.info("Estimating Juniper skill registration token cost")  # Log the cost calculation.
        token_count = sum(math.ceil(len(skill.index_text) / TOKEN_CHAR_WIDTH) for skill in skills)  # Estimate tokens.
        window_share = (token_count / self.context_window) * 100 if self.context_window else 0.0  # Convert tokens.
        over_budget = tuple(  # Find descriptions that the package generator should tighten.
            skill for skill in skills if self._description_tokens(skill) > DESCRIPTION_TOKEN_BUDGET
        )
        action, message = self._decision(window_share, force)  # Apply safety thresholds to the projection.
        logger.debug("Estimated %d tokens for %d skills", token_count, len(skills))  # Record measured cost.
        return CostReport(  # Return the full report with the model context and description budget findings.
            tuple(skills), token_count, window_share, self.context_window, over_budget, action, message
        )

    def _decision(self, window_share: float, force: bool) -> tuple[str, str]:
        if window_share > REFUSAL_SHARE:  # Block plans that consume too much working context.
            return "refused", "This registration uses more than 70 percent of the context window."  # Explain refusal.
        if window_share > FORCE_SHARE and not force:  # Require a deliberate override for large registrations.
            message = "This registration uses more than half of the context window."  # Explain force need.
            return "refused", message  # Return the blocked threshold decision.
        if window_share > WARNING_SHARE:  # Warn when the index cost becomes operationally visible.
            return "warned", "This registration uses more than 30 percent of the context window."  # Explain warning.
        return "ok", "This registration stays below the 30 percent warning threshold."  # State the safe result.

    def _description_tokens(self, skill: SkillMetadata) -> int:
        return math.ceil(len(skill.description) / TOKEN_CHAR_WIDTH)  # Estimate only the description budget cost.


class SkillInstaller:
    """Publish canonical Juniper skills into each local agent host."""

    def __init__(
        self,
        store_path: Path | None = None,
        repo_path: Path | None = None,
        home_path: Path | None = None,
        context_window: int = DEFAULT_TOKEN_WINDOW,
    ) -> None:
        """Initialize the SkillInstaller instance."""
        self.home_path = home_path or Path.home()  # Use the caller home so tests can isolate host paths.
        self.repo_path = repo_path or Path.cwd()  # Use the active worktree for the repository skill target.
        self.store_path = store_path or self.home_path / "juniper-agent-skills"  # Keep the store outside OneDrive.
        self.skills_path = self.store_path / "skills"  # Keep routing tier skills below one canonical folder.
        self.packages_path = self.store_path / "packages"  # Keep document packages below domain folders.
        self.catalog = SkillCatalogIndex(self.store_path)  # Share package discovery and catalog writing.
        self.estimator = SkillCostEstimator(context_window)  # Share threshold logic for all registration modes.

    def initialize_store(self) -> list[InstallOutcome]:
        """Run the initialize store operation."""
        logger.info("Preparing canonical Juniper skill store at %s", self.store_path)  # Log the store creation step.
        self.skills_path.mkdir(parents=True, exist_ok=True)  # Create the routing tier folder.
        self.packages_path.mkdir(parents=True, exist_ok=True)  # Create the per-document package root.
        logger.debug("Prepared canonical skill store at %s", self.store_path)  # Record the folder that now exists.
        self._write_store_file("README.md", self._readme_text())  # Add operator guidance for the store.
        self._write_store_file(".gitignore", self._store_gitignore_text())  # Keep local artifacts out of the store.
        self._write_store_file("LICENSE", self._license_text())  # Publish the selected knowledge-corpus license.
        return self.generate_catalog()  # Generate the catalog so no operator edits it by hand.

    def install_skill(self, skill_name: str) -> list[InstallOutcome]:
        """Run the install skill operation."""
        logger.info("Installing Juniper skill %s", skill_name)  # Log the requested skill publication.
        outcomes = self._install_metadata(self.catalog.find(skill_name))  # Install one source with safety checks.
        if any(outcome.action == "refused" for outcome in outcomes):  # Preserve legacy refusal behavior.
            return outcomes  # Do not write catalogs after a refused install.
        return outcomes + self.generate_catalog()  # Refresh catalogs after a successful install.

    def install_all(self) -> list[InstallOutcome]:
        """Run the install all operation."""
        logger.info("Installing all canonical Juniper skills")  # Log the legacy batch publication step.
        outcomes = self._install_many(self.catalog.available_skills())  # Publish routers and document packages.
        logger.debug("Installed all Juniper skills with %d outcomes", len(outcomes))  # Record the batch result count.
        return outcomes  # Return the full report to the caller.

    def install_routers(self, force: bool = False, dry_run: bool = False) -> tuple[CostReport, list[InstallOutcome]]:
        """Run the install routers operation."""
        logger.info("Installing Juniper routing tier skills")  # Log the default registration mode.
        return self._install_plan(self.catalog.available_routers(), force, dry_run)  # Publish routing tier only.

    def install_domain(
        self, domain: str, force: bool = False, dry_run: bool = False
    ) -> tuple[CostReport, list[InstallOutcome]]:
        """Run the install domain operation."""
        logger.info("Installing Juniper package domain %s", domain)  # Log the selected domain mode.
        packages = self.catalog.packages_for_domain(domain)  # Read all packages in the requested domain.
        return self._install_plan(packages, force, dry_run)  # Publish the measured domain plan.

    def install_packages(
        self, slugs: list[str], force: bool = False, dry_run: bool = False
    ) -> tuple[CostReport, list[InstallOutcome]]:
        """Run the install packages operation."""
        logger.info("Installing selected Juniper packages")  # Log the selected package mode.
        packages = [self.catalog.find(slug) for slug in slugs]  # Resolve each operator slug before cost checks.
        return self._install_plan(packages, force, dry_run)  # Publish the measured package plan.

    def install_all_packages(
        self, force: bool = False, dry_run: bool = False
    ) -> tuple[CostReport, list[InstallOutcome]]:
        """Run the install all packages operation."""
        logger.info("Installing every Juniper document package")  # Log the high-cost registration mode.
        packages = self._qualifying_packages()  # Select documents that are large enough for one skill each.
        return self._install_plan(packages, force, dry_run)  # Publish the qualifying document package set.

    def unregister_all(self) -> list[InstallOutcome]:
        """Run the unregister all operation."""
        logger.info("Unregistering every canonical Juniper skill")  # Log the batch removal mode.
        outcomes = self._uninstall_many(self.catalog.available_skills())  # Remove junctions for all known skills.
        logger.debug("Unregistered every canonical Juniper skill with %d outcomes", len(outcomes))  # Record count.
        return outcomes + self.generate_catalog()  # Refresh the searchable catalog after removal.

    def uninstall_skill(self, skill_name: str) -> list[InstallOutcome]:
        """Run the uninstall skill operation."""
        logger.info("Uninstalling Juniper skill %s", skill_name)  # Log the requested skill removal.
        skill = self.catalog.find(skill_name)  # Resolve the slug to the host registration name.
        outcomes = [self._remove_junction(path) for path in self._target_paths(skill.name)]  # Remove each junction.
        logger.debug("Uninstalled Juniper skill %s with %d outcomes", skill_name, len(outcomes))  # Count removals.
        return outcomes + self.generate_catalog()  # Keep the catalog current after removal.

    def verify_skill(self, skill_name: str) -> list[InstallOutcome]:
        """Run the verify skill operation."""
        logger.info("Verifying Juniper skill %s", skill_name)  # Log the verification request.
        skill = self.catalog.find(skill_name)  # Resolve the requested skill from the canonical store.
        outcomes = [self._verify_target(path, skill.package_path) for path in self._target_paths(skill.name)]  # Check.
        logger.debug("Verified Juniper skill %s with %d outcomes", skill_name, len(outcomes))  # Count records.
        return outcomes  # Return the host-by-host verification result.

    def verify_all(self) -> list[InstallOutcome]:
        """Run the verify all operation."""
        logger.info("Verifying all canonical Juniper skills")  # Log the batch verification request.
        outcomes: list[InstallOutcome] = []  # Collect every verification result for the caller.
        for skill in self.catalog.available_skills():  # Verify each canonical package that exists.
            outcomes.extend(self.verify_skill(skill.slug))  # Reuse the single-skill verification path.
        logger.debug("Verified all Juniper skills with %d outcomes", len(outcomes))  # Record the result count.
        return outcomes  # Return the full verification report.

    def generate_catalog(self) -> list[InstallOutcome]:
        """Run the generate catalog operation."""
        logger.info("Generating Juniper skill catalogs")  # Log the catalog write.
        packages = self.catalog.rebuild()  # Write the JSON catalog of all document packages.
        skills = self.catalog.available_routers() + packages  # Include routers in the human catalog.
        lines = self._catalog_lines(skills)  # Build the human-readable catalog from measured metadata.
        catalog_path = self.store_path / "CATALOG.md"  # Keep the generated catalog at the store root.
        catalog_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # Write UTF-8 for local tools.
        logger.debug("Generated Juniper catalogs with %d package records", len(skills))  # Record catalog size.
        return [InstallOutcome(catalog_path, "generated", "catalog updated from canonical skills")]  # Report write.

    def list_inventory(self) -> list[str]:
        """Run the list inventory operation."""
        logger.info("Listing Juniper skill inventory")  # Log the operator inventory request.
        lines = ["Installed Juniper skills:"]  # Start with registered skills for action planning.
        lines.extend(self._installed_lines())  # Add installed sources from target junctions.
        lines.extend(["", "Available Juniper skills:"])  # Separate installed and available records.
        lines.extend(self._available_lines())  # Add every canonical registration source.
        logger.debug("Built Juniper skill inventory with %d lines", len(lines))  # Record report size.
        return lines  # Return lines so the CLI can print them.

    def search_catalog(self, keyword: str) -> list[SkillMetadata]:
        """Run the search catalog operation."""
        logger.info("Searching Juniper skill inventory for %s", keyword)  # Log the search mode.
        return self.catalog.search(keyword)  # Delegate search to the catalog index.

    def _qualifying_packages(self) -> list[SkillMetadata]:
        logger.info("Selecting qualifying Juniper document packages")  # Log the default registration filter.
        packages = self.catalog.available_packages()  # Read all document packages before applying the page filter.
        selected = [package for package in packages if package.page_count >= MIN_REGISTER_ALL_PAGES]  # Keep large docs.
        logger.debug("Selected %d qualifying Juniper document packages", len(selected))  # Record filtered count.
        return selected  # Return the default registration set.

    def _install_plan(
        self, skills: list[SkillMetadata], force: bool, dry_run: bool
    ) -> tuple[CostReport, list[InstallOutcome]]:
        report = self.estimator.estimate(skills, force)  # Measure index cost before changing host registrations.
        if dry_run or report.action == "refused":  # Stop when the caller only wants a preview or thresholds block.
            return report, []  # Return the report without changing any target.
        outcomes = self._install_many(skills)  # Publish the approved package set.
        return report, outcomes  # Return both the cost proof and the file actions.

    def _install_many(self, skills: list[SkillMetadata]) -> list[InstallOutcome]:
        outcomes: list[InstallOutcome] = []  # Collect every skill result for one CLI report.
        for skill in skills:  # Process each skill with full single-skill safety checks.
            outcomes.extend(self._install_metadata(skill))  # Add junction outcomes for this skill.
        return outcomes + self.generate_catalog()  # Refresh catalogs after the batch.

    def _uninstall_many(self, skills: list[SkillMetadata]) -> list[InstallOutcome]:
        outcomes: list[InstallOutcome] = []  # Collect every removal result for one CLI report.
        for skill in skills:  # Remove each known host registration.
            outcomes.extend(self._remove_junction(path) for path in self._target_paths(skill.name))  # Remove hosts.
        return outcomes  # Return removal outcomes before catalog refresh.

    def _install_metadata(self, skill: SkillMetadata) -> list[InstallOutcome]:
        blockers = self._find_install_blockers(skill.name, skill.package_path)  # Refuse before a partial install.
        if blockers:  # Stop if any target would overwrite a hand-written skill.
            logger.debug("Refused Juniper skill %s with %d blockers", skill.name, len(blockers))  # Count blockers.
            return blockers  # Return all blockers so the operator can repair each path.
        outcomes = self._create_junctions(skill.name, skill.package_path)  # Publish the package into each host.
        logger.debug("Installed Juniper skill %s with %d outcomes", skill.name, len(outcomes))  # Count actions.
        return outcomes  # Return host publication outcomes.

    def _target_paths(self, skill_name: str) -> list[Path]:
        return [  # Return paths in the contract order for predictable reports.
            self.home_path / ".copilot" / "skills" / skill_name,  # Target GitHub Copilot CLI and applications.
            self.home_path / ".claude" / "skills" / skill_name,  # Target Claude local skills.
            self.repo_path / ".github" / "skills" / skill_name,  # Target VS Code repository skills.
        ]

    def _find_install_blockers(self, skill_name: str, source_path: Path) -> list[InstallOutcome]:
        logger.info("Checking install targets for Juniper skill %s", skill_name)  # Log the preflight check.
        blockers = [self._blocker_for(path, source_path) for path in self._target_paths(skill_name)]  # Test targets.
        found = [blocker for blocker in blockers if blocker is not None]  # Keep only unsafe targets.
        logger.debug("Found %d install blockers for Juniper skill %s", len(found), skill_name)  # Record count.
        return found  # Return blockers before any junction is created.

    def _blocker_for(self, target_path: Path, source_path: Path) -> InstallOutcome | None:
        if not target_path.exists():  # A missing target is safe to create.
            return None  # Report no blocker for a new junction.
        if self._is_expected_junction(target_path, source_path):  # An existing correct junction is idempotent.
            return None  # Report no blocker when nothing must change.
        detail = "target exists and is not this canonical junction"  # Explain why the installer refused.
        return InstallOutcome(target_path, "refused", detail)  # Preserve hand-written or foreign skill content.

    def _create_junctions(self, skill_name: str, source_path: Path) -> list[InstallOutcome]:
        logger.info("Creating junction targets for Juniper skill %s", skill_name)  # Log target publication.
        self._add_repo_gitignore_entry(skill_name)  # Prevent Git from reading the store through the repo junction.
        outcomes = [
            self._create_junction(path, source_path) for path in self._target_paths(skill_name)
        ]  # Publish hosts.
        logger.debug("Created %d junction outcomes for Juniper skill %s", len(outcomes), skill_name)  # Count results.
        return outcomes  # Return one outcome for each host target.

    def _create_junction(self, target_path: Path, source_path: Path) -> InstallOutcome:
        if self._is_expected_junction(target_path, source_path):  # Keep an existing correct junction unchanged.
            return InstallOutcome(target_path, "unchanged", "junction already points to canonical skill")  # No change.
        logger.info("Creating junction %s", target_path)  # Log the junction creation command.
        target_path.parent.mkdir(parents=True, exist_ok=True)  # Create the host skill directory when missing.
        command = [self._command_processor(), "/c", "mklink", "/J", str(target_path), str(source_path)]  # Use cmd.
        subprocess.run(command, check=True)  # nosec B603 - The command uses a fixed verb and validated paths.
        logger.debug("Created junction %s to %s", target_path, source_path)  # Record the created path pair.
        return InstallOutcome(target_path, "created", "junction created to canonical skill")  # Report the junction.

    def _command_processor(self) -> str:
        system_root = os.environ.get("SystemRoot")  # Read the Windows root without assuming one drive letter.
        if system_root is None:  # Refuse junction creation when Windows variables are absent.
            raise RuntimeError("Windows SystemRoot is required to create a junction")  # Give a clear operator error.
        fallback = Path(system_root) / "System32" / "cmd.exe"  # Build the OS path without separators.
        command_processor = os.environ.get("ComSpec", str(fallback))  # Prefer the configured Windows shell.
        return str(Path(command_processor))  # Return an absolute executable path for process creation.

    def _remove_junction(self, target_path: Path) -> InstallOutcome:
        if not target_path.exists():  # A missing target is already uninstalled.
            return InstallOutcome(target_path, "unchanged", "target is absent")  # Report idempotent removal.
        if not self._is_junction(target_path):  # Never remove a real directory or file.
            return InstallOutcome(target_path, "refused", "target is not a junction")  # Preserve hand-written content.
        logger.info("Removing junction %s", target_path)  # Log the safe removal action.
        target_path.rmdir()  # Remove the junction itself without deleting canonical files.
        logger.debug("Removed junction %s", target_path)  # Record the completed removal.
        return InstallOutcome(target_path, "removed", "junction removed without deleting canonical skill")  # Report it.

    def _verify_target(self, target_path: Path, source_path: Path) -> InstallOutcome:
        if not self._is_expected_junction(target_path, source_path):  # Require the host path to point at the store.
            return InstallOutcome(target_path, "failed", "target is not the expected canonical junction")  # Mismatch.
        skill_file = target_path / "SKILL.md"  # Use the required skill entry file for readability proof.
        if not skill_file.is_file():  # A skill without SKILL.md cannot be discovered by the host.
            return InstallOutcome(target_path, "failed", "SKILL.md is missing")  # Report the missing entry file.
        detail = "read {} characters from SKILL.md".format(len(skill_file.read_text(encoding="utf-8")))  # Prove read.
        return InstallOutcome(target_path, "verified", detail)  # Report a readable junction target.

    def _is_expected_junction(self, target_path: Path, source_path: Path) -> bool:
        if not self._is_junction(target_path):  # Only a junction can be an expected host target.
            return False  # Reject real directories and files.
        return target_path.resolve() == source_path.resolve()  # Compare resolved paths to prove the source.

    def _is_junction(self, target_path: Path) -> bool:
        checker = getattr(target_path, "is_junction", None)  # Use the Windows junction detector when available.
        return bool(checker and checker())  # Return false on non-Windows platforms and old Python versions.

    def _add_repo_gitignore_entry(self, skill_name: str) -> None:
        logger.info("Adding repository gitignore entry for %s", skill_name)  # Log the repository guard update.
        gitignore_path = self.repo_path / ".gitignore"  # Keep the guard in the repository ignore file.
        entry = f".github/skills/{skill_name}/"  # Ignore the junction path before Git traverses it.
        content = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""  # Preserve rules.
        if entry not in content.splitlines():  # Add the entry only once for idempotence.
            gitignore_path.write_text(content.rstrip() + "\n" + entry + "\n", encoding="utf-8")  # Append guard.
        logger.debug("Repository gitignore contains entry %s", entry)  # Record that the guard is present.

    def _installed_lines(self) -> list[str]:
        target_root = self.repo_path / ".github" / "skills"  # Use the repository host as the visible installed set.
        installed = sorted(path.name for path in target_root.iterdir()) if target_root.exists() else []  # Read names.
        return [f"- {name}" for name in installed] or ["- none"]  # Keep empty output explicit.

    def _available_lines(self) -> list[str]:
        skills = self.catalog.available_skills()  # Read routers and packages from the canonical store.
        return [self._available_line(skill) for skill in skills] or ["- none"]  # Keep empty output explicit.

    def _available_line(self, skill: SkillMetadata) -> str:
        return f"- {skill.slug}: {skill.title} ({skill.domain}, {skill.kind})"  # Describe source.

    def _catalog_lines(self, packages: list[SkillMetadata]) -> list[str]:
        lines = [
            "# Skill catalog",
            "",
            "This file is generated by the Juniper skill installer. Do not edit it by hand.",
        ]
        lines.extend(["", "## Layout", "", "- `skills/<router-skill>/` holds routing tier skills."])
        lines.append("- `packages/<domain>/<document-slug>/` holds complete document skill packages.")
        lines.extend(["", "## Packages", "", "| Skill | Domain | Kind | Entry file |", "| - | - | - | - |"])
        if not packages:  # State the empty store clearly for first use.
            return lines + ["| none | none | none | none |"]  # Keep the empty catalog valid Markdown.
        for skill in packages:  # Add one row for each package that can be recalled by a router.
            lines.append(self._catalog_line(skill))  # Keep row construction isolated for readability.
        return lines  # Return the generated catalog lines.

    def _catalog_line(self, skill: SkillMetadata) -> str:
        relative = skill.package_path.relative_to(self.store_path)  # Make the catalog portable across home folders.
        return "| `{}` | {} | {} | `{}` |".format(skill.name, skill.domain, skill.kind, relative / "SKILL.md")

    def _write_store_file(self, name: str, content: str) -> None:
        logger.info("Writing canonical store file %s", name)  # Log each store metadata write.
        (self.store_path / name).write_text(content, encoding="utf-8")  # Write deterministic UTF-8 metadata.
        logger.debug("Wrote canonical store file %s", name)  # Record the completed metadata write.

    def _readme_text(self) -> str:
        return "\n".join(  # Build deterministic STE prose for the canonical store.
            [
                "# Juniper agent skills",
                "",
                "This repository is the canonical store for generated Juniper skills.",
                "",
                "The skill factory publishes each skill to agent hosts with Windows directory junctions.",
                "The source of truth stays here.",
                "",
                "## Layout",
                "",
                "- `skills/<router-skill>/` holds routing tier skills.",
                "- `packages/<domain>/<document-slug>/` holds complete document skill packages.",
                "- `SKILL_CATALOG.json` lists every document package for keyword recall.",
                "- `CATALOG.md` summarizes the same layout for operators.",
                "",
                "A router can use the catalog to find a package that is not registered.",
                "Register a domain or selected packages only when you need the extra context.",
                "",
                "## License choice",
                "",
                "This repository uses the Creative Commons Attribution 4.0 International License.",
                "Juniper Networks holds the copyright of the source documents.",
                "This repository must not copy source prose.",
            ]
        )

    def _store_gitignore_text(self) -> str:
        return "\n".join(  # Keep generated cache files out of this store.
            [
                "# Local build artifacts",
                "__pycache__/",
                "*.py[cod]",
                ".pytest_cache/",
                "",
                "# Editor state",
                ".vscode/",
                ".idea/",
                "",
            ]
        )

    def _license_text(self) -> str:
        return "\n".join(  # Keep the license file short and point to the complete public license text.
            [
                "Creative Commons Attribution 4.0 International Public License",
                "",
                "This repository is licensed under CC-BY-4.0.",
                "",
                "You may share and adapt this material for any purpose if you give appropriate credit.",
                "Link to the license and state if changes were made.",
                "",
                "The full license text is available at:",
                "https://creativecommons.org/licenses/by/4.0/legalcode",
                "",
                "Juniper Networks holds the copyright of the source documents.",
                "This repository stores restated knowledge only and must not copy source prose.",
                "",
            ]
        )
