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


def test_mermaid_action_asks_setup_node_for_no_cache() -> None:
    """Keep setup-node from hashing a lock file outside the caller workspace.

    setup-node hashes only the files in the caller workspace. A caller runs this
    action from a folder outside that workspace, so a cache input fails the step.
    """
    action = load_action()
    steps = action["runs"]["steps"]
    setup_node = next(step for step in steps if step.get("uses", "").startswith("actions/setup-node@"))

    assert setup_node["with"] == {"node-version": "${{ inputs.node-version }}"}


def test_mermaid_action_installs_from_its_own_lockfile() -> None:
    """Require npm ci to read the lock file that ships with the action."""
    action = load_action()
    runs = [step.get("run", "") for step in action["runs"]["steps"]]

    assert any(run.startswith('npm ci --prefix "${GITHUB_ACTION_PATH}"') for run in runs)


def test_mermaid_package_versions_match_lockfile() -> None:
    """Require the parser pins to match the reproducible lock file."""
    package = load_json(PACKAGE_PATH)
    lock = load_json(LOCK_PATH)

    assert package["dependencies"] == {"jsdom": "^29.1.1", "mermaid": "^12.1.0"}
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
