"""Tests for the Mermaid syntax lint action."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPOSITORY_ROOT / ".github" / "actions" / "mermaid-lint"
ACTION_PATH = ACTION_DIR / "action.yml"
PACKAGE_PATH = ACTION_DIR / "package.json"
LOCK_PATH = ACTION_DIR / "package-lock.json"
SCRIPT_PATH = ACTION_DIR / "lint_mermaid.mjs"


def load_action() -> dict[str, Any]:
    """Load the composite action manifest."""
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file from the action directory."""
    return json.loads(path.read_text(encoding="utf-8"))


def test_mermaid_action_files_exist() -> None:
    """Require the action and its locked parser dependencies."""
    assert ACTION_PATH.is_file()
    assert SCRIPT_PATH.is_file()
    assert PACKAGE_PATH.is_file()
    assert LOCK_PATH.is_file()


def test_mermaid_action_declares_composite_inputs() -> None:
    """Require the caller contract that matches MistHelper."""
    action = load_action()

    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["docs-dir"]["default"] == "documentation"
    assert action["inputs"]["extra-files"]["default"] == "README.md"
    assert action["inputs"]["node-version"]["default"] == "24"


def test_mermaid_action_uses_action_lockfile_for_npm_cache() -> None:
    """Require setup-node to cache the action dependencies only."""
    action = load_action()
    setup_node = action["runs"]["steps"][0]

    assert setup_node["uses"] == "actions/setup-node@v7"
    assert setup_node["with"]["cache"] == "npm"
    assert setup_node["with"]["cache-dependency-path"] == ("${{ github.action_path }}/package-lock.json")


def test_mermaid_package_versions_match_lockfile() -> None:
    """Require the parser pins to match the reproducible lock file."""
    package = load_json(PACKAGE_PATH)
    lock = load_json(LOCK_PATH)

    assert package["dependencies"] == {"jsdom": "^27.0.0", "mermaid": "^11.12.1"}
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
