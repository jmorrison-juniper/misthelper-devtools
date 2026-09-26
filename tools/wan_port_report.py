"""Print a summary of the WAN ports on Mist gateways from a saved port list.

The input is a JSON list of Mist port statistic records, for example a dump of
the port cache of MistWANPerformance. Each record can hold ``device_type``,
``port_usage``, ``port_id``, ``up``, ``disabled``, and ``mac``. The report
gives the WAN ports on each gateway, the ports that are down or disabled, and a
count of each gateway port type.

This tool replaces ``check_ports.py`` in MistWANPerformance (commit ddbf849).
The report text is the same as the text of that script.

Usage::

    wan-port-report                      # Reads $TEMP/ports.json
    wan-port-report --input ports.json
"""

from __future__ import annotations  # Keep annotations stable across supported Python versions.

import argparse  # Parse the command-line options.
import json  # Decode the saved port list.
import logging  # Record each report step for operator diagnostics.
import os  # Read the TEMP directory for the default input path.
import sys  # Write the report to standard output.
from collections.abc import Mapping, Sequence  # Type the decoded port records.
from dataclasses import dataclass  # Store the report input in an explicit record.
from pathlib import Path  # Keep path handling portable across Windows and Linux.
from typing import Any, TextIO  # Type the decoded JSON and the output stream.

LOGGER = logging.getLogger(__name__)  # Use a module logger so callers control output and level.
DEFAULT_FILE_NAME = "ports.json"  # Match the file name that the earlier script read.

PortRecord = Mapping[str, Any]  # Name one decoded Mist port statistic record.


def default_input_path() -> Path:
    """Return the path that the earlier script read: ports.json in TEMP, or in /tmp."""
    return Path(os.environ.get("TEMP", "/tmp")) / DEFAULT_FILE_NAME  # nosec B108  # WHY: match the earlier script.


@dataclass(frozen=True)
class WanPortReport:
    """Build the WAN port report lines from a list of port records."""

    ports: Sequence[PortRecord]  # Store the decoded port records in input order.

    def gateway_ports(self) -> list[PortRecord]:
        """Return each port record of a gateway device."""
        return [port for port in self.ports if port.get("device_type") == "gateway"]  # Keep gateway records only.

    def lines(self) -> list[str]:
        """Return the report lines in the order that the earlier script printed them."""
        gateway_ports = self.gateway_ports()  # Read the gateway records one time.
        wan_ports = [port for port in gateway_ports if port.get("port_usage") == "wan"]  # Keep the WAN records.
        LOGGER.info("Read %d gateway port(s), %d WAN port(s)", len(gateway_ports), len(wan_ports))  # Log counts.
        lines = [f"Total WAN ports: {len(wan_ports)}", "", "WAN Port Status:"]  # Start with the WAN port list.
        lines.extend(
            f"  {port.get('port_id')}: up={port.get('up')}, disabled={port.get('disabled')}" for port in wan_ports
        )  # Give the state of each WAN port.
        gateway_macs = {port.get("mac") for port in gateway_ports}  # Count each gateway MAC one time.
        lines.extend(["", f"Gateway devices found: {len(gateway_macs)}"])  # Give the gateway count.
        down_ports = [port for port in wan_ports if not port.get("up", True)]  # A missing up field means up.
        disabled_ports = [port for port in wan_ports if port.get("disabled", False)]  # A missing field means enabled.
        lines.extend(["", f"WAN ports that are DOWN (up=False): {len(down_ports)}"])  # Give the down count.
        lines.extend(f"  {port.get('port_id')}: up={port.get('up')}" for port in down_ports)  # List each down port.
        lines.extend(["", f"WAN ports that are DISABLED (disabled=True): {len(disabled_ports)}"])  # Disabled count.
        lines.extend(f"  {port.get('port_id')}: disabled={port.get('disabled')}" for port in disabled_ports)  # List.
        lines.extend(["", "", "All gateway port types:"])  # Start the port type section.
        usage_counts = sorted(self._usage_counts(gateway_ports).items())  # Sort by the type and state text.
        lines.extend(f"  {usage}: {count}" for usage, count in usage_counts)  # Give each count on its own line.
        return lines  # Return every line for the caller to print.

    @staticmethod
    def _usage_counts(gateway_ports: Sequence[PortRecord]) -> dict[str, int]:
        """Return a count of each port type and state on the gateways."""
        counts: dict[str, int] = {}  # Collect one count for each type and state text.
        for port in gateway_ports:  # Visit each gateway port record.
            usage = port.get("port_usage", "NONE")  # A missing type shows as NONE, as in the earlier script.
            state = f"up={port.get('up', True)}, disabled={port.get('disabled', False)}"  # Missing means up, enabled.
            key = f"{usage} ({state})"  # Join the type and the state into one count key.
            counts[key] = counts.get(key, 0) + 1  # Add the port to the count for its type and state.
        return counts  # Return the counts for the sorted report section.


def load_ports(path: Path) -> list[PortRecord]:
    """Decode the saved port list from one JSON file."""
    LOGGER.info("Reading the port list from %s", path)  # Log before the read, so a wrong path is visible.
    with path.open(encoding="utf-8") as handle:  # The Mist API and the cache dump write UTF-8 JSON.
        ports = json.load(handle)  # Decode the whole list.
    if not isinstance(ports, list):  # The report needs a list of port records.
        raise ValueError(f"The port file must hold a JSON list of port records: {path}")
    if not all(isinstance(port, dict) for port in ports):  # Each record must be a JSON object.
        raise ValueError(f"Each item in the port file must be a JSON object: {path}")
    return ports  # Return the decoded records.


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the report."""
    parser = argparse.ArgumentParser(
        prog="wan-port-report",
        description="Print a summary of the WAN ports on Mist gateways from a saved port list.",
    )  # Name the command as the console script names it.
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=f"The JSON port list. The default is {DEFAULT_FILE_NAME} in the TEMP directory.",
    )  # Let a caller name a different dump file.
    parser.add_argument("-v", "--verbose", action="store_true", help="Log each report step.")  # Show the steps.
    return parser  # Return the parser to main and to the tests.


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    """Print the WAN port report and return the exit status."""
    arguments = build_parser().parse_args(argv)  # Read the options from the command line or the test.
    logging.basicConfig(
        level=logging.INFO if arguments.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )  # Keep the log on standard error, so standard output holds the report only.
    path = arguments.input if arguments.input is not None else default_input_path()  # Pick the input file.
    try:  # Report a bad input as one clear line and not as a traceback.
        ports = load_ports(path)  # Decode the port records.
    except (OSError, ValueError) as error:  # A JSON decode fault is a ValueError too.
        LOGGER.error("The port list could not be read: %s", error)  # Name the fault and the path.
        return 2  # Tell the shell that the report did not run.
    output = stdout if stdout is not None else sys.stdout  # Write to the caller stream in a test.
    for line in WanPortReport(ports).lines():  # Print each report line in order.
        print(line, file=output)  # Write the report to standard output, as the earlier script did.
    return 0  # The report is information only, so it always passes.


if __name__ == "__main__":  # Run the report only when the module is executed as a program.
    raise SystemExit(main())  # Return the report status to the shell.
