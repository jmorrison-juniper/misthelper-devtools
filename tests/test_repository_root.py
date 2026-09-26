"""Tests for the shared repository root resolver.

An installed tool must read the repository the operator names, not the
site-packages directory that holds the tool. These tests prove that each
consumer accepts an explicit root and that the search finds the nearest
``.git`` entry when the caller names no root.
"""

import subprocess  # nosec B404 - The tests build a small repository with a fixed argument list.
import sys
from pathlib import Path

import pytest

from misthelper_devtools.prompt_audit import FunctionIndex, menu_file_for, read_menu_handlers, source_roots_for
from misthelper_devtools.repository_root import resolve_repository_root
from misthelper_devtools.symbol_diff.comparator import SymbolTableComparator

_MENU_TABLE = """"1": GlobalImportManager.MenuEntry(
        label="List sites",
        handler=SiteOperations.list_sites,
    ),
"""


def _build_repository(root: Path) -> Path:
    """Create a small git repository that holds a menu table and one source file."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # nosec B603 B607
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)  # nosec B603 B607
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)  # nosec B603 B607
    (root / "MistHelper.py").write_text(_MENU_TABLE, encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "operations.py").write_text(
        "class SiteOperations:\n    def list_sites(self):\n        return select_site()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # nosec B603 B607
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)  # nosec B603 B607
    return root


def test_resolver_returns_the_root_the_caller_names(tmp_path: Path) -> None:
    """A named root must win over the working directory."""
    assert resolve_repository_root(tmp_path) == tmp_path.resolve()


def test_resolver_finds_the_nearest_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no named root, the search must walk up to the nearest .git entry."""
    repository = _build_repository(tmp_path / "repo")
    nested = repository / "src"
    monkeypatch.chdir(nested)

    assert resolve_repository_root() == repository.resolve()


def test_resolver_falls_back_to_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory with no checkout above it must still return a usable root."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)

    assert resolve_repository_root() == plain.resolve()


def test_prompt_audit_reads_the_named_repository(tmp_path: Path) -> None:
    """The menu reader and the function index must read the named checkout."""
    repository = _build_repository(tmp_path / "repo")

    assert menu_file_for(repository) == repository.resolve() / "MistHelper.py"
    assert source_roots_for(repository) == (repository.resolve() / "src",)
    assert read_menu_handlers(repository) == {"1": "SiteOperations.list_sites"}
    assert FunctionIndex(repository).lookup("SiteOperations.list_sites") is not None


def test_prompt_audit_no_longer_reads_its_own_install_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default root must follow the working directory, not the install directory."""
    package_directory = Path(sys.modules["misthelper_devtools.prompt_audit"].__file__ or "").resolve().parent
    repository = _build_repository(tmp_path / "repo")
    monkeypatch.chdir(repository)

    assert menu_file_for() == repository.resolve() / "MistHelper.py"
    assert menu_file_for().parent != package_directory


def test_symbol_diff_reads_the_named_repository(tmp_path: Path) -> None:
    """The comparator must run git inside the named checkout."""
    repository = _build_repository(tmp_path / "repo")
    comparator = SymbolTableComparator(repository)

    text = comparator.read_revision("HEAD", Path("src/operations.py"))

    assert text is not None
    assert "class SiteOperations" in text


def test_symbol_diff_reports_a_lost_name_in_the_named_repository(tmp_path: Path) -> None:
    """A deleted module-level name must appear in the delta for the named checkout."""
    repository = _build_repository(tmp_path / "repo")
    (repository / "src" / "operations.py").write_text("", encoding="utf-8")
    comparator = SymbolTableComparator(repository)

    base = comparator.collect_names(comparator.read_revision("HEAD", Path("src/operations.py")) or "", "base")
    head = comparator.collect_names("", "head")
    delta = comparator.compare(base or set(), head or set(), "src/operations.py")

    assert delta.lost == ("SiteOperations",)
