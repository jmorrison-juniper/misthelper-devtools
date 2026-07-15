"""Unit tests for TestFileDiscoverer + MistApiExcluder (T015).

Coverage:
    - discovery walks a tmp_path tree and returns POSIX-sorted paths.
    - discovery handles both `test_*.py` and `*_test.py` name patterns.
    - discovery skips non-test files and non-existent roots gracefully.
    - MistApiExcluder classifies the four import shapes (import, from-import,
      src.api prefix, unrelated) correctly, plus the empty-module edge case.
    - Regression anchor: `from src.api.api_data_fetcher import fetch` is
      classified `mist_api_excluded` per SC-002.
"""

from __future__ import annotations  # Postponed annotations for consistency.

import ast  # Used to build synthetic ASTs from string source.
from pathlib import Path  # Path arg for classify() and discover().

from tools.test_quality_analyzer.discovery import (  # SUTs.
    MistApiExcluder,
    TestFileDiscoverer,
)


def _mk_tree(tmp_path: Path, layout: dict[str, str]) -> None:
    """Create files under tmp_path from a {relative_path: contents} mapping."""
    # Each entry becomes a file with the given text under tmp_path.
    for rel, contents in layout.items():
        target = tmp_path / rel  # Assemble the absolute file location.
        target.parent.mkdir(parents=True, exist_ok=True)  # mkdir -p equivalent.
        target.write_text(contents, encoding="utf-8")  # Write test source verbatim.


def test_discover_finds_test_underscore_prefix(tmp_path: Path) -> None:
    """`test_*.py` files must be discovered, non-test siblings must not."""
    _mk_tree(
        tmp_path,
        {
            "a/test_alpha.py": "",  # Match: test_ prefix.
            "a/helper.py": "",  # Non-match: no test_ prefix and no _test suffix.
            "a/b/test_beta.py": "",  # Match: nested test_ prefix.
        },
    )
    files = TestFileDiscoverer().discover([tmp_path])  # Run discovery.
    names = [p.name for p in files]  # Extract just filenames for readability.
    assert "test_alpha.py" in names  # First match must appear.
    assert "test_beta.py" in names  # Nested match must appear.
    assert "helper.py" not in names  # Non-match must be filtered out.


def test_discover_finds_underscore_test_suffix(tmp_path: Path) -> None:
    """`*_test.py` files (unittest-style) must also match discovery."""
    _mk_tree(tmp_path, {"suite/alpha_test.py": ""})  # Only entry uses suffix pattern.
    files = TestFileDiscoverer().discover([tmp_path])  # Run discovery.
    assert len(files) == 1  # Exactly one file discovered.
    assert files[0].name == "alpha_test.py"  # Matching filename.


def test_discover_sorts_output(tmp_path: Path) -> None:
    """Discovery output must be sorted for deterministic downstream ordering."""
    _mk_tree(
        tmp_path,
        {"z/test_z.py": "", "a/test_a.py": "", "m/test_m.py": ""},  # Alpha across dirs.
    )
    files = TestFileDiscoverer().discover([tmp_path])  # Run discovery.
    posix = [p.as_posix() for p in files]  # Convert to POSIX strings for comparison.
    assert posix == sorted(posix)  # Output must already be sorted ascending.


def test_discover_missing_root_is_skipped(tmp_path: Path) -> None:
    """A non-existent root must not raise; it is warned and skipped."""
    missing = tmp_path / "does_not_exist"  # Path that does not exist.
    files = TestFileDiscoverer().discover([missing])  # Should not raise.
    assert files == []  # No files discovered from the missing root.


def test_discover_returns_posix_paths(tmp_path: Path) -> None:
    """Every returned path must be free of backslashes (POSIX invariant)."""
    _mk_tree(tmp_path, {"nested/dir/test_leaf.py": ""})  # One nested match.
    files = TestFileDiscoverer().discover([tmp_path])  # Run discovery.
    for path in files:
        assert "\\" not in path.as_posix()  # No backslashes anywhere in the string.


def test_excluder_import_mistapi_is_excluded() -> None:
    """`import mistapi` at module scope must be classified mist_api_excluded."""
    tree = ast.parse("import mistapi\n")  # Simplest banned-import shape.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is not None  # Must return a SkippedFile record.
    assert result.reason == "mist_api_excluded"  # Reason string matches spec.
    assert result.matched_rule == "mist_api_predicate"  # R4: matched_rule set.


def test_excluder_from_mistapi_is_excluded() -> None:
    """`from mistapi import X` at module scope must be classified excluded."""
    tree = ast.parse("from mistapi import Session\n")  # From-import banned shape.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is not None  # Must return a SkippedFile record.


def test_excluder_src_api_prefix_is_excluded() -> None:
    """`from src.api.foo import bar` at module scope must be classified excluded."""
    # Regression anchor per SC-002: real prefix used elsewhere in the tree.
    tree = ast.parse("from src.api.api_data_fetcher import fetch\n")  # Real anchor.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is not None  # Must return a SkippedFile record.
    assert result.matched_rule == "mist_api_predicate"  # Predicate name in record.


def test_excluder_unrelated_import_is_none() -> None:
    """An unrelated import like `import requests` must yield None (no exclusion)."""
    tree = ast.parse("import requests\n")  # Standard library-ish, not banned.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is None  # No exclusion emitted.


def test_excluder_empty_module_is_none() -> None:
    """An empty module (no statements) must yield None."""
    tree = ast.parse("")  # Empty source, zero statements in tree.body.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is None  # Nothing to match; no exclusion.


def test_excluder_nested_import_does_not_count() -> None:
    """Imports inside a function body must NOT trigger exclusion (module-scope only)."""
    # Function-scoped `import mistapi` should be ignored per FR-002 wording.
    source = "def helper():\n    import mistapi\n    return mistapi\n"  # Nested import.
    tree = ast.parse(source)  # Parse the nested-import example.
    result = MistApiExcluder().classify(Path("tests/x/test_x.py"), tree)  # Classify.
    assert result is None  # No exclusion because import is not module-scope.
