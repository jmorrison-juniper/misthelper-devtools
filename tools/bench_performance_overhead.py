"""Measure the cost the performance package adds.

The harness reports two results.

1. The absolute cost of one span, in nanoseconds. That value does not depend on
   the measured operation, so it is the honest number to track.
2. The overhead share against a representative operation. The specification
   budget applies to a real request, not to a one microsecond call.

Run it with the backend virtual environment:

    python benchmarks/bench_observability_overhead.py --repeats 9
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Import src without installing.

from src.utils.performance.event import EventSource
from src.utils.performance.recorder import Recorder, RecorderSettings

PROBE = EventSource(file="tools/bench_performance_overhead.py", symbol="run_spans")

BASE_BUDGET = 1.0  # The base level must add less than one percent median latency.
TARGETED_BUDGET = 2.0  # The targeted level must add less than two percent.
REFERENCE_OPERATION_NS = 5_000_000  # A five millisecond request stands for a real call.


def _empty() -> None:
    """Do nothing, so the loop measures the span cost and not a payload."""
    return None  # An empty call isolates the instrumentation cost.


def run_bare(calls: int) -> int:
    """Time the loop without any span, to remove the loop cost from the result."""
    started = time.perf_counter_ns()  # Read the clock once, outside the loop.
    for _ in range(calls):  # Repeat the empty call the requested number of times.
        _empty()  # The loop and the call, with no measurement.
    return time.perf_counter_ns() - started  # Report the total elapsed nanoseconds.


def run_spans(recorder: Recorder, calls: int, layer: str) -> int:
    """Time the same loop with one span for each pass."""
    started = time.perf_counter_ns()  # Read the clock once, outside the loop.
    for _ in range(calls):  # Repeat the span the requested number of times.
        with recorder.span(PROBE, family=layer) as span:
            span.label("operation", "probe")  # One label, as a real hook would add.
            span.count("items_total", 1)  # One measurement, as a real hook would add.
            _empty()  # The same empty call the bare loop used.
    elapsed = time.perf_counter_ns() - started  # Read before the drain, to exclude it.
    recorder.sink.drain()  # Empty the queue, so each repeat starts from one state.
    return elapsed  # Report the total elapsed nanoseconds.


def collect(calls: int, repeats: int) -> dict[str, list[int]]:
    """Collect raw samples for every condition, interleaved to share any drift."""
    samples: dict[str, list[int]] = {"bare": [], "off": [], "base": [], "targeted": []}
    recorders = {  # Build each recorder once, outside the measured loop.
        "off": Recorder(RecorderSettings(level="off")),  # The default disabled state.
        "base": Recorder(RecorderSettings(level="base")),  # The production layer only.
        "targeted": Recorder(RecorderSettings(level="targeted")),  # Both layers.
    }
    layers = {"off": "operation", "base": "operation", "targeted": "serialization"}
    for _ in range(repeats):  # Interleave the conditions across the repeats.
        samples["bare"].append(run_bare(calls))  # Measure the loop with no span.
        for name, recorder in recorders.items():  # Measure each level in turn.
            samples[name].append(run_spans(recorder, calls, layers[name]))
    return samples  # Return every raw sample, because a median alone hides instability.


def per_span_cost(samples: dict[str, list[int]], calls: int) -> dict[str, int]:
    """Return the median added cost of one span, in nanoseconds, for each level."""
    bare_median = statistics.median(samples["bare"])  # The loop cost with no span.
    costs: dict[str, int] = {}  # Collect one absolute cost for each level.
    for name in ("off", "base", "targeted"):  # Skip the bare condition itself.
        level_median = statistics.median(samples[name])  # The loop cost with spans.
        costs[name] = int((level_median - bare_median) / calls)  # The cost of one span.
    return costs  # These values do not depend on the measured operation.


def summarize(samples: dict[str, list[int]], calls: int, reference_ns: int) -> dict:
    """Return the per-span cost, the overhead share, and every raw sample."""
    costs = per_span_cost(samples, calls)  # The workload independent result.
    report: dict = {
        "sample_count": len(samples["bare"]),  # How many repeats produced the medians.
        "calls_per_sample": calls,  # How many spans each repeat measured.
        "reference_operation_ns": reference_ns,  # The operation the budget applies to.
        "levels": {},  # One entry for each measured level.
    }
    for name, cost in costs.items():  # Describe one level at a time.
        values = samples[name]  # The raw loop totals for this level.
        quartiles = statistics.quantiles(values, n=4) if len(values) >= 4 else [0, 0, 0]
        report["levels"][name] = {
            "per_span_ns": cost,  # The absolute added cost of one span.
            "overhead_percent_at_reference": round(100 * cost / reference_ns, 4),
            "loop_median_ns": int(statistics.median(values)),  # The measured total.
            "loop_iqr_ns": int(quartiles[2] - quartiles[0]),  # The spread, which shows noise.
            "raw_ns": values,  # Keep every sample, so a reviewer can check the run.
        }
    return report  # The caller prints or stores this record.


def verdict(report: dict) -> tuple[bool, list[str]]:
    """Compare each level against its budget at the reference operation size."""
    levels = report["levels"]  # The per-level summary built above.
    checks = (("base", BASE_BUDGET), ("targeted", TARGETED_BUDGET))  # The two gates.
    lines: list[str] = []  # Collect one readable line for each gate.
    passed = True  # Stays True while every gate holds.
    for name, budget in checks:  # Check one gate at a time.
        share = levels[name]["overhead_percent_at_reference"]  # The measured share.
        cost = levels[name]["per_span_ns"]  # The absolute cost of one span.
        passed = passed and share < budget  # One failed gate fails the whole run.
        lines.append(
            f"{name}: {cost} ns for one span, {share:.4f} percent of a "
            f"{report['reference_operation_ns']} ns operation, budget {budget:.1f} percent"
        )
    lines.append(f"off: {levels['off']['per_span_ns']} ns for one disabled span")
    return passed, lines  # Report the outcome and the detail.


def main() -> int:
    """Run the harness and print the report."""
    parser = argparse.ArgumentParser(description="Measure performance hook overhead.")
    parser.add_argument("--calls", type=int, default=20_000, help="Spans for each repeat.")
    parser.add_argument("--repeats", type=int, default=9, help="Independent repeats.")
    parser.add_argument(
        "--reference-ns",
        type=int,
        default=REFERENCE_OPERATION_NS,
        help="The operation duration the budget applies to.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional raw artifact path.")
    args = parser.parse_args()

    samples = collect(args.calls, args.repeats)  # Measure every condition.
    report = summarize(samples, args.calls, args.reference_ns)  # Build the record.
    passed, lines = verdict(report)  # Compare the result against the budgets.
    print(json.dumps(report, indent=2))  # Print the full record, including raw samples.
    for line in lines:  # Print the readable gate result after the record.
        print(line)
    if args.out is not None:  # Store the raw artifact when the caller asked for one.
        args.out.parent.mkdir(parents=True, exist_ok=True)  # Create the directory once.
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1  # A failed gate returns a nonzero exit code.


if __name__ == "__main__":
    raise SystemExit(main())
