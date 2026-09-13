"""Tests for the performance memory harness.

Why:
    The tests prove that the measured objects obey the memory bounds.
"""

from __future__ import annotations  # Keep annotations lazy during test collection.

import logging  # Report test actions before and after they run.

from src.utils.performance import privacy  # Check the safe value cache bound.
from src.utils.performance.sink import DEFAULT_MAX_BYTES, BoundedSink  # Check the queue capacity bound.
from tools.performance_memory import EventFactory, PerformanceMemoryHarness  # Reuse the measured scenarios.

_LOGGER = logging.getLogger(__name__)  # Share one logger for this test module.


def test_the_queue_never_exceeds_capacity() -> None:
    """A bounded sink keeps only the newest events up to capacity."""
    _LOGGER.info("Creating a small bounded sink")  # Log the setup action.
    capacity = 8  # Use a small capacity so the test stays fast.
    sink = BoundedSink(capacity=capacity)  # Create the bounded queue under test.
    factory = EventFactory()  # Create events with the same shape as the harness.
    _LOGGER.debug("Created sink capacity=%s", capacity)  # Log the setup result.
    _LOGGER.info("Emitting more events than the sink capacity")  # Log the fill action.
    for index in range(capacity * 3):  # Cross the queue capacity several times.
        sink.emit(factory.minimal(index))  # Add a distinct event so eviction must occur.
    retained = sink.drain()  # Drain the queue to count retained events.
    _LOGGER.debug("Retained %s events after overflow", len(retained))  # Log the retained count.
    assert len(retained) == capacity  # Prove the queue did not exceed its capacity.
    assert sink.dropped == capacity * 2  # Prove the sink counted each evicted event.


def test_large_events_hit_the_byte_bound_before_capacity() -> None:
    """A large event queue evicts by bytes before it reaches capacity."""
    _LOGGER.info("Creating a byte limited sink")  # Log the setup action.
    sink = BoundedSink(capacity=32, max_bytes=12_000)  # Use a byte cap that holds one large event.
    factory = EventFactory()  # Create worst case events with the harness contract.
    _LOGGER.debug("Created sink max_bytes=%s", sink.max_bytes)  # Log the configured byte cap.
    _LOGGER.info("Emitting large events past the byte cap")  # Log the fill action.
    for index in range(3):  # Emit more large events than the byte budget keeps.
        sink.emit(factory.worst(index))  # Add one legal worst case event.
    retained = sink.drain()  # Drain the queue to count retained events.
    _LOGGER.debug("Retained %s large events", len(retained))  # Log the retained count.
    assert len(retained) == 1  # Prove the byte limit won before the entry limit.
    assert sink.dropped == 2  # Prove byte evictions incremented the drop counter.


def test_small_events_still_hit_the_entry_bound() -> None:
    """A small event queue still evicts by the entry count."""
    _LOGGER.info("Creating an entry limited sink")  # Log the setup action.
    sink = BoundedSink(capacity=2, max_bytes=DEFAULT_MAX_BYTES)  # Make bytes too large to win.
    factory = EventFactory()  # Create minimal events with the harness contract.
    _LOGGER.debug("Created sink capacity=%s", 2)  # Log the configured entry cap.
    _LOGGER.info("Emitting small events past the entry cap")  # Log the fill action.
    for index in range(5):  # Emit more small events than the entry budget keeps.
        sink.emit(factory.minimal(index))  # Add one legal minimal event.
    retained = sink.drain()  # Drain the queue to count retained events.
    _LOGGER.debug("Retained %s small events", len(retained))  # Log the retained count.
    assert len(retained) == 2  # Prove the entry limit still bounds small events.
    assert sink.dropped == 3  # Prove entry evictions incremented the drop counter.


def test_the_drop_counter_counts_byte_and_entry_evictions() -> None:
    """The drop counter includes each eviction path."""
    _LOGGER.info("Creating a sink with both limits")  # Log the setup action.
    sink = BoundedSink(capacity=2, max_bytes=12_000)  # Set limits so each path runs once.
    factory = EventFactory()  # Create small and large events with one contract.
    _LOGGER.debug("Created sink with byte and entry limits")  # Log the setup result.
    _LOGGER.info("Emitting a large event that fills most byte space")  # Log the first event.
    sink.emit(factory.worst(0))  # Keep one large event near the byte cap.
    _LOGGER.debug("Queue estimate after first event=%s", sink.queued_bytes)  # Log the charge.
    _LOGGER.info("Emitting a second large event that causes byte eviction")  # Log the byte path.
    sink.emit(factory.worst(1))  # Force the byte bound to evict the first large event.
    _LOGGER.debug("Drop count after byte eviction=%s", sink.dropped)  # Log the first drop.
    _LOGGER.info("Emitting two small events that cause entry eviction")  # Log the entry path.
    sink.emit(factory.minimal(2))  # Fill the second queue entry without byte pressure.
    sink.emit(factory.minimal(3))  # Force the entry bound to evict the oldest event.
    _LOGGER.debug("Drop count after both evictions=%s", sink.dropped)  # Log the final drops.
    assert len(sink.drain()) == 2  # Prove the queue kept the newest two events.
    assert sink.dropped == 2  # Prove the counter includes byte and entry evictions.


def test_memory_plateaus_under_sustained_load() -> None:
    """A sustained run keeps memory near the full queue value."""
    _LOGGER.info("Measuring a small full queue")  # Log the first measurement.
    full_report = PerformanceMemoryHarness(capacity=16, sustained_events=16).run()  # Measure one full queue.
    full_row = _row(full_report, "full_queue_minimal_events")  # Read the full queue row.
    _LOGGER.debug("Full queue traced current bytes=%s", full_row["traced_current_bytes"])  # Log the baseline.
    _LOGGER.info("Measuring sustained events above capacity")  # Log the sustained measurement.
    sustained_report = PerformanceMemoryHarness(capacity=16, sustained_events=512).run()  # Measure overflow.
    sustained_row = _row(sustained_report, "sustained_minimal_events")  # Read the sustained row.
    _LOGGER.debug("Sustained traced current bytes=%s", sustained_row["traced_current_bytes"])  # Log the result.
    limit = (full_row["traced_current_bytes"] * 4) + 50_000  # Allow allocator noise but reject linear growth.
    assert sustained_row["queued_events"] == 16  # Prove the queue still holds only capacity events.
    assert sustained_row["actual_dropped"] == 496  # Prove overflow events were evicted.
    assert sustained_row["traced_current_bytes"] <= limit  # Prove the retained memory plateaued.


def test_worst_case_memory_stays_under_the_configured_byte_bound() -> None:
    """A worst case queue stays below the configured byte bound."""
    _LOGGER.info("Measuring a byte bounded worst case queue")  # Log the measurement action.
    report = PerformanceMemoryHarness(capacity=64, max_bytes=96_000, sustained_events=512).run()  # Measure small.
    row = _row(report, "byte_bounded_worst_case_events")  # Read the bounded worst case row.
    _LOGGER.debug("Worst case traced current bytes=%s", row["traced_current_bytes"])  # Log the result.
    assert row["estimated_queued_bytes"] <= row["configured_max_bytes"]  # Prove the estimator bound held.
    assert row["traced_current_bytes"] <= 300_000  # Prove the true retained memory stayed small.


def test_safe_value_cache_clears_at_the_limit() -> None:
    """The safe value cache clears when the next value would exceed the limit."""
    _LOGGER.info("Clearing the safe value cache")  # Log the cache reset.
    privacy._SAFE_VALUE_CACHE.clear()  # Reset the module cache for a deterministic test.
    _LOGGER.debug("Safe value cache entries=%s", len(privacy._SAFE_VALUE_CACHE))  # Log the reset result.
    _LOGGER.info("Filling the safe value cache past its limit")  # Log the fill action.
    for index in range(privacy._SAFE_VALUE_CACHE_LIMIT + 1):  # Add one more value than the limit.
        privacy.scrub_value(f"safe_value_{index}")  # Add a safe value that enters the cache.
    _LOGGER.debug("Safe value cache entries=%s", len(privacy._SAFE_VALUE_CACHE))  # Log the final size.
    assert len(privacy._SAFE_VALUE_CACHE) == 1  # Prove the clear-at-limit policy ran.
    assert len(privacy._SAFE_VALUE_CACHE) <= privacy._SAFE_VALUE_CACHE_LIMIT  # Prove the bound holds.
    privacy._SAFE_VALUE_CACHE.clear()  # Leave the module cache empty for the next test.


def _row(report: dict[str, object], scenario: str) -> dict[str, int]:
    """Return one scenario row from a harness report."""
    _LOGGER.info("Selecting memory scenario %s", scenario)  # Log the lookup action.
    rows = report["results"]  # Read the measured rows from the report.
    for row in rows:  # Search the small scenario list.
        if row["scenario"] == scenario:  # Match the requested scenario name.
            _LOGGER.debug("Found memory scenario %s", scenario)  # Log the lookup result.
            return row  # Return the row for the assertion.
    raise AssertionError(f"Missing memory scenario {scenario}")  # Fail clearly if the harness changed.
