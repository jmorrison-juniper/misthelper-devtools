"""Apply the Juniper domain taxonomy to source documents."""

from __future__ import annotations  # Keep annotation imports lazy for command use.

import logging  # Emit trace records for each database and file action.
import re  # Match taxonomy keywords and model patterns consistently.
import sqlite3  # Update the existing factory database without rebuilding tables.
from collections import Counter  # Build report totals with deterministic counters.
from pathlib import Path  # Resolve source Markdown paths without hardcoded separators.

from .models import DomainAssignment, DomainDocument, DomainReport, DomainRule  # Share public classifier contracts.
from .taxonomy import (
    DOCUMENT_TYPE_RULES,
    DOMAIN_RULES,
    GENERAL_DOMAIN,
    SIGNAL_CONFIDENCE,
    SIGNAL_ORDER,
    SUBSTANTIAL_ONLY_DOMAINS,
)  # Use contract rules.

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.

LOW_CONFIDENCE_LIMIT = 0.70  # Flag fallback and content-only matches for review.
CONTENT_CHAR_LIMIT = 12000  # Bound source reads so large command references stay cheap.


class DomainClassifier:
    """Classify source documents and persist the single source-of-truth domain."""

    def __init__(self, source_root: Path | None = None) -> None:
        """Initialize the DomainClassifier instance."""
        self.source_root = source_root or Path(r"C:\Users\jmorrison\Downloads\juniper-harvest-md")  # Use contract root.

    def classify_document(self, document: DomainDocument) -> DomainAssignment:
        """Run the classify document operation."""
        logger.info("Classifying domain for document %s", document.document_key)  # Trace every classification request.
        signals = self._signals(document)  # Build signals once so the rule scan stays deterministic.
        assignment = self._match_document_type(document, signals)  # Apply document-type precedence from the contract.
        assignment = assignment or self._match_domain_rules(
            document, signals
        )  # Apply ordered domain rules after type rules.
        assignment = assignment or self._fallback(document)  # Assign the required fallback domain when no rule matches.
        logger.debug(
            "Classified %s as %s by %s", document.document_key, assignment.domain, assignment.signal
        )  # Summarize.
        return assignment

    def migrate_database(self, database_path: Path) -> None:
        """Run the migrate database operation."""
        logger.info("Adding domain columns to %s", database_path)  # Trace the schema migration before it starts.
        with sqlite3.connect(
            database_path
        ) as connection:  # Use a context manager so SQLite commits or rolls back atomically.
            self._add_column(
                connection, "domain", "TEXT NOT NULL DEFAULT ''"
            )  # Store the owner domain for each source row.
            self._add_column(
                connection, "domain_confidence", "REAL NOT NULL DEFAULT 0.0"
            )  # Store classifier confidence.
            self._add_column(
                connection, "domain_signal", "TEXT NOT NULL DEFAULT ''"
            )  # Store the audit trail for the match.
        logger.debug("Domain columns are present in %s", database_path)  # Confirm migration completion for operators.

    def classify_database(self, database_path: Path) -> DomainReport:
        """Run the classify database operation."""
        logger.info("Classifying all source documents in %s", database_path)  # Trace the bulk classification start.
        self.migrate_database(database_path)  # Ensure the populated database can hold the result.
        documents = self._load_documents(database_path)  # Load every source row with headings and content signals.
        assignments = tuple(self.classify_document(document) for document in documents)  # Classify each row once.
        self._persist_assignments(database_path, assignments)  # Write the single source-of-truth fields back to SQLite.
        report = self._build_report(documents, assignments)  # Produce distribution and low-confidence evidence.
        self._validate_report(report)  # Fail if the run did not classify each row exactly once.
        logger.debug(
            "Classified %s documents from %s", report.total_documents, database_path
        )  # Summarize the bulk result.
        return report

    def _add_column(self, connection: sqlite3.Connection, name: str, definition: str) -> None:
        logger.info("Checking source_document column %s", name)  # Trace each idempotent migration decision.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source_document)")}  # Read existing schema.
        if name not in columns:  # Add only missing columns so reruns are safe.
            connection.execute(
                f"ALTER TABLE source_document ADD COLUMN {name} {definition}"
            )  # Add a nullable-safe column.
        logger.debug("Column %s exists: %s", name, name in columns)  # Report whether this run changed the schema.

    def _load_documents(self, database_path: Path) -> tuple[DomainDocument, ...]:
        logger.info("Loading source documents from %s", database_path)  # Trace the input read before the query runs.
        with sqlite3.connect(database_path) as connection:  # Open the database only for the load step.
            rows = connection.execute(self._document_query()).fetchall()  # Read each source row with part paths.
        documents = tuple(self._row_to_document(row) for row in rows)  # Convert raw rows into classifier contracts.
        logger.debug("Loaded %s source documents", len(documents))  # Report the document count for reconciliation.
        return documents

    def _document_query(self) -> str:
        return (
            "SELECT d.document_key, d.title, d.category, d.source_pdf, d.pages, "  # Select rule signals.
            "COALESCE(GROUP_CONCAT(p.relative_path, char(10)), '') "  # Collect Markdown paths.
            "FROM source_document d LEFT JOIN source_part p ON p.document_key = d.document_key "  # Keep rows.
            "GROUP BY d.document_key, d.title, d.category, d.source_pdf, d.pages "  # Keep one row.
            "ORDER BY d.document_key"  # Make reruns deterministic for tests and reports.
        )

    def _row_to_document(self, row: tuple[object, ...]) -> DomainDocument:
        logger.info("Reading source signals for %s", row[0])  # Trace each source document conversion.
        paths = tuple(Path(value) for value in str(row[5]).splitlines() if value)  # Preserve all known Markdown parts.
        headings, content = self._read_markdown_signals(
            paths
        )  # Read headings and sampled content from available files.
        page_count = int(str(row[4]))  # Convert SQLite values through text so strict typing accepts the value.
        document = DomainDocument(  # Build the classifier input from persisted source signals.
            str(row[0]), str(row[1]), str(row[2]), str(row[3]), page_count, headings, content, paths
        )
        logger.debug(
            "Read %s headings and %s content chars for %s", len(headings), len(content), row[0]
        )  # Summarize signals.
        return document

    def _read_markdown_signals(self, paths: tuple[Path, ...]) -> tuple[tuple[str, ...], str]:
        headings: list[str] = []  # Gather heading text for a medium-confidence signal.
        chunks: list[str] = []  # Gather bounded body text for the final content signal.
        for relative_path in paths[:5]:  # Read enough parts to identify a domain without scanning huge documents.
            headings.extend(self._headings_from_file(relative_path))  # Add headings from the current source part.
            chunks.append(self._content_from_file(relative_path))  # Add body text from the same source part.
        return (
            tuple(headings[:50]),
            "\n".join(chunks)[:CONTENT_CHAR_LIMIT],
        )  # Bound memory and keep deterministic output.

    def _headings_from_file(self, relative_path: Path) -> tuple[str, ...]:
        logger.info("Reading Markdown headings from %s", relative_path)  # Trace the file read before opening it.
        text = self._safe_read(relative_path)  # Read the source file if the corpus exists locally.
        headings = tuple(
            line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")
        )  # Extract headings.
        logger.debug("Read %s headings from %s", len(headings), relative_path)  # Summarize the heading signal.
        return headings

    def _content_from_file(self, relative_path: Path) -> str:
        logger.info("Reading Markdown content from %s", relative_path)  # Trace the content read before opening it.
        text = self._safe_read(relative_path)[:CONTENT_CHAR_LIMIT]  # Bound the text sample for large source documents.
        logger.debug("Read %s content chars from %s", len(text), relative_path)  # Summarize the content signal size.
        return text

    def _safe_read(self, relative_path: Path) -> str:
        path = self.source_root / relative_path  # Join paths with pathlib for Windows and Linux compatibility.
        if not path.exists():  # Missing corpora must not stop database-only tests.
            return ""  # Return no signal when the source file is unavailable.
        return path.read_text(encoding="utf-8", errors="ignore")  # Decode damaged vendor text without failing the run.

    def _signals(self, document: DomainDocument) -> dict[str, str]:
        path_signal = document.source_pdf  # Use the canonical source path to avoid duplicate-part noise.
        return {  # Keep each signal separate so the audit trail names the winning source.
            "title": document.title,  # Title is the strongest signal.
            "path": path_signal,  # Path is the second signal because it carries category slugs.
            "category": document.category,  # Category is useful but many rows are uncategorized.
            "heading": "\n".join(document.headings),  # Headings resolve broad titles.
            "content": document.content,  # Content catches command vocabulary and product terms.
        }

    def _match_document_type(self, document: DomainDocument, signals: dict[str, str]) -> DomainAssignment | None:
        for rule in DOCUMENT_TYPE_RULES:  # Scan required document-type exceptions before normal rules.
            assignment = self._match_rule(
                document, rule, signals, ("title", "path", "category")
            )  # Use stable metadata only.
            if assignment:  # Stop on the first required document-type match.
                return assignment
        return None  # Continue to normal domain rules when no document type matches.

    def _match_domain_rules(self, document: DomainDocument, signals: dict[str, str]) -> DomainAssignment | None:
        for signal_name in SIGNAL_ORDER:  # Honor the stated signal order before broad content rules.
            assignment = self._match_signal_rules(
                document, signals, signal_name
            )  # Apply domain priority within one signal.
            if assignment:  # Stop when the strongest available signal selects a domain.
                return assignment
        return None  # Let the caller assign the required fallback.

    def _match_signal_rules(
        self, document: DomainDocument, signals: dict[str, str], signal_name: str
    ) -> DomainAssignment | None:
        for rule in DOMAIN_RULES:  # Apply the contract priority order within one signal.
            if self._skip_small_product_gap(document, rule):  # Leave short product mentions for the flyer agent.
                continue
            keyword = self._matched_keyword(rule, signal_name, signals[signal_name])  # Test one rule and one signal.
            if keyword:  # Stop on the first contract rule that matches this signal.
                return self._assignment(document, rule, signal_name, keyword)  # Preserve the exact rule and keyword.
        return None  # No domain rule matched this signal.

    def _skip_small_product_gap(self, document: DomainDocument, rule: DomainRule) -> bool:
        if rule.domain not in SUBSTANTIAL_ONLY_DOMAINS:  # Apply the page floor only to new product-gap domains.
            return False
        return document.pages < 20  # Keep small flyers and case studies in the fallback bucket.

    def _match_rule(
        self, document: DomainDocument, rule: DomainRule, signals: dict[str, str], signal_order: tuple[str, ...]
    ) -> DomainAssignment | None:
        for signal_name in signal_order:  # Search each signal in the stated order.
            keyword = self._matched_keyword(
                rule, signal_name, signals[signal_name]
            )  # Test this rule against this signal.
            if keyword:  # Build the decision immediately because first match wins.
                return self._assignment(document, rule, signal_name, keyword)  # Preserve the exact rule and keyword.
        return None  # No keyword in this rule matched any available signal.

    def _matched_keyword(self, rule: DomainRule, signal_name: str, value: str) -> str:
        normalized = self._normalize(value)  # Normalize case and separators before matching.
        for keyword in self._keywords_for_signal(rule, signal_name):  # Test category-specific or general keywords.
            normalized_keyword = self._normalize(keyword)  # Normalize the keyword the same way as the signal.
            if self._keyword_matches(normalized_keyword, normalized):  # Avoid matching short terms inside other words.
                return keyword  # Return the keyword so the signal can be audited.
        for pattern in rule.regex_keywords:  # Test regex patterns after literal keywords.
            if re.search(pattern, normalized):  # Use regex only for compact model and acronym rules.
                return pattern  # Return the pattern because it explains the match.
        return ""  # Return no keyword when this signal has no match.

    def _keyword_matches(self, keyword: str, normalized: str) -> bool:
        if " " in keyword:  # Phrases already carry context and can use substring matching.
            return keyword in normalized  # Match contract phrases across title, path, heading, or content.
        pattern = rf"\b{re.escape(keyword)}\b"  # Require word boundaries for short product terms.
        return bool(re.search(pattern, normalized))  # Return whether the signal has the exact keyword.

    def _keywords_for_signal(self, rule: DomainRule, signal_name: str) -> tuple[str, ...]:
        if (
            signal_name == "category" and rule.category_keywords
        ):  # Prefer exact category vocabulary for category signals.
            return rule.category_keywords  # Avoid broad content words such as API inside categories.
        return (
            rule.keywords + rule.category_keywords
        )  # Permit category labels in paths and titles when they appear there.

    def _normalize(self, value: str) -> str:
        lowered = value.casefold()  # Make matching independent of vendor capitalization.
        return re.sub(r"[_/\\\-.]+", " ", lowered)  # Treat slugs, paths, and prose as the same signal form.

    def _assignment(
        self, document: DomainDocument, rule: DomainRule, signal_name: str, keyword: str
    ) -> DomainAssignment:
        confidence = SIGNAL_CONFIDENCE[signal_name]  # Convert the deciding signal to a confidence score.
        low_confidence = confidence < LOW_CONFIDENCE_LIMIT  # Flag content-only and fallback decisions for review.
        signal = f"{signal_name}:{rule.domain}:{keyword}"  # Store a compact audit trail in the database.
        return DomainAssignment(document.document_key, rule.domain, confidence, signal, rule.domain, low_confidence)

    def _fallback(self, document: DomainDocument) -> DomainAssignment:
        logger.info("Assigning fallback domain to %s", document.document_key)  # Trace every taxonomy miss.
        signal = f"fallback:{GENERAL_DOMAIN}:no-rule"  # Make fallback assignments searchable in reports.
        assignment = DomainAssignment(document.document_key, GENERAL_DOMAIN, 0.20, signal, GENERAL_DOMAIN, True)
        logger.debug("Fallback domain assigned to %s", document.document_key)  # Confirm the low-confidence assignment.
        return assignment

    def _persist_assignments(self, database_path: Path, assignments: tuple[DomainAssignment, ...]) -> None:
        logger.info("Persisting %s domain assignments", len(assignments))  # Trace the write size before SQLite updates.
        rows = [
            (item.domain, item.confidence, item.signal, item.document_key) for item in assignments
        ]  # Prepare safe binds.
        with sqlite3.connect(database_path) as connection:  # Use one transaction for a consistent classification run.
            connection.executemany(self._update_sql(), rows)  # Update only the new classification columns.
        logger.debug("Persisted %s domain assignments", len(assignments))  # Confirm the update count.

    def _update_sql(self) -> str:
        return (  # Keep SQL in one method so tests can inspect the exact write contract.
            "UPDATE source_document SET domain = ?, domain_confidence = ?, domain_signal = ? "  # Write fields.
            "WHERE document_key = ?"  # Preserve all source inventory columns.
        )

    def _build_report(
        self, documents: tuple[DomainDocument, ...], assignments: tuple[DomainAssignment, ...]
    ) -> DomainReport:
        logger.info("Building the domain classification report")  # Trace report construction for operators.
        pages = {document.document_key: document.pages for document in documents}  # Index pages by primary key.
        domain_counts = Counter(item.domain for item in assignments)  # Count document ownership by domain.
        domain_pages = Counter(
            {domain: 0 for domain in domain_counts}
        )  # Seed all domains so empty additions stay stable.
        for item in assignments:  # Add page counts by assigned domain.
            domain_pages[item.domain] += pages[item.document_key]  # Reconcile every source page to exactly one domain.
        signal_counts = Counter(
            item.signal.split(":", 1)[0] for item in assignments
        )  # Count the deciding signal types.
        low_confidence = tuple(item for item in assignments if item.low_confidence)  # Keep weak decisions for review.
        logger.debug("Built report for %s assignments", len(assignments))  # Summarize report size.
        return DomainReport(
            len(documents),
            sum(pages.values()),
            dict(domain_counts),
            dict(domain_pages),
            dict(signal_counts),
            low_confidence,
        )

    def _validate_report(self, report: DomainReport) -> None:
        logger.info("Validating the domain classification report")  # Trace the final safety check.
        assigned = sum(report.domain_counts.values())  # Count persisted decisions across all domains.
        if (
            report.total_documents == 0 or assigned != report.total_documents
        ):  # Fail if the run missed rows or read nothing.
            raise RuntimeError("domain classification did not assign each source document exactly once")
        logger.debug("Validated %s domain assignments", assigned)  # Confirm the exact one-domain invariant.
