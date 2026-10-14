"""BE acceptance with real Mongo/Redis and explicitly controlled AI adapters.

These tests exercise HTTP, storage, durable workflows and HR audit. The parser,
extractor and screening adapters supply known synthetic outputs: this is not a
MinerU/cloud-model quality or process-kill acceptance test.
"""
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
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import cache, job_queue, redis as redis_state
from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.rate_limiter import limiter
from app.core.security import issue_token_pair
from app.main import app
from app.models.ai_model import AIModel
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.job_application import JobApplication
from app.models.resume_extraction import ResumeExtractionRun, ResumeProfile, EXTRACTOR_VERSION
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_override import ScreeningOverride
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.models.user import User
from app.schemas.extraction import ExtractionServiceOutput
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.services.processing_service import ProcessingService
from app.workers.runtime import Worker, GROUP
from scripts.generate_parse_samples import digital_pdf
from test_screening_profile_transactions import grant

pytestmark = pytest.mark.asyncio
CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (CONTRACTS / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch, tmp_path):
    mongo_uri, redis_url = os.getenv("TEST_MONGODB_URI"), os.getenv("TEST_REDIS_URL")
    if not mongo_uri or not redis_url:
        pytest.skip("Explicit TEST_MONGODB_URI and TEST_REDIS_URL for isolated test instances are required")
    namespace = "mvp_be_workflow_test_" + uuid4().hex
    mongo = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
    redis = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
    initialized = False
    try:
        await require_transaction_topology(mongo)
        await redis.ping()
        await init_beanie(database=mongo[namespace], document_models=DOCUMENT_MODELS)
        initialized = True
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(settings, "REDIS_KEY_PREFIX", namespace)
        monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", tmp_path / "uploads")
        monkeypatch.setattr(settings, "EXTRACTION_API_URL", "http://controlled-extractor:8002")
        monkeypatch.setattr(settings, "EXTRACTION_MODEL_NAME", "controlled-extraction-test-double")
        monkeypatch.setattr(settings, "EXTRACTION_SERVICE_TOKEN", SecretStr("controlled-test-token-" + "x" * 32))
        monkeypatch.setattr(redis_state, "redis_client", redis)
        monkeypatch.setattr(limiter, "enabled", False)
        yield SimpleNamespace(redis=redis, namespace=namespace)
    finally:
        try:
            if initialized:
                owned = [key async for key in redis.scan_iter(match=namespace + ":*", count=100)]
                assert all(key.startswith(namespace + ":") for key in owned)
                for offset in range(0, len(owned), 100):
                    await redis.delete(*owned[offset:offset + 100])
        finally:
            if initialized:
                assert namespace.startswith("mvp_be_workflow_test_")
                await mongo.drop_database(namespace)
            await redis.aclose()
            mongo.close()


class ControlledAIAdapters:
    """Known fixture outputs only; no file parsing or provider HTTP is performed."""
    def __init__(self):
        self.parse_calls = self.extract_calls = self.screen_calls = 0

    async def parse(self, run):
        self.parse_calls += 1
        return ParseOutput(parsed_data=ParsedResumeData(raw_text=SOURCE, parser_version="controlled-parser-test-double"),
                           provider="controlled_test_double", quality_score=1)

    async def extract(self, run):
        self.extract_calls += 1
        assert run.source_text == SOURCE
        profile = json.loads((CONTRACTS / "resume-extraction-v1.example.json").read_text(encoding="utf-8"))
        return ExtractionServiceOutput(profile=profile, model_name=run.model_name, prompt_version=run.prompt_version,
                                       extractor_version=EXTRACTOR_VERSION)

    async def screen(self, run):
        self.screen_calls += 1
        source = run.source_snapshot
        assert source.profile_origin == "hr" and len(source.hr_annotations) == 1
        fact = source.hr_annotations[0]
        offset = SOURCE.index("Python")
        return ScreeningOutput(criteria_evaluations={
            "python": {"status": "evaluated", "score": 80, "reason": "CV lists Python",
                       "evidence": [{"origin": "cv", "text": "Python", "start": offset, "end": offset + 6}]},
            "production_apis": {"status": "evaluated", "score": 90, "reason": "HR confirmation",
                                "evidence": [{"origin": "hr", "hr_fact_id": fact.id, "text": fact.value}]},
        })

    async def aclose(self):
        pass


async def request(client, method, path, headers, *, expected=200, **kwargs):
    response = await client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == expected, response.text
    return response.json()["data"]


async def prepare_uploaded_application(client):
    key = uuid4().hex
    owner = await User(email=key + "@example.com", hashed_password="unused", is_verified=True).insert()
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branch = await CompanyBranch(company_id=company.id, branch_name="Hiring", bussiness_type="IT",
                                 address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert()
    await grant(owner, {"candidates:create", "candidates:view", "job_requirements:create", "job_applications:create",
                        "job_applications:view", "job_scorecards:create", "resume_files:upload", "resume_files:view",
                        "resume_files:parse", "resume_files:extract", "resume_profiles:view", "resume_profiles:edit",
                        "screening_runs:create", "screening_runs:view", "screening_results:override"})
    pair = await issue_token_pair(owner)
    headers = {"Authorization": "Bearer " + pair.access_token}
    candidate = await request(client, "POST", "/api/v1/candidates", headers, expected=201,
                              json={"company_id": str(company.id), "full_name": "Synthetic CV candidate"})
    job = await request(client, "POST", "/api/v1/job-requirements/", headers, expected=201,
                        json={"company_branch_id": str(branch.id), "title": "Synthetic backend engineer",
                              "experience_level": "junior", "programming_languages": ["Python"],
                              "skills_required": ["SQL"], "status": "published"})
    card = await request(client, "POST", "/api/v1/recruitment/scorecards", {**headers, "Idempotency-Key": "card-" + key}, expected=201,
                         json={"company_id": str(company.id), "job_requirement_id": job["id"], "pass_threshold": 70,
                               "criteria": [{"code": "python", "name": "Python", "weight": .7, "required": True, "minimum_score": 60},
                                            {"code": "production_apis", "name": "Production APIs", "weight": .3}]})
    pdf = digital_pdf()
    resume = await request(client, "POST", "/api/v1/resumes", headers, expected=201,
                           data={"company_id": str(company.id), "candidate_id": candidate["id"], "company_branch_id": str(branch.id)},
                           files={"file": ("synthetic.pdf", pdf, "application/pdf")})
    application = await request(client, "POST", "/api/v1/recruitment/applications", {**headers, "Idempotency-Key": "apply-" + key}, expected=201,
                                json={"company_id": str(company.id), "candidate_id": candidate["id"], "resume_file_id": resume["id"],
                                      "job_requirement_id": job["id"]})
    model = await AIModel(name="Controlled screening fixture", provider="synthetic", model_id="controlled-screening-test-double",
                          model_type="scoring", created_by=owner.id).insert()
    return SimpleNamespace(key=key, owner=owner, company=company, headers=headers, candidate=candidate,
                           job=job, scorecard=card, resume=resume, application=application, model=model, pdf=pdf)


async def start_parse(client, item):
    return await request(client, "POST", f"/api/v1/resumes/{item.resume['id']}/parse-runs",
                         {**item.headers, "Idempotency-Key": "parse-" + item.key}, expected=202,
                         json={"company_id": str(item.company.id)})


async def delivery(worker, run_id):
    batches = await job_queue.read(worker.queue, GROUP, worker.worker_id, count=1, block_ms=100)
    messages = [message for _, batch in batches for message in batch]
    assert len(messages) == 1
    message_id, fields = messages[0]
    assert fields["resource_id"] == str(run_id)
    return message_id, fields


async def process(worker, run_id, redis):
    message_id, fields = await delivery(worker, run_id)
    await worker.handle(message_id, fields)
    pending = await redis.xpending(cache.cache_key("queue", worker.queue), GROUP)
    assert pending["pending"] == 0


async def test_be_http_workflow_with_real_redis_and_controlled_ai_outputs(mock_db):
    adapters = ControlledAIAdapters()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        item = await prepare_uploaded_application(client)
        assert item.application["current_stage"] == "applied"
        catalog = await request(client, "GET", "/api/v1/ai-models", item.headers)
        assert any(model["id"] == str(item.model.id) for model in catalog["items"])
        assert all("config" not in model for model in catalog["items"])
        metadata = await request(client, "GET", f"/api/v1/resumes/{item.resume['id']}", item.headers)
        assert metadata["id"] == item.resume["id"] and "file_path" not in metadata
        download = await client.get(f"/api/v1/resumes/{item.resume['id']}/download", headers=item.headers)
        assert download.status_code == 200 and download.content == item.pdf
        assert download.headers["Cache-Control"] == "private, no-store"

        parsing = await start_parse(client, item)
        assert parsing["status"] == "queued"
        await process(Worker("resume-parse", adapters), parsing["id"], mock_db.redis)
        parsed = await request(client, "GET", f"/api/v1/resumes/{item.resume['id']}/parse-runs/{parsing['id']}", item.headers)
        assert parsed["status"] == "completed" and parsed["attempt"] == 1
        extracted = await request(client, "POST", f"/api/v1/resumes/{item.resume['id']}/extraction-runs",
                                  {**item.headers, "Idempotency-Key": "extract-" + item.key}, expected=202,
                                  json={"company_id": str(item.company.id)})
        await process(Worker("resume-extract", adapters), extracted["id"], mock_db.redis)
        extraction_status = await request(client, "GET", f"/api/v1/resumes/{item.resume['id']}/extraction-runs/{extracted['id']}", item.headers)
        assert extraction_status["status"] == "completed"
        ai_profile = await request(client, "GET", f"/api/v1/resumes/{item.resume['id']}/profiles/latest", item.headers)
        assert ai_profile["origin"] == "ai" and ai_profile["version"] == 1 and ai_profile["is_current"]
        original_profile = await ResumeProfile.get_motor_collection().find_one({"_id": ObjectId(ai_profile["id"])})

        hr_profile = await request(client, "POST", f"/api/v1/resumes/{item.resume['id']}/profile-revisions",
                                   {**item.headers, "Idempotency-Key": "hr-profile-" + item.key}, expected=201,
                                   json={"base_profile_id": ai_profile["id"], "expected_version": 1,
                                         "reason": "HR xác minh thông tin tại phỏng vấn.",
                                         "hr_annotations": [{"section": "experiences", "label": "Production APIs",
                                                             "value": "Đã phát triển API FastAPI trong production.",
                                                             "reason": "Ứng viên mô tả dự án và HR xác minh."}]})
        assert hr_profile["origin"] == "hr" and hr_profile["version"] == 2
        assert hr_profile["parent_profile_id"] == ai_profile["id"] and hr_profile["edited_by"] == str(item.owner.id)
        assert hr_profile["hr_annotations"][0]["confirmed_by"] == str(item.owner.id)
        assert await ResumeProfile.get_motor_collection().find_one({"_id": ObjectId(ai_profile["id"])}) == original_profile
        history = await request(client, "GET", f"/api/v1/resumes/{item.resume['id']}/profiles", item.headers)
        assert [value["version"] for value in history["items"]] == [2, 1]

        screening = await request(client, "POST", "/api/v1/recruitment/screenings",
                                  {**item.headers, "Idempotency-Key": "screen-" + item.key}, expected=202,
                                  json={"company_id": str(item.company.id), "application_id": item.application["id"], "ai_model_id": str(item.model.id)})
        assert screening["resume_profile_id"] == hr_profile["id"] and screening["profile_version"] == 2
        await process(Worker("screening", adapters), screening["id"], mock_db.redis)
        state = await request(client, "GET", f"/api/v1/recruitment/screenings/{screening['id']}", item.headers,
                              params={"company_id": str(item.company.id)})
        result = await request(client, "GET", f"/api/v1/recruitment/screenings/{screening['id']}/result", item.headers,
                               params={"company_id": str(item.company.id)})
        assert state["status"] == "completed" and result["overall_score"] == 83 and result["decision"] == "pass"
        assert result["evidence_coverage"] == 100 and result["resume_profile_id"] == hr_profile["id"]
        assert result["criteria_evaluations"]["python"]["evidence"][0]["origin"] == "cv"
        hr_evidence = result["criteria_evaluations"]["production_apis"]["evidence"][0]
        assert hr_evidence["origin"] == "hr" and hr_evidence["hr_fact_id"] == hr_profile["hr_annotations"][0]["id"]
        assert hr_evidence["start"] is None and hr_evidence["end"] is None
        result_before = await ScreeningResult.get_motor_collection().find_one({"_id": ObjectId(result["id"])})
        run_before = await ScreeningRun.get_motor_collection().find_one({"_id": ObjectId(screening["id"])})

        override_body = {"company_id": str(item.company.id), "expected_version": 0, "overall_score": 95, "decision": "pass",
                         "reason": "HR đánh giá thêm năng lực sau phỏng vấn."}
        override_headers = {**item.headers, "Idempotency-Key": "hr-override-" + item.key}
        override = await request(client, "POST", f"/api/v1/recruitment/screenings/{screening['id']}/overrides", override_headers,
                                 expected=201, json=override_body)
        replay = await request(client, "POST", f"/api/v1/recruitment/screenings/{screening['id']}/overrides", override_headers,
                               expected=201, json=override_body)
        assert replay["id"] == override["id"] and override["ai_base"]["resume_profile_id"] == hr_profile["id"]
        assessment = await request(client, "GET", f"/api/v1/recruitment/screenings/{screening['id']}/assessment", item.headers,
                                   params={"company_id": str(item.company.id)})
        overrides = await request(client, "GET", f"/api/v1/recruitment/screenings/{screening['id']}/overrides", item.headers,
                                  params={"company_id": str(item.company.id)})
        assert assessment["ai"]["overall_score"] == 83 and assessment["effective"]["overall_score"] == 95
        assert assessment["effective"]["source"] == "hr_override" and overrides["total"] == 1
        assert assessment["source_is_current"] and assessment["is_latest_result"]
        application = await request(client, "GET", f"/api/v1/recruitment/applications/{item.application['id']}", item.headers,
                                    params={"company_id": str(item.company.id)})
        assert application["current_stage"] == "screened" and application["latest_screening_result_id"] == result["id"]

    result_after = await ScreeningResult.get_motor_collection().find_one({"_id": ObjectId(result["id"])})
    operational = {"override_revision", "latest_override_id"}
    assert {key: value for key, value in result_after.items() if key not in operational} == {
        key: value for key, value in result_before.items() if key not in operational}
    assert await ScreeningRun.get_motor_collection().find_one({"_id": ObjectId(screening["id"])}) == run_before
    assert await ResumeProfile.get_motor_collection().find_one({"_id": ObjectId(ai_profile["id"])}) == original_profile
    assert (adapters.parse_calls, adapters.extract_calls, adapters.screen_calls) == (1, 1, 1)
    assert await ResumeParseRun.find_all().count() == 1 and await ResumeExtractionRun.find_all().count() == 1
    assert await ScreeningResult.find_all().count() == 1 and await ScreeningOverride.find_all().count() == 1


async def test_actual_redis_pending_delivery_is_reclaimed_after_ack_failure_without_reprocessing(mock_db, monkeypatch):
    adapters = ControlledAIAdapters()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        item = await prepare_uploaded_application(client)
        parsing = await start_parse(client, item)
    old = Worker("resume-parse", adapters)
    message_id, fields = await delivery(old, parsing["id"])
    with monkeypatch.context() as outage:
        outage.setattr(job_queue, "acknowledge", AsyncMock(side_effect=RedisConnectionError("controlled ACK connection failure")))
        with pytest.raises(RedisConnectionError):
            await old.handle(message_id, fields)
    assert (await ResumeParseRun.get(ObjectId(parsing["id"]))).status == "completed"
    stream = cache.cache_key("queue", "resume-parse")
    assert (await mock_db.redis.xpending(stream, GROUP))["pending"] == 1
    # Age only this owned pending delivery without a wall-clock sleep. The new
    # consumer then uses the production XAUTOCLAIM helper against real Redis.
    await mock_db.redis.xclaim(stream, GROUP, old.worker_id, min_idle_time=0, message_ids=[message_id],
                              idle=settings.QUEUE_REDELIVERY_SECONDS * 1000 + 1)
    restarted = Worker("resume-parse", adapters)
    _, pending = await job_queue.reclaim("resume-parse", GROUP, restarted.worker_id)
    assert len(pending) == 1 and pending[0][0] == message_id
    await restarted.handle(*pending[0])
    assert (await mock_db.redis.xpending(stream, GROUP))["pending"] == 0
    assert adapters.parse_calls == 1 and (await ResumeFile.get(ObjectId(item.resume["id"]))).parse_revision == 1
    assert (await ResumeParseRun.get(ObjectId(parsing["id"]))).attempt == 1


async def test_mongo_outbox_survives_controlled_publish_failure_and_real_redis_recovers_it(mock_db, monkeypatch):
    adapters = ControlledAIAdapters()
    failing = SimpleNamespace(xadd=AsyncMock(side_effect=RedisConnectionError("controlled publish connection failure")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        item = await prepare_uploaded_application(client)
        with monkeypatch.context() as outage:
            outage.setattr(job_queue, "get_redis", lambda: failing)
            parsing = await start_parse(client, item)
        assert failing.xadd.await_count == 1
        stored = await ResumeParseRun.get(ObjectId(parsing["id"]))
        assert stored.status == "queued" and stored.last_enqueued_at is None
        assert not await mock_db.redis.exists(cache.cache_key("queue", "resume-parse"))
        await ProcessingService.recover_expired_leases()
        await process(Worker("resume-parse", adapters), parsing["id"], mock_db.redis)
    completed = await ResumeParseRun.get(ObjectId(parsing["id"]))
    assert completed.status == "completed" and completed.idempotency_key == "parse-" + item.key
    assert completed.attempt == 1 and adapters.parse_calls == 1
    assert await ResumeParseRun.find_all().count() == 1
