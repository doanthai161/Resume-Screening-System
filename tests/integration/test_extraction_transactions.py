"""Real Mongo replica set tests; model inference is a controlled HTTP test double."""
import asyncio
from datetime import timedelta
import json
import os
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

from app.core import job_queue
from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, issue_token_pair
from app.extraction_service import app as extractor
from app.main import app
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.permission import Permission
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.resume_extraction import ResumeExtractionRun, ResumeProfile, PROMPT_VERSION, EXTRACTOR_VERSION
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.schemas.extraction import ExtractionServiceOutput
from app.services.extraction_service import ExtractionService
from app.services.processing_service import ProcessingService
from app.utils.time import now_utc
from app.workers.extraction_adapter import HTTPExtractionAdapter, ExtractionAdapterError
from app.workers.runtime import Worker

pytestmark = pytest.mark.asyncio
FOLDER = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (FOLDER / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")


def output():
    data = json.loads((FOLDER / "resume-extraction-v1.example.json").read_text(encoding="utf-8"))
    return ExtractionServiceOutput(profile=data, model_name="test-model", prompt_version=PROMPT_VERSION, extractor_version=EXTRACTOR_VERSION)


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "phase3_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "EXTRACTION_API_URL", "http://extractor:8002")
    monkeypatch.setattr(settings, "EXTRACTION_SERVICE_TOKEN", SecretStr("test-token-" + "x" * 32))
    monkeypatch.setattr(settings, "EXTRACTION_MODEL_NAME", "test-model")
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("phase3_test_")
        await client.drop_database(name)
        client.close()


async def data():
    key = uuid4().hex
    owner = await User(email=key + "@example.com", hashed_password="unused", is_verified=True).insert()
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branch = await CompanyBranch(company_id=company.id, branch_name="Test", bussiness_type="IT",
                                 address="Test", company_size=1, working_days=[], created_by=owner.id).insert()
    resume = await ResumeFile(company_id=company.id, company_branch_id=branch.id, filename="synthetic.pdf",
                              original_filename="synthetic.pdf", file_path="unused", file_size=100, mime_type="application/pdf",
                              uploader_id=owner.id, checksum="a" * 64, status="parsed", parse_revision=1,
                              parsed_data=ParsedResumeData(raw_text=SOURCE)).insert()
    current = CurrentUser(owner, permission_names={"resume_files:extract", "resume_files:view", "resume_profiles:view"})
    return owner, company, branch, resume, current


async def start():
    _, _, _, resume, current = await data()
    run = await ExtractionService.start(str(resume.id), str(resume.company_id), uuid4().hex, current)
    return resume, current, run


async def grant(user, names):
    actor = await Actor(name=uuid4().hex, is_active=True).insert()
    await UserActor(user_id=user.id, actor_id=actor.id, created_by=user.id).insert()
    for name in names:
        permission = await Permission.find_one({"name": name})
        if not permission:
            permission = await Permission(name=name, is_active=True).insert()
        await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()


async def test_idempotency_survives_model_configuration_change():
    resume, current, run = await start()
    settings.EXTRACTION_MODEL_NAME = "new-model"
    again = await ExtractionService.start(str(resume.id), str(resume.company_id), run.idempotency_key, current)
    assert again.id == run.id and again.model_name == "test-model"
    assert await ResumeExtractionRun.find_all().count() == 1


@pytest.mark.parametrize("running", [False, True])
async def test_http_active_run_rejects_another_key_without_mutating_database(running):
    owner, company, _, resume, current = await data()
    original_key, another_key = uuid4().hex, uuid4().hex
    run = await ExtractionService.start(str(resume.id), str(company.id), original_key, current)
    if running:
        assert await ExtractionService.claim(str(run.id), "worker")
    await grant(owner, ["resume_files:extract"])
    token = await issue_token_pair(owner)
    runs = ResumeExtractionRun.get_motor_collection()
    resumes = ResumeFile.get_motor_collection()
    before_run = await runs.find_one({"_id": run.id})
    before_resume = await resumes.find_one({"_id": resume.id})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.post(
            f"/api/v1/resumes/{resume.id}/extraction-runs",
            json={"company_id": str(company.id)},
            headers={"Authorization": "Bearer " + token.access_token, "Idempotency-Key": another_key},
        )

    assert response.status_code == 409, response.text
    assert await runs.find_one({"_id": run.id}) == before_run
    assert await resumes.find_one({"_id": resume.id}) == before_resume
    assert await ResumeExtractionRun.find_all().count() == 1
    assert await ResumeExtractionRun.find_one({"idempotency_key": another_key}) is None
    assert await ResumeProfile.find_all().count() == 0


async def test_completed_run_keeps_original_key_and_new_key_requires_explicit_start():
    resume, current, original = await start()
    another_key = uuid4().hex
    with pytest.raises(CustomError) as caught:
        await ExtractionService.start(str(resume.id), str(resume.company_id), another_key, current)
    assert caught.value.status_code == 409
    assert await ResumeExtractionRun.find_all().count() == 1

    claimed = await ExtractionService.claim(str(original.id), "worker")
    assert await ExtractionService.complete(str(original.id), "worker", output(), generation=claimed.attempt)
    repeated = await ExtractionService.start(str(resume.id), str(resume.company_id), original.idempotency_key, current)
    assert repeated.id == original.id and repeated.status == "completed"
    assert await ResumeExtractionRun.find_all().count() == 1
    assert await ResumeProfile.find_all().count() == 1

    second = await ExtractionService.start(str(resume.id), str(resume.company_id), another_key, current)
    assert second.id != original.id and second.idempotency_key == another_key
    assert second.status == "queued"
    assert await ResumeExtractionRun.find_all().count() == 2
    assert await ResumeProfile.find_all().count() == 1


async def test_concurrent_claims_and_duplicate_completion_write_one_profile():
    resume, current, run = await start()
    claims = await asyncio.gather(ExtractionService.claim(str(run.id), "first"), ExtractionService.claim(str(run.id), "second"))
    winner = next(item for item in claims if item)
    assert sum(item is not None for item in claims) == 1
    assert await ExtractionService.complete(str(run.id), winner.worker_id, output(), generation=winner.attempt)
    assert not await ExtractionService.complete(str(run.id), winner.worker_id, output(), generation=winner.attempt)
    assert await ResumeProfile.find_all().count() == 1
    result, current_version = await ExtractionService.get_profile(str(resume.id), current)
    assert current_version and result.version == 1
    assert result.profile.personal_info.full_name.value == "Nguyễn An"


async def test_delayed_claim_returns_its_own_generation_after_another_worker_takes_over(monkeypatch):
    _, _, run = await start()
    collection = ResumeExtractionRun.get_motor_collection()
    claimed_in_database, release_response = asyncio.Event(), asyncio.Event()

    def delay_claim_response(operation):
        async def delayed_response(*args, **kwargs):
            # Pause after the real write for both the old UPDATE/GET path and
            # atomic post-image path, so this reproduces the former race too.
            result = await operation(*args, **kwargs)
            update = args[1] if len(args) > 1 else kwargs["update"]
            if update.get("$set", {}).get("worker_id") == "old":
                claimed_in_database.set()
                await release_response.wait()
            return result
        return delayed_response

    monkeypatch.setattr(collection, "update_one", delay_claim_response(collection.update_one))
    monkeypatch.setattr(collection, "find_one_and_update", delay_claim_response(collection.find_one_and_update))
    old_claim = asyncio.create_task(ExtractionService.claim(str(run.id), "old"))
    try:
        await asyncio.wait_for(claimed_in_database.wait(), timeout=10)
        await collection.update_one(
            {"_id": run.id}, {"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}}
        )
        await ProcessingService.recover_expired_leases()
        fresh = await ExtractionService.claim(str(run.id), "fresh")
        assert fresh is not None and fresh.worker_id == "fresh" and fresh.attempt == 2
        release_response.set()
        old = await asyncio.wait_for(old_claim, timeout=10)
        assert old is not None and old.worker_id == "old" and old.attempt == 1
        assert not await ExtractionService.renew(str(run.id), "old", generation=old.attempt)
        assert not await ExtractionService.complete(str(run.id), "old", output(), generation=old.attempt)
        assert await ExtractionService.complete(str(run.id), "fresh", output(), generation=fresh.attempt)
        assert await ResumeProfile.find_all().count() == 1
    finally:
        release_response.set()
        old_claim.cancel()
        await asyncio.gather(old_claim, return_exceptions=True)


async def test_expired_lease_and_old_generation_are_fenced_after_recovery():
    resume, current, run = await start()
    old = await ExtractionService.claim(str(run.id), "old")
    await ResumeExtractionRun.find_one({"_id": run.id}).update({"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}})
    assert not await ExtractionService.renew(str(run.id), "old", generation=old.attempt)
    assert not await ExtractionService.complete(str(run.id), "old", output(), generation=old.attempt)
    await ProcessingService.recover_expired_leases()
    fresh = await ExtractionService.claim(str(run.id), "fresh")
    assert fresh.attempt == old.attempt + 1
    assert not await ExtractionService.fail(str(run.id), "old", "error", "sensitive", generation=old.attempt)
    assert not await ExtractionService.complete(str(run.id), "fresh", output(), generation=old.attempt)
    assert await ExtractionService.complete(str(run.id), "fresh", output(), generation=fresh.attempt)
    assert await ResumeProfile.find_all().count() == 1


async def test_immediate_renew_accepts_live_lease_when_mongo_write_is_unchanged(monkeypatch):
    _, _, run = await start()
    now = now_utc()
    frozen = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    monkeypatch.setattr("app.services.extraction_service.now_utc", lambda: frozen)
    claimed = await ExtractionService.claim(str(run.id), "worker")
    assert claimed is not None
    collection = ResumeExtractionRun.get_motor_collection()
    before = await collection.find_one({"_id": run.id})

    assert await ExtractionService.renew(str(run.id), "worker", generation=claimed.attempt)
    assert await collection.find_one({"_id": run.id}) == before
    assert not await ExtractionService.renew(str(run.id), "another-worker", generation=claimed.attempt)
    assert not await ExtractionService.renew(str(run.id), "worker", generation=claimed.attempt + 1)
    assert await collection.find_one({"_id": run.id}) == before


async def test_stale_claim_is_rejected_before_inference_without_mutating_new_owner(monkeypatch):
    _, _, run = await start()
    old = await ExtractionService.claim(str(run.id), "old")
    collection = ResumeExtractionRun.get_motor_collection()
    await collection.update_one(
        {"_id": run.id}, {"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}}
    )
    await ProcessingService.recover_expired_leases()
    fresh = await ExtractionService.claim(str(run.id), "fresh")
    assert fresh is not None and fresh.attempt == old.attempt + 1
    before = await collection.find_one({"_id": run.id})

    adapter = SimpleNamespace(extract=AsyncMock(return_value=output()))
    worker = Worker("resume-extract", adapter)
    worker.worker_id = "old"
    # Simulate delivery of the original successful claim response only after
    # recovery handed the run to another worker. Renew uses the real DB policy.
    worker.claim = AsyncMock(return_value=old)
    worker.complete = AsyncMock(return_value=False)
    acknowledge = AsyncMock()
    monkeypatch.setattr(job_queue, "acknowledge", acknowledge)

    await worker.handle("stale-claim", {"resource_id": str(run.id), "company_id": str(run.company_id)})

    adapter.extract.assert_not_awaited()
    worker.complete.assert_not_awaited()
    acknowledge.assert_not_awaited()
    assert await collection.find_one({"_id": run.id}) == before
    assert await ResumeProfile.find_all().count() == 0


@pytest.mark.parametrize("change", ["deleted", "revision", "text", "processing"])
async def test_changed_or_deleted_source_never_accepts_old_results(change):
    resume, current, run = await start()
    claimed = await ExtractionService.claim(str(run.id), "worker")
    changes = {"deleted": {"is_deleted": True}, "revision": {"parse_revision": 2},
               "text": {"parsed_data.raw_text": SOURCE + "Changed"}, "processing": {"status": "processing"}}
    await ResumeFile.find_one({"_id": resume.id}).update({"$set": changes[change]})
    with pytest.raises(CustomError) as caught:
        await ExtractionService.complete(str(run.id), "worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ResumeProfile.find_all().count() == 0
    assert (await ResumeFile.get(resume.id)).extraction_revision == 0


async def test_transaction_rolls_back_pointer_when_profile_insert_fails(monkeypatch):
    resume, current, run = await start()
    claimed = await ExtractionService.claim(str(run.id), "worker")
    monkeypatch.setattr(ResumeProfile, "insert", AsyncMock(side_effect=RuntimeError("injected write failure")))
    with pytest.raises(RuntimeError):
        await ExtractionService.complete(str(run.id), "worker", output(), generation=claimed.attempt)
    stored = await ResumeFile.get(resume.id)
    assert stored.latest_extraction_profile_id is None and stored.extraction_revision == 0
    assert (await ResumeExtractionRun.get(run.id)).status == "running"


async def test_profile_versions_are_immutable_and_old_version_can_be_read():
    resume, current, first = await start()
    claimed = await ExtractionService.claim(str(first.id), "worker")
    await ExtractionService.complete(str(first.id), "worker", output(), generation=claimed.attempt)
    old, _ = await ExtractionService.get_profile(str(resume.id), current)
    second = await ExtractionService.start(str(resume.id), str(resume.company_id), uuid4().hex, current)
    claimed = await ExtractionService.claim(str(second.id), "worker")
    await ExtractionService.complete(str(second.id), "worker", output(), generation=claimed.attempt)
    latest, is_current = await ExtractionService.get_profile(str(resume.id), current)
    history, old_current = await ExtractionService.get_profile(str(resume.id), current, str(old.id))
    assert latest.version == 2 and is_current
    assert history.version == 1 and not old_current
    assert await ResumeProfile.find_all().count() == 2


async def test_http_service_worker_mongo_path_and_ack_redelivery(monkeypatch):
    resume, current, run = await start()
    model_calls = []
    def model(request):
        model_calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": output().profile.model_dump_json()}}]})
    monkeypatch.setattr(settings, "EXTRACTION_LLM_CHAT_URL", "http://model/v1/chat/completions")
    monkeypatch.setattr(settings, "EXTRACTION_LLM_API_KEY", None)
    monkeypatch.setattr(extractor.state, "slots", asyncio.Semaphore(2))
    ack = AsyncMock(side_effect=RuntimeError("Redis disconnected after commit"))
    monkeypatch.setattr(job_queue, "acknowledge", ack)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor)) as client:
            adapter = HTTPExtractionAdapter(client, "http://extractor", settings.EXTRACTION_SERVICE_TOKEN.get_secret_value())
            worker = Worker("resume-extract", adapter)
            fields = {"resource_id": str(run.id), "company_id": str(run.company_id)}
            with pytest.raises(RuntimeError):
                await worker.handle("1-0", fields)
            ack.side_effect = None
            await worker.handle("1-0", fields)
    assert len(model_calls) == 1 and await ResumeProfile.find_all().count() == 1
    assert (await ResumeExtractionRun.get(run.id)).status == "completed"


async def test_retry_is_durable_bounded_and_never_logs_remote_error(monkeypatch):
    resume, current, run = await start()
    adapter = SimpleNamespace(extract=AsyncMock(side_effect=ExtractionAdapterError("extraction_service_timeout", retryable=True)))
    worker = Worker("resume-extract", adapter)
    monkeypatch.setattr(job_queue, "acknowledge", AsyncMock())
    fields = {"resource_id": str(run.id), "company_id": str(run.company_id)}
    for attempt in range(3):
        if attempt:
            await ResumeExtractionRun.find_one({"_id": run.id}).update({"$set": {"next_retry_at": now_utc() - timedelta(seconds=1)}})
        await worker.handle("1-0", fields)
        stored = await ResumeExtractionRun.get(run.id)
        assert stored.attempt == attempt + 1
        assert stored.status == ("failed" if attempt == 2 else "queued")
        if attempt < 2:
            assert await ExtractionService.claim(str(run.id), "too-early") is None
    assert stored.is_terminal and stored.error_message == "Resume extraction failed"
    assert await ResumeProfile.find_all().count() == 0


@pytest.mark.parametrize("access", ["allowed", "other_company", "other_branch", "no_permission"])
async def test_http_real_tokens_check_tenant_branch_and_permission(access):
    owner, company, branch, resume, current = await data()
    caller = owner
    if access in {"other_company", "other_branch"}:
        caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
        if access == "other_branch":
            other = await CompanyBranch(company_id=company.id, branch_name="Other", bussiness_type="IT", address="Other",
                                        company_size=1, working_days=[], created_by=owner.id).insert()
            await UserCompany(user_id=caller.id, company_branch_id=other.id, role="recruiter", is_active=True, assigned_by=owner.id).insert()
    if access != "no_permission":
        await grant(caller, ["resume_files:extract", "resume_files:view", "resume_profiles:view"])
    token = await issue_token_pair(caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.post(f"/api/v1/resumes/{resume.id}/extraction-runs", json={"company_id": str(company.id)},
                                    headers={"Authorization": "Bearer " + token.access_token, "Idempotency-Key": uuid4().hex})
        assert response.status_code == (202 if access == "allowed" else 403), response.text
        if access == "allowed":
            run = response.json()["data"]
            assert "source_text" not in run and "triggered_by" not in run
            status = await client.get(f"/api/v1/resumes/{resume.id}/extraction-runs/{run['id']}", headers={"Authorization": "Bearer " + token.access_token})
            assert status.status_code == 200
    assert await ResumeExtractionRun.find_all().count() == (1 if access == "allowed" else 0)


@pytest.mark.parametrize("access", ["allowed", "other_company", "other_branch", "cv_view_only"])
async def test_profile_http_requires_own_permission_and_branch(access):
    owner, company, branch, resume, current = await data()
    run = await ExtractionService.start(str(resume.id), str(company.id), uuid4().hex, current)
    claimed = await ExtractionService.claim(str(run.id), "worker")
    await ExtractionService.complete(str(run.id), "worker", output(), generation=claimed.attempt)
    caller = owner
    if access in {"other_company", "other_branch"}:
        caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
        if access == "other_branch":
            other = await CompanyBranch(company_id=company.id, branch_name="Other", bussiness_type="IT", address="Other",
                                        company_size=1, working_days=[], created_by=owner.id).insert()
            await UserCompany(user_id=caller.id, company_branch_id=other.id, role="recruiter", assigned_by=owner.id).insert()
    await grant(caller, ["resume_files:view"] if access == "cv_view_only" else ["resume_profiles:view"])
    token = await issue_token_pair(caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.get(f"/api/v1/resumes/{resume.id}/profiles/latest", headers={"Authorization": "Bearer " + token.access_token})
    assert response.status_code == (200 if access == "allowed" else 403), response.text
    if access == "allowed":
        result = response.json()["data"]
        assert result["profile"]["personal_info"]["full_name"]["value"] == "Nguyễn An"
        assert result["version"] == 1 and result["is_current"]
        assert "source_text" not in result and "file_path" not in result
    assert await ResumeProfile.find_all().count() == 1
