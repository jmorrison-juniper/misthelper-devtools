"""Guard the agent instruction templates under ``templates/agent-instructions/``.

Each repository of the owner copies ``AGENTS.md`` from this folder, byte for
byte, and starts its own ``.github/copilot-instructions.md`` from the skeleton.
A template that scores below the STE threshold would fail the ``ste-lint.yml``
caller of each repository, so these tests grade each template with the shared
``ste-linter.toml`` at the same threshold.

CI has no ASD-STE100 dictionary, so the linter runs the structural rules there.
A workstation can hold a user-level dictionary. The tests grade each file in
both modes, so a template passes with and without the vocabulary rules.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from misthelper_devtools.ste_linter.cli import main as ste_linter_main

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "agent-instructions"
CONFIG = TEMPLATES / "ste-linter.toml"
MIN_SCORE = 80
MARKDOWN_FILES = sorted(TEMPLATES.glob("*.md"))
MARKDOWN_IDS = [path.name for path in MARKDOWN_FILES]

# PyYAML reads the bare key `on` as the boolean True.
TRIGGERS = True


def load_config() -> dict[str, Any]:
    """Parse the shared linter configuration.

    Returns:
        The ``[tool.ste_linter]`` table.
    """
    with CONFIG.open("rb") as handle:
        table = tomllib.load(handle)["tool"]["ste_linter"]
    assert isinstance(table, dict), "ste-linter.toml must hold a [tool.ste_linter] table."
    return table


def test_the_folder_holds_each_template() -> None:
    expected = {"AGENTS.md", "CLAUDE.md", "copilot-instructions.md", "ste-linter.toml", "ste-lint.yml"}
    assert {path.name for path in TEMPLATES.iterdir()} == expected


@pytest.mark.parametrize("path", MARKDOWN_FILES, ids=MARKDOWN_IDS)
def test_each_template_passes_the_ste_gate_as_configured(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The run uses the dictionary that the lookup finds. CI finds none, so the
    # structural rules run. A workstation with a user-level copy also runs the
    # vocabulary rules. The template must pass either way.
    code = ste_linter_main(["--config", str(CONFIG), "--min-score", str(MIN_SCORE), str(path)])
    report = capsys.readouterr().out
    assert code == 0, report
    assert "PASS" in report, report


@pytest.mark.parametrize("path", MARKDOWN_FILES, ids=MARKDOWN_IDS)
def test_each_template_passes_the_ste_gate_without_a_dictionary(
    path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # An explicit missing dictionary path has no fallback, so this run matches
    # the CI job of each consumer repository.
    missing = tmp_path / "no-dictionary.json"
    code = ste_linter_main(
        ["--config", str(CONFIG), "--dictionary", str(missing), "--min-score", str(MIN_SCORE), str(path)]
    )
    report = capsys.readouterr().out
    assert code == 0, report
    assert "dictionary: skipped" in report, report


def test_the_config_sets_the_threshold_and_no_dictionary_path() -> None:
    table = load_config()
    assert table["min_score"] == MIN_SCORE
    # A `dictionary` key would name a path that exists in one repository only.
    # Without the key, the linter finds the user-level copy on a workstation.
    assert "dictionary" not in table


def test_the_allowlist_is_sorted_and_lower_case() -> None:
    allowlist = load_config()["allowlist"]
    assert allowlist == sorted(allowlist), "Keep the allowlist sorted."
    assert all(word == word.lower() for word in allowlist), "Keep the allowlist lower case."
    assert len(allowlist) == len(set(allowlist)), "Each allowlist word appears one time."


def test_the_generic_file_points_to_the_repository_file() -> None:
    text = (TEMPLATES / "AGENTS.md").read_text(encoding="utf-8")
    assert "`.github/copilot-instructions.md`" in text
    assert "templates/agent-instructions/AGENTS.md" in text
    # A template that names a product, a menu number, or a port is no longer generic.
    for word in ("MistHelper", "Podman", "pytest", "ruff", "mistapi", "PowerShell"):
        assert word not in text, f"AGENTS.md must not name {word}."


def test_the_skeleton_points_to_the_generic_file() -> None:
    text = (TEMPLATES / "copilot-instructions.md").read_text(encoding="utf-8")
    assert text.startswith("# `<repository name>` agent instructions\n")
    assert "`AGENTS.md`" in text.split("\n\n")[1]


def test_the_pointer_file_imports_both_files() -> None:
    text = (TEMPLATES / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in text
    assert "@.github/copilot-instructions.md" in text


def test_the_caller_workflow_pins_a_full_commit_with_a_release_comment() -> None:
    text = (TEMPLATES / "ste-lint.yml").read_text(encoding="utf-8")
    pattern = (
        r"uses: jmorrison-juniper/misthelper-devtools/\.github/workflows/reusable-ste-lint\.yml"
        r"@([0-9a-f]{40}) # v\d+\.\d+\.\d+"
    )
    match = re.search(pattern, text)
    assert match, "The caller must pin a 40-character commit SHA and name the release in a comment."


def test_the_caller_workflow_grades_the_three_files_with_the_shared_config() -> None:
    document = yaml.safe_load((TEMPLATES / "ste-lint.yml").read_text(encoding="utf-8"))
    job = document["jobs"]["ste-lint"]
    files = job["with"]["files"].split()
    assert files == ["README.md", "AGENTS.md", ".github/copilot-instructions.md"]
    assert job["with"]["config"] == ".ste-linter.toml"
    assert job["with"]["min-score"] == MIN_SCORE
    assert job["permissions"] == {"contents": "read"}


def test_the_caller_workflow_carries_the_run_guards() -> None:
    # One event starts one run, and a new commit cancels the previous run,
    # except on main. A `paths` filter would stop a required check from
    # reporting, so the triggers carry none.
    document = yaml.safe_load((TEMPLATES / "ste-lint.yml").read_text(encoding="utf-8"))
    triggers = document[TRIGGERS]
    assert triggers["push"] == {"branches": ["main"]}
    assert triggers["pull_request"] is None
    assert "workflow_dispatch" in triggers
    assert document["concurrency"]["group"] == "${{ github.workflow }}-${{ github.head_ref || github.ref }}"
    assert document["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"
