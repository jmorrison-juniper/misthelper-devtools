"""Test the Juniper skill installer junction safety rules."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.juniper_skills.install_skills import InstallSkillsCli
from src.juniper_skills.install import SkillInstaller


class TestSkillInstaller:
    """Verify safe junction install and removal behavior."""

    def setup_method(self) -> None:
        self._require_windows()  # Require Windows because the contract uses directory junctions.
        self.root = Path.cwd() / ".pytest-juniper-skills-install" / uuid.uuid4().hex  # Keep test files in the worktree.
        self.store = self.root / "store"  # Isolate the canonical store from the real user store.
        self.repo = self.root / "repo"  # Isolate the repository target from the real worktree.
        self.home = self.root / "home"  # Isolate user skill targets from the real profile.
        self.skill = self.store / "skills" / "juniper-test-skill"  # Use a real skill package shape.
        self.skill.mkdir(parents=True)  # Create the canonical skill package for junction tests.
        self._write_skill_file("SKILL.md", self._skill_text())  # Add the required skill entry file.
        (self.repo / ".github").mkdir(parents=True)  # Create the repository metadata folder for .gitignore.
        (self.repo / ".gitignore").write_text("# test rules\n", encoding="utf-8")  # Seed an existing ignore file.
        self.installer = SkillInstaller(self.store, self.repo, self.home)  # Point the installer at isolated paths.

    def teardown_method(self) -> None:
        self._remove_tree(self.root)  # Remove test folders and any junctions created by the test.

    def test_install_verify_and_uninstall_keep_source_files(self) -> None:
        outcomes = self.installer.install_skill("juniper-test-skill")  # Publish the test skill to all hosts.
        assert [outcome.action for outcome in outcomes].count("created") == 3  # Confirm each host received a junction.
        verify = self.installer.verify_skill("juniper-test-skill")  # Read SKILL.md through all three junction paths.
        assert [outcome.action for outcome in verify] == [
            "verified",
            "verified",
            "verified",
        ]  # Confirm discovery files read.
        second = self.installer.install_skill("juniper-test-skill")  # Run install again to prove idempotence.
        assert [outcome.action for outcome in second].count(
            "unchanged"
        ) == 3  # Confirm the second run changes no junction.
        uninstall = self.installer.uninstall_skill("juniper-test-skill")  # Remove the junctions from each host.
        assert [outcome.action for outcome in uninstall].count(
            "removed"
        ) == 3  # Confirm each junction was removed safely.
        assert (self.skill / "SKILL.md").is_file()  # Prove junction removal did not delete canonical contents.

    def test_install_refuses_to_overwrite_real_directory(self) -> None:
        real_target = self.repo / ".github" / "skills" / "juniper-test-skill"  # Select one contract target.
        real_target.mkdir(parents=True)  # Create a hand-written skill directory that the installer must preserve.
        (real_target / "SKILL.md").write_text("manual skill\n", encoding="utf-8")  # Add content that must survive.
        outcomes = self.installer.install_skill("juniper-test-skill")  # Try to install over a real directory.
        assert [outcome.action for outcome in outcomes] == ["refused"]  # Confirm the installer stops before changes.
        assert (real_target / "SKILL.md").read_text(
            encoding="utf-8"
        ) == "manual skill\n"  # Confirm content stayed intact.
        assert not (
            self.home / ".copilot" / "skills" / "juniper-test-skill"
        ).exists()  # Confirm no partial install occurred.

    def test_catalog_is_generated_from_skill_directory(self) -> None:
        outcomes = self.installer.generate_catalog()  # Generate the catalog from the canonical skill folder.
        catalog_text = (self.store / "CATALOG.md").read_text(encoding="utf-8")  # Read the generated catalog.
        assert outcomes[0].action == "generated"  # Confirm the installer reports catalog generation.
        assert "`juniper-test-skill`" in catalog_text  # Confirm the catalog lists the installed domain skill.

    def test_cost_report_warns_above_thirty_percent_without_installing(self) -> None:
        self._write_package("apstra", "large-doc", "juniper-apstra-large", "a" * 1_300_000)  # Create a large index.
        report, outcomes = self.installer.install_packages(["large-doc"], dry_run=True)  # Measure without installing.
        assert report.action == "warned"  # Confirm the 30 percent threshold warns instead of refusing.
        assert report.token_count > 300_000  # Prove the estimate crossed 30 percent of 1,000,000 tokens.
        assert outcomes == []  # Confirm dry-run mode did not create junctions.
        assert report.over_budget_descriptions[0].slug == "large-doc"  # Confirm long descriptions are reported.

    def test_register_domain_installs_each_package_in_domain(self) -> None:
        self._write_package("apstra", "fabric-day0", "juniper-apstra-fabric-day0")  # Add the first domain package.
        self._write_package("apstra", "fabric-day2", "juniper-apstra-fabric-day2")  # Add the second domain package.
        self._write_package("junos", "routing-day2", "juniper-junos-routing-day2")  # Add another domain package.
        report, outcomes = self.installer.install_domain("apstra")  # Register the requested domain only.
        assert report.skill_count == 2  # Confirm only Apstra packages entered the cost plan.
        assert [outcome.action for outcome in outcomes].count("created") == 6  # Confirm two packages hit three hosts.
        assert not (self.repo / ".github" / "skills" / "juniper-junos-routing-day2").exists()  # Exclude other domain.

    def test_search_finds_unregistered_package(self) -> None:
        self._write_package("apstra", "evpn-reference", "juniper-apstra-evpn")  # Add an unregistered package.
        outcomes = self.installer.generate_catalog()  # Write the searchable catalog from available packages.
        matches = self.installer.search_catalog("evpn")  # Search by a routing keyword from the package content.
        assert outcomes[0].action == "generated"  # Confirm the catalog write ran before search.
        assert [match.slug for match in matches] == ["evpn-reference"]  # Confirm keyword recall found the package.
        assert not (self.repo / ".github" / "skills" / "juniper-apstra-evpn").exists()  # Prove it is unregistered.

    def test_register_all_requires_force_at_high_projection(self, capsys: pytest.CaptureFixture[str]) -> None:
        for number in range(23):  # Create enough packages to cross the 50 percent force threshold.
            self._write_package("mega", f"doc-{number}", f"juniper-mega-doc-{number}", "b" * 90_000)  # Add one package.
        args = [  # Build the CLI arguments with isolated paths for this test.
            "--store",
            str(self.store),
            "--repo",
            str(self.repo),
            "--home",
            str(self.home),
            "--register-all",
            "--dry-run",
        ]
        status = InstallSkillsCli().run(args)  # Run the CLI so the cost report text is proved.
        output = capsys.readouterr().out  # Capture the cost report that operators read.
        assert status == 1  # Confirm the CLI refuses the high projection without force.
        assert "Decision: refused." in output  # Confirm the output states the threshold decision.
        assert "Window share:" in output  # Confirm the output includes the projected context share.
        assert "1,000,000 tokens" in output  # Confirm the corrected default context window.

    def test_default_registration_uses_qualifying_document_packages(self, capsys: pytest.CaptureFixture[str]) -> None:
        self._write_package("apstra", "doc-a", "juniper-apstra-doc-a")  # Add one qualifying document package.
        self._write_package("apstra", "doc-b", "juniper-apstra-doc-b")  # Add another qualifying document package.
        args = ["--store", str(self.store), "--repo", str(self.repo), "--home", str(self.home), "--dry-run"]
        status = InstallSkillsCli().run(args)  # Run the default mode without an explicit registration option.
        output = capsys.readouterr().out  # Capture the cost report that proves the default selection.
        assert status == 0  # Confirm the default registration plan is allowed.
        assert "Skills: 2" in output  # Confirm the default selected packages, not the single router.

    def test_context_window_changes_threshold_decision(self) -> None:
        self._write_package("apstra", "small-window", "juniper-apstra-small-window", "c" * 130_000)  # Add a test doc.
        installer = SkillInstaller(self.store, self.repo, self.home, context_window=100_000)  # Use a smaller model.
        report, outcomes = installer.install_packages(["small-window"], dry_run=True)  # Measure against that window.
        assert report.action == "warned"  # Confirm the caller-selected window changes the threshold.
        assert outcomes == []  # Confirm the window test changes no host registration.

    def test_projection_above_seventy_percent_refuses_with_force(self) -> None:
        self._write_package("apstra", "too-large", "juniper-apstra-too-large", "d" * 290_000)  # Add a huge doc.
        installer = SkillInstaller(self.store, self.repo, self.home, context_window=100_000)  # Use a small window.
        report, outcomes = installer.install_packages(["too-large"], force=True, dry_run=True)  # Measure forced plan.
        assert report.action == "refused"  # Confirm the 70 percent threshold cannot be forced.
        assert outcomes == []  # Confirm the refused plan changes no host registration.

    def _write_skill_file(self, name: str, content: str) -> None:
        (self.skill / name).write_text(content, encoding="utf-8")  # Write deterministic skill test content.

    def _write_package(
        self, domain: str, slug: str, name: str, description: str = "Apstra EVPN day2 reference."
    ) -> Path:
        package = self.store / "packages" / domain / slug  # Build the canonical per-document package path.
        package.mkdir(parents=True)  # Create the package folder for the installer catalog.
        (package / "SKILL.md").write_text(self._package_skill_text(name, description), encoding="utf-8")  # Write entry.
        (package / "INDEX.md").write_text("# EVPN topics\n\n- day2 validation\n", encoding="utf-8")  # Add keywords.
        (package / "sources.md").write_text("pages: 42\n", encoding="utf-8")  # Add a measurable page count.
        topic_text = "# Overlay EVPN\n\nUse this for day2 checks.\n"  # Keep topic text reusable and readable.
        (package / "topic.md").write_text(topic_text, encoding="utf-8")  # Add topic keyword content.
        return package  # Return the package path for focused assertions.

    def _remove_tree(self, root: Path) -> None:
        if not root.exists():  # Nothing needs cleanup when setup failed early.
            return  # Keep teardown idempotent.
        for path in sorted(root.rglob("*"), reverse=True):  # Remove child paths before their parents.
            self._remove_path(path)  # Remove junctions without following them.
        shutil.rmtree(root, ignore_errors=True)  # Remove any empty folders left after safe path cleanup.

    def _remove_path(self, path: Path) -> None:
        if self._is_junction(path):  # A junction must be removed with rmdir to preserve its source.
            path.rmdir()  # Remove only the junction entry.

    def _is_junction(self, path: Path) -> bool:
        checker = getattr(path, "is_junction", None)  # Use the Windows junction detector supplied by pathlib.
        return bool(checker and checker())  # Return false where junction detection is unavailable.

    def _require_windows(self) -> None:
        if os.name != "nt":  # The installer contract is Windows-specific.
            pytest.skip("Windows directory junctions are required for this installer.")  # Explain the skipped platform.

    def _skill_text(self) -> str:
        return "\n".join(  # Build a small valid skill entry file for discovery tests.
            [
                "---",
                "name: juniper-test-skill",
                "description: Test skill for installer verification.",
                "---",
                "",
                "# Juniper test skill",
                "",
                "Read this file to verify junction discovery.",
                "",
            ]
        )

    def _package_skill_text(self, name: str, description: str) -> str:
        return "\n".join(  # Build a complete per-document skill entry file.
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                "# Apstra EVPN reference",
                "",
                "Read INDEX.md, sources.md, and the topic files for this package.",
                "",
            ]
        )
