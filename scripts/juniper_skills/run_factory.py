"""Command-line entry point for the Juniper skill factory runner."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # Resolve the repository root before package imports.
sys.path.insert(0, str(REPO_ROOT))  # Let direct script execution import the local src package.


class RunFactoryCommand:
    """Parse CLI arguments and run the resumable factory."""

    def run(self) -> None:
        args = self._parser().parse_args()  # Read CLI arguments once at process start.
        logging.basicConfig(
            level=getattr(logging, args.log_level), format="%(levelname)s %(message)s"
        )  # Configure logs.
        module = importlib.import_module("src.juniper_skills.orchestrate")  # Import after sys.path is ready.
        config = self._config(args, module.FactoryRunConfig)  # Convert raw arguments into typed options.
        report = module.FactoryRunner(config).run()  # Run the long-lived factory loop.
        print(json.dumps(report, indent=2, sort_keys=True, default=str))  # Emit a structured progress report.

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Run the Juniper skill factory orchestrator.")  # Create parser.
        parser.add_argument("--limit", type=int, default=None, help="Stop after this many documents.")  # Add limit.
        parser.add_argument("--domain", default=None, help="Run only one Juniper domain skill.")  # Add domain.
        parser.add_argument("--dry-run", action="store_true", help="Skip install and git commit steps.")  # Add dry run.
        parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers.")  # Add workers.
        parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")  # Add log level.
        parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root path.")  # Add root.
        parser.add_argument("--store", type=Path, default=Path.home() / "juniper-agent-skills")  # Add store.
        return parser  # Return the configured parser.

    def _config(self, args: argparse.Namespace, config_class: object):
        repo_root = args.repo_root.resolve()  # Normalize the repository root for subprocesses.
        database = repo_root / "data" / "juniper_skills" / "factory.db"  # Use the locked queue location.
        return config_class(repo_root, database, args.store, args.limit, args.domain, args.dry_run, args.workers)


if __name__ == "__main__":
    RunFactoryCommand().run()  # Run the class-based CLI entry point.
