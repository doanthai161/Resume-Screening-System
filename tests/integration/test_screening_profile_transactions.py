"""Screening source snapshots and races on an explicitly configured test replica set."""
import json
import os
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import SecretStr
import pytest

from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, issue_token_pair
from app.main import app
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.models.ai_model import AIModel
from app.models.application_stage_event import ApplicationStageEvent
from app.models.candidate import Candidate
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.job_application import JobApplication, ApplicationStage
from app.models.job_requirement import JobRequirement
from app.models.job_scorecard import JobScorecard
from app.models.permission import Permission
from app.models.resume_extraction import ResumeProfile, PROMPT_VERSION, EXTRACTOR_VERSION
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ScreeningRun
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.schemas.recruitment import ScreeningStartRequest
from app.schemas.screening_http import ScreeningServiceRequest, ScreeningServiceResponse
from app.schemas.worker import ScreeningOutput
from app.services.processing_service import ProcessingService
from app.services.recruitment_service import ScreeningService
from app.workers.runtime import Worker, GROUP
from app.workers.screening_adapter import HTTPScreeningAdapter
from app.utils.time import now_utc, ensure_utc

pytestmark = pytest.mark.asyncio
CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (CONTRACTS / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "screening_profile_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SCREENING_API_URL", "http://screening-service:8003")
    monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr("synthetic-screening-token-" + "x" * 32))
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("screening_profile_test_")
        await client.drop_database(name)
        client.close()


async def create_profile(company, resume, *, version=1, revision=1):
    return await ResumeProfile(
        company_id=company.id, resume_file_id=resume.id, extraction_run_id=ObjectId(),
        version=version, source_parse_revision=revision, model_name="synthetic-extraction-model",
        prompt_version=PROMPT_VERSION, extractor_version=EXTRACTOR_VERSION,
        profile=json.loads((CONTRACTS / "resume-extraction-v1.example.json").read_text(encoding="utf-8")),
        review_required=True,
    ).insert()


async def data():
    key = uuid4().hex
    owner = await User(email=key + "@example.com", hashed_password="unused", is_verified=True).insert()
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branch = await CompanyBranch(company_id=company.id, branch_name="Hiring", bussiness_type="IT",
                                 address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert()
    job = await JobRequirement(user_id=owner.id, company_branch_id=branch.id, title="Backend",
                               experience_level="junior").insert()
    candidate = await Candidate(company_id=company.id, full_name="Synthetic candidate", created_by=owner.id).insert()
    resume = await ResumeFile(
        company_id=company.id, company_branch_id=branch.id, candidate_id=candidate.id,
        filename="synthetic.pdf", original_filename="synthetic.pdf", file_path="unused", file_size=100,
        mime_type="application/pdf", uploader_id=owner.id, checksum="a" * 64,
        status="parsed", parse_revision=1, parsed_data=ParsedResumeData(raw_text=SOURCE),
    ).insert()
    profile = await create_profile(company, resume)
    await resume.set({"latest_extraction_profile_id": profile.id, "extraction_revision": 1})
    application = await JobApplication(
        company_id=company.id, company_branch_id=branch.id, candidate_id=candidate.id,
        resume_file_id=resume.id, job_requirement_id=job.id, applied_by=owner.id,
    ).insert()
    scorecard = await JobScorecard(
        company_id=company.id, job_requirement_id=job.id, created_by=owner.id,
        criteria=[{"code": "python", "name": "Python", "weight": 1}], pass_threshold=70,
    ).insert()
    model = await AIModel(name=key, provider="synthetic", model_type="scoring", model_id=key,
                          config={"model_name": "synthetic-screening-model"}, created_by=owner.id).insert()
    current = CurrentUser(owner, permission_names={"screening_runs:create", "resume_profiles:view"})
    request = ScreeningStartRequest(company_id=str(company.id), application_id=str(application.id), ai_model_id=str(model.id))
    return SimpleNamespace(owner=owner, company=company, branch=branch, job=job, candidate=candidate,
                           resume=resume, profile=profile, application=application, scorecard=scorecard,
                           model=model, current=current, request=request)


async def grant(user, names):
    actor = await Actor(name=uuid4().hex, is_active=True).insert()
    await UserActor(user_id=user.id, actor_id=actor.id, created_by=user.id).insert()
    for name in names:
        permission = await Permission.find_one({"name": name})
        if not permission:
            permission = await Permission(name=name, is_active=True).insert()
        await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()


def output():
    offset = SOURCE.index("Python")
    return ScreeningOutput(criteria_evaluations={"python": {
        "status": "evaluated", "score": 80, "reason": "CV lists Python",
        "evidence": [{"text": "Python", "start": offset, "end": offset + 6}],
    }})


async def start_and_claim():
    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    return item, run, claimed


def screening_http_response(payload):
    request = ScreeningServiceRequest.model_validate(payload)
    return ScreeningServiceResponse(
        contract_version="1.0", run_id=request.run_id, input_hash=request.input_hash,
        source_text_sha256=request.source.source_text_sha256,
        resume_profile_id=request.source.resume_profile_id, profile_version=request.source.profile_version,
        scorecard_id=request.scorecard.scorecard_id, scorecard_version=request.scorecard.version,
        model=request.model, prompt_version=request.prompt_version, output=output(),
    ).model_dump(mode="json")


async def test_http_screening_worker_uses_frozen_model_scorecard_and_verified_profile(monkeypatch):
    from app.core import job_queue

    item = await data()
    config_secret = "must-not-send-arbitrary-model-config-secret"
    await item.model.set({"config": {"model_name": "ignored-config-name", "api_key": config_secret}})
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    frozen_model = {"ai_model_id": str(item.model.id), "provider": item.model.provider,
                    "model_id": item.model.model_id, "version": item.model.version}
    await item.model.set({"provider": "changed-provider", "model_id": "changed-live-model", "version": "2.0",
                          "config": {"api_key": "must-not-send-new-live-secret"}})
    await item.scorecard.set({"version": 2, "criteria": [{"code": "new_live_criterion", "name": "Changed", "weight": 1}]})
    calls = []

    def model(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.url.path == "/v1/screen"
        assert request.headers["Idempotency-Key"] == str(run.id)
        assert request.headers["Authorization"] == "Bearer " + settings.SCREENING_SERVICE_TOKEN.get_secret_value()
        assert payload["model"] == frozen_model
        assert payload["prompt_version"] == "screening-v1"
        assert payload["source"]["source_text"] == SOURCE
        assert payload["source"]["resume_profile_id"] == str(item.profile.id)
        assert payload["scorecard"]["version"] == 1
        assert payload["scorecard"]["criteria"][0]["code"] == "python"
        assert "model_config" not in payload and config_secret not in request.content.decode()
        assert "must-not-send-new-live-secret" not in request.content.decode()
        return httpx.Response(200, json=screening_http_response(payload))

    acknowledge = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        await worker.handle("http-screen", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    stored = await ScreeningRun.get(run.id)
    assert len(calls) == 1 and stored.status == "completed"
    result = await ScreeningResult.get(stored.result_id)
    assert result.overall_score == 80 and result.evidence_coverage == 100 and result.decision == "pass"
    assert result.source_snapshot == run.source_snapshot
    assert result.scorecard_snapshot["version"] == 1
    evidence = result.criteria_evaluations["python"].evidence[0]
    assert SOURCE[evidence.start:evidence.end] == evidence.text == "Python"
    acknowledge.assert_awaited_once_with("screening", GROUP, "http-screen")


@pytest.mark.parametrize("field", ["run_id", "input_hash"])
async def test_http_response_from_another_run_is_terminal_without_writing_a_result(monkeypatch, field):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    calls = []

    def model(request):
        calls.append(request)
        response = screening_http_response(json.loads(request.content))
        response[field] = str(ObjectId()) if field == "run_id" else "0" * 64
        return httpx.Response(200, json=response)

    acknowledge = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        await worker.handle("wrong-binding", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    stored = await ScreeningRun.get(run.id)
    assert len(calls) == 1 and stored.status == "failed" and stored.is_terminal and stored.attempt == 1
    assert await ScreeningResult.find_all().count() == 0
    assert (await JobApplication.get(item.application.id)).latest_screening_result_id is None
    acknowledge.assert_awaited_once_with("screening", GROUP, "wrong-binding")


async def test_http_adapter_rejects_fabricated_quote_before_completion(monkeypatch):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)

    def model(request):
        response = screening_http_response(json.loads(request.content))
        response["output"]["criteria_evaluations"]["python"]["evidence"] = [
            {"text": "Invented technology", "start": 0, "end": 19}
        ]
        return httpx.Response(200, json=response)

    monkeypatch.setattr(job_queue, "acknowledge", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        await worker.handle("fabricated-evidence", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    stored = await ScreeningRun.get(run.id)
    assert stored.status == "failed" and stored.is_terminal
    assert await ScreeningResult.find_all().count() == 0
    assert (await JobApplication.get(item.application.id)).latest_screening_result_id is None


async def test_http_transient_failure_retries_same_run_with_same_frozen_payload(monkeypatch):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    calls = []

    def model(request):
        calls.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        if len(calls) == 1:
            return httpx.Response(503, text="private upstream failure")
        return httpx.Response(200, json=screening_http_response(calls[-1][1]))

    acknowledge = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        fields = {"resource_id": str(run.id), "company_id": str(run.company_id)}
        await worker.handle("transient-first", fields)
        failed_attempt = await ScreeningRun.get(run.id)
        assert failed_attempt.status == "queued" and not failed_attempt.is_terminal and failed_attempt.attempt == 1
        assert failed_attempt.next_retry_at is not None and ensure_utc(failed_attempt.next_retry_at) > now_utc()
        assert await ScreeningResult.find_all().count() == 0
        assert await ProcessingService.claim_screening(str(run.id), "too-early") is None
        await ScreeningRun.get_motor_collection().update_one(
            {"_id": run.id}, {"$set": {"next_retry_at": now_utc() - timedelta(seconds=1)}}
        )
        await worker.handle("transient-retry", fields)

    completed = await ScreeningRun.get(run.id)
    assert completed.status == "completed" and completed.attempt == 2
    assert completed.next_retry_at is None
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][0] == str(run.id)
    assert await ScreeningResult.find_all().count() == 1
    assert acknowledge.await_count == 2


@pytest.mark.parametrize("status", [401, 422])
async def test_http_permanent_rejection_is_not_retried_or_stored_as_result(monkeypatch, caplog, status):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    calls = []

    def model(request):
        calls.append(request)
        return httpx.Response(status, text="private upstream CV or credential")

    monkeypatch.setattr(job_queue, "acknowledge", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        await worker.handle("permanent-rejection", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    stored = await ScreeningRun.get(run.id)
    assert len(calls) == 1 and stored.status == "failed" and stored.is_terminal and stored.attempt == 1
    assert await ScreeningResult.find_all().count() == 0
    assert "private upstream" not in stored.error_message and "private upstream" not in caplog.text


async def test_http_commit_then_ack_failure_does_not_repeat_inference_on_redelivery(monkeypatch):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    calls = []

    def model(request):
        calls.append(request)
        return httpx.Response(200, json=screening_http_response(json.loads(request.content)))

    acknowledge = AsyncMock(side_effect=RuntimeError("Redis ACK unavailable after commit"))
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        fields = {"resource_id": str(run.id), "company_id": str(run.company_id)}
        with pytest.raises(RuntimeError, match="ACK unavailable"):
            await worker.handle("ack-redelivery", fields)
        assert (await ScreeningRun.get(run.id)).status == "completed"
        assert await ScreeningResult.find_all().count() == 1
        acknowledge.side_effect = None
        await worker.handle("ack-redelivery", fields)

    assert len(calls) == 1 and await ScreeningResult.find_all().count() == 1
    assert (await ScreeningRun.get(run.id)).attempt == 1
    assert acknowledge.await_count == 2


async def test_http_timeout_is_retryable_and_never_persists_sensitive_error(monkeypatch, caplog):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)

    def model(request):
        raise httpx.ReadTimeout("private CV and secret token in network error", request=request)

    monkeypatch.setattr(job_queue, "acknowledge", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        worker = Worker("screening", HTTPScreeningAdapter(client, settings.SCREENING_API_URL,
                                                          settings.SCREENING_SERVICE_TOKEN.get_secret_value()))
        await worker.handle("http-timeout", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    stored = await ScreeningRun.get(run.id)
    assert stored.status == "queued" and not stored.is_terminal and stored.attempt == 1
    assert await ScreeningResult.find_all().count() == 0
    assert "private CV" not in caplog.text and "private CV" not in stored.error_message


async def test_retry_waits_until_deadline_and_maintenance_publishes_durable_run_after_redis_recovers(monkeypatch):
    from app.core import job_queue

    item, run, claimed = await start_and_claim()
    now = now_utc()
    clock = [now.replace(microsecond=(now.microsecond // 1000) * 1000)]
    monkeypatch.setattr("app.services.processing_service.now_utc", lambda: clock[0])
    monkeypatch.setattr("app.utils.time.now_utc", lambda: clock[0])
    redis = SimpleNamespace(xadd=AsyncMock(return_value="123-0"))
    monkeypatch.setattr(job_queue, "get_redis", lambda: redis)

    assert await ProcessingService.fail_screening(
        str(run.id), "worker", "screening_service_unavailable", "Worker processing failed", generation=claimed.attempt
    )
    queued = await ScreeningRun.get(run.id)
    assert queued.status == "queued" and ensure_utc(queued.next_retry_at) == clock[0] + timedelta(seconds=5)
    from app.api.recruitment import _run_response
    response = await _run_response(queued)
    assert response.next_retry_at == ensure_utc(queued.next_retry_at)
    assert response.next_retry_at.tzinfo is timezone.utc
    assert response.model_dump(mode="json")["next_retry_at"].endswith("Z")
    assert queued.queue_message_id is None and queued.last_enqueued_at is None
    redis.xadd.assert_not_awaited()
    assert await ProcessingService.claim_screening(str(run.id), "too-early") is None
    await job_queue.publish_committed("screening", str(run.id), str(run.company_id))
    await ProcessingService.recover_expired_leases()
    redis.xadd.assert_not_awaited()

    clock[0] = ensure_utc(queued.next_retry_at)
    redis.xadd.side_effect = RuntimeError("test Redis temporarily unavailable")
    await ProcessingService.recover_expired_leases()
    after_outage = await ScreeningRun.get(run.id)
    assert after_outage.status == "queued" and after_outage.last_enqueued_at is None
    assert after_outage.idempotency_key == run.idempotency_key and after_outage.attempt == 1
    redis.xadd.side_effect = None
    await ProcessingService.recover_expired_leases()
    published = await ScreeningRun.get(run.id)
    assert published.queue_message_id == "123-0" and ensure_utc(published.last_enqueued_at) == clock[0]
    assert redis.xadd.await_count == 2
    assert redis.xadd.await_args.args[1] == {"resource_id": str(run.id), "company_id": str(run.company_id)}
    await ProcessingService.recover_expired_leases()
    assert redis.xadd.await_count == 2

    fresh = await ProcessingService.claim_screening(str(run.id), "fresh")
    assert fresh is not None and fresh.attempt == 2 and fresh.next_retry_at is None
    result = await ProcessingService.complete_screening(str(run.id), "fresh", output(), generation=fresh.attempt)
    completed = await ScreeningRun.get(run.id)
    assert completed.status == "completed" and completed.next_retry_at is None
    assert completed.idempotency_key == run.idempotency_key and completed.result_id == result.id
    assert await ScreeningRun.find_all().count() == 1 and await ScreeningResult.find_all().count() == 1


@pytest.mark.parametrize("max_attempts,delays", [(3, [5, 10]), (6, [5, 10, 20, 40, 60])])
async def test_retry_backoff_preserves_bounded_attempt_budget_and_original_key(monkeypatch, max_attempts, delays):
    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    await run.set({"max_attempts": max_attempts})
    now = now_utc()
    clock = [now.replace(microsecond=(now.microsecond // 1000) * 1000)]
    monkeypatch.setattr("app.services.processing_service.now_utc", lambda: clock[0])

    for attempt in range(1, max_attempts + 1):
        claimed = await ProcessingService.claim_screening(str(run.id), "worker")
        assert claimed is not None and claimed.attempt == attempt and claimed.next_retry_at is None
        assert await ProcessingService.fail_screening(
            str(run.id), "worker", "screening_service_unavailable", "Worker processing failed", generation=attempt
        )
        stored = await ScreeningRun.get(run.id)
        assert stored.idempotency_key == run.idempotency_key and stored.attempt == attempt
        if attempt == max_attempts:
            assert stored.status == "failed" and stored.is_terminal and stored.next_retry_at is None
        else:
            assert stored.status == "queued" and not stored.is_terminal
            assert ensure_utc(stored.next_retry_at) == clock[0] + timedelta(seconds=delays[attempt - 1])
            assert await ProcessingService.claim_screening(str(run.id), "too-early") is None
            clock[0] = ensure_utc(stored.next_retry_at)
    assert await ProcessingService.claim_screening(str(run.id), "extra-attempt") is None
    assert await ScreeningRun.find_all().count() == 1 and await ScreeningResult.find_all().count() == 0


async def test_stale_lease_cannot_schedule_or_replace_a_successor_retry():
    item, run, old = await start_and_claim()
    collection = ScreeningRun.get_motor_collection()
    await collection.update_one({"_id": run.id}, {"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}})
    before = await collection.find_one({"_id": run.id})
    assert not await ProcessingService.fail_screening(str(run.id), "worker", "timeout", "stale", generation=old.attempt)
    assert await collection.find_one({"_id": run.id}) == before
    await ProcessingService.recover_expired_leases()
    fresh = await ProcessingService.claim_screening(str(run.id), "fresh")
    assert fresh is not None and fresh.attempt == old.attempt + 1
    assert await ProcessingService.fail_screening(str(run.id), "fresh", "timeout", "safe", generation=fresh.attempt)
    scheduled = await collection.find_one({"_id": run.id})
    assert ensure_utc(scheduled["next_retry_at"]) > now_utc()
    assert not await ProcessingService.fail_screening(str(run.id), "worker", "stale", "stale", generation=old.attempt)
    assert await collection.find_one({"_id": run.id}) == scheduled
    assert await ProcessingService.claim_screening(str(run.id), "too-early") is None
    assert await ScreeningResult.find_all().count() == 0


async def test_legacy_queued_run_without_retry_deadline_remains_claimable():
    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    await ScreeningRun.get_motor_collection().update_one({"_id": run.id}, {"$unset": {"next_retry_at": ""}})
    assert (await ScreeningRun.get(run.id)).next_retry_at is None
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    assert claimed is not None and claimed.attempt == 1 and claimed.next_retry_at is None


@pytest.mark.parametrize("access", ["allowed", "no_screening_permission", "no_profile_permission", "other_company", "other_application_branch", "other_resume_branch", "expired_membership"])
async def test_http_start_requires_profile_permission_and_both_resource_branches(access):
    from datetime import timedelta
    from app.utils.time import now_utc

    item = await data()
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    membership_branch = item.branch
    if access == "other_company":
        company = await Company(user_id=caller.id, name="Other", company_short_name="O", company_code=uuid4().hex,
                                email=caller.email, website="https://example.com").insert()
        membership_branch = await CompanyBranch(company_id=company.id, branch_name="Other", bussiness_type="IT",
                                                address="Other", company_size=1, working_days=[], created_by=caller.id).insert()
    elif access in {"other_application_branch", "other_resume_branch"}:
        other = await CompanyBranch(company_id=item.company.id, branch_name="Other", bussiness_type="IT",
                                    address="Other", company_size=1, working_days=[], created_by=item.owner.id).insert()
        target = item.application if access == "other_application_branch" else item.resume
        await target.set({"company_branch_id": other.id})
    await UserCompany(user_id=caller.id, company_branch_id=membership_branch.id, role="recruiter",
                      end_date=now_utc() - timedelta(seconds=1) if access == "expired_membership" else None,
                      assigned_by=item.owner.id).insert()
    permissions = {"screening_runs:create", "resume_profiles:view"}
    if access == "no_screening_permission":
        permissions.remove("screening_runs:create")
    elif access == "no_profile_permission":
        permissions.remove("resume_profiles:view")
    await grant(caller, permissions)
    token = await issue_token_pair(caller)
    applications = JobApplication.get_motor_collection()
    before = await applications.find_one({"_id": item.application.id})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.post("/api/v1/recruitment/screenings", json=item.request.model_dump(),
                                     headers={"Authorization": "Bearer " + token.access_token, "Idempotency-Key": uuid4().hex})
    assert response.status_code == (202 if access == "allowed" else 403), response.text
    assert await ScreeningRun.find_all().count() == (1 if access == "allowed" else 0)
    if access != "allowed":
        assert await applications.find_one({"_id": item.application.id}) == before
        assert await ApplicationStageEvent.find_all().count() == 0


async def test_cached_idempotency_key_cannot_expose_original_resume_after_application_rebinding():
    item = await data()
    original_key = uuid4().hex
    run = await ScreeningService.start(item.request, original_key, item.current)
    restricted = await CompanyBranch(company_id=item.company.id, branch_name="Restricted", bussiness_type="IT",
                                     address="Other", company_size=1, working_days=[], created_by=item.owner.id).insert()
    await item.resume.set({"company_branch_id": restricted.id})
    replacement = await ResumeFile(
        company_id=item.company.id, company_branch_id=item.branch.id, candidate_id=item.candidate.id,
        filename="replacement.pdf", original_filename="replacement.pdf", file_path="unused", file_size=100,
        mime_type="application/pdf", uploader_id=item.owner.id, checksum="b" * 64,
        status="parsed", parse_revision=1, parsed_data=ParsedResumeData(raw_text=SOURCE),
    ).insert()
    await JobApplication.get_motor_collection().update_one(
        {"_id": item.application.id}, {"$set": {"resume_file_id": replacement.id}, "$inc": {"revision": 1}}
    )
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    await UserCompany(user_id=caller.id, company_branch_id=item.branch.id, role="recruiter",
                      assigned_by=item.owner.id).insert()
    await grant(caller, ["screening_runs:create", "resume_profiles:view"])
    token = await issue_token_pair(caller)
    runs = ScreeningRun.get_motor_collection()
    applications = JobApplication.get_motor_collection()
    before_run = await runs.find_one({"_id": run.id})
    before_application = await applications.find_one({"_id": item.application.id})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.post("/api/v1/recruitment/screenings", json=item.request.model_dump(),
                                     headers={"Authorization": "Bearer " + token.access_token, "Idempotency-Key": original_key})

    assert response.status_code == 403, response.text
    assert await ScreeningRun.find_all().count() == 1
    assert await runs.find_one({"_id": run.id}) == before_run
    assert await applications.find_one({"_id": item.application.id}) == before_application
    assert await ScreeningResult.find_all().count() == 0


@pytest.mark.parametrize("problem", ["no_profile", "stale_revision", "wrong_company", "wrong_resume", "wrong_hash", "not_parsed"])
async def test_start_rejects_unusable_profile_without_queueing_or_changing_stage(problem):
    item = await data()
    if problem == "no_profile":
        await item.resume.set({"latest_extraction_profile_id": None})
    elif problem == "stale_revision":
        await item.resume.set({"parse_revision": 2})
    elif problem == "not_parsed":
        await item.resume.set({"status": "processing"})
    else:
        fields = {"wrong_company": {"company_id": ObjectId()}, "wrong_resume": {"resume_file_id": ObjectId()},
                  "wrong_hash": {"profile.source_text_sha256": "0" * 64}}[problem]
        await ResumeProfile.get_motor_collection().update_one({"_id": item.profile.id}, {"$set": fields})
    before = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ScreeningService.start(item.request, uuid4().hex, item.current)
    assert caught.value.status_code in {409, 422}
    assert await ScreeningRun.find_all().count() == 0
    assert await ScreeningResult.find_all().count() == 0
    assert await ApplicationStageEvent.find_all().count() == 0
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before


async def test_run_result_snapshot_and_idempotency_survive_new_profile():
    item, run, claimed = await start_and_claim()
    frozen = run.source_snapshot.model_dump(mode="json")
    assert str(run.source_snapshot.resume_profile_id) == str(item.profile.id)
    assert run.source_snapshot.source_text == SOURCE
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert result.source_snapshot.model_dump(mode="json") == frozen
    newer = await create_profile(item.company, item.resume, version=2, revision=2)
    await item.resume.set({"parse_revision": 2, "latest_extraction_profile_id": newer.id, "extraction_revision": 2})

    repeated = await ScreeningService.start(item.request, run.idempotency_key, item.current)
    assert repeated.id == run.id
    assert repeated.source_snapshot.model_dump(mode="json") == frozen
    assert await ScreeningRun.find_all().count() == 1
    next_run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    assert str(next_run.source_snapshot.resume_profile_id) == str(newer.id)
    assert next_run.source_snapshot.profile_version == 2
    assert next_run.input_hash != run.input_hash
    assert (await ScreeningResult.get(result.id)).source_snapshot.model_dump(mode="json") == frozen


@pytest.mark.parametrize("change", ["new_profile", "new_parse", "deleted"])
async def test_http_historical_result_keeps_source_metadata_and_reports_it_is_no_longer_current(change):
    item, run, claimed = await start_and_claim()
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    await grant(item.owner, ["screening_runs:view"])
    token = await issue_token_pair(item.owner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        async def read(path):
            response = await client.get(path, params={"company_id": str(item.company.id)},
                                        headers={"Authorization": "Bearer " + token.access_token})
            assert response.status_code == 200, response.text
            return response.json()["data"]

        before = await read(f"/api/v1/recruitment/screenings/{run.id}/result")
        assert before["source_is_current"] is True
        if change == "new_profile":
            newer = await create_profile(item.company, item.resume, version=2)
            await item.resume.set({"latest_extraction_profile_id": newer.id, "extraction_revision": 2})
        elif change == "new_parse":
            await item.resume.set({"parse_revision": 2, "latest_extraction_profile_id": None})
        else:
            await item.resume.set({"is_deleted": True})
        historical = await read(f"/api/v1/recruitment/screenings/{run.id}/result")
        run_data = await read(f"/api/v1/recruitment/screenings/{run.id}")

    for response in (historical, run_data):
        assert response["resume_profile_id"] == str(item.profile.id)
        assert response["profile_version"] == 1 and response["source_parse_revision"] == 1
        assert response["source_text_sha256"] == item.profile.profile.source_text_sha256
        assert response["source_is_current"] is False
        assert "source_snapshot" not in response and "source_text" not in response
    assert historical["overall_score"] == before["overall_score"] == 80
    assert (await ScreeningResult.get(result.id)).source_snapshot == run.source_snapshot


async def test_active_screening_does_not_accept_a_different_idempotency_key():
    item, run, _ = await start_and_claim()
    before = await ScreeningRun.get_motor_collection().find_one({"_id": run.id})
    with pytest.raises(CustomError) as caught:
        await ScreeningService.start(item.request, uuid4().hex, item.current)
    assert caught.value.status_code == 409
    assert await ScreeningRun.find_all().count() == 1
    assert await ScreeningRun.get_motor_collection().find_one({"_id": run.id}) == before


@pytest.mark.parametrize("problem", ["legacy", "stale_revision", "application_resume", "application_job", "application_company", "application_missing"])
async def test_worker_preflight_rejects_unusable_source_before_model_inference(monkeypatch, problem):
    from app.core import job_queue

    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    if problem == "legacy":
        await ScreeningRun.get_motor_collection().update_one({"_id": run.id}, {"$unset": {"source_snapshot": ""}})
    elif problem == "stale_revision":
        await item.resume.set({"parse_revision": 2})
    elif problem == "application_missing":
        await JobApplication.get_motor_collection().delete_one({"_id": item.application.id})
    else:
        field = {"application_resume": "resume_file_id", "application_job": "job_requirement_id",
                 "application_company": "company_id"}[problem]
        await JobApplication.get_motor_collection().update_one({"_id": item.application.id}, {"$set": {field: ObjectId()}})
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    before_events = await ApplicationStageEvent.find_all().count()
    adapter = SimpleNamespace(screen=AsyncMock(return_value=output()))
    worker = Worker("screening", adapter)
    acknowledge = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)

    await worker.handle("unusable-source", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    adapter.screen.assert_not_awaited()
    stored = await ScreeningRun.get(run.id)
    assert stored.status == "failed" and stored.is_terminal and stored.attempt == 1
    assert stored.error_code == "worker_invalid_input"
    acknowledge.assert_awaited_once_with("screening", GROUP, "unusable-source")
    assert await ScreeningResult.find_all().count() == 0
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application
    assert await ApplicationStageEvent.find_all().count() == before_events


@pytest.mark.parametrize("problem", ["invented_quote", "wrong_offsets", "unverified_page", "insufficient_bad_quote", "conflicting_bad_quote"])
async def test_completion_rejects_bad_evidence_without_writing_any_result(problem):
    item, run, claimed = await start_and_claim()
    payload = output().model_dump(mode="json")
    evaluation = payload["criteria_evaluations"]["python"]
    evidence = evaluation["evidence"][0]
    if problem == "wrong_offsets":
        evidence["start"] += 1
        evidence["end"] += 1
    elif problem == "unverified_page":
        evidence["page"] = 1
    else:
        evidence.update({"text": "Invented technology", "start": 0, "end": 19})
        if problem in {"insufficient_bad_quote", "conflicting_bad_quote"}:
            evaluation.update({"status": "insufficient_evidence" if problem.startswith("insufficient") else "conflicting_evidence", "score": None})
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", ScreeningOutput.model_validate(payload), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application


@pytest.mark.parametrize("path,value", [
    ("source_snapshot.source_text_sha256", "0" * 64),
    ("source_snapshot.profile.source_text_sha256", "0" * 64),
    ("source_snapshot.profile.skills.items.0.name.evidence.0.quote", "foobar"),
])
async def test_completion_rejects_corrupted_snapshot_before_writing_a_result(path, value):
    item, run, claimed = await start_and_claim()
    await ScreeningRun.get_motor_collection().update_one({"_id": run.id}, {"$set": {path: value}})
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application


@pytest.mark.parametrize("change", ["deleted", "processing", "checksum", "text", "revision", "profile"])
async def test_completion_fences_source_changes_after_screening_started(change):
    item, run, claimed = await start_and_claim()
    changes = {"deleted": {"is_deleted": True}, "processing": {"status": "processing"},
               "checksum": {"checksum": "b" * 64}, "text": {"parsed_data.raw_text": SOURCE + "Changed"},
               "revision": {"parse_revision": 2}, "profile": {"latest_extraction_profile_id": ObjectId()}}
    await item.resume.set(changes[change])
    before = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == application


async def test_result_insert_failure_rolls_back_source_fence_and_application_pointer(monkeypatch):
    item, run, claimed = await start_and_claim()
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    before_events = await ApplicationStageEvent.find_all().count()
    monkeypatch.setattr(ScreeningResult, "insert", AsyncMock(side_effect=RuntimeError("injected result insert failure")))
    with pytest.raises(RuntimeError, match="injected"):
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await ScreeningResult.find_all().count() == 0
    assert await ApplicationStageEvent.find_all().count() == before_events


async def test_external_source_write_during_completion_aborts_transaction(monkeypatch):
    item, run, claimed = await start_and_claim()
    collection = ResumeFile.get_motor_collection()
    original_update = collection.update_one
    source_changed = False

    async def change_before_fence(*args, **kwargs):
        nonlocal source_changed
        update = args[1] if len(args) > 1 else kwargs["update"]
        if not source_changed and update.get("$inc", {}).get("screening_fence_version") == 1:
            source_changed = True
            # This write is outside the completion transaction, even while the
            # service's ContextVar carries its own session.
            await original_update({"_id": item.resume.id}, {"$inc": {"parse_revision": 1}}, session=None)
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(collection, "update_one", change_before_fence)
    application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert source_changed
    assert caught.value.status_code in {409, 422}
    assert (await ResumeFile.get(item.resume.id)).parse_revision == 2
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == application


@pytest.mark.parametrize("stage", [ApplicationStage.INTERVIEW, ApplicationStage.REJECTED])
async def test_completion_preserves_later_hr_stage_while_attaching_result(stage):
    item, run, claimed = await start_and_claim()
    await JobApplication.get_motor_collection().update_one(
        {"_id": item.application.id}, {"$set": {"current_stage": stage.value, "status": "rejected" if stage == ApplicationStage.REJECTED else "active"}, "$inc": {"revision": 1}}
    )
    before = await JobApplication.get(item.application.id)
    before_events = await ApplicationStageEvent.find_all().count()
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    stored = await JobApplication.get(item.application.id)
    assert stored.current_stage == stage and stored.revision == before.revision + 1
    assert stored.latest_screening_result_id == result.id
    assert await ApplicationStageEvent.find_all().count() == before_events


@pytest.mark.parametrize("field", ["company_id", "resume_file_id", "job_requirement_id"])
async def test_completion_rejects_an_application_rebound_to_other_resources(field):
    item, run, claimed = await start_and_claim()
    collection = JobApplication.get_motor_collection()
    await collection.update_one({"_id": item.application.id}, {"$set": {field: ObjectId()}, "$inc": {"revision": 1}})
    before = await collection.find_one({"_id": item.application.id})
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await collection.find_one({"_id": item.application.id}) == before
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume


async def test_concurrent_application_rebinding_rolls_back_result_without_unconditional_pointer_write(monkeypatch):
    item, run, claimed = await start_and_claim()
    collection = JobApplication.get_motor_collection()
    original_update = collection.update_one
    replacement_resume = ObjectId()
    application_changed = False

    async def change_before_pointer_write(*args, **kwargs):
        nonlocal application_changed
        update = args[1] if len(args) > 1 else kwargs["update"]
        if not application_changed and "latest_screening_result_id" in update.get("$set", {}):
            application_changed = True
            await original_update(
                {"_id": item.application.id},
                {"$set": {"resume_file_id": replacement_resume}, "$inc": {"revision": 1}}, session=None,
            )
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(collection, "update_one", change_before_pointer_write)
    before_resume = await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert application_changed and caught.value.status_code == 409
    stored = await JobApplication.get(item.application.id)
    assert stored.resume_file_id == replacement_resume and stored.latest_screening_result_id is None
    assert await ScreeningResult.find_all().count() == 0
    assert (await ScreeningRun.get(run.id)).status == "running"
    assert await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}) == before_resume


async def test_legacy_run_without_source_snapshot_cannot_complete():
    item = await data()
    run = await ScreeningRun(
        company_id=item.company.id, application_id=item.application.id, resume_file_id=item.resume.id,
        job_requirement_id=item.job.id, scorecard_id=item.scorecard.id, ai_model_id=item.model.id,
        triggered_by=item.owner.id, idempotency_key=uuid4().hex, input_hash="a" * 64,
        config_snapshot={"scorecard": item.scorecard.model_dump(mode="json")},
    ).insert()
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert (await JobApplication.get(item.application.id)).latest_screening_result_id is None
