"""Install generated Juniper skills into local agent hosts."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]  # Find the repository root when the script runs by file path.
if str(REPO_ROOT) not in sys.path:  # Ensure the local src package wins over any installed package.
    sys.path.insert(0, str(REPO_ROOT))  # Add the worktree root for imports without changing the current directory.


class InstallSkillsCli:
    """Parse installer commands and print a concise operator report."""

    def __init__(self) -> None:
        self.parser = self._build_parser()  # Build the parser once so tests can inspect behavior.

    def run(self, argv: list[str] | None = None) -> int:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")  # Give operators a readable log.
        args = self.parser.parse_args(argv)  # Parse the requested install action.
        installer_class = self._installer_class()  # Resolve the class after the local source path is ready.
        installer = installer_class(  # Build the installer from selected paths and the model window.
            args.store, args.repo, args.home, args.context_window
        )
        outcomes = self._dispatch(args, installer)  # Execute exactly one requested action.
        self._print_outcomes(outcomes)  # Print a stable report for scripts and operators.
        return 1 if any(outcome.action in {"failed", "refused"} for outcome in outcomes) else 0  # Signal failure.

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Install generated Juniper skills.")  # State the CLI purpose.
        parser.add_argument("skill", nargs="?", help=argparse.SUPPRESS)  # Keep the legacy one-skill command.
        parser.add_argument("--register-routers", action="store_true", help="Register only routing tier skills.")
        parser.add_argument("--register-domain", metavar="DOMAIN", help="Register every package in one domain.")
        parser.add_argument("--register", nargs="+", metavar="SLUG", help="Register one or more package slugs.")
        parser.add_argument("--register-all", action="store_true", help="Register the qualifying document packages.")
        parser.add_argument("--unregister", metavar="SLUG", help="Remove a package or skill junction.")
        parser.add_argument("--unregister-all", action="store_true", help="Remove all known Juniper skill junctions.")
        parser.add_argument("--list", action="store_true", help="List installed and available skills.")
        parser.add_argument("--search", metavar="KEYWORD", help="Search the full package catalog.")
        return self._add_common_arguments(parser)  # Add shared path and safety options.

    def _add_common_arguments(self, parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        parser.add_argument("--all", action="store_true", help=argparse.SUPPRESS)  # Keep the legacy all command.
        parser.add_argument("--uninstall", metavar="SKILL", help=argparse.SUPPRESS)  # Keep the legacy removal command.
        parser.add_argument("--verify", nargs="?", const="__all__", metavar="SKILL", help="Verify one skill or all.")
        parser.add_argument("--init-store", action="store_true", help="Create or refresh the canonical store files.")
        parser.add_argument("--dry-run", action="store_true", help="Print the cost report and change nothing.")
        parser.add_argument("--force", action="store_true", help="Allow a large projected skill index.")
        parser.add_argument("--context-window", type=int, default=1_000_000, help="Model context window in tokens.")
        parser.add_argument("--store", type=Path, default=None, help="Canonical store path.")
        parser.add_argument("--repo", type=Path, default=None, help="Repository root path.")
        parser.add_argument("--home", type=Path, default=None, help="User profile path.")
        return parser  # Return the complete parser to the CLI runner.

    def _installer_class(self) -> Any:
        from src.juniper_skills.install import SkillInstaller  # Import after sys.path is prepared.

        return SkillInstaller  # Return the installer type for testable construction.

    def _dispatch(self, args: argparse.Namespace, installer: Any) -> list[Any]:
        if args.init_store:  # Initialize the store when requested.
            return installer.initialize_store()  # Write store metadata and catalog files.
        if args.list:  # Show inventory without changing host registrations.
            return self._print_lines(installer.list_inventory())  # Print inventory lines.
        if args.search:  # Search package metadata without changing host registrations.
            return self._print_search(installer.search_catalog(args.search))  # Print catalog matches.
        return self._dispatch_mutation(args, installer)  # Continue with operations that can change host paths.

    def _dispatch_mutation(self, args: argparse.Namespace, installer: Any) -> list[Any]:
        if args.unregister_all:  # Remove every known junction when requested.
            return installer.unregister_all()  # Remove only host junctions.
        if args.unregister or args.uninstall:  # Remove one junction set when requested.
            return installer.uninstall_skill(args.unregister or args.uninstall)  # Remove only junctions.
        if args.verify == "__all__":  # Verify all canonical skills when no name follows --verify.
            return installer.verify_all()  # Check every canonical package target.
        if args.verify:  # Verify one canonical skill when a name follows --verify.
            return installer.verify_skill(args.verify)  # Check the named package targets.
        return self._dispatch_registration(args, installer)  # Continue with registration modes.

    def _dispatch_registration(self, args: argparse.Namespace, installer: Any) -> list[Any]:
        if args.register_routers:  # Keep router-only registration as the explicit exception.
            return self._run_registration(installer.install_routers(args.force, args.dry_run))  # Run router plan.
        if args.all or args.register_all:  # Register every document package when requested.
            return self._run_registration(installer.install_all_packages(args.force, args.dry_run))  # Run the plan.
        if args.register_domain:  # Register one complete domain when requested.
            return self._run_registration(installer.install_domain(args.register_domain, args.force, args.dry_run))
        if args.register:  # Register selected document packages when requested.
            return self._run_registration(installer.install_packages(args.register, args.force, args.dry_run))
        if args.skill:  # Keep the legacy positional install path for existing scripts.
            return self._run_registration(installer.install_packages([args.skill], args.force, args.dry_run))
        return self._run_registration(installer.install_all_packages(args.force, args.dry_run))  # Default to packages.

    def _run_registration(self, result: tuple[Any, list[Any]]) -> list[Any]:
        from src.juniper_skills.install import InstallOutcome  # Import only when a refused outcome is needed.

        report, outcomes = result  # Split the measured projection from the install actions.
        self._print_cost_report(report)  # Print the projection before any install outcome.
        if report.action == "refused":  # Return a refused outcome so the process exits nonzero.
            return [InstallOutcome(Path("skill-index"), "refused", report.message)]  # Explain the threshold block.
        return outcomes  # Return installer outcomes for normal printing.

    def _print_cost_report(self, report: Any) -> None:
        print("Projected skill index cost:")  # Introduce the required cost report.
        print(f"  Skills: {report.skill_count}")  # Print the number of registered skills in the plan.
        print(f"  Estimate: {report.token_count} tokens, approximately.")  # State token approximation.
        print(f"  Window share: {report.window_share:.2f}% of {report.context_window:,} tokens.")  # Print share.
        print(f"  Decision: {report.action}. {report.message}")  # Print threshold decision.
        self._print_description_report(report)  # Report descriptions that exceed the routing budget.

    def _print_description_report(self, report: Any) -> None:
        count = len(report.over_budget_descriptions)  # Count descriptions that the generator should tighten.
        print(f"  Descriptions over 100 tokens: {count}")  # Print the description budget result.
        for skill in report.over_budget_descriptions[:10]:  # Keep the report bounded for very large installs.
            print(f"    - {skill.slug}: {len(skill.description) // 4} tokens, approximately.")  # Name the package.

    def _print_search(self, matches: list[Any]) -> list[Any]:
        print("Catalog search results:")  # Label search output for humans and agents.
        for match in matches:  # Print one stable line per package match.
            print(self._search_line(match))  # Keep search rendering isolated for tests.
        if not matches:  # State that no package matched the keyword.
            print("No packages matched the keyword.")  # Keep empty search output explicit.
        return []  # Search changes nothing, so no install outcomes exist.

    def _search_line(self, match: Any) -> str:
        return f"- {match.slug} [{match.domain}] {match.title}"  # Print slug, domain, and title.

    def _print_lines(self, lines: list[str]) -> list[Any]:
        for line in lines:  # Print each inventory line exactly once.
            print(line)  # Keep line formatting under the installer inventory method.
        return []  # Listing changes nothing, so no install outcomes exist.

    def _print_outcomes(self, outcomes: list[Any]) -> None:
        for outcome in outcomes:  # Print one line per host or generated file.
            print(f"{outcome.action}: {outcome.target} - {outcome.detail}")  # Keep output readable.


if __name__ == "__main__":  # Run the CLI when an operator executes the script.
    raise SystemExit(InstallSkillsCli().run())  # Return a process status that matches the installer report.
