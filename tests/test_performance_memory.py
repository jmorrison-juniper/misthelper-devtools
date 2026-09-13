"""Tests for the performance memory harness.

Why:
    The tests prove that the measured objects obey the memory bounds.
"""

from __future__ import annotations  # Keep annotations lazy during test collection.

import logging  # Report test actions before and after they run.

from src.utils.performance import privacy  # Check the safe value cache bound.
from src.utils.performance.sink import BoundedSink  # Check the queue capacity bound.
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
