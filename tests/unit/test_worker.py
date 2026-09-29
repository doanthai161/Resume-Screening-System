import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.core import job_queue
from app.core.config import settings
from app.workers.adapters import load_adapter
from app.workers.runtime import Worker, GROUP

pytestmark = pytest.mark.asyncio


@pytest.fixture
def runtime(monkeypatch):
    run = SimpleNamespace(id=ObjectId(), company_id=ObjectId(), attempt=2)
    adapter = SimpleNamespace(parse=AsyncMock(return_value={"parsed_data": {"skills": ["Python"]}}),
                              screen=AsyncMock(return_value={"criteria_evaluations": {}}), aclose=AsyncMock())
    ack = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", ack)
    return run, adapter, ack


def setup_worker(queue, run, adapter):
    worker = Worker(queue, adapter)
    worker.model = SimpleNamespace(find_one=AsyncMock(return_value=run))
    worker.claim = AsyncMock(return_value=run)
    worker.renew = AsyncMock(return_value=True)
    worker.complete = AsyncMock(return_value=object())
    worker.fail = AsyncMock(return_value=True)
    return worker


def fields(run):
    return {"resource_id": str(run.id), "company_id": str(run.company_id)}


@pytest.mark.parametrize("queue", ["resume-parse", "screening"])
async def test_worker_completes_before_ack_with_generation(runtime, queue):
    run, adapter, ack = runtime
    worker = setup_worker(queue, run, adapter)

    async def complete(*args, **kwargs):
        ack.assert_not_awaited()
        assert kwargs["generation"] == 2
        return True

    worker.complete.side_effect = complete
    await worker.handle("1-0", fields(run))
    worker.complete.assert_awaited_once()
    worker.fail.assert_not_awaited()
    ack.assert_awaited_once_with(queue, GROUP, "1-0")


@pytest.mark.parametrize("problem,code", [("exception", "worker_adapter_error"), ("schema", "worker_invalid_output"), ("timeout", "worker_timeout")])
async def test_adapter_failure_uses_sanitized_failure_and_ack(runtime, monkeypatch, problem, code):
    run, adapter, ack = runtime
    if problem == "exception":
        adapter.parse.side_effect = RuntimeError("secret token in exception")
    elif problem == "schema":
        adapter.parse.return_value = {"parsed_data": {"skills": "invalid"}}
    else:
        monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", .01)
        adapter.parse.side_effect = lambda *_: None

        async def blocked(*_):
            await asyncio.Event().wait()
        adapter.parse.side_effect = blocked
    worker = setup_worker("resume-parse", run, adapter)
    await worker.handle("1-0", fields(run))
    worker.complete.assert_not_awaited()
    assert worker.fail.await_args.args[2:] == (code, "Worker processing failed")
    assert worker.fail.await_args.kwargs == {"generation": 2}
    ack.assert_awaited_once()


@pytest.mark.parametrize("failure", [False, RuntimeError("DB unavailable")])
async def test_lease_loss_cancels_adapter_without_commit_or_ack(runtime, monkeypatch, failure):
    run, adapter, ack = runtime
    monkeypatch.setattr(settings, "WORKER_HEARTBEAT_SECONDS", .01)
    cancelled = asyncio.Event()

    async def blocked(*_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    adapter.parse.side_effect = blocked
    worker = setup_worker("resume-parse", run, adapter)
    worker.renew = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=False)
    await asyncio.wait_for(worker.handle("1-0", fields(run)), 1)
    assert cancelled.is_set()
    worker.complete.assert_not_awaited()
    worker.fail.assert_not_awaited()
    ack.assert_not_awaited()


async def test_shutdown_cancellation_leaves_delivery_pending(runtime):
    run, adapter, ack = runtime
    started = asyncio.Event()

    async def blocked(*_):
        started.set()
        await asyncio.Event().wait()

    adapter.parse.side_effect = blocked
    worker = setup_worker("resume-parse", run, adapter)
    task = asyncio.create_task(worker.handle("1-0", fields(run)))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    worker.complete.assert_not_awaited()
    worker.fail.assert_not_awaited()
    ack.assert_not_awaited()


async def test_unknown_commit_outcome_is_not_turned_into_adapter_failure(runtime):
    run, adapter, ack = runtime
    worker = setup_worker("screening", run, adapter)
    worker.complete.side_effect = RuntimeError("connection lost during commit")
    with pytest.raises(RuntimeError):
        await worker.handle("1-0", fields(run))
    worker.fail.assert_not_awaited()
    ack.assert_not_awaited()


@pytest.mark.parametrize("status", [422, 409])
async def test_completion_validation_and_conflict_are_distinct(runtime, status):
    from app.core.errors import CustomError, ErrorCodes
    run, adapter, ack = runtime
    worker = setup_worker("screening", run, adapter)
    worker.complete.side_effect = CustomError(ErrorCodes.VALIDATION, "failure", status)
    if status == 409:
        with pytest.raises(CustomError):
            await worker.handle("1-0", fields(run))
        worker.fail.assert_not_awaited()
        ack.assert_not_awaited()
    else:
        await worker.handle("1-0", fields(run))
        assert worker.fail.await_args.args[2] == "worker_invalid_output"
        ack.assert_awaited_once()


async def test_heartbeat_renews_current_attempt_while_work_is_running(runtime, monkeypatch):
    run, adapter, ack = runtime
    monkeypatch.setattr(settings, "WORKER_HEARTBEAT_SECONDS", .01)
    renewed = asyncio.Event()

    async def renew(*args, **kwargs):
        assert kwargs["generation"] == run.attempt
        renewed.set()
        return True

    async def execute(_):
        await asyncio.wait_for(renewed.wait(), 1)
        return {"parsed_data": {}}

    adapter.parse.side_effect = execute
    worker = setup_worker("resume-parse", run, adapter)
    worker.renew.side_effect = renew
    await worker.handle("1-0", fields(run))
    assert worker.renew.await_count >= 1
    worker.complete.assert_awaited_once()
    ack.assert_awaited_once()


@pytest.mark.parametrize("problem", ["malformed", "wrong_tenant", "duplicate"])
async def test_bad_or_duplicate_delivery_does_not_execute_adapter(runtime, problem):
    run, adapter, ack = runtime
    worker = setup_worker("resume-parse", run, adapter)
    payload = fields(run)
    if problem == "malformed":
        payload["resource_id"] = {"$ne": None}
    elif problem == "wrong_tenant":
        worker.model.find_one.return_value = None
    else:
        worker.claim.return_value = None
    await worker.handle("1-0", payload)
    adapter.parse.assert_not_awaited()
    ack.assert_awaited_once()


async def test_reclaim_uses_cursor_and_idle_threshold(monkeypatch):
    redis = SimpleNamespace(xgroup_create=AsyncMock(), xautoclaim=AsyncMock(return_value=["42-0", [("1-0", {})], []]))
    monkeypatch.setattr(job_queue, "get_redis", lambda: redis)
    cursor, items = await job_queue.reclaim("resume-parse", GROUP, "worker", "20-0")
    assert cursor == "42-0" and items == [("1-0", {})]
    assert redis.xautoclaim.await_args.kwargs == {
        "start_id": "20-0", "count": 1, "min_idle_time": settings.QUEUE_REDELIVERY_SECONDS * 1000,
    }


async def test_no_adapter_fails_before_connecting(monkeypatch):
    from app import worker as entrypoint
    monkeypatch.setattr(settings, "WORKER_ADAPTER_FACTORY", "")
    db = AsyncMock()
    monkeypatch.setattr(entrypoint, "AsyncIOMotorClient", db)
    with pytest.raises(ValueError, match="WORKER_ADAPTER_FACTORY"):
        await entrypoint.serve("resume-parse")
    db.assert_not_called()


async def test_sync_adapter_is_rejected(monkeypatch):
    from app.workers import adapters
    monkeypatch.setattr(adapters.importlib, "import_module", lambda _: SimpleNamespace(factory=lambda: SimpleNamespace(parse=lambda _: {}, aclose=AsyncMock())))
    with pytest.raises(ValueError, match="async parse"):
        load_adapter("trusted:factory", "resume-parse")


@pytest.mark.parametrize("startup_failure", [False, True])
async def test_worker_entrypoint_closes_resources_on_stop_or_startup_failure(monkeypatch, startup_failure):
    from app import worker as entrypoint
    adapter = SimpleNamespace(aclose=AsyncMock())
    mongo = MagicMock()
    redis = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    if startup_failure:
        redis.ping.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(entrypoint, "load_adapter", lambda *_: adapter)
    monkeypatch.setattr(entrypoint, "AsyncIOMotorClient", lambda *_, **__: mongo)
    monkeypatch.setattr(entrypoint.Redis, "from_url", lambda *_, **__: redis)
    monkeypatch.setattr(entrypoint, "require_transaction_topology", AsyncMock())
    monkeypatch.setattr(entrypoint, "init_beanie", AsyncMock())
    monkeypatch.setattr(entrypoint.signal, "signal", MagicMock())
    monkeypatch.setattr(entrypoint, "maintain", AsyncMock())

    async def finish(stop):
        stop.set()
    monkeypatch.setattr(entrypoint, "Worker", lambda *_: SimpleNamespace(worker_id="test", run=finish))
    if startup_failure:
        with pytest.raises(RuntimeError):
            await entrypoint.serve("resume-parse")
    else:
        await asyncio.wait_for(entrypoint.serve("resume-parse"), 1)
    adapter.aclose.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    mongo.close.assert_called_once()
    assert entrypoint.redis_state.redis_client is None
