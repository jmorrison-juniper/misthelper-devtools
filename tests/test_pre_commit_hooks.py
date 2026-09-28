"""Guard the pre-commit hook manifest of this repository.

A Mist repository runs a hook of this repository through pre-commit. The
manifest names a console script of this package as the entry of each hook. If
a script gets a new name and the manifest keeps the old name, the hook fails
in each consumer repository. These tests compare the two files.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_KEYS = ("id", "name", "entry", "language")


def load_hooks() -> list[dict[str, Any]]:
    """Parse the hook manifest.

    Returns:
        One mapping for each hook.
    """
    document = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert isinstance(document, list), ".pre-commit-hooks.yaml must parse to a list."
    return document


def console_scripts() -> set[str]:
    """Return the name of each console script that the package declares."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return set(tomllib.load(handle)["project"]["scripts"])


def unknown_entries(hooks: list[dict[str, Any]], scripts: set[str]) -> list[str]:
    """Find each hook that runs a command that the package does not install.

    Args:
        hooks: The parsed hook manifest.
        scripts: The console script names of the package.

    Returns:
        The ID of each hook whose entry is not a console script.
    """
    return [hook["id"] for hook in hooks if hook["entry"].split()[0] not in scripts]


def test_manifest_holds_at_least_one_hook() -> None:
    assert load_hooks(), ".pre-commit-hooks.yaml must hold at least one hook."


def test_each_hook_has_the_required_keys() -> None:
    for hook in load_hooks():
        missing = [key for key in REQUIRED_KEYS if not hook.get(key)]
        assert missing == [], f"Hook {hook.get('id')!r} has no value for {missing}."


def test_hook_ids_are_unique() -> None:
    ids = [hook["id"] for hook in load_hooks()]
    assert len(ids) == len(set(ids)), f"Two hooks share an ID: {sorted(ids)}."


def test_each_hook_runs_a_console_script_of_this_package() -> None:
    assert unknown_entries(load_hooks(), console_scripts()) == []


def test_a_hook_that_runs_a_removed_script_is_reported() -> None:
    hooks = [{"id": "kept", "entry": "ste-linter"}, {"id": "gone", "entry": "removed-tool --flag"}]
    assert unknown_entries(hooks, {"ste-linter"}) == ["gone"]


def test_each_hook_installs_this_package() -> None:
    # The python language makes pre-commit install this repository at the tag
    # of the consumer. Another language would run a different release.
    languages = {hook["id"]: hook["language"] for hook in load_hooks()}
    assert set(languages.values()) == {"python"}, languages


def test_ste_linter_hook_reads_markdown_and_python() -> None:
    hooks = {hook["id"]: hook for hook in load_hooks()}
    assert hooks["ste-linter"]["types_or"] == ["markdown", "python"]


def test_markdown_link_check_hook_reads_markdown() -> None:
    hooks = {hook["id"]: hook for hook in load_hooks()}
    assert hooks["markdown-link-check"]["types"] == ["markdown"]
