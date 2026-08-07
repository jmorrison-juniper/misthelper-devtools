"""Tests for the third-party override exemption in the STRUCT-PARAMS rule (issue #1800)."""

from __future__ import annotations

import ast

from tools.compliance_analyzer.analyzers import StructuralComplexityAnalyzer as Analyzer


def _tree(source: str) -> ast.Module:
    """Parse a source snippet into a module tree."""
    return ast.parse(source)


def test_foreign_import_names_are_collected() -> None:
    """A from-import outside the repository binds a foreign name."""
    names = Analyzer._third_party_import_names(_tree("from requests.adapters import HTTPAdapter\n"))
    assert names == {"HTTPAdapter"}


def test_first_party_import_names_are_not_foreign() -> None:
    """An import from a package the repository owns is not foreign."""
    names = Analyzer._third_party_import_names(_tree("from src.db.router import DatabaseRouter\n"))
    assert names == set()


def test_relative_import_names_are_not_foreign() -> None:
    """A relative import is always first-party regardless of depth."""
    assert Analyzer._third_party_import_names(_tree("from .sibling import Thing\n")) == set()


def test_plain_import_binds_the_root_package() -> None:
    """``import requests`` binds ``requests``, and the alias form binds the alias."""
    names = Analyzer._third_party_import_names(_tree("import requests\nimport os.path as p\n"))
    assert names == {"requests", "p"}


def test_method_of_a_third_party_subclass_is_exempt() -> None:
    """The real case: an adapter override whose signature the library fixes."""
    source = (
        "from requests.adapters import HTTPAdapter\n"
        "class TimeoutAdapter(HTTPAdapter):\n"
        "    def send(self, request, stream, timeout, verify, cert, proxies):\n"
        "        return None\n"
    )
    assert Analyzer._third_party_override_methods(_tree(source)) != set()


def test_method_of_a_first_party_subclass_is_not_exempt() -> None:
    """A class we own must still obey the parameter budget."""
    source = (
        "from src.db.router import DatabaseRouter\n"
        "class MyRouter(DatabaseRouter):\n"
        "    def send(self, a, b, c, d, e, f):\n"
        "        return None\n"
    )
    assert Analyzer._third_party_override_methods(_tree(source)) == set()


def test_class_without_bases_is_not_exempt() -> None:
    """A plain class inherits no foreign contract."""
    source = (
        "from requests.adapters import HTTPAdapter\n"
        "class Plain:\n"
        "    def f(self, a, b, c, d, e, f):\n"
        "        return None\n"
    )
    assert Analyzer._third_party_override_methods(_tree(source)) == set()


def test_dotted_base_is_resolved_through_its_root() -> None:
    """``class C(requests.adapters.HTTPAdapter)`` is bound through ``requests``."""
    source = (
        "import requests\n"
        "class A(requests.adapters.HTTPAdapter):\n"
        "    def send(self, a, b, c, d, e, f):\n"
        "        return None\n"
    )
    assert Analyzer._third_party_override_methods(_tree(source)) != set()


def test_nested_class_inside_a_function_is_covered() -> None:
    """The real case declares the adapter inside a function, so nesting must work."""
    source = (
        "from requests.adapters import HTTPAdapter\n"
        "def install():\n"
        "    class TimeoutAdapter(HTTPAdapter):\n"
        "        def send(self, request, stream, timeout, verify, cert, proxies):\n"
        "            return None\n"
    )
    assert Analyzer._third_party_override_methods(_tree(source)) != set()


def test_no_foreign_imports_short_circuits() -> None:
    """With nothing imported from outside, no method can be exempt."""
    assert Analyzer._third_party_override_methods(_tree("class C:\n    pass\n")) == set()
