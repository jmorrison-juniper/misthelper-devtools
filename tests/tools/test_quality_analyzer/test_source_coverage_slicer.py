"""Regression tests for source-driven failure coverage selection."""

from __future__ import annotations

import ast
from pathlib import Path

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
    (tmp_path / "pyproject.toml").write_text("[project]\\nname = 'fixture'\\n", encoding="utf-8")
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
