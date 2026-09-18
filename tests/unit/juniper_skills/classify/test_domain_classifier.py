"""Tests for the Juniper domain classifier."""

from __future__ import annotations  # Keep annotations lazy for pytest import speed.

import sqlite3  # Build a local populated database for migration tests.
from pathlib import Path  # Use portable paths for the test database.
from time import sleep  # Wait briefly for Windows to release SQLite handles.

from src.juniper_skills.classify import DomainClassifier, DomainDocument, DomainLookup  # Test the public interface.

TEST_DATABASE = Path("tests") / "unit" / "juniper_skills" / "classify" / "factory-test.db"  # Keep test files in repo.


class ClassifierCaseBuilder:
    """Build documents and databases for classifier tests."""

    def document(
        self,
        title: str,
        category: str = "uncategorized",
        source_pdf: str = "uncategorized/source.pdf",
        headings: tuple[str, ...] = (),
        content: str = "",
    ) -> DomainDocument:
        key = title.casefold().replace(" ", "-").replace(":", "")  # Create a stable test primary key.
        return DomainDocument(key, title, category, source_pdf, 1, headings, content)  # Return all supplied signals.

    def database(self) -> Path:
        if TEST_DATABASE.exists():  # Remove an old test database before creating a clean one.
            TEST_DATABASE.unlink()  # Keep each test independent without a temporary directory.
        TEST_DATABASE.parent.mkdir(parents=True, exist_ok=True)  # Ensure the controlled test directory exists.
        with sqlite3.connect(TEST_DATABASE) as connection:  # Create a populated source database.
            connection.execute(self._source_sql())  # Create the source table without domain columns.
            connection.execute(self._part_sql())  # Create the part table used for path aggregation.
            connection.executemany(self._insert_sql(), self._rows())  # Insert representative source documents.
            connection.executemany(self._part_insert_sql(), self._part_rows())  # Insert Markdown path signals.
        return TEST_DATABASE  # Return the database path for migration and lookup tests.

    def cleanup(self) -> None:
        for _attempt in range(5):  # Retry because Windows can release SQLite handles slowly.
            if not TEST_DATABASE.exists():  # Stop when another cleanup attempt already removed the file.
                return  # Leave the cleanup method after the owned file is absent.
            try:  # Handle a transient Windows file lock without failing the classifier test.
                TEST_DATABASE.unlink()  # Remove only the database owned by these tests.
                return  # Stop after the controlled artifact is removed.
            except PermissionError:  # Retry a short delay when Windows still holds the file.
                sleep(0.2)  # Wait for the SQLite handle to close.

    def _source_sql(self) -> str:
        return (  # Match the populated factory schema before the migration adds domain fields.
            "CREATE TABLE source_document (document_key TEXT PRIMARY KEY, title TEXT NOT NULL, "  # Store identity.
            "category TEXT NOT NULL, source_pdf TEXT NOT NULL, pages INTEGER NOT NULL, "  # Store signals.
            "status TEXT NOT NULL, group_method TEXT NOT NULL, "  # Store state.
            "part_count INTEGER NOT NULL, "  # Store part count.
            "text_chars INTEGER NOT NULL, priority INTEGER NOT NULL, "  # Store metrics.
            "duplicate_losers INTEGER NOT NULL, "  # Store duplicate count.
            "version_family_key TEXT NOT NULL DEFAULT '', version_value TEXT NOT NULL DEFAULT '', "  # Store versions.
            "version_status TEXT NOT NULL DEFAULT 'unversioned', "  # Store version status.
            "build_topics INTEGER NOT NULL DEFAULT 1)"  # Store topic flag.
        )

    def _part_sql(self) -> str:
        return (  # Match only the source_part columns that the classifier reads.
            "CREATE TABLE source_part (part_key TEXT PRIMARY KEY, document_key TEXT NOT NULL, "  # Store identity.
            "root_name TEXT NOT NULL, relative_path TEXT NOT NULL, content_hash TEXT NOT NULL, "  # Store path signal.
            "size_bytes INTEGER NOT NULL, text_chars INTEGER NOT NULL, "  # Store sizes.
            "has_front_matter INTEGER NOT NULL)"  # Store front matter state.
        )

    def _insert_sql(self) -> str:
        return (  # Keep source inserts readable and deterministic.
            "INSERT INTO source_document (document_key, title, category, source_pdf, pages, status, "  # Select fields.
            "group_method, part_count, text_chars, priority, duplicate_losers) VALUES (?, ?, ?, ?, ?, "  # Bind metrics.
            "'ok', 'test', 1, 100, 1, 0)"  # Use stable inventory defaults.
        )

    def _part_insert_sql(self) -> str:
        return (  # Keep part inserts readable and deterministic.
            "INSERT INTO source_part (part_key, document_key, root_name, relative_path, content_hash, "  # Select paths.
            "size_bytes, text_chars, has_front_matter) "  # Select metrics.
            "VALUES (?, ?, 'juniper-harvest-md', ?, 'hash', 10, 10, 0)"  # Bind.
        )

    def _rows(self) -> tuple[tuple[str, str, str, str, int], ...]:
        return (  # Provide rows that prove migration, classification, and lookup.
            ("day-one", "Day One: Beginner's Guide to Learning Junos", "uncategorized", "day-one.pdf", 10),
            ("evpn", "Junos OS EVPN User Guide", "uncategorized", "evpn.pdf", 20),
            ("api", "Developer Automation Handbook", "api", "api.pdf", 5),
            ("fallback", "General Product Notice", "uncategorized", "notice.pdf", 1),
        )

    def _part_rows(self) -> tuple[tuple[str, str, str], ...]:
        return (  # Include a path-only signal for the EVPN row.
            ("p1", "day-one", "day-one.md"),
            ("p2", "evpn", "uncategorized/ex__configuration__evpn-vxlan/evpn.md"),
            ("p3", "api", "api.md"),
            ("p4", "fallback", "notice.md"),
        )


class TestDomainClassifierSignals:
    """Verify each classifier signal and precedence path."""

    def setup_method(self) -> None:
        self.builder = ClassifierCaseBuilder()  # Share document construction across signal tests.
        self.classifier = DomainClassifier()  # Use the public classifier without a source file dependency.

    def test_title_signal_selects_fundamentals(self) -> None:
        document = self.builder.document("Day One: Beginner's Guide to Learning Junos")  # Use a known-risk title.
        assignment = self.classifier.classify_document(document)  # Classify from the title signal.
        assert assignment.domain == "juniper-junos-fundamentals"  # Day One Junos must not route to general.
        assert assignment.signal.startswith("title:")  # The title must explain the decision.

    def test_path_signal_selects_evpn(self) -> None:
        document = self.builder.document(
            "Fabric Guide", source_pdf="uncategorized/ex__configuration__evpn-vxlan/evpn.pdf"
        )
        assignment = self.classifier.classify_document(document)  # Classify from the path signal.
        assert assignment.domain == "juniper-evpn-vxlan-fabric"  # EVPN path signals must select fabric.
        assert assignment.signal.startswith("path:")  # The path must explain the decision.

    def test_category_signal_selects_api(self) -> None:
        document = self.builder.document("Reference Guide", category="api")  # Keep title broad so category decides.
        assignment = self.classifier.classify_document(document)  # Classify from the category signal.
        assert assignment.domain == "juniper-api-automation"  # API category must select automation.
        assert assignment.signal.startswith("category:")  # The category must explain the decision.

    def test_heading_signal_selects_mpls(self) -> None:
        document = self.builder.document(
            "Transport Guide", headings=("MPLS and LDP overview",)
        )  # Supply heading evidence.
        assignment = self.classifier.classify_document(document)  # Classify from headings.
        assert assignment.domain == "juniper-mpls-transport"  # MPLS headings must select transport.
        assert assignment.signal.startswith("heading:")  # The heading must explain the decision.

    def test_content_signal_selects_routing_protocols(self) -> None:
        document = self.builder.document("Protocol Guide", content="Configure BGP route reflection for this topology.")
        assignment = self.classifier.classify_document(document)  # Classify from content.
        assert assignment.domain == "juniper-routing-protocols"  # BGP content must select routing protocols.
        assert assignment.signal.startswith("content:")  # The content must explain the decision.
        assert assignment.low_confidence is True  # Content-only matches must be flagged for review.

    def test_release_notes_precedence_selects_lifecycle(self) -> None:
        document = self.builder.document("Junos CLI Reference Release Notes")  # Combine CLI and release signals.
        assignment = self.classifier.classify_document(document)  # Apply document-type precedence first.
        assert assignment.domain == "juniper-software-lifecycle"  # Release notes must stay in lifecycle.
        assert assignment.signal.startswith("title:")  # The title must explain the document-type rule.

    def test_srx_precedence_selects_firewall(self) -> None:
        document = self.builder.document("SRX VPN Configuration Guide")  # Combine SRX and VPN signals.
        assignment = self.classifier.classify_document(document)  # Apply SRX precedence before VPN rules.
        assert assignment.domain == "juniper-security-srx-firewall"  # SRX documents must select firewall first.
        assert assignment.signal.startswith("title:")  # The title must explain the SRX document-type rule.

    def test_low_confidence_fallback_is_reported(self) -> None:
        document = self.builder.document("General Product Notice")  # Use a title with no taxonomy signal.
        assignment = self.classifier.classify_document(document)  # Force the fallback domain.
        assert assignment.domain == "juniper-general-reference"  # The fallback domain is the required default.
        assert assignment.low_confidence is True  # Fallback assignments must be visible for rule repair.
        assert (
            assignment.signal == "fallback:juniper-general-reference:no-rule"
        )  # The signal must show no rule matched.

    def test_generic_junos_does_not_select_fundamentals(self) -> None:
        document = self.builder.document("Junos OS Overview Guide")  # Use a title without fundamentals terms.
        assignment = self.classifier.classify_document(document)  # Prove a bare Junos word is not enough.
        assert assignment.domain == "juniper-general-reference"  # Broad Junos titles must not bloat fundamentals.
        assert assignment.low_confidence is True  # The broad title must stay visible for taxonomy review.

    def test_small_product_gap_document_stays_fallback(self) -> None:
        document = self.builder.document("SRC Short Flyer")  # Use a short document from a new product-gap domain.
        assignment = self.classifier.classify_document(document)  # Prove the substantial-document floor applies.
        assert assignment.domain == "juniper-general-reference"  # Short product mentions stay with flyer grouping.
        assert assignment.low_confidence is True  # The fallback report still exposes the short document.


class TestDomainClassifierKnownSet:
    """Measure known-answer accuracy for unambiguous documents."""

    def setup_method(self) -> None:
        self.builder = ClassifierCaseBuilder()  # Share document construction for the known-answer set.
        self.classifier = DomainClassifier()  # Use the public classifier for all accuracy cases.

    def test_known_correct_set_accuracy_is_complete(self) -> None:
        cases = self._known_cases()  # Build at least 30 unambiguous routing cases.
        failures = []  # Collect all failures so the test reports every bad rule.
        for title, domain in cases:  # Measure each known document.
            assignment = self.classifier.classify_document(
                self.builder.document(title)
            )  # Classify the synthetic title.
            if assignment.domain != domain:  # Record the mismatch instead of stopping at the first one.
                failures.append((title, domain, assignment.domain, assignment.signal))  # Preserve enough audit data.
        accuracy = (len(cases) - len(failures)) / len(cases)  # Compute the measured known-answer accuracy.
        assert len(cases) >= 30  # Enforce the requested minimum test set size.
        assert accuracy == 1.0, failures  # Require each known-correct document to pass.

    def _known_cases(self) -> tuple[tuple[str, str], ...]:
        return (  # Cover each domain rule with clear product or subject titles.
            ("Day One: Beginner's Guide to Learning Junos", "juniper-junos-fundamentals"),
            ("Junos OS EVPN User Guide", "juniper-evpn-vxlan-fabric"),
            ("Junos OS MPLS Applications User Guide", "juniper-mpls-transport"),
            ("Junos OS BGP User Guide", "juniper-routing-protocols"),
            ("Junos OS Junos CLI Reference", "juniper-cli-reference"),
            ("Session Smart Router User Guide", "juniper-sdwan-wan"),
            ("Apstra User Guide", "juniper-datacenter-apstra"),
            ("Mist Cloud Operations Guide", "juniper-mist-ai-cloud"),
            ("Security Director Administration Guide", "juniper-security-srx-firewall"),
            ("SRX Series Firewall Policy Guide", "juniper-security-srx-firewall"),
            ("Juniper Secure Analytics SIEM Guide", "juniper-security-analytics-compliance"),
            ("Common Criteria Validation Report", "juniper-security-analytics-compliance"),
            ("IPsec VPN Configuration Guide", "juniper-security-vpn-threat"),
            ("Sky ATP Threat Prevention Guide", "juniper-security-vpn-threat"),
            ("Routing Director User Guide", "juniper-routing-director-operations"),
            ("Paragon Insights User Guide", "juniper-observability-operations"),
            ("HealthBot Monitoring Guide", "juniper-observability-operations"),
            ("Broadband Subscriber Sessions User Guide", "juniper-subscriber-services"),
            ("Dynamic Profiles for PPPoE Subscribers", "juniper-subscriber-services"),
            ("Routing Policies Firewall Filters and Traffic Policers User Guide", "juniper-routing-policy-firewall"),
            ("Class of Service Configuration Guide", "juniper-routing-policy-firewall"),
            ("Contrail Cloud Native Guide", "juniper-cloud-native-contrail"),
            ("CN2 Kubernetes CNI Guide", "juniper-cloud-native-contrail"),
            ("EX4650 Ethernet Switch Hardware Guide", "juniper-campus-branch-switching"),
            ("QFX Series Switching Guide", "juniper-datacenter-switching"),
            ("MX Series Hardware Datasheet", "juniper-hardware-platforms"),
            ("Optics Transceiver Cable Guide", "juniper-hardware-platforms"),
            ("Rack Mount Installation Guide", "juniper-installation-maintenance"),
            ("FRU Replacement Maintenance Guide", "juniper-installation-maintenance"),
            ("SD-WAN Solution Brief", "juniper-sdwan-wan"),
            ("Customer Case Study", "juniper-business-solutions"),
            ("Privacy and Trademark Policy", "juniper-legal-corporate"),
        )


class TestDomainClassifierDatabase:
    """Verify migration, persistence, reporting, and lookup."""

    def setup_method(self) -> None:
        self.builder = ClassifierCaseBuilder()  # Build a populated local database for each test.
        self.classifier = DomainClassifier()  # Use the public classifier with no source root dependency.

    def teardown_method(self) -> None:
        self.builder.cleanup()  # Remove the controlled database artifact after each test.

    def test_migration_classification_and_lookup(self) -> None:
        database_path = self.builder.database()  # Create a populated database without domain columns.
        report = self.classifier.classify_database(database_path)  # Run the migration and classify all rows.
        lookup = DomainLookup(database_path)  # Read persisted domains through the consumer interface.
        evpn = lookup.get("evpn")  # Read one known document by primary key.
        fallback = lookup.get("fallback")  # Read the low-confidence fallback row by primary key.
        assert report.total_documents == 4  # The report must reconcile every populated source row.
        assert sum(report.domain_counts.values()) == 4  # Each source row must have exactly one domain.
        assert evpn.domain == "juniper-evpn-vxlan-fabric"  # Lookup must return the persisted domain.
        assert fallback.low_confidence is True  # Lookup must preserve low-confidence status.
        assert report.signal_counts["fallback"] == 1  # The report must count fallback decisions.
        assert len(report.low_confidence) == 1  # The report must list each low-confidence row.
