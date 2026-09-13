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
import math  # Calculate the batch size that crosses the measured CPU tick.
import os  # Set the documented environment level for each collection.
import random  # Build a deterministic bootstrap interval for paired differences.
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
CLOCK_SAMPLE_COUNT = 200_000  # Measure Windows clock ticks from data, not from assumption.
AMPLIFIED_LOOPS = 100  # Batch real operations so one hook effect can accumulate.
AMPLIFIED_REPEATS = 5  # Use paired batches so slow host drift is visible.
PAIRED_PAIRS = 0  # Keep paired interval sampling opt-in, because 500 pairs can be slow.
BOOTSTRAP_REPEATS = 1_000  # Estimate a stable median interval without a new dependency.


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


@dataclass(frozen=True, slots=True)
class AmplifiedRow:
    """Hold one amplified end-to-end measurement row."""

    size: int  # The number of clients in the offline workload.
    calls: int  # The number of real operations inside one timed boundary.
    batch_delta_ns: int  # The measured hooked-minus-disabled batch delta.
    per_operation_ns: float  # The measured overhead normalized after timing.
    measured_percent: float  # The measured overhead as a percent of operation time.
    control_delta_ns: int  # The unrelated batch delta from the same method.
    composed_percent: float  # The independent composed overhead bound.
    needed_for_cpu_tick: int  # The batch size needed to cross one CPU tick.
    status: str  # The resolution verdict for this row.


@dataclass(frozen=True, slots=True)
class PairedRow:
    """Hold one paired wall-clock interval row."""

    size: int  # The number of clients in the offline workload.
    pairs: int  # The number of off/base pairs in the sample.
    median_percent: float  # The median paired wall-clock difference.
    ci_low_percent: float  # The lower bootstrap confidence bound.
    ci_high_percent: float  # The upper bootstrap confidence bound.
    half_width_percent: float  # The larger distance from the median to the bound.
    composed_percent: float  # The composed bound kept as the independent check.
    contains_zero: bool  # True when the interval does not resolve the sign.
    contains_composed: bool  # True when the interval contains the composed bound.
    required_pairs: int  # Estimated pairs needed to meet the target half width.
    status: str  # The final paired-method verdict.


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


def _time_batch(function: Any, size: int, calls: int) -> int:
    """Return the wall time for one amplified operation batch."""
    total = 0  # Consume each result so the interpreter must run the full workload.
    wall_start = time.perf_counter_ns()  # Read the high-resolution wall clock before work starts.
    for _ in range(calls):  # Repeat the real operation inside one timed boundary.
        total += function(size)  # Run the same path as the direct benchmark.
    wall_elapsed = time.perf_counter_ns() - wall_start  # Stop before validation or reporting.
    if total < 0:  # Keep the accumulator observable without changing normal output.
        raise RuntimeError("The benchmark accumulator went below zero.")  # Fail on impossible data.
    return wall_elapsed  # Return the full batch so the hook effect can accumulate.


def _time_once(function: Any, size: int) -> int:
    """Return the wall time for one operation."""
    wall_start = time.perf_counter_ns()  # Use the high-resolution wall clock for small effects.
    total = function(size)  # Run exactly one operation so batching does not keep ratio noise.
    wall_elapsed = time.perf_counter_ns() - wall_start  # Stop the clock next to the operation.
    if total < 0:  # Keep the result observable so the interpreter cannot discard work.
        raise RuntimeError("The benchmark accumulator went below zero.")  # Fail on impossible data.
    return wall_elapsed  # Return one wall-clock sample.


def _clock_resolution(clock: Any) -> dict[str, float | int | list[int]]:
    """Return the smallest nonzero tick for one Python clock."""
    last = clock()  # Set the first reading before the tight loop starts.
    deltas: list[int] = []  # Store nonzero deltas to expose the reported clock tick.
    zero_count = 0  # Count repeated reads that stay inside one coarse tick.
    for _ in range(CLOCK_SAMPLE_COUNT):  # Sample enough times to reveal Windows CPU ticks.
        current = clock()  # Read the clock without inserted work.
        delta = current - last  # Compare adjacent readings from the same clock.
        if delta:  # Keep only a value that the clock can report.
            deltas.append(delta)  # Preserve the raw nonzero tick value.
            last = current  # Move the baseline only when the clock moves.
        else:  # Count zero values so a coarse clock is visible.
            zero_count += 1  # Show why direct CPU deltas can read as zero.
    return {
        "samples": CLOCK_SAMPLE_COUNT,
        "zero_count": zero_count,
        "nonzero_count": len(deltas),
        "min_ns": min(deltas) if deltas else 0,
        "median_ns": statistics.median(deltas) if deltas else 0,
        "first_ticks_ns": sorted(set(deltas))[:5],
    }  # Report measured data for the host.


def _clock_rows() -> dict[str, dict[str, float | int | list[int]]]:
    """Return measured resolution data for both benchmark clocks."""
    return {
        "perf_counter_ns": _clock_resolution(time.perf_counter_ns),
        "process_time_ns": _clock_resolution(time.process_time_ns),
    }  # Measure both clocks in the process that runs the benchmark.


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
    if len(ordered) == 1:  # A smoke run can keep only one clean collection.
        only = ordered[0]  # Reuse the single value as both quartiles.
        return Summary(only, 0.0, 1)  # Report zero spread instead of hiding the result.
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


def collect_amplified_rows(rows: list[Row], calls: int, repeats: int, span_ns: float) -> list[AmplifiedRow]:
    """Collect amplified end-to-end measurements for each workload size."""
    return [
        _amplified_row(row, calls, repeats, span_ns)  # Build one amplified result for this size.
        for row in rows  # Reuse the sizes that already passed output equality.
    ]


def collect_paired_rows(rows: list[Row], pairs: int, repeats: int) -> list[PairedRow]:
    """Collect paired wall-clock interval rows for each workload size."""
    return [
        _paired_interval_row(row, pairs, repeats)  # Measure off/base pairs for this workload.
        for row in rows  # Reuse only workloads that passed output equality and span checks.
    ]


def _paired_interval_row(row: Row, pairs: int, repeats: int) -> PairedRow:
    """Build one paired wall-clock interval row."""
    differences = _paired_wall_differences(row.size, pairs)  # Collect signed paired differences.
    median_percent = statistics.median(differences)  # Use the robust center of paired samples.
    ci_low, ci_high = _bootstrap_median_interval(differences, repeats)  # Estimate uncertainty.
    half_width = max(median_percent - ci_low, ci_high - median_percent)  # Report the worst side.
    contains_zero = ci_low <= 0.0 <= ci_high  # Check whether the sign was resolved.
    contains_composed = ci_low <= row.composed_percent <= ci_high  # Check the composed bound.
    required_pairs = _required_pairs(pairs, half_width, max(row.composed_percent / 2.0, 0.001))  # Size.
    status = _paired_status(contains_zero, contains_composed)  # Convert checks into a verdict.
    return PairedRow(
        row.size,
        pairs,
        median_percent,
        ci_low,
        ci_high,
        half_width,
        row.composed_percent,
        contains_zero,
        contains_composed,
        required_pairs,
        status,
    )  # Return the paired-method result.


def _paired_wall_differences(size: int, pairs: int) -> list[float]:
    """Return paired wall-clock percent differences for one workload."""
    differences: list[float] = []  # Store one signed percent value per pair.
    for index in range(pairs):  # Collect enough pairs for the standard error to fall.
        if index % 2 == 0:  # Alternate order so first-run bias cannot become overhead.
            off_ns = _timed_level(LEVEL_OFF, size)  # Measure the disabled path first.
            base_ns = _timed_level(LEVEL_BASE, size)  # Measure the enabled path second.
        else:  # Reverse the pair order on every other pair.
            base_ns = _timed_level(LEVEL_BASE, size)  # Measure the enabled path first.
            off_ns = _timed_level(LEVEL_OFF, size)  # Measure the disabled path second.
        differences.append(_percent(off_ns, base_ns))  # Normalize the paired difference.
    return differences  # Return raw differences so the interval can use them.


def _timed_level(level: str, size: int) -> int:
    """Return one wall-clock operation sample at one performance level."""
    _set_production_level(level)  # Apply the level outside the timed boundary.
    return _time_once(operation, size)  # Measure one real comparison operation.


def _bootstrap_median_interval(samples: list[float], repeats: int) -> tuple[float, float]:
    """Return a deterministic 95 percent bootstrap interval for the median."""
    generator = random.Random(0)  # nosec B311 - The benchmark uses this draw for repeatable sampling only.
    medians: list[float] = []  # Store one bootstrap median for each resample.
    for _ in range(repeats):  # Build enough resamples for a stable displayed interval.
        resample = [generator.choice(samples) for _ in samples]  # Sample with replacement.
        medians.append(statistics.median(resample))  # Keep the statistic under test.
    ordered = sorted(medians)  # Sort once so percentile selection is stable.
    low_index = max(0, int(0.025 * len(ordered)) - 1)  # Select the lower percentile.
    high_index = min(len(ordered) - 1, int(0.975 * len(ordered)))  # Select the upper percentile.
    return ordered[low_index], ordered[high_index]  # Return the bootstrap confidence interval.


def _required_pairs(current_pairs: int, half_width: float, target_half_width: float) -> int:
    """Estimate the pair count needed for a smaller confidence interval."""
    if half_width <= target_half_width:  # The current run already meets the target width.
        return current_pairs  # Return the achieved pair count.
    factor = (half_width / target_half_width) ** 2  # Standard error falls with square root of n.
    return math.ceil(current_pairs * factor)  # Report the approximate needed sample count.


def _paired_status(contains_zero: bool, contains_composed: bool) -> str:
    """Return the paired interval verdict."""
    if contains_zero:  # An interval crossing zero cannot resolve positive overhead.
        return "not resolved: interval includes zero"  # State the remaining noise limit.
    if not contains_composed:  # A resolved sign that misses the bound is not agreement.
        return "not resolved: interval misses composed bound"  # State the disagreement.
    return "resolved: interval excludes zero and contains composed bound"  # State resolution.


def _amplified_row(row: Row, calls: int, repeats: int, span_ns: float) -> AmplifiedRow:
    """Build one amplified row for one client count."""
    operation_off, operation_base = _paired_batch_samples(
        row.size, calls, repeats, operation, True
    )  # Measure full-sample hook batches to match the composed bound.
    control_off, control_base = _paired_batch_samples(
        row.size, calls, repeats, control, False
    )  # Measure unrelated batches with the production level only.
    operation_deltas = _paired_deltas(operation_off, operation_base)  # Compare operation batches.
    control_deltas = _paired_deltas(control_off, control_base)  # Compare unrelated control batches.
    batch_delta = statistics.median(operation_deltas)  # Use the robust hooked-minus-off delta.
    control_delta = statistics.median(control_deltas)  # Use the same statistic for noise.
    per_operation = batch_delta / calls  # Normalize only after the batch effect is measured.
    operation_ns = statistics.median(operation_off) / calls  # Use the disabled batch as the baseline.
    measured_percent = _percent(operation_ns, operation_ns + per_operation)  # Convert to percent.
    needed = _needed_calls(span_ns, _clock_resolution(time.process_time_ns)["min_ns"])  # Size CPU batches.
    status = _amplified_status(batch_delta, control_delta, row.composed_percent, measured_percent)  # Label.
    return AmplifiedRow(
        row.size,
        calls,
        int(batch_delta),
        per_operation,
        max(0.0, measured_percent),
        int(control_delta),
        row.composed_percent,
        needed,
        status,
    )  # Return one printable row.


def _paired_batch_samples(
    size: int, calls: int, repeats: int, function: Any, full_sample: bool
) -> tuple[list[int], list[int]]:
    """Collect paired amplified wall-time samples."""
    off_samples: list[int] = []  # Store disabled batch samples.
    base_samples: list[int] = []  # Store enabled batch samples.
    for _ in range(repeats):  # Pair each enabled batch with disabled batches around it.
        _set_production_level(LEVEL_OFF)  # Select the disabled level before the first bound.
        before = _time_batch(function, size, calls)  # Measure the disabled batch before the hook.
        if full_sample:  # The composed bound assumes one emitted span per operation.
            _set_full_sample_level(LEVEL_BASE)  # Select a recorder that emits every success.
        else:  # The control path has no hook to emit.
            _set_production_level(LEVEL_BASE)  # Select the normal enabled setting for symmetry.
        base_samples.append(_time_batch(function, size, calls))  # Measure the hooked batch.
        _set_production_level(LEVEL_OFF)  # Select the disabled level after the hook batch.
        after = _time_batch(function, size, calls)  # Measure the disabled batch after the hook.
        off_samples.append((before + after) // 2)  # Average the bounds to cancel slow drift.
    return off_samples, base_samples  # Return full batch times without division.


def _paired_deltas(before_values: list[int], after_values: list[int]) -> list[int]:
    """Return paired batch differences."""
    return [
        after - before  # Preserve the signed difference for honest overhead reporting.
        for before, after in zip(before_values, after_values, strict=True)  # Pair samples by order.
    ]


def _amplified_status(
    batch_delta: float, control_delta: float, composed_percent: float, measured_percent: float
) -> str:
    """Return the verdict for one amplified measurement."""
    if batch_delta <= 0.0:  # A negative measured delta cannot be real overhead.
        return "not resolved: negative batch delta"  # State the impossible result directly.
    if abs(batch_delta) <= abs(control_delta):  # The unrelated control moved as much as the hook.
        return "not resolved: below control drift"  # State the observed limit.
    if composed_percent == 0.0:  # A zero bound cannot support a ratio comparison.
        return "resolved: no composed comparison"  # Avoid a divide by zero.
    ratio = measured_percent / composed_percent  # Compare measurement with composition.
    if 0.25 <= ratio <= 4.0:  # Agreement means both methods point to the same small effect.
        return "resolved: agrees with composed bound"  # State the resolved cross-check.
    return "not resolved: disagrees with composed bound"  # Treat disagreement as drift.


def _needed_calls(per_span_ns: float, tick_ns: float) -> int:
    """Return the batch size needed for the expected effect to cross one tick."""
    if per_span_ns <= 0.0:  # A zero measured span cost cannot size amplification.
        return 0  # Report that the required batch size is unknown.
    return max(1, math.ceil(tick_ns / per_span_ns))  # Size the batch from measured data.


def _format_ns(value: float) -> str:
    """Return a compact millisecond value."""
    return f"{value / 1_000_000:.3f}"  # Convert nanoseconds to milliseconds for the table.


def _noise_label(direct: Summary, control: Summary) -> str:
    """Return the direct measurement verdict."""
    unresolved = direct.median_ns < 0 or abs(direct.median_ns) <= abs(control.median_ns)  # Compare to noise.
    if unresolved:  # A negative or control-sized delta cannot be real overhead.
        return "below noise floor, not resolvable"  # Return the noise-floor label.
    return "not resolved: direct method is not the verdict"  # Keep direct data out of the verdict.


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


def print_clock_table(rows: dict[str, dict[str, float | int | list[int]]]) -> None:
    """Print the measured clock resolution table."""
    print("| clock | min nonzero ns | median nonzero ns | zero reads | nonzero reads |")  # Header.
    print("| --- | ---: | ---: | ---: | ---: |")  # Markdown separator.
    for name, row in rows.items():  # Print each measured clock.
        values = (  # Build the row in parts to keep line length controlled.
            name,  # Add the Python clock name.
            f"{row['min_ns']}",  # Add the smallest reported movement.
            f"{row['median_ns']}",  # Add the median reported movement.
            f"{row['zero_count']}",  # Add zero reads to show coarse ticks.
            f"{row['nonzero_count']}",  # Add nonzero reads to show the sample count.
        )
        print("| " + " | ".join(values) + " |")  # Print the row.


def print_amplified_table(rows: list[AmplifiedRow]) -> None:
    """Print the amplified end-to-end measurement table."""
    print("")  # Separate the table from the direct-delta table.
    print(
        "| clients | calls | batch delta ns | per operation ns | measured overhead % | "
        "control delta ns | composed overhead % | CPU tick calls | verdict |"
    )  # Header.
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")  # Separator.
    for row in rows:  # Print one amplified row per workload.
        values = (  # Build the row in parts to keep the line readable.
            f"{row.size}",  # Add the workload size.
            f"{row.calls}",  # Add the batch amplification count.
            f"{row.batch_delta_ns}",  # Add the signed batch difference.
            f"{row.per_operation_ns:.1f}",  # Add normalized overhead.
            f"{row.measured_percent:.3f}",  # Add measured overhead percent.
            f"{row.control_delta_ns}",  # Add the unrelated control movement.
            f"{row.composed_percent:.3f}",  # Add the composed cross-check.
            f"{row.needed_for_cpu_tick}",  # Add the CPU tick batch need.
            row.status,  # Add the resolution verdict.
        )
        print("| " + " | ".join(values) + " |")  # Print the row.


def print_paired_table(rows: list[PairedRow]) -> None:
    """Print the paired wall-clock interval table."""
    print("")  # Separate the paired table from the amplified table.
    print(
        "| clients | pairs | median % | CI low % | CI high % | half width % | "
        "composed % | required pairs | verdict |"
    )  # Header.
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")  # Separator.
    for row in rows:  # Print one paired result per workload.
        values = (  # Build the row in parts so reviewers can scan it.
            f"{row.size}",  # Add the workload size.
            f"{row.pairs}",  # Add the number of off/base pairs.
            f"{row.median_percent:.4f}",  # Add the median paired difference.
            f"{row.ci_low_percent:.4f}",  # Add the lower interval bound.
            f"{row.ci_high_percent:.4f}",  # Add the upper interval bound.
            f"{row.half_width_percent:.4f}",  # Add the achieved half width.
            f"{row.composed_percent:.4f}",  # Add the composed cross-check.
            f"{row.required_pairs}",  # Add the estimated pair count needed.
            row.status,  # Add the paired-method verdict.
        )
        print("| " + " | ".join(values) + " |")  # Print the row.


def parse_args() -> argparse.Namespace:
    """Return parsed benchmark options."""
    parser = argparse.ArgumentParser(description="Measure the MistHelper client comparison hook overhead.")  # Parser.
    parser.add_argument("--budget-percent", type=float, default=1.0)  # Set the overhead budget.
    parser.add_argument("--collections", type=int, default=DEFAULT_COLLECTIONS)  # Set collection count.
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)  # Set repeats per collection.
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))  # Set workload sizes.
    parser.add_argument("--amplified-loops", type=int, default=AMPLIFIED_LOOPS)  # Set batch size.
    parser.add_argument("--amplified-repeats", type=int, default=AMPLIFIED_REPEATS)  # Set pairs.
    parser.add_argument("--paired-pairs", type=int, default=PAIRED_PAIRS)  # Set paired count.
    parser.add_argument("--bootstrap-repeats", type=int, default=BOOTSTRAP_REPEATS)  # Set interval repeats.
    return parser.parse_args()  # Return parsed values.


def main() -> int:
    """Run the benchmark and return a process status."""
    logging.basicConfig(level=logging.WARNING)  # Keep timed path logging quiet.
    args = parse_args()  # Read command-line options.
    clock_rows = _clock_rows()  # Measure clock resolution before relying on a direct CPU delta.
    span_ns, rows = collect_rows(tuple(args.sizes), args.collections, args.repeats)  # Collect rows.
    amplified_rows = collect_amplified_rows(
        rows, args.amplified_loops, args.amplified_repeats, span_ns
    )  # Measure amplified batches after output equality passes.
    print_clock_table(clock_rows)  # Print the host clock data first.
    print_table(span_ns, rows)  # Print both benchmark tables.
    print_amplified_table(amplified_rows)  # Print the resolved or unresolved amplified data.
    if args.paired_pairs > 0:  # Run the long paired method only when the caller requests it.
        paired_rows = collect_paired_rows(rows, args.paired_pairs, args.bootstrap_repeats)  # Pair.
        print_paired_table(paired_rows)  # Print the paired interval verdict.
    failed = [row for row in rows if row.composed_percent > args.budget_percent]  # Find failures.
    return 1 if failed else 0  # Return non-zero when any size exceeds the budget.


if __name__ == "__main__":
    raise SystemExit(main())  # Use the function result as the process exit code.
