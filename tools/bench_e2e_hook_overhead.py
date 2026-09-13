"""Measure the client comparison hook overhead on a real offline path.

Why:
    Issue 2482 needs a composed overhead bound for a real command path. Direct
    timing stays in the report as a noise check only. This harness drives the
    real `compare_clients` operation with generated offline captures. It never
    opens a network socket and never reads a credential.
"""

from __future__ import annotations  # Keep annotations lazy for Python startup cost.

import argparse  # Parse the budget and repeat controls.
import importlib  # Load project modules after the script adds the repository root.
import logging  # Record each benchmark phase without printing inside the timed path.
import os  # Set the documented environment level for each collection.
import statistics  # Compute medians and interquartile ranges.
import sys  # Insert the repository root into the import path for file execution.
import time  # Read the wall and CPU clocks for each measured operation.
from dataclasses import dataclass  # Store results in small typed records.
from pathlib import Path  # Locate the repository root on Windows and Linux.
from typing import Any  # Type capture dictionaries from the fixture builder.

REPO_ROOT = Path(__file__).resolve().parents[1]  # Locate the repository root from tools.
sys.path.insert(0, str(REPO_ROOT))  # Make src importable when the script runs by file path.
clients = importlib.import_module("src.upgrade_portal.compare.clients")  # Import the measured path.
performance = importlib.import_module("src.utils.performance")  # Import the recorder package.
Recorder = performance.Recorder  # Store the recorder class for level changes.
RecorderSettings = performance.RecorderSettings  # Store the settings class for level changes.

LEVEL_OFF = "off"  # The default disabled performance level.
LEVEL_BASE = "base"  # The lowest enabled performance level.
CONTROL_LIMIT_PERCENT = 3.0  # A larger control move marks the window as contaminated.
DEFAULT_COLLECTIONS = 3  # The requirement asks for at least three collections.
DEFAULT_REPEATS = 25  # Each collection repeats paired samples.
DEFAULT_SIZES = (500, 2500, 5000)  # Include the small and large real path sizes.
INNER_LOOPS = 10  # Average each sample over repeated real operations.
SPAN_CALLS = 10_000  # Enough calls to measure one emitted span.
SPAN_REPEATS = 9  # Enough repeats to report a stable median.


@dataclass(frozen=True, slots=True)
class ClockSample:
    """Hold one wall time and one process CPU time sample."""

    wall_ns: int  # The elapsed wall time.
    cpu_ns: int  # The elapsed process CPU time.


@dataclass(frozen=True, slots=True)
class Summary:
    """Hold one timing summary for one level and one workload."""

    median_ns: float  # The middle sample value.
    iqr_ns: float  # The interquartile range of the samples.
    samples: int  # The number of samples behind the summary.


@dataclass(frozen=True, slots=True)
class Row:
    """Hold the output row for one workload size."""

    size: int  # The number of wired clients in the pre-check capture.
    span_count: int  # The number of emitted events for one full-sample operation.
    direct_wall: Summary  # The direct wall-time delta summary.
    direct_cpu: Summary  # The direct CPU-time delta summary.
    control_wall: Summary  # The control wall-time movement summary.
    operation_ns: float  # The disabled operation median wall time.
    composed_percent: float  # The composed overhead bound.


def _client_mac(index: int) -> str:
    """Return one synthetic client address."""
    return f"aabbcc{index:06x}"  # Use a fake vendor prefix and a stable ordinal.


def _device_mac(index: int, moved: bool) -> str:
    """Return one synthetic serving device address."""
    offset = index + (10_000 if moved else 0)  # Move some clients after the upgrade.
    return f"001122{offset:06x}"  # Use a fake device address.


def _client_row(index: int, moved: bool) -> dict[str, str]:
    """Return one client row in the capture schema."""
    return {  # Build only the fields that the comparison reads.
        clients.MAC_KEY: _client_mac(index),  # Give the row a stable key.
        clients.DEVICE_MAC_KEY: _device_mac(index, moved),  # Select the serving device.
        clients.DEVICE_NAME_KEY: f"switch-{index % 24}",  # Keep the name cardinality bounded.
        clients.HOSTNAME_KEY: f"client-{index}",  # Give the row a stable name.
    }  # Return one row for the capture fixture.


def build_capture(size: int, moved: bool = False) -> dict[str, Any]:
    """Build one offline capture with wired, wireless, and guest clients."""
    wired = [_client_row(index, moved and index % 10 == 0) for index in range(size)]  # Main workload.
    wireless = [_client_row(size + index, False) for index in range(size // 5)]  # Add a second section.
    guest = [_client_row(size * 2 + index, False) for index in range(size // 10)]  # Add a third section.
    return {  # Match the real capture shape.
        clients.CLIENTS_KEY: {  # Group rows by client kind.
            clients.KIND_WIRED: wired,  # Store wired clients.
            clients.KIND_WIRELESS: wireless,  # Store wireless clients.
            clients.KIND_GUEST: guest,  # Store guest clients.
        }
    }


def canonical_result(size: int, level: str) -> dict[str, Any]:
    """Return the normalized output of one comparison."""
    _set_production_level(level)  # Apply the requested performance level outside timing.
    result = clients.compare_clients(build_capture(size), build_capture(size, moved=True))  # Run the real path.
    return result.to_dict() | {"proved_present": result.proved_present}  # Include every public result value.


def _set_production_level(level: str) -> None:
    """Apply one documented performance level to the comparison module."""
    logging.info("Setting performance level %s", level)  # Log before the setting change.
    os.environ["MISTHELPER_PERF_LEVEL"] = level  # Use the documented environment variable.
    clients._PERFORMANCE_RECORDER = Recorder(clients._performance_settings())  # Use production settings.
    logging.debug("Set performance level %s", level)  # Log after the setting change.


def _set_full_sample_level(level: str) -> None:
    """Apply one full-sample recorder for a span count assertion."""
    logging.info("Setting full-sample performance level %s", level)  # Log before the setting change.
    clients._PERFORMANCE_RECORDER = Recorder(RecorderSettings(level=level))  # Emit every success.
    logging.debug("Set full-sample performance level %s", level)  # Log after the setting change.


def _time_call(function: Any, size: int) -> ClockSample:
    """Return the wall and CPU time of one averaged function call."""
    total = 0  # Consume each return value so Python cannot skip the work.
    wall_start = time.perf_counter_ns()  # Read the wall clock next to the work.
    cpu_start = time.process_time_ns()  # Read the CPU clock next to the work.
    for _ in range(INNER_LOOPS):  # Batch real calls to reduce scheduler noise.
        total += function(size)  # Run the operation and consume its result.
    cpu_elapsed = time.process_time_ns() - cpu_start  # Read CPU before validation.
    wall_elapsed = time.perf_counter_ns() - wall_start  # Read wall before validation.
    if total < 0:  # Keep the accumulator observable without changing normal output.
        raise RuntimeError("The benchmark accumulator went below zero.")  # Fail on impossible data.
    return ClockSample(wall_elapsed // INNER_LOOPS, cpu_elapsed // INNER_LOOPS)  # Return one average sample.


def operation(size: int) -> int:
    """Run the real comparison path and return a consumed result count."""
    result = clients.compare_clients(build_capture(size), build_capture(size, moved=True))  # Run the real path.
    return len(result.deltas) + result.proved_present  # Consume the result inside the timed boundary.


def control(size: int) -> int:
    """Run an unhooked control path that should not move with the level."""
    captures = (build_capture(size), build_capture(size, moved=True))  # Build the same two fixtures.
    addresses: list[str] = []  # Store derived addresses for deterministic sort work.
    for capture in captures:  # Read both sides of the fixture.
        sections = capture[clients.CLIENTS_KEY]  # Read the client section map.
        for kind in clients.CLIENT_KINDS:  # Walk the same section order as the comparison.
            addresses.extend(row[clients.MAC_KEY].replace(":", "") for row in sections[kind])  # Normalize keys.
    return sum(len(address) for address in sorted(set(addresses)))  # Consume data without the hook.


def _summary(samples: list[float]) -> Summary:
    """Return the median, the interquartile range, and the sample count."""
    ordered = sorted(samples)  # Sort once for the quartile calculation.
    lower = statistics.median(ordered[: len(ordered) // 2])  # Calculate the lower quartile.
    upper = statistics.median(ordered[(len(ordered) + 1) // 2 :])  # Calculate the upper quartile.
    return Summary(statistics.median(ordered), upper - lower, len(ordered))  # Return the timing summary.


def _percent(before: float, after: float) -> float:
    """Return the percentage difference from before to after."""
    return ((after - before) / before) * 100.0 if before else 0.0  # Avoid divide by zero.


def _paired_samples(size: int, repeats: int, function: Any) -> tuple[list[ClockSample], list[ClockSample]]:
    """Collect paired off and base samples for one function."""
    off_samples: list[ClockSample] = []  # Store disabled samples.
    base_samples: list[ClockSample] = []  # Store enabled samples.
    for index in range(repeats):  # Pair samples to control slow host drift.
        if index % 2 == 0:  # Alternate order to reduce second-run bias.
            _set_production_level(LEVEL_OFF)  # Select the disabled level.
            off_samples.append(_time_call(function, size))  # Measure the disabled path.
            _set_production_level(LEVEL_BASE)  # Select the enabled level.
            base_samples.append(_time_call(function, size))  # Measure the enabled path.
        else:
            _set_production_level(LEVEL_BASE)  # Select the enabled level.
            base_samples.append(_time_call(function, size))  # Measure the enabled path.
            _set_production_level(LEVEL_OFF)  # Select the disabled level.
            off_samples.append(_time_call(function, size))  # Measure the disabled path.
    return off_samples, base_samples  # Return paired samples for comparison.


def _wall_percents(off: list[ClockSample], base: list[ClockSample]) -> list[float]:
    """Return paired wall-time percentages."""
    return [_percent(left.wall_ns, right.wall_ns) for left, right in zip(off, base, strict=True)]  # Pair samples.


def _cpu_percents(off: list[ClockSample], base: list[ClockSample]) -> list[float]:
    """Return paired CPU-time percentages."""
    return [_percent(left.cpu_ns, right.cpu_ns) for left, right in zip(off, base, strict=True)]  # Pair samples.


def _validate_output(size: int) -> None:
    """Fail loudly when the enabled hook changes the function output."""
    logging.info("Validating output equality for size %s", size)  # Log before the equality check.
    off = canonical_result(size, LEVEL_OFF)  # Build the reference result outside timing.
    base = canonical_result(size, LEVEL_BASE)  # Build the candidate result outside timing.
    if off != base:  # Compare the public output shape.
        raise SystemExit(f"Output changed for size {size}")  # Fail with the affected size.
    logging.debug("Validated output equality for size %s", size)  # Log after the equality check.


def _assert_span_count(size: int) -> int:
    """Return the emitted event count for one full-sample operation."""
    _set_full_sample_level(LEVEL_BASE)  # Use an unsampled recorder for the assertion.
    clients.compare_clients(build_capture(size), build_capture(size, moved=True))  # Run one operation.
    count = len(clients._PERFORMANCE_RECORDER.sink.drain())  # Count emitted operation events.
    if count != 1:  # The composed bound requires exactly one event per operation.
        raise SystemExit(f"Expected one event for size {size}, got {count}")  # Fail loudly.
    return count  # Return the asserted span count.


def _span_overhead_ns() -> float:
    """Return the median added wall time of one emitted span."""
    overheads = [_span_run_overhead() for _ in range(SPAN_REPEATS)]  # Collect independent span runs.
    return statistics.median(overheads) / SPAN_CALLS  # Normalize the median run cost to one span.


def _span_run_overhead() -> int:
    """Return the added wall time for many emitted spans."""
    recorder = Recorder(RecorderSettings(level=LEVEL_BASE))  # Use a recorder that emits each span.
    source = clients._PERFORMANCE_SOURCE  # Reuse the hook source.
    bare = _empty_loop_time(SPAN_CALLS)  # Measure the loop without spans.
    measured = _span_loop_time(recorder, source, SPAN_CALLS)  # Measure the same loop with spans.
    return max(0, measured - bare)  # Clamp noise so the bound never becomes negative.


def _empty_loop_time(calls: int) -> int:
    """Return the time for the empty loop body."""
    started = time.perf_counter_ns()  # Read the clock once before the loop.
    for _ in range(calls):  # Repeat the empty work.
        pass  # Keep the body empty to isolate loop cost.
    return time.perf_counter_ns() - started  # Return elapsed wall time.


def _span_loop_time(recorder: Any, source: Any, calls: int) -> int:
    """Return the time for the loop with one emitted span per pass."""
    started = time.perf_counter_ns()  # Read the clock once before the loop.
    for _ in range(calls):  # Repeat the span measurement.
        with recorder.span(source, family="operation") as span:  # Open one emitted operation span.
            span.label("operation", "compare_clients")  # Add one fixed operation label.
            span.count("work.items_total", 1)  # Add one representative count.
            span.count("perf.calls_total", 1)  # Add one call count.
    elapsed = time.perf_counter_ns() - started  # Exclude the drain from the measured window.
    recorder.sink.drain()  # Drain after timing so the next repeat starts clean.
    return elapsed  # Return elapsed wall time.


def _attempt(size: int, repeats: int, span_ns: float) -> Row | None:
    """Run one collection attempt and reject a contaminated window."""
    control_off, control_base = _paired_samples(size, repeats, control)  # Measure the control path.
    control_wall = _summary(_wall_percents(control_off, control_base))  # Summarize control movement.
    if abs(control_wall.median_ns) > CONTROL_LIMIT_PERCENT:  # Reject a contaminated window.
        return None  # Let the caller retry this collection.
    operation_off, operation_base = _paired_samples(size, repeats, operation)  # Measure the real path.
    direct_wall = _summary(_wall_percents(operation_off, operation_base))  # Summarize direct wall delta.
    direct_cpu = _summary(_cpu_percents(operation_off, operation_base))  # Summarize direct CPU delta.
    operation_ns = statistics.median(sample.wall_ns for sample in operation_off)  # Summarize real work cost.
    span_count = _assert_span_count(size)  # Prove the hook emits one event at full sample.
    composed = span_count * span_ns / operation_ns * 100.0  # Compute the composed bound.
    return Row(size, span_count, direct_wall, direct_cpu, control_wall, operation_ns, composed)  # Return the row.


def collect_rows(sizes: tuple[int, ...], collections: int, repeats: int) -> tuple[float, list[Row]]:
    """Collect clean rows for each size."""
    span_ns = _span_overhead_ns()  # Measure the cost of one emitted span.
    rows: list[Row] = []  # Store one aggregate row for each size.
    for size in sizes:  # Measure each representative input size.
        _validate_output(size)  # Prove the hook preserves output before timing.
        attempts = _clean_attempts(size, collections, repeats, span_ns)  # Collect clean attempts.
        rows.append(_merge_rows(size, attempts))  # Merge collections into one report row.
    return span_ns, rows  # Return the span cost and every workload row.


def _clean_attempts(size: int, collections: int, repeats: int, span_ns: float) -> list[Row]:
    """Return enough clean collection attempts for one size."""
    attempts: list[Row] = []  # Store clean collection rows for this size.
    for attempt in range(collections * 4):  # Retry only when the control detects contamination.
        logging.info("Running collection %s for size %s", attempt + 1, size)  # Log before an attempt.
        row = _attempt(size, repeats, span_ns)  # Measure one candidate collection.
        if row is not None:  # Accept only clean control windows.
            attempts.append(row)  # Keep the clean attempt.
        logging.debug("Size %s has %s clean collections", size, len(attempts))  # Log the clean count.
        if len(attempts) == collections:  # Stop when the requirement is met.
            return attempts  # Return the clean attempts.
    raise SystemExit(f"Control stayed contaminated for size {size}")  # State the blocked size.


def _merge_rows(size: int, rows: list[Row]) -> Row:
    """Merge clean collection rows into one report row."""
    direct_wall = _summary([row.direct_wall.median_ns for row in rows])  # Summarize direct wall medians.
    direct_cpu = _summary([row.direct_cpu.median_ns for row in rows])  # Summarize direct CPU medians.
    control_wall = _summary([row.control_wall.median_ns for row in rows])  # Summarize control medians.
    operation_ns = statistics.median(row.operation_ns for row in rows)  # Summarize operation medians.
    composed = statistics.median(row.composed_percent for row in rows)  # Summarize composed bounds.
    return Row(size, rows[0].span_count, direct_wall, direct_cpu, control_wall, operation_ns, composed)  # Row.


def _format_ns(value: float) -> str:
    """Return a compact millisecond value."""
    return f"{value / 1_000_000:.3f}"  # Convert nanoseconds to milliseconds for the table.


def _noise_label(direct: Summary, control: Summary) -> str:
    """Return the direct measurement verdict."""
    unresolved = direct.median_ns < 0 or abs(direct.median_ns) <= abs(control.median_ns)  # Compare to noise.
    return "below noise floor, not resolvable" if unresolved else "resolved"  # Return the label.


def print_table(span_ns: float, rows: list[Row]) -> None:
    """Print the benchmark tables."""
    print(f"Per-span wall cost ns: {span_ns:.0f}")  # Print the measured span cost.
    print("| clients | spans | operation median ms | composed overhead % | budget % | verdict |")  # Header.
    print("| --- | ---: | ---: | ---: | ---: | --- |")  # Markdown separator.
    for row in rows:  # Print one composed-bound row per workload.
        verdict = "pass" if row.composed_percent <= 1.0 else "fail"  # Compare against the fixed budget.
        values = (  # Build the row in parts to keep the line readable.
            f"{row.size}",  # Add the workload size.
            f"{row.span_count}",  # Add the span count.
            _format_ns(row.operation_ns),  # Add the operation cost.
            f"{row.composed_percent:.3f}",  # Add the composed bound.
            "1.000",  # Add the fixed budget.
            verdict,  # Add the verdict.
        )
        print("| " + " | ".join(values) + " |")  # Print the row.
    print("")  # Separate the two tables for readers.
    print(
        "| clients | direct wall % | direct wall IQR | direct CPU % | control % | control IQR | samples | verdict |"
    )  # Header.
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")  # Markdown separator.
    for row in rows:  # Print one direct-delta row per workload.
        verdict = _noise_label(row.direct_wall, row.control_wall)  # Label unresolved rows.
        values = (  # Build the row in parts to keep the line readable.
            f"{row.size}",  # Add the workload size.
            f"{row.direct_wall.median_ns:.3f}",  # Add the direct wall delta.
            f"{row.direct_wall.iqr_ns:.3f}",  # Add the direct wall IQR.
            f"{row.direct_cpu.median_ns:.3f}",  # Add the direct CPU delta.
            f"{row.control_wall.median_ns:.3f}",  # Add the control movement.
            f"{row.control_wall.iqr_ns:.3f}",  # Add the control IQR.
            f"{row.direct_wall.samples}",  # Add the collection count.
            verdict,  # Add the noise label.
        )
        print("| " + " | ".join(values) + " |")  # Print the row.


def parse_args() -> argparse.Namespace:
    """Return parsed benchmark options."""
    parser = argparse.ArgumentParser(description="Measure the MistHelper client comparison hook overhead.")  # Parser.
    parser.add_argument("--budget-percent", type=float, default=1.0)  # Set the overhead budget.
    parser.add_argument("--collections", type=int, default=DEFAULT_COLLECTIONS)  # Set collection count.
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)  # Set repeats per collection.
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))  # Set workload sizes.
    return parser.parse_args()  # Return parsed values.


def main() -> int:
    """Run the benchmark and return a process status."""
    logging.basicConfig(level=logging.WARNING)  # Keep timed path logging quiet.
    args = parse_args()  # Read command-line options.
    span_ns, rows = collect_rows(tuple(args.sizes), args.collections, args.repeats)  # Collect rows.
    print_table(span_ns, rows)  # Print both benchmark tables.
    failed = [row for row in rows if row.composed_percent > args.budget_percent]  # Find failures.
    return 1 if failed else 0  # Return non-zero when any size exceeds the budget.


if __name__ == "__main__":
    raise SystemExit(main())  # Use the function result as the process exit code.
