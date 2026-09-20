"""Bad fixture for issue #2963 pasted failure-mode helper coverage."""

from __future__ import annotations  # Keep annotation evaluation postponed.

import requests  # Imported so the detector recognizes HTTP status risk.


def call_api() -> dict:
    """Return the JSON body of a GET request to a URL."""
    response = requests.get("https://example.com/api")  # nosec B113 -- fixture only.
    return response.json()  # Return the parsed JSON body.


def test_observable_failure_mode_contracts() -> None:
    from tests.support import failure_mode_observations as failure_modes

    failure_modes.assert_http_status_observation(400)
