"""Guard the agent instruction files of this repository.

The root ``AGENTS.md`` is a copy of ``templates/agent-instructions/AGENTS.md``.
Each repository of the owner holds the same generic file, byte for byte, and
this repository is no exception. The repository-specific rules live in
``.github/copilot-instructions.md``, and ``.ste-linter.toml`` holds the shared
linter settings. These tests fail when a copy drifts from its template, when an
instruction file scores below the STE threshold, or when the CI job stops
grading the instruction files.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from misthelper_devtools.agent_instructions_check import EXIT_MATCH
from misthelper_devtools.agent_instructions_check import main as check_main
from misthelper_devtools.ste_linter.cli import main as ste_linter_main

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "agent-instructions"
CONFIG = ROOT / ".ste-linter.toml"
MIN_SCORE = 80
INSTRUCTION_FILES = (ROOT / "AGENTS.md", ROOT / ".github" / "copilot-instructions.md")
INSTRUCTION_IDS = ["AGENTS.md", "copilot-instructions.md"]


def load_table(path: Path) -> dict[str, Any]:
    """Return the ``[tool.ste_linter]`` table of one TOML file."""
    with path.open("rb") as handle:
        table = tomllib.load(handle)["tool"]["ste_linter"]
    assert isinstance(table, dict), f"{path.name} must hold a [tool.ste_linter] table."
    return table


def test_the_root_agents_file_is_the_template_byte_for_byte() -> None:
    assert (ROOT / "AGENTS.md").read_bytes() == (TEMPLATES / "AGENTS.md").read_bytes()


def test_the_drift_checker_accepts_the_root_copy(capsys: pytest.CaptureFixture[str]) -> None:
    # The command that each consumer runs must also accept this repository.
    code = check_main(["--root", str(ROOT), "--canonical", str(TEMPLATES / "AGENTS.md")])
    assert code == EXIT_MATCH, capsys.readouterr().out


def test_the_root_linter_settings_follow_the_template() -> None:
    root = load_table(CONFIG)
    template = load_table(TEMPLATES / "ste-linter.toml")
    assert root["min_score"] == template["min_score"] == MIN_SCORE
    assert "dictionary" not in root
    # The root file may add a technical term, but it must keep each template term.
    assert set(template["allowlist"]) <= set(root["allowlist"])


def test_pyproject_holds_no_second_linter_table() -> None:
    # One file holds the linter settings, so the CI job, the local command, and
    # a consumer copy read the same values.
    with (ROOT / "pyproject.toml").open("rb") as handle:
        assert "ste_linter" not in tomllib.load(handle).get("tool", {})


@pytest.mark.parametrize("path", INSTRUCTION_FILES, ids=INSTRUCTION_IDS)
def test_each_instruction_file_passes_the_ste_gate_as_configured(
    path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = ste_linter_main(["--config", str(CONFIG), "--min-score", str(MIN_SCORE), str(path)])
    report = capsys.readouterr().out
    assert code == 0, report
    assert "PASS" in report, report


@pytest.mark.parametrize("path", INSTRUCTION_FILES, ids=INSTRUCTION_IDS)
def test_each_instruction_file_passes_the_ste_gate_without_a_dictionary(
    path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "no-dictionary.json"
    code = ste_linter_main(
        ["--config", str(CONFIG), "--dictionary", str(missing), "--min-score", str(MIN_SCORE), str(path)]
    )
    report = capsys.readouterr().out
    assert code == 0, report
    assert "dictionary: skipped" in report, report


def test_the_specific_file_starts_with_the_fixed_paragraph() -> None:
    text = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert text.startswith("# `misthelper-devtools` agent instructions\n")
    first_paragraph = text.split("\n\n")[1]
    assert first_paragraph.startswith("This file holds the rules that apply to `misthelper-devtools` only.")
    assert "`AGENTS.md`" in first_paragraph
    # The skeleton comment block is a work aid, not a rule.
    assert "<!--" not in text


def test_each_console_script_has_a_row_in_the_command_table() -> None:
    # The specific file tells an agent that each console script has a line in
    # pyproject.toml and a row in the command table of the tooling guide.
    with (ROOT / "pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle)["project"]["scripts"]
    guide = (ROOT / "docs" / "tooling-guide.md").read_text(encoding="utf-8")
    missing = [name for name in scripts if f"| `{name}` |" not in guide]
    assert missing == [], f"Add a command table row for {missing}."


def test_the_ci_job_grades_the_instruction_files_with_the_shared_config() -> None:
    document = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = document["jobs"]["ste-lint"]
    files = job["with"]["files"].split()
    assert "AGENTS.md" in files
    assert ".github/copilot-instructions.md" in files
    assert "README.md" in files
    assert job["with"]["config"] == ".ste-linter.toml"
    assert job["uses"] == "./.github/workflows/reusable-ste-lint.yml"


def test_no_retired_instruction_file_exists() -> None:
    # A lower-case agents.md or an instructions folder would hold a second copy
    # of a rule. The two-file model keeps each rule in one place. The check reads
    # the exact names, because a case-insensitive file system matches agents.md
    # to AGENTS.md.
    assert "agents.md" not in {entry.name for entry in ROOT.iterdir()}
    assert "instructions" not in {entry.name for entry in (ROOT / ".github").iterdir()}
