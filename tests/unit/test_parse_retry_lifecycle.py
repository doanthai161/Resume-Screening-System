"""Exercise Mongo query/state behavior with mongomock, outside transactions.

Replica-set transaction guarantees are covered by the integration suite.
"""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.core import job_queue
from app.core.config import settings
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_run import ResumeParseRun, ProcessingStatus
from app.schemas.worker import ParseOutput
from app.services.processing_service import ProcessingService
from app.utils.time import now_utc, ensure_utc

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def lifecycle(monkeypatch):
    clock = [now_utc()]
    monkeypatch.setattr("app.services.processing_service.now_utc", lambda: clock[0])
    monkeypatch.setattr("app.utils.time.now_utc", lambda: clock[0])
    enqueue = AsyncMock(return_value="1-0")
    monkeypatch.setattr(job_queue, "enqueue", enqueue)
    resume = await ResumeFile(
        company_id=ObjectId(), filename="cv.pdf", original_filename="cv.pdf",
        file_path="uploads/resumes/cv.pdf", file_size=1, mime_type="application/pdf",
        checksum="a" * 64, uploader_id=ObjectId(),
    ).insert()
    run = await ResumeParseRun(
        company_id=resume.company_id, resume_file_id=resume.id, triggered_by=resume.uploader_id,
        idempotency_key="retry-lifecycle", input_hash="b" * 64, parser_version="v1",
    ).insert()
    return run, resume, clock, enqueue


claim = ProcessingService.claim_parse.__wrapped__
fail = ProcessingService.fail_parse.__wrapped__
recover = ProcessingService.recover_expired_leases.__wrapped__
complete = ProcessingService.complete_parse.__wrapped__


async def test_repeated_circuit_deferrals_preserve_budget_and_fence_old_workers(lifecycle):
    run, resume, clock, enqueue = lifecycle
    for generation in range(1, 5):
        claimed = await claim(str(run.id), "same-worker")
        assert claimed.attempt == generation
        assert claimed.attempt - claimed.deferred_attempts == 1
        assert await fail(str(run.id), "same-worker", "mineru_circuit_open", "Waiting",
                          generation=generation, deferred=True)
        stored = await ResumeParseRun.get(run.id)
        assert stored.status == ProcessingStatus.QUEUED and not stored.is_terminal
        assert stored.deferred_attempts == generation
        assert ensure_utc(stored.next_retry_at) > clock[0]
        assert not await claim(str(run.id), "early-worker")
        await recover()
        await job_queue.publish_committed("resume-parse", str(run.id), str(run.company_id))
        enqueue.assert_not_awaited()
        clock[0] += timedelta(seconds=settings.MINERU_CIRCUIT_OPEN_SECONDS + 1)
        await recover()
        enqueue.assert_awaited_once()
        enqueue.reset_mock()

    claimed = await claim(str(run.id), "same-worker")
    assert claimed.attempt == 5
    output = ParseOutput(parsed_data=ParsedResumeData(raw_text="Recovered CV"))
    assert not await complete(str(run.id), "same-worker", output, generation=1)
    assert not await fail(str(run.id), "same-worker", "stale", "stale", generation=1)
    assert await complete(str(run.id), "same-worker", output, generation=5)
    stored = await ResumeParseRun.get(run.id)
    assert stored.status == ProcessingStatus.COMPLETED
    assert stored.next_retry_at is None and stored.error_code is None
    assert (await ResumeFile.get(resume.id)).status == "parsed"


async def test_lost_resource_retries_back_off_and_stop_at_budget(lifecycle):
    run, resume, clock, enqueue = lifecycle
    delays = []
    for generation in range(1, 4):
        claimed = await claim(str(run.id), "worker")
        assert claimed is not None
        assert await fail(str(run.id), "worker", "mineru_resource_lost", "Lost",
                          generation=generation)
        stored = await ResumeParseRun.get(run.id)
        enqueue.assert_not_awaited()
        if generation < 3:
            assert not stored.is_terminal
            assert not await claim(str(run.id), "early")
            delays.append((ensure_utc(stored.next_retry_at) - clock[0]).total_seconds())
            clock[0] = ensure_utc(stored.next_retry_at) + timedelta(seconds=1)
        else:
            assert stored.is_terminal and stored.status == ProcessingStatus.FAILED
            assert stored.next_retry_at is None
            assert not await claim(str(run.id), "exhausted")
    assert delays[1] > delays[0] > 0
    assert (await ResumeFile.get(resume.id)).status == "error"


async def test_expired_lease_recovery_uses_effective_budget_after_deferrals(lifecycle):
    run, _, clock, enqueue = lifecycle
    collection = ResumeParseRun.get_motor_collection()
    await collection.update_one({"_id": run.id}, {"$set": {
        "status": "running", "attempt": 5, "deferred_attempts": 4,
        "worker_id": "dead-worker", "lease_expires_at": clock[0] - timedelta(seconds=1),
    }})
    await recover()
    stored = await ResumeParseRun.get(run.id)
    assert stored.status == ProcessingStatus.QUEUED and not stored.is_terminal
    assert await claim(str(run.id), "replacement")
    enqueue.reset_mock()
    await collection.update_one({"_id": run.id}, {"$set": {
        "status": "running", "attempt": 7,
        "lease_expires_at": clock[0] - timedelta(seconds=1),
    }})
    await recover()
    assert (await ResumeParseRun.get(run.id)).status == ProcessingStatus.FAILED
    enqueue.assert_not_awaited()


async def test_old_run_without_scheduling_fields_can_still_be_claimed(lifecycle):
    run, _, _, _ = lifecycle
    await ResumeParseRun.get_motor_collection().update_one(
        {"_id": run.id}, {"$unset": {"deferred_attempts": "", "next_retry_at": ""}}
    )
    claimed = await claim(str(run.id), "worker")
    assert claimed and claimed.attempt == 1 and claimed.deferred_attempts == 0
