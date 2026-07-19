"""Bad fixture for MissingFailureModeDetector (T033).

README (bad fixture scenario for MissingFailureModeDetector):
    This module exercises an HTTP-style SUT (a requests call) but only
    covers the happy path. MissingFailureModeDetector must emit exactly
    one finding per uncovered failure mode from FR-006:

        - missing_fm_connection_timeout
        - missing_fm_connection_error
        - missing_fm_http_4xx
        - missing_fm_http_5xx
        - missing_fm_malformed_json
        - missing_fm_empty_body

Expected finding count: 6.
"""

from __future__ import annotations  # Postponed annotations for cleaner typing.

from unittest.mock import MagicMock  # Fake response used for the happy-path test.

import requests  # Imported so the detector recognizes this as an HTTP-testing module.


def call_api() -> dict:  # Trivial SUT that would issue a real HTTP call.
    """Return the JSON body of a GET request to a URL."""
    response = requests.get("https://example.com/api")  # nosec B113 -- fixture SUT; no real HTTP call in tests
    return response.json()  # Return the parsed JSON body.


def test_happy_path(monkeypatch) -> None:
    """Only the happy path is exercised -- no failure modes covered."""
    # Build a fake response object that mimics requests.Response for the happy case.
    fake = MagicMock()  # Fake response substitute.
    fake.json.return_value = {"ok": True}  # Configure the JSON body.
    fake.status_code = 200  # Configure the HTTP status.
    # Patch requests.get to return the fake, avoiding real network calls.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # SUT dependency injection.
    # Verify the SUT returns the expected happy-path payload.
    assert call_api() == {"ok": True}
