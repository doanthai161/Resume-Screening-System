import asyncio
import threading

import pytest

from app.core.monitoring import monitoring, MetricType
from app.core.password_work import run_password_work
from app.core.security import get_password_hash_async, verify_password_async


@pytest.mark.asyncio
async def test_password_roundtrip():
    hashed = await get_password_hash_async("ResourceTestPassword1")
    assert await verify_password_async("ResourceTestPassword1", hashed)
    assert not await verify_password_async("wrong", hashed)
    assert not await verify_password_async("wrong", "invalid-hash")


@pytest.mark.asyncio
async def test_password_cancellation_keeps_capacity_until_worker_finishes():
    release = threading.Event()
    lock = threading.Lock()
    started = 0

    def work():
        nonlocal started
        with lock:
            started += 1
        assert release.wait(5)
        return 1

    tasks = [asyncio.create_task(run_password_work(work)) for _ in range(2)]
    try:
        async with asyncio.timeout(3):
            while started != 2:
                await asyncio.sleep(0.01)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        tasks.append(asyncio.create_task(run_password_work(work)))
        await asyncio.sleep(0.05)  # The event loop stays responsive while workers block.
        assert started == 2
        release.set()
        assert await tasks[1] == 1
        assert await tasks[2] == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_password_worker_error_releases_capacity():
    def fail():
        raise ValueError("worker failure")

    for _ in range(3):
        with pytest.raises(ValueError, match="worker failure"):
            await run_password_work(fail)
    assert await asyncio.wait_for(run_password_work(lambda: 42), 2) == 42


def test_metrics_bound_samples_cardinality_and_snapshot():
    monitoring.clear_metrics()
    try:
        for i in range(10000):
            monitoring.record_metric("latency", i, MetricType.HISTOGRAM)
            monitoring.record_metric("requests", tags={"id": str(i)})
        snapshot = monitoring.get_metrics()
        assert len(snapshot) == monitoring.MAX_SERIES
        assert snapshot["latency_"] == list(range(10000 - monitoring.MAX_SAMPLES, 10000))
        snapshot["latency_"].clear()
        assert len(monitoring.get_metrics()["latency_"]) == monitoring.MAX_SAMPLES
        monitoring.record_metric("latency", 1, MetricType.COUNTER)
        assert len(monitoring.get_metrics()["latency_"]) == monitoring.MAX_SAMPLES
    finally:
        monitoring.clear_metrics()


def test_metrics_reject_oversized_keys_and_nonfinite_values():
    monitoring.clear_metrics()
    try:
        monitoring.record_metric("x" * 2000)
        monitoring.record_metric("x", tags={"id": "x" * 2000})
        monitoring.record_metric("x", float("nan"))
        monitoring.record_metric("x", float("inf"))
        assert monitoring.get_metrics() == {}
        monitoring.record_metric("count", 2)
        monitoring.record_metric("count", 3)
        assert monitoring.get_metrics()["count_"] == 5
    finally:
        monitoring.clear_metrics()
