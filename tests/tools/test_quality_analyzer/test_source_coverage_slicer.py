"""Regression tests for source-driven failure coverage selection."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from misthelper_devtools.test_quality_analyzer.detection.missing_failure_mode import (
    FailureModeApplicabilityInferer,
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


@pytest.mark.parametrize(
    ("dependency", "expected_http"),
    [
        ("from mistapi.__logger import LogSanitizer\n        return LogSanitizer", False),
        ("import mistapi.__logger as sdk_logging\n        return sdk_logging", False),
        ("import mistapi.__logger\n        return mistapi.__logger.LogSanitizer", False),
        ("from mistapi import __logger\n        return __logger.LogSanitizer", False),
        ("from mistapi import __logger, api\n        return __logger.LogSanitizer", True),
        ("from mistapi import api, __logger\n        return api", True),
        ("from logging import Filter\n        return Filter", False),
        ("import requests\n        return requests.get('https://example.invalid')", True),
        ("from mistapi.api.v1.orgs.sites import listOrgSites\n        return listOrgSites()", True),
    ],
)
def test_sdk_logging_import_does_not_create_http_applicability(
    tmp_path: Path, dependency: str, expected_http: bool
) -> None:
    """An unrelated logging import cannot impose HTTP coverage on an offline class test."""
    test_source = """\
import pytest
from subject import Bootstrap

@pytest.fixture
def bootstrap(monkeypatch):
    monkeypatch.setattr(Bootstrap, "start", lambda self: "offline")
    return Bootstrap()

def test_offline_start(bootstrap):
    assert bootstrap.start() == "offline"
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        "class Bootstrap:\n    def start(self):\n        return 'started'\n\n"
        f"    def other_method(self):\n        {dependency}\n",
        encoding="utf-8",
    )
    risk = FailureModeApplicabilityInferer(test_path, tree).infer()
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)

    assert risk.http_status is expected_http
    http_rules = {finding.rule_id for finding in findings if finding.rule_id.startswith("missing_fm_http_")}
    assert http_rules == ({"missing_fm_http_4xx", "missing_fm_http_5xx"} if expected_http else set())


@pytest.mark.parametrize(
    ("imports", "expression", "connection_error", "timeout"),
    [
        ("import mistapi", "mistapi.APISession()", True, False),
        ("import mistapi as sdk", "sdk.APISession()", True, False),
        ("from mistapi import APISession", "APISession()", True, False),
        ("from mistapi import APISession as Session", "Session()", True, False),
        ("import mistapi", "mistapi.api.v1.self.self.getSelf(session)", False, False),
        ("from mistapi.__logger import LogSanitizer", "LogSanitizer()", False, False),
        ("from mistapi import APISession", "object()", False, False),
        ("import requests", "requests.get('https://example.invalid')", True, True),
    ],
)
def test_sdk_constructor_requires_connection_but_not_timeout_coverage(
    tmp_path: Path, imports: str, expression: str, connection_error: bool, timeout: bool
) -> None:
    """SDK construction can expose connection failures without exposing token-validation timeouts."""
    test_source = """\
from subject import Client

def test_success():
    Client().fetch()
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        f"{imports}\n\nclass Client:\n    def fetch(self):\n        return {expression}\n", encoding="utf-8"
    )
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    rules = {finding.rule_id for finding in findings}

    assert ("missing_fm_connection_error" in rules) is connection_error
    assert ("missing_fm_connection_timeout" in rules) is timeout


@pytest.mark.parametrize(
    ("module_source", "parameters", "body", "expected"),
    [
        ("import mistapi.api.v1.orgs.sites", "", "return mistapi.APISession()", True),
        ("import mistapi", ", mistapi", "return mistapi.APISession()", False),
        ("from mistapi import APISession as Session", ", Session", "return Session()", False),
        ("def unrelated():\n    from mistapi import APISession as Session", ", Session", "return Session()", False),
        ("def unrelated():\n    from mistapi import APISession as Session", "", "return Session()", False),
        ("", "", "from mistapi import APISession as Session\n        return Session()", True),
        ("", "", "import mistapi as sdk\n        return sdk.APISession()", True),
        ("import mistapi", "", "mistapi = object()\n        return mistapi.APISession()", False),
        ("from mistapi import APISession as Session\nSession = object", "", "return Session()", False),
        ("import mistapi.api.v1.orgs.sites as sites", ", mistapi", "return mistapi.APISession()", False),
        ("", "", "if True:\n            from mistapi import APISession as Session\n        return Session()", True),
        (
            "",
            "",
            "def unrelated():\n            from mistapi import APISession as Session\n        return Session()",
            False,
        ),
    ],
)
def test_sdk_constructor_binding_scope(
    tmp_path: Path, module_source: str, parameters: str, body: str, expected: bool
) -> None:
    """Only constructor imports visible in the calling scope can impose a connection obligation."""
    test_source = """\
from subject import Client

def test_success():
    Client().fetch()
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        f"{module_source}\n\nclass Client:\n    def fetch(self{parameters}):\n        {body}\n",
        encoding="utf-8",
    )
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    rules = {finding.rule_id for finding in findings}

    assert ("missing_fm_connection_error" in rules) is expected
    assert "missing_fm_connection_timeout" not in rules


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return (lambda: sdk.APISession())()", True),
        ("from mistapi import APISession as Session\n        return (lambda: Session())()", True),
        ("return (lambda session=sdk.APISession(): session)()", True),
        ("return (lambda sdk=sdk.APISession(): sdk)()", True),
        ("ignored = [sdk for sdk in ()]\n        return sdk.APISession()", True),
        ("return [sdk for sdk in (sdk.APISession(),)]", True),
        ("sdk = object()\n        import mistapi as sdk\n        return sdk.APISession()", True),
        ("return [sdk.APISession() for item in (1,)]", True),
        ("return (lambda sdk: sdk.APISession())(object())", False),
        ("return [sdk.APISession() for sdk in ()]", False),
        ("import mistapi as sdk\n        sdk = object()\n        return sdk.APISession()", False),
        (
            "sdk = object()\n        if enabled:\n            import mistapi as sdk\n        return sdk.APISession()",
            False,
        ),
    ],
)
def test_sdk_expression_scopes(tmp_path: Path, body: str, expected: bool) -> None:
    """Lambda and comprehension bindings must not replace enclosing SDK imports."""
    test_source = "from subject import Client\n\ndef test_client():\n    Client().make()\n"
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        f"import mistapi as sdk\n\nclass Client:\n    def make(self):\n        {body}\n", encoding="utf-8"
    )
    risk = FailureModeApplicabilityInferer(test_path, tree).infer()
    assert risk.sdk_connection_error is expected
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, test_source)}
    assert ("missing_fm_connection_error" in rules) is expected
    assert "missing_fm_connection_timeout" not in rules


@pytest.mark.parametrize(
    "import_text", ["from src import client", "from src import client as api", "import src.client as client"]
)
@pytest.mark.parametrize("shadow", ["", "parameter", "local", "module"])
def test_package_submodule_import_scope(tmp_path: Path, import_text: str, shadow: str) -> None:
    """Package-imported source modules retain risk only through unshadowed calls."""
    alias = "api" if " as api" in import_text else "client"
    source = (
        f"{import_text}\n"
        + (f"{alias} = object()\n" if shadow == "module" else "")
        + f"\ndef test_fetch({alias if shadow == 'parameter' else ''}):\n"
        + (f"    {alias} = object()\n" if shadow == "local" else "")
        + f"    {alias}.fetch()\n"
    )
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == (
        {"missing_fm_http_4xx", "missing_fm_http_5xx", "missing_fm_connection_error", "missing_fm_connection_timeout"}
        if not shadow
        else set()
    )


@pytest.mark.parametrize("calls_fetch", [True, False])
def test_package_import_does_not_infer_uncalled_http_symbol(tmp_path: Path, calls_fetch: bool) -> None:
    """Only the called symbol in a package submodule supplies applicability."""
    source = (
        "from src import client as api\n\ndef test_lookup():\n" f"    api.{'fetch' if calls_fetch else 'lookup'}()\n"
    )
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def lookup():\n    from mistapi.__logger import LogSanitizer\n    return LogSanitizer\n\n"
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n",
        encoding="utf-8",
    )
    risk = FailureModeApplicabilityInferer(test_path, tree).infer()
    assert risk.http_status is calls_fetch
    assert risk.network_exception is calls_fetch


@pytest.mark.parametrize(
    ("import_text", "alias"),
    [
        ("import src.client as client", "client"),
        ("from src import client", "client"),
        ("from src import client as api", "api"),
    ],
)
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return (lambda: client.fetch())()", True),
        ("return [client.fetch() for _ in range(1)]", True),
        ("unrelated = [client for client in (1,)]\n    return client.fetch()", True),
        ("return (lambda client: client.fetch())(None)", False),
        ("return [client.fetch() for client in ()]", False),
        ("return (lambda client=client.fetch(): client)()", True),
        ("return [client for client in (client.fetch(),)]", True),
        ("return [item for client in () for item in client.fetch()]", False),
    ],
)
def test_module_alias_expression_scope(tmp_path: Path, import_text: str, alias: str, body: str, expected: bool) -> None:
    """Module aliases remain visible in expressions unless expression parameters or targets replace them."""
    source = f"{import_text}\n\ndef test_fetch():\n    {body.replace('client', alias)}\n"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == (
        {"missing_fm_http_4xx", "missing_fm_http_5xx", "missing_fm_connection_error", "missing_fm_connection_timeout"}
        if expected
        else set()
    )


@pytest.mark.parametrize(
    ("import_text", "alias"),
    [
        ("import src.client as client", "client"),
        ("from src import client", "client"),
        ("from src import client as api", "api"),
    ],
)
@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ("def test_fetch(result=client.fetch()):\n    return result\n", True),
        ("def test_fetch(*, result=client.fetch()):\n    return result\n", True),
        ("@client.fetch()\ndef test_fetch():\n    return 1\n", True),
        ("def test_fetch():\n    @client.fetch()\n    def inner():\n        return 1\n", True),
        ("def test_fetch(client=client.fetch()):\n    return client\n", True),
        (
            "def test_fetch():\n    def inner(result=client.fetch()):\n"
            "        client = object()\n        return result\n",
            True,
        ),
        ("def test_fetch(client):\n    def inner(result=client.fetch()):\n        return result\n", False),
        (
            "def test_fetch():\n    client = object()\n    @client.fetch()\n    def inner():\n        return 1\n",
            False,
        ),
    ],
)
def test_module_alias_definition_scope(
    tmp_path: Path, import_text: str, alias: str, definition: str, expected: bool
) -> None:
    """Defaults and decorators use enclosing bindings, not the bindings inside their defined function."""
    source = f"{import_text}\n\n{definition.replace('client', alias)}"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == (
        {"missing_fm_http_4xx", "missing_fm_http_5xx", "missing_fm_connection_error", "missing_fm_connection_timeout"}
        if expected
        else set()
    )


@pytest.mark.parametrize(
    ("import_text", "alias"),
    [
        ("import src.client as client", "client"),
        ("from src import client", "client"),
        ("from src import client as api", "api"),
    ],
)
@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ("@client.fetch()\n    def test_fetch(self):\n        return 1\n", True),
        ("def test_fetch(self, result=client.fetch()):\n        return result\n", True),
        ("def test_fetch(self, *, result=client.fetch()):\n        return result\n", True),
        ("def test_fetch(self, client=client.fetch()):\n        return client\n", True),
        (
            "def test_fetch(self, result=client.fetch()):\n        client = object()\n        return result\n",
            True,
        ),
        (
            "def test_fetch(self, result=client.fetch()):\n        return result\n    client = object()\n",
            True,
        ),
        ("client = object()\n    @client.fetch()\n    def test_fetch(self):\n        return 1\n", False),
        (
            "client = object()\n    def test_fetch(self, result=client.fetch()):\n        return result\n",
            False,
        ),
    ],
)
def test_module_alias_method_definition_scope(
    tmp_path: Path, import_text: str, alias: str, definition: str, expected: bool
) -> None:
    """Method defaults and decorators read the class bindings that exist before the method definition."""
    source = f"{import_text}\n\nclass TestClient:\n    {definition.replace('client', alias)}"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == (
        {"missing_fm_http_4xx", "missing_fm_http_5xx", "missing_fm_connection_error", "missing_fm_connection_timeout"}
        if expected
        else set()
    )


@pytest.mark.parametrize(
    "definition",
    [
        "@client.fetch()\nclass TestClient:\n    pass\n",
        "class TestClient(client.fetch()):\n    pass\n",
        "class TestClient(metaclass=client.fetch()):\n    pass\n",
    ],
)
def test_module_alias_class_header_scope(tmp_path: Path, definition: str) -> None:
    """Class decorators, bases, and keywords read the enclosing module alias."""
    source = f"import src.client as client\n\n{definition}"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "def fetch():\n    import requests\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == {
        "missing_fm_http_4xx",
        "missing_fm_http_5xx",
        "missing_fm_connection_error",
        "missing_fm_connection_timeout",
    }


@pytest.mark.parametrize("alias", ["Client", "Service"])
@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("{alias}.classify('ap')", False),
        ("{alias}.fetch()", True),
        ("{alias}.forward()", True),
        ("{alias}().classify('ap')", True),
    ],
)
def test_static_class_risk_uses_called_methods(tmp_path: Path, alias: str, call: str, expected: bool) -> None:
    """Explicit static calls exclude unrelated methods without reducing constructed-class risk."""
    source = f"from src.client import Client as {alias}\n\ndef test_client():\n    {call.format(alias=alias)}\n"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "class Client:\n"
        "    @staticmethod\n    def classify(value):\n        return value\n\n"
        "    @staticmethod\n    def fetch():\n"
        "        import requests\n        return requests.get('https://example.invalid')\n\n"
        "    @staticmethod\n    def forward():\n        return Client.fetch()\n",
        encoding="utf-8",
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert rules == (
        {"missing_fm_http_4xx", "missing_fm_http_5xx", "missing_fm_connection_error", "missing_fm_connection_timeout"}
        if expected
        else set()
    )


@pytest.mark.parametrize(
    "class_source",
    [
        "class Client:\n    @classmethod\n    def classify(cls, value):\n        return value\n",
        "class Client(Base):\n    @staticmethod\n    def classify(value):\n        return value\n",
        "class Client:\n    @staticmethod\n    def classify(value):\n        return value\n"
        "    @staticmethod\n    def other(value=make_default()):\n        return value\n",
        "class Client:\n    @staticmethod\n    def classify(value):\n        return Client.unknown(value)\n",
        "class Client:\n    @staticmethod\n    def classify(value):\n        return value\n"
        "    @custom_decorator\n    def other(self):\n        return 1\n",
    ],
)
def test_uncertain_class_dispatch_retains_http_risk(tmp_path: Path, class_source: str) -> None:
    """Unsupported dispatch and definition side effects must not lose existing HTTP requirements."""
    source = "from src.client import Client\n\ndef test_client():\n    Client.classify('ap')\n"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        class_source + "    def fetch(self):\n        import requests\n"
        "        return requests.get('https://example.invalid')\n",
        encoding="utf-8",
    )
    risk = FailureModeApplicabilityInferer(test_path, tree).infer()
    assert risk.http_status
    assert risk.network_exception


def test_constructed_class_keeps_sdk_connection_obligation(tmp_path: Path) -> None:
    """A pure method does not remove risk from its class constructor."""
    source = "from src.client import Client\n\ndef test_client():\n    Client().classify('ap')\n"
    test_path, tree, _ = _case(tmp_path, source)
    (tmp_path / "src/client.py").write_text(
        "import mistapi\n\nclass Client:\n    def __init__(self):\n        self.session = mistapi.APISession()\n"
        "    @staticmethod\n    def classify(value):\n        return value\n",
        encoding="utf-8",
    )
    rules = {item.rule_id for item in MissingFailureModeDetector().detect(test_path, tree, source)}
    assert "missing_fm_connection_error" in rules
    assert "missing_fm_connection_timeout" not in rules


def test_sdk_constructor_connection_coverage_clears_only_its_obligation(tmp_path: Path) -> None:
    """A selected constructor failure test satisfies connection coverage without requiring a timeout."""
    test_source = """\
from subject import Client
import pytest

def test_connection_failure():
    with pytest.raises(ConnectionError):
        Client().fetch()
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        "import mistapi\n\nclass Client:\n    def fetch(self):\n        return mistapi.APISession()\n",
        encoding="utf-8",
    )
    findings = MissingFailureModeDetector().detect(test_path, tree, test_source)
    rules = {finding.rule_id for finding in findings}

    assert "missing_fm_connection_error" not in rules
    assert "missing_fm_connection_timeout" not in rules
    assert rules == {"missing_fm_http_4xx", "missing_fm_http_5xx"}


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


@pytest.mark.parametrize("relative", [True, False])
@pytest.mark.parametrize(("status_code", "missing_rule"), [(403, "missing_fm_http_4xx"), (503, "missing_fm_http_5xx")])
def test_nested_project_fake_response_does_not_count_as_source_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool, status_code: int, missing_rule: str
) -> None:
    """A fake response cannot cover an imported source call for either path form."""
    test_source = f"""\
from subject import fetch

class FakeResponse:
    status_code = {status_code}

def test_fake_response():
    response = FakeResponse()
    assert response.status_code == {status_code}
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        "import requests\n\ndef fetch():\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path.parent)
    analyzed_path = test_path.relative_to(tmp_path.parent) if relative else test_path

    findings = MissingFailureModeDetector().detect(analyzed_path, tree, test_source)
    sliced = SourceDrivenCoverageSlicer(analyzed_path, tree).source_text()

    assert sliced == ""
    assert missing_rule in [finding.rule_id for finding in findings]


@pytest.mark.parametrize("relative", [True, False])
def test_nested_project_direct_source_call_is_selected_for_both_path_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    """A real imported call remains source-driven for both path forms."""
    test_source = """\
from subject import fetch

def test_source_request():
    fetch()
"""
    test_path, tree, _ = _case(tmp_path, test_source)
    (tmp_path / "subject.py").write_text(
        "import requests\n\ndef fetch():\n    return requests.get('https://example.invalid')\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path.parent)
    analyzed_path = test_path.relative_to(tmp_path.parent) if relative else test_path

    assert "def test_source_request" in SourceDrivenCoverageSlicer(analyzed_path, tree).source_text()


@pytest.mark.parametrize("relative", [True, False])
def test_analyzer_fixture_paths_keep_local_source_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    """Local source in analyzer fixtures remains allowed outside a tests directory."""
    fixture_path = tmp_path / "src/misthelper_devtools/test_quality_analyzer/fixtures/good/test_local.py"
    fixture_path.parent.mkdir(parents=True)
    fixture_source = """\
import requests

def local_subject():
    return requests.get("https://example.invalid")

def test_local():
    local_subject()
"""
    fixture_path.write_text(fixture_source, encoding="utf-8")
    tree = ast.parse(fixture_source)
    monkeypatch.chdir(tmp_path.parent)
    analyzed_path = fixture_path.relative_to(tmp_path.parent) if relative else fixture_path

    applicability = FailureModeApplicabilityInferer(analyzed_path, tree)
    slicer = SourceDrivenCoverageSlicer(analyzed_path, tree)

    assert applicability._allows_local_source()
    assert "def test_local" in slicer.source_text()


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
