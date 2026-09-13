"""Measure the retained memory of the performance recorder.

Why:
    Operators need measured memory values, not structural claims. This module
    keeps traced Python memory separate from process memory.
"""

from __future__ import annotations  # Keep annotations lazy for the command path.

import ctypes  # Read Windows process memory without a new dependency.
import gc  # Collect released objects before each measured memory run.
import json  # Write a machine-readable report for the specification.
import logging  # Report each action before and after it runs.
import platform  # Record the measured platform in the report.
import sys  # Report the Python executable and version.
import tracemalloc  # Measure traced Python memory for live allocations.
from collections.abc import Callable  # Type the scenario callbacks.
from dataclasses import dataclass  # Keep process memory fields explicit.
from pathlib import Path  # Read pyproject with platform-safe paths.
from typing import Any, Final  # Mark constants and JSON-like values.

from src.utils.performance import privacy  # Measure the bounded safe value cache.
from src.utils.performance.event import (  # Import event helpers for event and cache measurements.
    EventSource,  # Build source records for retained events.
    PerformanceEvent,  # Build event records for retained queue memory.
    _dimension_key_ok,  # Measure the bounded label-key validator cache.
    _measurement_key_ok,  # Measure the bounded measurement-key validator cache.
)
from src.utils.performance.recorder import Recorder, RecorderSettings  # Measure the enabled and disabled span paths.
from src.utils.performance.sink import DEFAULT_MAX_BYTES, BoundedSink  # Measure the retained event queue.

_LOGGER = logging.getLogger(__name__)  # Share one logger for this measurement command.
_SOURCE: Final = EventSource("tools/performance_memory.py", "run", "PerformanceMemoryHarness")  # Reuse one source.


@dataclass(frozen=True, slots=True)
class ProcessMemory:
    """Hold one process memory sample.

    Why:
        A separate record prevents traced Python bytes from becoming RSS.
    """

    available: bool  # State whether the platform returned process memory.
    method: str  # Name the operating system method used for the sample.
    working_set_bytes: int | None  # Hold resident pages when the platform reports them.
    private_bytes: int | None  # Hold private committed bytes when the platform reports them.

    def to_dict(self) -> dict[str, int | str | bool | None]:
        """Return this sample as a JSON-ready record."""
        return {  # Keep all process memory fields together in output.
            "available": self.available,  # State whether the metric exists.
            "method": self.method,  # State the platform method.
            "working_set_bytes": self.working_set_bytes,  # Report resident process bytes.
            "private_bytes": self.private_bytes,  # Report private committed process bytes.
        }


@dataclass(frozen=True, slots=True)
class ScenarioMeasurement:
    """Hold one traced and process memory measurement.

    Why:
        One record keeps helper parameter counts under the project limit.
    """

    current: int  # Store live traced Python bytes after the scenario.
    peak: int  # Store peak traced Python bytes during the scenario.
    before: ProcessMemory  # Store process memory before the traced interval.
    after: ProcessMemory  # Store process memory after the traced interval.
    top_sites: list[dict[str, Any]]  # Store the largest live traced sites.


class ProcessMemoryReader:
    """Read process memory with the standard library only.

    Why:
        The project does not declare psutil, and this task must not add it.
    """

    def read(self) -> ProcessMemory:
        """Return the process memory sample for this platform."""
        _LOGGER.info("Reading process memory")  # Log before the platform call.
        if platform.system() == "Windows":  # Use the Windows API only on Windows.
            sample = self._read_windows()  # Read working set and private bytes.
            _LOGGER.debug("Read Windows process memory: %s", sample)  # Log the sample shape.
            return sample  # Return the Windows sample to the caller.
        sample = ProcessMemory(False, "unavailable", None, None)  # Report no portable standard API.
        _LOGGER.debug("Process memory is unavailable on this platform")  # Log the missing metric.
        return sample  # Return an explicit unavailable sample.

    def _read_windows(self) -> ProcessMemory:
        """Return Windows memory counters for the current process."""

        class ProcessMemoryCountersEx(ctypes.Structure):
            """Map the Windows PROCESS_MEMORY_COUNTERS_EX structure."""

            _fields_ = [  # Match the Windows structure layout for GetProcessMemoryInfo.
                ("cb", ctypes.c_ulong),  # Store the structure size in bytes.
                ("PageFaultCount", ctypes.c_ulong),  # Keep the documented field order.
                ("PeakWorkingSetSize", ctypes.c_size_t),  # Keep the documented field order.
                ("WorkingSetSize", ctypes.c_size_t),  # Store resident pages for this process.
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("QuotaPagedPoolUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("PagefileUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("PeakPagefileUsage", ctypes.c_size_t),  # Keep the documented field order.
                ("PrivateUsage", ctypes.c_size_t),  # Store private committed bytes.
            ]

        counters = ProcessMemoryCountersEx()  # Allocate the output structure for Windows.
        counters.cb = ctypes.sizeof(ProcessMemoryCountersEx)  # Tell Windows the structure size.
        kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)  # Load Kernel32 with error support.
        psapi = ctypes.WinDLL("Psapi.dll", use_last_error=True)  # Load Psapi with error support.
        psapi.GetProcessMemoryInfo.argtypes = [  # Declare arguments so ctypes passes the pointers correctly.
            ctypes.c_void_p,  # Pass the process handle from Kernel32.
            ctypes.POINTER(ProcessMemoryCountersEx),  # Pass the output structure pointer.
            ctypes.c_ulong,  # Pass the structure size as a DWORD.
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int  # Read the Win32 success flag as an integer.
        handle = kernel.GetCurrentProcess()  # Get the pseudo-handle for this process.
        status = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)  # Read counters.
        if not status:  # Report a clear unavailable metric if the API fails.
            return ProcessMemory(False, "Windows GetProcessMemoryInfo failed", None, None)  # Keep metrics separate.
        return ProcessMemory(True, "Windows GetProcessMemoryInfo", counters.WorkingSetSize, counters.PrivateUsage)


class EventFactory:
    """Create minimal and worst case performance events.

    Why:
        The same factory feeds one event, a full queue, and a sustained run.
    """

    def minimal(self, index: int) -> PerformanceEvent:
        """Return one minimal queued event."""
        return PerformanceEvent(  # Build the smallest valid event that the queue retains.
            event_type="operation",  # Use an allowed family from the contract.
            monitor_type="memory_probe",  # Use a stable monitor name for the harness.
            source=_SOURCE,  # Reuse one source to isolate event object cost.
            status="ok",  # Use the common success path for retained events.
            measurements={"wall_ns": float(index + 1)},  # Keep one positive required measurement.
            dimensions={},  # Keep labels empty for the minimum retained graph.
        )

    def worst(self, index: int) -> PerformanceEvent:
        """Return one largest contract-valid queued event."""
        dimensions = self._worst_dimensions(index)  # Build all sixteen bounded labels.
        measurements = self._worst_measurements(index)  # Build all thirty-two measurements.
        return PerformanceEvent(  # Build the largest valid event shape the contract allows.
            event_type="operation",  # Use an allowed family from the contract.
            monitor_type="memory_probe",  # Use a stable monitor name for the harness.
            source=_SOURCE,  # Reuse one source to isolate event object cost.
            status="ok",  # Use the common success path for retained events.
            measurements=measurements,  # Retain the full measurement dictionary.
            dimensions=dimensions,  # Retain the full label dictionary.
        )

    def _worst_dimensions(self, index: int) -> dict[str, str]:
        """Return sixteen labels that use the contract value length."""
        return {  # Build distinct strings so the retained value bytes are real.
            f"label{number}": f"{index:08d}_{number:02d}_" + ("x" * 84) for number in range(16)
        }

    def _worst_measurements(self, index: int) -> dict[str, float]:
        """Return thirty-two bounded measurements."""
        return {  # Build all allowed measurement slots for the contract bound.
            f"metric{number}.bytes": float(index + number + 1) for number in range(32)
        }


class PerformanceMemoryHarness:
    """Measure all memory scenarios for the performance recorder.

    Why:
        One class keeps the actions, measurements, and output together.
    """

    def __init__(
        self, capacity: int = 2048, max_bytes: int = DEFAULT_MAX_BYTES, sustained_events: int = 50_000
    ) -> None:
        """Store the scenario sizes and the process memory reader."""
        self.capacity = capacity  # Store the queue size under test.
        self.max_bytes = max_bytes  # Store the approximate byte budget under test.
        self.sustained_events = sustained_events  # Store the sustained event count.
        self.factory = EventFactory()  # Share one factory for all event scenarios.
        self.process_reader = ProcessMemoryReader()  # Use standard-library process memory.

    def run(self) -> dict[str, Any]:
        """Return the complete memory report."""
        _LOGGER.info("Starting performance memory measurements")  # Log the start of the report.
        results = [self._measure(name, action) for name, action in self._scenarios()]  # Measure each scenario.
        report = self._report(results)  # Build a stable JSON-like result.
        _LOGGER.debug("Completed %s memory measurements", len(results))  # Log the scenario count.
        return report  # Return the measured report.

    def _scenarios(self) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
        """Return the scenario list in report order."""
        return [  # Keep the order stable for the report table.
            ("disabled_span_level_off", self._disabled_span),  # Prove the off path retains no event.
            ("one_minimal_event", self._one_minimal_event),  # Measure one minimal queued event.
            ("one_worst_case_event", self._one_worst_event),  # Measure one full contract event.
            ("full_queue_minimal_events", self._full_minimal_queue),  # Measure a full minimal queue.
            ("full_queue_worst_case_events", self._full_worst_queue),  # Measure a full worst case queue.
            ("byte_bounded_worst_case_events", self._byte_bounded_worst_queue),  # Measure byte retention.
            ("sustained_minimal_events", self._sustained_minimal_events),  # Measure the plateau.
            ("bounded_caches", self._bounded_caches),  # Measure the validator and safe value caches.
        ]

    def _measure(self, name: str, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Measure one action with tracemalloc and process memory."""
        gc.collect()  # Reduce noise from objects released by the prior scenario.
        before = self.process_reader.read()  # Read process memory before tracing starts.
        _LOGGER.info("Running memory scenario %s", name)  # Log before traced memory starts.
        tracemalloc.start(25)  # Start traced Python memory for this one scenario.
        metadata = action()  # Retain scenario objects through metadata until the snapshot.
        current, peak = tracemalloc.get_traced_memory()  # Read traced current and peak bytes.
        snapshot = tracemalloc.take_snapshot()  # Capture live allocation sites for this scenario.
        top_sites = self._top_sites(snapshot)  # Convert top allocation sites to plain records.
        tracemalloc.stop()  # Stop tracing before any future timing run could execute.
        after = self.process_reader.read()  # Read process memory as a separate metric.
        _LOGGER.debug("Scenario %s current=%s peak=%s", name, current, peak)  # Log traced totals.
        measurement = ScenarioMeasurement(current, peak, before, after, top_sites)  # Group metrics for output.
        return self._result(name, metadata, measurement)  # Build one row with a small parameter list.

    def _result(self, name: str, metadata: dict[str, Any], measurement: ScenarioMeasurement) -> dict[str, Any]:
        """Build one result row."""
        result = {key: value for key, value in metadata.items() if key != "retained"}  # Drop live objects from JSON.
        result.update(  # Add traced and process memory fields with clear names.
            {
                "scenario": name,  # Name the scenario for the table.
                "traced_current_bytes": measurement.current,  # Report live traced Python bytes.
                "traced_peak_bytes": measurement.peak,  # Report peak traced Python bytes.
                "process_memory_before": measurement.before.to_dict(),  # Report process memory separately.
                "process_memory_after": measurement.after.to_dict(),  # Report process memory separately.
                "top_allocation_sites": measurement.top_sites,  # Report the largest traced live sites.
            }
        )
        return result  # Return the complete row.

    def _top_sites(self, snapshot: tracemalloc.Snapshot) -> list[dict[str, Any]]:
        """Return the five largest live allocation sites."""
        sites = []  # Collect plain dictionaries for JSON output.
        for stat in snapshot.statistics("lineno")[:5]:  # Keep only the five largest sites.
            frame = stat.traceback[0]  # Use the top frame as the site label.
            sites.append({"file": frame.filename, "line": frame.lineno, "bytes": stat.size})  # Store the site.
        return sites  # Return a stable JSON-ready list.

    def _disabled_span(self) -> dict[str, Any]:
        """Return retained objects for a disabled span."""
        recorder = Recorder(RecorderSettings(level="off"))  # Create the recorder in the default off state.
        with recorder.span(_SOURCE):  # Exercise the null span path that should retain no event.
            pass  # Keep the measured operation empty to isolate the disabled path.
        return {"queued_events": len(recorder.sink.drain()), "retained": recorder}  # Keep recorder live.

    def _one_minimal_event(self) -> dict[str, Any]:
        """Return retained objects for one enabled minimal span."""
        recorder = Recorder(RecorderSettings(level="base", measure_cpu=False))  # Enable one allowed family.
        with recorder.span(_SOURCE, family="operation") as span:  # Create one enabled span.
            span.count("items", 1.0)  # Add one caller measurement beside wall and CPU counters.
        return {"queued_events": 1, "per_event_bytes_basis": "one span", "retained": recorder}  # Keep event live.

    def _one_worst_event(self) -> dict[str, Any]:
        """Return retained objects for one worst case event."""
        sink = BoundedSink(capacity=1)  # Use one slot so the row is one event only.
        sink.emit(self.factory.worst(0))  # Queue the full contract event for retention.
        return {"queued_events": 1, "per_event_bytes_basis": "one event", "retained": sink}  # Keep event live.

    def _full_minimal_queue(self) -> dict[str, Any]:
        """Return retained objects for a full minimal queue."""
        sink = BoundedSink(capacity=self.capacity, max_bytes=self.max_bytes)  # Use both configured bounds.
        for index in range(self.capacity):  # Fill each slot exactly once.
            sink.emit(self.factory.minimal(index))  # Add a distinct minimal event.
        return self._sink_metadata(sink)  # Report the retained queue and keep it live.

    def _full_worst_queue(self) -> dict[str, Any]:
        """Return retained objects for a full worst case queue."""
        sink = BoundedSink(capacity=self.capacity, max_bytes=self.max_bytes)  # Use both configured bounds.
        for index in range(self.capacity):  # Fill each slot exactly once.
            sink.emit(self.factory.worst(index))  # Add a distinct worst case event.
        return self._sink_metadata(sink)  # Report the retained queue and keep it live.

    def _byte_bounded_worst_queue(self) -> dict[str, Any]:
        """Return retained worst case events after byte pressure."""
        sink = BoundedSink(capacity=self.capacity, max_bytes=self.max_bytes)  # Use the byte budget under test.
        for index in range(self.capacity * 3):  # Emit enough large events to force byte evictions.
            sink.emit(self.factory.worst(index))  # Add the largest legal event shape.
        metadata = self._sink_metadata(sink)  # Report the retained count and byte charge.
        metadata["emitted_events"] = self.capacity * 3  # State the input count for the report.
        return metadata  # Keep the bounded sink live for the traced snapshot.

    def _sustained_minimal_events(self) -> dict[str, Any]:
        """Return retained objects after many more events than capacity."""
        sink = BoundedSink(capacity=self.capacity, max_bytes=self.max_bytes)  # Use both configured bounds.
        for index in range(self.sustained_events):  # Emit far more events than the queue can hold.
            sink.emit(self.factory.minimal(index))  # Add distinct events so eviction must hold the bound.
        expected_dropped = max(0, self.sustained_events - self.capacity)  # Calculate the entry-only drop count.
        return {  # Report the sustained queue state and keep the sink alive.
            "queued_events": sink.queued_count,  # Report the retained event count without clearing it.
            "expected_dropped": expected_dropped,  # Report the expected number of evicted events.
            "actual_dropped": sink.dropped,  # Report the sink counter for evicted events.
            "retained": sink,  # Keep the queue live until tracemalloc takes the snapshot.
        }

    def _sink_metadata(self, sink: BoundedSink) -> dict[str, Any]:
        """Return queue metadata and keep the sink live."""
        queued_events = sink.queued_count  # Count the queue without clearing retained events.
        return {  # Report the queue state with its approximate byte charge.
            "queued_events": queued_events,  # Report the retained event count.
            "dropped_events": sink.dropped,  # Report all evicted or refused events.
            "estimated_queued_bytes": sink.queued_bytes,  # Report the cheap byte estimate.
            "configured_max_bytes": sink.max_bytes,  # Report the configured byte budget.
            "retained": sink,  # Keep the queue object live until tracemalloc takes the snapshot.
        }

    def _bounded_caches(self) -> dict[str, Any]:
        """Return retained objects for the bounded caches."""
        privacy._SAFE_VALUE_CACHE.clear()  # Reset the safe value cache before this scenario.
        _dimension_key_ok.cache_clear()  # Reset the label-key validator cache.
        _measurement_key_ok.cache_clear()  # Reset the measurement-key validator cache.
        for index in range(privacy._SAFE_VALUE_CACHE_LIMIT + 1):  # Cross the clear-at-limit boundary.
            privacy.scrub_value(f"safe_value_{index}")  # Add one safe value per step.
        for index in range(300):  # Cross the 256-entry label-key cache limit.
            _dimension_key_ok(f"label{index}")  # Fill the label-key validator cache.
        for index in range(600):  # Cross the 512-entry measurement-key cache limit.
            _measurement_key_ok(f"metric{index}")  # Fill the measurement-key validator cache.
        return self._cache_metadata()  # Report cache sizes after the fills.

    def _cache_metadata(self) -> dict[str, Any]:
        """Return the measured cache bounds."""
        dimension_info = _dimension_key_ok.cache_info()  # Read the label-key cache state.
        measurement_info = _measurement_key_ok.cache_info()  # Read the measurement-key cache state.
        return {  # Report cache state and retain modules through active caches.
            "safe_value_cache_entries": len(privacy._SAFE_VALUE_CACHE),  # Show the clear-at-limit result.
            "safe_value_cache_limit": privacy._SAFE_VALUE_CACHE_LIMIT,  # Show the documented limit.
            "dimension_cache_entries": dimension_info.currsize,  # Show the bounded label-key cache size.
            "dimension_cache_limit": dimension_info.maxsize,  # Show the label-key cache limit.
            "measurement_cache_entries": measurement_info.currsize,  # Show the bounded measurement cache size.
            "measurement_cache_limit": measurement_info.maxsize,  # Show the measurement cache limit.
        }

    def _report(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the full report with measurement notes."""
        return {  # Keep report fields stable for documentation updates.
            "platform": platform.platform(),  # State the measured operating system.
            "python": sys.version,  # State the measured Python build.
            "python_executable": sys.executable,  # State the interpreter path.
            "capacity": self.capacity,  # State the queue capacity under test.
            "max_bytes": self.max_bytes,  # State the approximate byte budget under test.
            "sustained_events": self.sustained_events,  # State the sustained event count.
            "traced_method": "tracemalloc current and peak live allocation bytes",  # Label traced bytes.
            "process_method": self.process_reader.read().method,  # Label process memory method.
            "timing_note": "Timing was not measured while tracemalloc was active.",  # Keep timing separate.
            "results": results,  # Include each measured scenario row.
        }


def main() -> int:
    """Run the memory harness and print JSON."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")  # Keep command logs simple.
    _LOGGER.info("Building the performance memory harness")  # Log before object creation.
    harness = PerformanceMemoryHarness()  # Use the default capacity and sustained event count.
    _LOGGER.debug("Built the performance memory harness")  # Log the default harness construction.
    report = harness.run()  # Execute the memory-only scenarios.
    Path("data").mkdir(exist_ok=True)  # Ensure the normal data directory exists for local evidence.
    Path("data/performance-memory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")  # Persist results.
    print(json.dumps(report, indent=2))  # Print the same report for the caller.
    return 0  # Report command success.


if __name__ == "__main__":  # Run only when invoked as a command.
    raise SystemExit(main())  # Return the command status to the shell.
