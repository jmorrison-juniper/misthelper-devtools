"""Bad fixture that proves a comment number does not cover HTTP 5xx."""

from __future__ import annotations  # PEP 563 postponed evaluation must not cover 5xx.

from unittest.mock import MagicMock  # Build a fake response without a network call.

import requests  # Imported so the detector recognizes HTTP status risk.


def call_api() -> dict:
    """Return the JSON body of a GET request to a URL."""
    response = requests.get("https://example.com/api")  # nosec B113 -- fixture only.
    return response.json()  # Return the parsed JSON body.


def test_happy_path(monkeypatch) -> None:
    """Only the happy path is exercised, so HTTP failure modes remain missing."""
    fake = MagicMock()  # Build a response double for the happy path.
    fake.json.return_value = {"ok": True}  # Configure the successful JSON body.
    fake.status_code = 200  # Configure a successful status only.
    monkeypatch.setattr(requests, "get", lambda url: fake)  # Avoid a real network call.
    assert call_api() == {"ok": True}  # Verify only the happy path.
