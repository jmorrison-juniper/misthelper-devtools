"""Unit tests for the WAN port report."""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import io  # Capture the report lines.
import json  # Write the sample port lists.
import os  # Build the environment for the parity run.
import subprocess  # nosec B404  # WHY: run the earlier script with the current interpreter only.
import sys  # Name the current interpreter for the parity run.
from pathlib import Path  # Write the sample files.
from typing import Any  # Type the sample port records.

import pytest  # Set the TEMP variable for one test.

from tools.wan_port_report import WanPortReport, default_input_path, main

# The script that MistWANPerformance held as check_ports.py (commit ddbf849).
# The parity test runs it and compares its output with the output of the tool.
EARLIER_SCRIPT = """
import json
import os
temp_path = os.path.join(os.environ.get('TEMP', '/tmp'), 'ports.json')
with open(temp_path, 'r') as f:
    ports = json.load(f)
wan_ports = [p for p in ports if p.get('device_type') == 'gateway' and p.get('port_usage') == 'wan']
print(f"Total WAN ports: {len(wan_ports)}")
print("\\nWAN Port Status:")
for port in wan_ports:
    print(f"  {port.get('port_id')}: up={port.get('up')}, disabled={port.get('disabled')}")
gateway_ports = [p for p in ports if p.get('device_type') == 'gateway']
gateway_macs = set(p.get('mac') for p in gateway_ports)
print(f"\\nGateway devices found: {len(gateway_macs)}")
down_ports = [p for p in wan_ports if not p.get('up', True)]
disabled_ports = [p for p in wan_ports if p.get('disabled', False)]
print(f"\\nWAN ports that are DOWN (up=False): {len(down_ports)}")
for p in down_ports:
    print(f"  {p.get('port_id')}: up={p.get('up')}")
print(f"\\nWAN ports that are DISABLED (disabled=True): {len(disabled_ports)}")
for p in disabled_ports:
    print(f"  {p.get('port_id')}: disabled={p.get('disabled')}")
print("\\n\\nAll gateway port types:")
gateway_usages = {}
for p in gateway_ports:
    usage = p.get('port_usage', 'NONE')
    up = p.get('up', True)
    disabled = p.get('disabled', False)
    key = f"{usage} (up={up}, disabled={disabled})"
    gateway_usages[key] = gateway_usages.get(key, 0) + 1
for usage, count in sorted(gateway_usages.items()):
    print(f"  {usage}: {count}")
"""


def sample_ports() -> list[dict[str, Any]]:
    """Return port records with each field form that the report reads."""
    return [
        {"device_type": "gateway", "port_usage": "wan", "port_id": "ge-0/0/0", "up": True, "mac": "aa"},
        {"device_type": "gateway", "port_usage": "wan", "port_id": "ge-0/0/1", "up": False, "mac": "aa"},
        {"device_type": "gateway", "port_usage": "wan", "port_id": "ge-0/0/2", "disabled": True, "mac": "bb"},
        {"device_type": "gateway", "port_usage": "lan", "port_id": "ge-0/0/3", "up": True, "mac": "bb"},
        {"device_type": "gateway", "port_id": "ge-0/0/4", "up": None, "mac": "cc"},  # No type and an unknown state.
        {"device_type": "gateway", "port_usage": None, "port_id": "ge-0/0/5"},  # A null type and no MAC.
        {"device_type": "switch", "port_usage": "wan", "port_id": "ge-0/0/6", "up": False, "mac": "dd"},
    ]  # Return the records in a fixed order.


def run_report(path: Path) -> tuple[int, str]:
    """Run the report main function on one file and return the status and the output."""
    output = io.StringIO()  # Capture the report lines.
    status = main(["--input", str(path)], stdout=output)  # Read the named file.
    return status, output.getvalue()  # Return both values for the assertions.


class TestWanPortReport:
    """Verify the report text, the default path, and the input checks."""

    def test_report_matches_the_earlier_script(self, tmp_path: Path) -> None:
        ports_file = tmp_path / "ports.json"  # Use the file name that the earlier script reads.
        ports_file.write_text(json.dumps(sample_ports()), encoding="utf-8")  # Save the sample records.
        environment = {**os.environ, "TEMP": str(tmp_path)}  # Point the earlier script at the sample file.
        earlier = subprocess.run(  # nosec B603  # WHY: a fixed script runs with the current interpreter.
            [sys.executable, "-c", EARLIER_SCRIPT],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=60,
        )  # Run the earlier script on the sample.
        status, output = run_report(ports_file)  # Run the tool on the same file.
        assert status == 0, f"The report must pass. Output: {output!r}"  # Prove the pass status.
        assert output == earlier.stdout, f"Output {output!r} != {earlier.stdout!r}"  # Prove the same text.

    def test_report_counts(self) -> None:
        lines = WanPortReport(sample_ports()).lines()  # Build the report lines in memory.
        assert lines[0] == "Total WAN ports: 3", lines  # The switch WAN port is not a gateway port.
        assert "Gateway devices found: 4" in lines, lines  # Three MACs and one missing MAC give four values.
        assert "WAN ports that are DOWN (up=False): 1" in lines, lines  # Only ge-0/0/1 reports up=False.
        assert "WAN ports that are DISABLED (disabled=True): 1" in lines, lines  # Only ge-0/0/2 is disabled.
        assert "  NONE (up=None, disabled=False): 1" in lines, lines  # A missing type shows as NONE.
        assert "  None (up=True, disabled=False): 1" in lines, lines  # A null type shows as None.

    def test_default_path_uses_temp(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("TEMP", str(tmp_path))  # Point TEMP at the test directory.
        assert default_input_path() == tmp_path / "ports.json", default_input_path()  # Prove the default path.
        monkeypatch.delenv("TEMP")  # Remove TEMP to reach the fallback.
        assert default_input_path() == Path("/tmp") / "ports.json", default_input_path()  # Prove the fallback.

    def test_missing_file_returns_status_two(self, tmp_path: Path) -> None:
        status, output = run_report(tmp_path / "absent.json")  # Name a file that does not exist.
        assert status == 2, f"A missing file must return status 2, not {status}"  # Prove the input fault status.
        assert output == "", f"A missing file must print no report: {output!r}"  # Prove no partial report.

    def test_object_file_returns_status_two(self, tmp_path: Path) -> None:
        ports_file = tmp_path / "ports.json"  # Name the input file.
        ports_file.write_text(json.dumps({"results": []}), encoding="utf-8")  # Save an object, not a list.
        status, output = run_report(ports_file)  # Run the report on the object.
        assert status == 2, f"An object must return status 2, not {status}. Output: {output!r}"  # Prove the check.

    def test_list_of_non_objects_returns_status_two(self, tmp_path: Path) -> None:
        ports_file = tmp_path / "ports.json"  # Name the input file.
        ports_file.write_text(json.dumps(["ge-0/0/0"]), encoding="utf-8")  # Save a list of strings.
        status, output = run_report(ports_file)  # Run the report on the strings.
        assert status == 2, f"A list of strings must return status 2, not {status}. Output: {output!r}"  # Prove it.
