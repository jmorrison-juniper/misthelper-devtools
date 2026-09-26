"""Good fixture for issue #2963 source-driven failure-mode coverage."""

from __future__ import annotations  # Keep annotation evaluation postponed.

from types import SimpleNamespace  # Build response-shaped objects for the fixture.
from typing import Any  # Keep the fixture session type simple.

import mistapi  # Imported so the detector recognizes HTTP status risk without exception risk.
import pytest  # Parametrize the two HTTP status families.


def call_api(session: Any) -> Any:
    """Return a response object from a GET request to a URL."""
    return mistapi.api.v1.orgs.stats.getOrgStats(session, "org-id")  # Return the Mist API response object.


@pytest.mark.parametrize("status_code", [404, 503], ids=["client-error", "server-error"])
def test_http_status_observation_calls_source(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    """HTTP status coverage must call the source function under test."""
    response = SimpleNamespace(status_code=status_code, data={"detail": "failure"})  # Real response-shaped object.
    monkeypatch.setattr(  # Route the source call to the response double.
        mistapi.api.v1.orgs.stats,
        "getOrgStats",
        lambda _session, _org_id: response,
    )
    result = call_api(object())  # Drive the source function under test.
    assert result.status_code == status_code  # Prove the observed status came through the source call.
