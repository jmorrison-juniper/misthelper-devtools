"""Tests for the generic Markdown link checker."""

from __future__ import annotations

import subprocess
from pathlib import Path

from misthelper_devtools.markdown_link_check import MarkdownLinkChecker, main


def _git(repository: Path, *args: str) -> None:
    """Run git in a temporary repository."""
    subprocess.run(["git", *args], cwd=repository, check=True, timeout=30)


def _init_repository(repository: Path) -> None:
    """Create a temporary git repository for link tests."""
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")


def _commit_all(repository: Path) -> None:
    """Commit every file in a temporary repository."""
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "seed")


def test_markdown_link_checker_reports_missing_files_and_anchors(tmp_path: Path) -> None:
    """The checker reports repository-local dead links with source lines."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "docs").mkdir()
    (repository / "docs" / "target file.md").write_text("# Good Heading\n", encoding="utf-8")
    (repository / "docs" / "guide.md").write_text(
        "\n".join(
            [
                "# Guide",
                "[ok](target%20file.md#good-heading)",
                "[bad](missing.md)",
                "[bad anchor](target%20file.md#missing-heading)",
                "![bad image](missing.png)",
            ]
        ),
        encoding="utf-8",
    )
    _commit_all(repository)

    failures = MarkdownLinkChecker(repository).broken_links()

    assert [failure.report_line() for failure in failures] == [
        "docs/guide.md:3: missing.md (no such file)",
        "docs/guide.md:4: target%20file.md#missing-heading (no such anchor)",
        "docs/guide.md:5: missing.png (no such file)",
    ]


def test_markdown_link_checker_resolves_a_leading_slash_against_the_root(tmp_path: Path) -> None:
    """GitHub resolves a link that starts with a slash against the repository root."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "docs" / "api").mkdir(parents=True)
    (repository / "docs" / "guide.md").write_text("# Guide\n\n## Setup\n", encoding="utf-8")
    (repository / "docs" / "api" / "page.md").write_text(
        "\n".join(
            [
                "[root](/)",
                "[root anchor](/#operations/listThings)",
                "[guide](/docs/guide.md#setup)",
                "[missing](/docs/missing.md)",
                "[bad anchor](/docs/guide.md#missing-heading)",
                "[outside](/../outside.md)",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "outside.md").write_text("# Outside\n", encoding="utf-8")  # Exists, but outside the checkout.
    _commit_all(repository)

    failures = MarkdownLinkChecker(repository).broken_links()

    assert [failure.report_line() for failure in failures] == [
        "docs/api/page.md:4: /docs/missing.md (no such file)",
        "docs/api/page.md:5: /docs/guide.md#missing-heading (no such anchor)",
        "docs/api/page.md:6: /../outside.md (no such file)",
    ]


def test_markdown_link_checker_ignores_code_and_external_links(tmp_path: Path) -> None:
    """The checker keeps the source test rules for code and URL schemes."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "README.md").write_text(
        "\n".join(
            [
                "# Readme",
                "[external](https://example.invalid/missing)",
                "[mail](mailto:test@example.com)",
                "[same page](#missing)",
                "`[inline](missing.md)`",
                "```",
                "[fenced](missing.md)",
                "```",
                "<https://example.invalid>",
            ]
        ),
        encoding="utf-8",
    )
    _commit_all(repository)

    assert MarkdownLinkChecker(repository).broken_links() == ()


def test_markdown_link_checker_respects_exclude_and_path_limits(tmp_path: Path) -> None:
    """The command can skip files by glob and scan only caller paths."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "docs").mkdir()
    (repository / "docs" / "one.md").write_text("[bad](missing.md)\n", encoding="utf-8")
    (repository / "docs" / "two.md").write_text("[bad](missing.md)\n", encoding="utf-8")
    _commit_all(repository)

    excluded = MarkdownLinkChecker(repository, ("docs/two.md",)).broken_links((Path("docs"),))
    limited = MarkdownLinkChecker(repository).broken_links((Path("docs") / "two.md",))

    assert [failure.path.as_posix() for failure in excluded] == ["docs/one.md"]
    assert [failure.path.as_posix() for failure in limited] == ["docs/two.md"]


def test_markdown_link_check_cli_returns_one_for_dead_links(tmp_path: Path, capsys) -> None:
    """The CLI prints one line per dead link and returns status one."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "README.md").write_text("[bad](missing.md)\n", encoding="utf-8")
    _commit_all(repository)

    status = main(["--root", str(repository)])

    assert status == 1
    assert capsys.readouterr().out.strip() == "README.md:1: missing.md (no such file)"


def test_markdown_link_check_cli_scans_every_tree_unless_told_to_skip(tmp_path: Path, capsys) -> None:
    """The CLI has no built-in exempt tree, and --exclude skips a named one."""
    repository = tmp_path / "repo"
    _init_repository(repository)
    (repository / "documentation" / "wiki").mkdir(parents=True)
    (repository / "documentation" / "wiki" / "Page.md").write_text("[wiki](Bare-Page)\n", encoding="utf-8")
    _commit_all(repository)

    default_status = main(["--root", str(repository)])
    default_out = capsys.readouterr().out.strip()
    excluded_status = main(["--root", str(repository), "--exclude", "documentation/wiki/**"])

    assert default_status == 1
    assert default_out == "documentation/wiki/Page.md:1: Bare-Page (no such file)"
    assert excluded_status == 0
    assert capsys.readouterr().out.strip() == ""
