"""Regression tests for source-driven failure coverage selection."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from misthelper_devtools.test_quality_analyzer.detection.missing_failure_mode import (
    MissingFailureModeDetector,
    SourceDrivenCoverageSlicer,
)

_SOURCE = """\
import mistapi

class Manager:
    def request(self):
        return mistapi.api.request()
"""


def _case(tmp_path: Path, test_source: str) -> tuple[Path, ast.Module, str]:
    """Write a small source project and return its parsed test module."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "subject.py").write_text(_SOURCE, encoding="utf-8")
    test_path = tmp_path / "tests" / "test_subject.py"
    test_path.parent.mkdir()
    test_path.write_text(test_source, encoding="utf-8")
    return test_path, ast.parse(test_source), test_source


def _slice(tmp_path: Path, test_source: str) -> str:
    """Return the slicer output for one synthetic test module."""
    test_path, tree, _ = _case(tmp_path, test_source)
    return SourceDrivenCoverageSlicer(test_path, tree).source_text()


def test_malformed_source_class_lookup_reports_context(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed source remains nonfatal and reports its path and parse error."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture
def manager():
    return Manager()

def test_request(manager):
    manager.request()
"""
    test_path, tree, _ = _case(tmp_path, source)
    source_path = tmp_path / "subject.py"
    source_path.write_text("class Manager(\n", encoding="utf-8")

    with caplog.at_level("DEBUG"):
        sliced = SourceDrivenCoverageSlicer(test_path, tree).source_text()

    assert sliced == ""
    assert any(
        str(source_path) in record.getMessage() and "was never closed" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("uses_fixture", [True, False])
def test_direct_source_function_respects_fixture_parameter_shadowing(tmp_path: Path, uses_fixture: bool) -> None:
    """A fake callable parameter must not count as an imported source function."""
    source = f"""\
import pytest
from subject import fetch

class FakeResponse:
    status_code = 403

@pytest.fixture(name="fetch")
def fake_fetch():
    return lambda: FakeResponse()

def test_refusal({ "fetch" if uses_fixture else "" }):
    response = fetch()
    assert response.status_code == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)
    (tmp_path / "subject.py").write_text(
        "import mistapi\n\ndef fetch():\n    return mistapi.api.request()\n", encoding="utf-8"
    )
    sliced = SourceDrivenCoverageSlicer(test_path, tree).source_text()
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)

    assert bool(sliced) is not uses_fixture
    assert ("missing_fm_http_4xx" in [finding.rule_id for finding in findings]) is uses_fixture


@pytest.mark.parametrize(
    ("module_binding", "local_binding", "covered"),
    [
        ("", "fetch = lambda: Response()", False),
        ("fetch = lambda: Response()", "", False),
        ("", "", True),
        ("", "from subject import fetch", True),
        ("", "from subject import fetch\n    fetch = lambda: Response()", False),
    ],
)
def test_direct_source_call_requires_unreplaced_binding(
    tmp_path: Path, module_binding: str, local_binding: str, covered: bool
) -> None:
    """Replaced source names cannot prove coverage, but genuine imports still can."""
    source = f"""\
from subject import fetch

class Response:
    status_code = 403

{module_binding}

def test_refusal():
    {local_binding or "pass"}
    response = fetch()
    assert response.status_code == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)
    (tmp_path / "subject.py").write_text(
        'import mistapi\n\ndef fetch():\n    raise RuntimeError("source must not execute")\n'
        "    return mistapi.api.request()\n",
        encoding="utf-8",
    )
    sliced = SourceDrivenCoverageSlicer(test_path, tree).source_text()
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)

    assert bool(sliced) is covered
    assert ("missing_fm_http_4xx" in [finding.rule_id for finding in findings]) is not covered


@pytest.mark.parametrize(
    ("body", "covered"),
    [
        ("if True:\n        from subject import fetch", True),
        ("try:\n        from subject import fetch\n    except ImportError:\n        raise", True),
        ("if True:\n        from subject import fetch\n        fetch = lambda: Response()", False),
        ("if True:\n        from subject import fetch\n    fetch = lambda: Response()", False),
    ],
)
def test_control_block_source_import_preserves_binding_proof(tmp_path: Path, body: str, covered: bool) -> None:
    """Same-scope imports remain source evidence unless another binding replaces them."""
    source = f"""\
from subject import fetch
import subject
import requests

class Response:
    status_code = 403

def test_refusal(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *args: Response())
    {body}
    response = fetch()
    assert response.status_code == 403
    assert len(subject.calls) == {1 if covered else 0}
"""
    test_path, tree, test_source = _case(tmp_path, source)
    (tmp_path / "subject.py").write_text(
        "import requests\ncalls = []\n\ndef fetch():\n"
        '    calls.append("fetch")\n    return requests.get("https://example.invalid")\n',
        encoding="utf-8",
    )
    sliced = SourceDrivenCoverageSlicer(test_path, tree).source_text()

    assert bool(sliced) is covered
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert ("missing_fm_http_4xx" in [finding.rule_id for finding in findings]) is not covered
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixture_returning_imported_source_instance_selects_parameter_call(tmp_path: Path) -> None:
    """A pytest fixture must prove its returned imported source instance."""
    source = """\
from pytest import fixture as test_fixture
from subject import Manager

@test_fixture
def manager():
    instance = Manager()
    instance.ready = True
    return instance

def test_request(manager):
    manager.request()
"""

    sliced = _slice(tmp_path, source)

    assert "def test_request" in sliced
    assert "def manager" not in sliced
    assert "instance = Manager()" not in sliced


def test_fixture_setup_without_executable_return_does_not_select_test(tmp_path: Path) -> None:
    """A source call during fixture setup does not prove a returned source instance."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture
def manager() -> Manager:
    instance = Manager()
    instance.request()

def test_request(manager):
    manager.request()
"""

    assert _slice(tmp_path, source) == ""


def test_fixture_returning_mock_is_not_a_source_fixture(tmp_path: Path) -> None:
    """A source construction in setup cannot prove an unrelated returned object."""
    source = """\
import pytest
from unittest.mock import MagicMock
from subject import Manager

@pytest.fixture
def manager() -> Manager:
    instance = Manager()
    instance.request()
    return MagicMock()

def test_request(manager):
    manager.request()
"""

    assert _slice(tmp_path, source) == ""


@pytest.mark.parametrize("parameters", ["Manager", "*, Manager"])
@pytest.mark.parametrize("construction", ["return Manager()", "instance = Manager()\n    return instance"])
def test_fixture_parameter_hides_imported_source_class(tmp_path: Path, parameters: str, construction: str) -> None:
    """A fixture dependency can replace a source alias with a fake class."""
    source = f"""\
import pytest
from subject import Manager

class FakeManager:
    def request(self):
        return 403

@pytest.fixture(name="Manager")
def fake_manager_type():
    return FakeManager

@pytest.fixture
def manager({parameters}):
    {construction}

def test_fake_refusal(manager):
    assert manager.request() == 403
    assert {{"status_code": 403}}["status_code"] == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)

    assert SourceDrivenCoverageSlicer(test_path, tree).source_text() == ""
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert "missing_fm_http_4xx" in [finding.rule_id for finding in findings]


@pytest.mark.parametrize(
    ("module_binding", "fixture_binding", "fixture_replacement"),
    [
        ("", "from fake_module import Manager", ""),
        ("if True:\n    Manager = FakeManager", "", ""),
        ("", "", "subject = fake_subject"),
        ("", "import fake_module as Manager", ""),
        ("if True:\n    from fake_module import Manager", "", ""),
        ("", "", "if True:\n    subject = fake_subject"),
    ],
)
def test_static_bindings_cannot_replace_source_fixture_evidence(
    tmp_path: Path, module_binding: str, fixture_binding: str, fixture_replacement: str
) -> None:
    """Local imports and module bindings cannot preserve obsolete source proof."""
    source = f"""\
import pytest
from subject import Manager

class FakeManager:
    def request(self):
        return {{"status_code": 403}}

{module_binding}

@pytest.fixture
def fake_subject():
    return FakeManager()

@pytest.fixture
def subject():
    {fixture_binding or "pass"}
    return Manager()

{fixture_replacement}

def test_fake_refusal(subject):
    assert type(subject) is FakeManager
    assert subject.request()["status_code"] == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)

    assert SourceDrivenCoverageSlicer(test_path, tree).source_text() == ""
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert "missing_fm_http_4xx" in [finding.rule_id for finding in findings]


@pytest.mark.parametrize(
    "unrelated_binding",
    [
        "def unrelated():\n    Manager = object\n    subject = object",
        "class Unrelated:\n    Manager = object\n    subject = object",
        "if True:\n    unrelated = object",
    ],
)
def test_other_scopes_do_not_hide_genuine_source_fixture(tmp_path: Path, unrelated_binding: str) -> None:
    """Bindings in unrelated scopes cannot replace module source or fixture identities."""
    source = f"""\
import pytest
from subject import Manager

{unrelated_binding}

@pytest.fixture
def subject():
    instance = Manager()
    instance.ready = True
    return instance

def test_source_request(subject):
    subject.request()
"""
    assert "def test_source_request" in _slice(tmp_path, source)


@pytest.mark.parametrize(
    "replacement",
    [
        "instance: object = FakeManager()",
        "if True:\n        instance = FakeManager()",
        "if True:\n        del instance\n        instance = FakeManager()",
        "with open(__file__):\n        instance = FakeManager()",
    ],
)
def test_fixture_instance_rebinding_removes_source_evidence(tmp_path: Path, replacement: str) -> None:
    """Intervening writes cannot preserve proof of the original source instance."""
    source = f"""\
import pytest
from subject import Manager

class FakeManager:
    def request(self):
        return {{"status_code": 403}}

@pytest.fixture
def manager():
    instance = Manager()
    {replacement}
    return instance

def test_fake_refusal(manager):
    assert type(manager) is FakeManager
    assert manager.request()["status_code"] == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)

    assert SourceDrivenCoverageSlicer(test_path, tree).source_text() == ""
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert "missing_fm_http_4xx" in [finding.rule_id for finding in findings]


@pytest.mark.parametrize(
    ("module_mark", "class_mark", "class_body", "bases"),
    [
        ("", '@pytest.mark.parametrize("manager", [FakeManager()])', "", ""),
        ("", "", "    @pytest.fixture\n    def manager(self):\n        return FakeManager()\n", ""),
        ("", "", '    @pytest.fixture(name="manager")\n    def fake(self):\n        return FakeManager()\n', ""),
        ('pytestmark = pytest.mark.parametrize("manager", [FakeManager()])', "", "", ""),
        ("", "", '    pytestmark = pytest.mark.parametrize("manager", [FakeManager()])\n', ""),
        ("", "", "", "(BaseTests)"),
    ],
)
def test_class_fixture_scope_override_excludes_fake_coverage(
    tmp_path: Path, module_mark: str, class_mark: str, class_body: str, bases: str
) -> None:
    """Class fixtures, parameters, and unresolved bases invalidate module fixture proof."""
    source = f"""\
import pytest
from subject import Manager

class FakeManager:
    def request(self):
        return {{"status_code": 403}}

class BaseTests:
    @pytest.fixture
    def manager(self):
        return FakeManager()

@pytest.fixture
def manager():
    return Manager()

{module_mark}
{class_mark}
class TestFake{bases}:
{class_body}
    def test_refusal(self, manager):
        assert type(manager) is FakeManager
        assert manager.request()["status_code"] == 403
"""
    test_path, tree, test_source = _case(tmp_path, source)

    assert SourceDrivenCoverageSlicer(test_path, tree).source_text() == ""
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert "missing_fm_http_4xx" in [finding.rule_id for finding in findings]


@pytest.mark.parametrize("mark", ["", '@pytest.mark.parametrize("other", [403])'])
def test_class_method_keeps_unmodified_module_fixture(tmp_path: Path, mark: str) -> None:
    """An unrelated class parameter must not hide a genuine module source fixture."""
    source = f"""\
import pytest
from subject import Manager

@pytest.fixture
def manager():
    return Manager()

{mark}
class TestSource:
    def test_request(self, manager, other=403):
        manager.request()
"""
    assert "def test_request" in _slice(tmp_path, source)


def test_nested_class_cannot_assume_module_fixture_scope(tmp_path: Path) -> None:
    """Unresolved nested class owners must not inherit module fixture proof."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture
def manager():
    return Manager()

class TestOuter:
    @pytest.fixture
    def manager(self):
        return object()

    class TestInner:
        def test_request(self, manager):
            manager.request()
"""
    assert _slice(tmp_path, source) == ""


def test_unused_fixture_and_fixture_name_override_do_not_match_parameter(tmp_path: Path) -> None:
    """Fixture use follows pytest's declared name and requires a source call."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture(name="renamed")
def manager():
    return Manager()

def test_unused(renamed):
    assert renamed is not None

def test_wrong_name(manager):
    manager.request()
"""

    assert _slice(tmp_path, source) == ""


def test_fixture_name_override_selects_only_the_declared_fixture_name(tmp_path: Path) -> None:
    """A fixture alias matches its declared pytest name, not its function name."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture(name="renamed")
def manager():
    return Manager()

def test_request(renamed):
    renamed.request()
"""

    sliced = _slice(tmp_path, source)

    assert "def test_request" in sliced


def test_parametrize_override_and_local_shadowing_do_not_select_fixture(tmp_path: Path) -> None:
    """A parametrized or reassigned parameter no longer refers to its fixture."""
    source = """\
import pytest
from subject import Manager

@pytest.fixture
def manager():
    return Manager()

@pytest.mark.parametrize("manager", [object()])
def test_parametrized(manager):
    manager.request()

def test_shadowed(manager):
    manager = object()
    manager.request()
"""

    assert _slice(tmp_path, source) == ""


def test_shadowed_pytest_fixture_name_does_not_resolve_as_fixture(tmp_path: Path) -> None:
    """A shadowed pytest fixture binding must not establish fixture evidence."""
    source = """\
import pytest
from subject import Manager

pytest = object()

@pytest.fixture
def manager():
    return Manager()

def test_request(manager):
    manager.request()
"""

    assert _slice(tmp_path, source) == ""


def test_static_class_helper_closure_excludes_sibling_tests_and_foreign_helpers(tmp_path: Path) -> None:
    """A selected method adds only its own referenced static helper."""
    source = """\
from subject import Manager
from unittest.mock import MagicMock

class TestSelected:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = status_code
        return response

    @staticmethod
    def _unused_response(status_code):
        response = MagicMock()
        response.status_code = 599
        return response

    def test_selected(self):
        response = self._response(403)
        Manager.request()
        assert response.status_code

    def test_sibling(self):
        response = self._response(503)
        assert response.status_code

class TestForeign:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = 503
        return response

    def test_foreign(self):
        response = self._response(503)
        assert response.status_code
"""

    sliced = _slice(tmp_path, source)

    assert "def _response" in sliced
    assert "status_code = 599" not in sliced
    assert "def test_sibling" not in sliced
    assert "class TestForeign" not in sliced
    assert "def test_foreign" not in sliced


def test_recursive_class_helper_closure_terminates_and_stays_in_owner(tmp_path: Path) -> None:
    """Recursive helper references add only same-class static definitions."""
    source = """\
from subject import Manager
from unittest.mock import MagicMock

class TestSelected:
    @staticmethod
    def _response(status_code):
        return TestSelected._response(status_code)

    @staticmethod
    def _other_response(status_code):
        response = MagicMock()
        response.status_code = status_code
        return response

    def test_selected(self):
        self._response(403)
        Manager.request()

class TestForeign:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = 503
        return response
"""

    sliced = _slice(tmp_path, source)

    assert sliced.count("def _response") == 1
    assert "def _other_response" not in sliced
    assert "class TestForeign" not in sliced


def test_detector_counts_fixture_and_class_helper_statuses_only(tmp_path: Path) -> None:
    """Fixture calls and their selected class helper definitions clear both HTTP findings."""
    source = """\
import pytest
from subject import Manager
from unittest.mock import MagicMock

@pytest.fixture
def manager():
    instance = Manager()
    return instance

class TestSelected:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = status_code
        return response

    def test_client_refusal(self, manager):
        response = self._response(403)
        manager.request()
        TestForeign._response(503)
        assert response.status_code

    def test_server_refusal(self, manager):
        response = self._response(503)
        manager.request()
        assert response.status_code

    def test_unselected_sibling(self):
        response = self._response(599)
        assert response.status_code

class TestForeign:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = 418
        return response

    def test_unrelated(self):
        response = self._response(418)
        assert response.status_code
"""
    test_path, tree, test_source = _case(tmp_path, source)

    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)

    assert findings == []


@pytest.mark.parametrize(
    ("class_rebinding", "receiver_rebinding"),
    [
        ("", "self = Fake()"),
        ("    helper = staticmethod(fake_helper)", ""),
        ("    helper = fake_helper", ""),
    ],
)
def test_rebound_class_helper_cannot_supply_server_failure(
    tmp_path: Path, class_rebinding: str, receiver_rebinding: str
) -> None:
    """Replaced receivers and static helpers cannot prove the declared helper's status."""
    source = f"""\
from subject import Manager

def fake_helper(*values):
    return {{"status_code": 200}}

class Fake:
    helper = staticmethod(fake_helper)

class TestSelected:
    @staticmethod
    def helper(status_code):
        return {{"status_code": status_code}}

{class_rebinding}
    def test_request(self):
        {receiver_rebinding or "pass"}
        response = self.helper(503)
        Manager.request(response)
        assert response["status_code"] == 200
"""
    test_path, tree, test_source = _case(tmp_path, source)
    sliced = SourceDrivenCoverageSlicer(test_path, tree).source_text()

    assert "def test_request" in sliced
    assert "def helper(status_code)" not in sliced
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    assert "missing_fm_http_5xx" in [finding.rule_id for finding in findings]


def test_detector_does_not_count_foreign_class_or_unselected_helper_status(tmp_path: Path) -> None:
    """Unselected class helpers cannot supply missing HTTP status evidence."""
    source = """\
import pytest
from subject import Manager
from unittest.mock import MagicMock

@pytest.fixture
def manager():
    return Manager()

class TestSelected:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = status_code
        return response

    def test_client_refusal(self, manager):
        response = self._response(403)
        manager.request()
        TestForeign._response(503)
        assert response.status_code

    def test_unselected_server_refusal(self):
        response = self._response(503)
        assert response.status_code

class TestForeign:
    @staticmethod
    def _response(status_code):
        response = MagicMock()
        response.status_code = 503
        return response

    def test_unrelated(self):
        response = self._response(503)
        assert response.status_code
"""
    test_path, tree, test_source = _case(tmp_path, source)

    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)

    assert [finding.rule_id for finding in findings] == ["missing_fm_http_5xx"]
