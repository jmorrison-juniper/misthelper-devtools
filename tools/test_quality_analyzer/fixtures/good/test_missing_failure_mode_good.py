"""Good fixture for MissingFailureModeDetector (T034).

README (good fixture scenario for MissingFailureModeDetector):
    This module exercises the same HTTP-style SUT as the bad fixture but
    additionally covers every FR-006 failure mode. MissingFailureModeDetector
    must NOT emit any finding for this file.

Failure modes covered here (via distinctive markers the detector recognizes):
    - connection_timeout: `requests.exceptions.Timeout`
    - connection_error:   `requests.exceptions.ConnectionError`
    - http_4xx:           `status_code = 404`
    - http_5xx:           `status_code = 500`
    - malformed_json:     `json.JSONDecodeError`
    - empty_body:         empty response body `b""`

Expected finding count: 0.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

import json  # For JSONDecodeError reference.
from unittest.mock import MagicMock  # Fake response used across tests.

import pytest  # For pytest.raises.
import requests  # Imported so the detector recognizes this as an HTTP module.


def call_api() -> dict:  # SUT wrapper around requests.
    """Return the JSON body of a GET request to a URL."""
    response = requests.get("https://example.com/api")  # nosec B113 -- fixture SUT; no real HTTP call in tests
    return response.json()  # Return parsed JSON body.


def _fake_response(status_code: int, body: bytes) -> MagicMock:
    """Build a fake response for status-code-based failure-mode tests."""
    fake = MagicMock()  # Fresh mock -- one per test.
    fake.status_code = status_code  # Emulate the HTTP status.
    fake.content = body  # Emulate raw response bytes.
    return fake  # Ready-to-use fake.


def test_happy_path(monkeypatch) -> None:
    """Happy-path baseline case."""
    fake = MagicMock()  # Fresh fake response.
    fake.json.return_value = {"ok": True}  # Happy JSON body.
    fake.status_code = 200  # 2xx status code.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Patch SUT dependency.
    assert call_api() == {"ok": True}  # Verify SUT return value.


def test_connection_timeout(monkeypatch) -> None:
    """Failure mode: requests.exceptions.Timeout on network read."""

    def _raise(_url):
        raise requests.exceptions.Timeout("read timed out")  # Simulate a Timeout exception.

    monkeypatch.setattr(requests, "get", _raise)  # Patch SUT to raise Timeout.
    with pytest.raises(requests.exceptions.Timeout):
        call_api()  # SUT must propagate the timeout.


def test_connection_error(monkeypatch) -> None:
    """Failure mode: requests.exceptions.ConnectionError on socket failure."""

    def _raise(_url):
        raise requests.exceptions.ConnectionError("dns failure")  # Simulate a ConnectionError.

    monkeypatch.setattr(requests, "get", _raise)  # Patch SUT to raise ConnectionError.
    with pytest.raises(requests.exceptions.ConnectionError):
        call_api()  # SUT must propagate the connection failure.


def test_http_4xx(monkeypatch) -> None:
    """Failure mode: HTTP 404 (4xx)."""
    fake = _fake_response(status_code=404, body=b"Not Found")  # 4xx response.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Patch SUT dependency.
    assert fake.status_code == 404  # Verify the failure-mode marker.


def test_http_5xx(monkeypatch) -> None:
    """Failure mode: HTTP 500 (5xx)."""
    fake = _fake_response(status_code=500, body=b"Server Error")  # 5xx response.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Patch SUT dependency.
    assert fake.status_code == 500  # Verify the failure-mode marker.


def test_malformed_json(monkeypatch) -> None:
    """Failure mode: response body cannot be parsed as JSON."""
    fake = MagicMock()  # Fresh fake response.
    fake.json.side_effect = json.JSONDecodeError("bad", "", 0)  # Simulate malformed JSON.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Patch SUT dependency.
    with pytest.raises(json.JSONDecodeError):
        call_api()  # SUT must propagate malformed JSON.


def test_empty_body(monkeypatch) -> None:
    """Failure mode: response body is empty bytes."""
    fake = _fake_response(status_code=200, body=b"")  # Empty body response.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Patch SUT dependency.
    assert fake.content == b""  # Verify the failure-mode marker (empty body).
