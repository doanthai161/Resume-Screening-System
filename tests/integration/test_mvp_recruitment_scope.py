"""Recruitment branch authorization through real JWTs on an isolated replica set."""
from datetime import timedelta
from fnmatch import fnmatch
import os
from types import SimpleNamespace
from uuid import uuid4

from beanie import init_beanie
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from app.core.config import settings
from app.core import cache
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.rate_limiter import limiter
from app.core.security import issue_token_pair
from app.main import app
from app.models.actor import Actor
from app.models.application_review import ApplicationReview
from app.models.application_stage_event import ApplicationStageEvent
from app.models.company_branch import CompanyBranch
from app.models.job_application import JobApplication
from app.models.job_requirement import JobRequirement, JobStatus
from app.models.job_scorecard import JobScorecard
from app.models.resume_file import ResumeFile
from app.models.screening_override import ScreeningOverride
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ScreeningRun
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.utils.time import now_utc
from app.services.processing_service import ProcessingService
from app.services.recruitment_service import ScreeningService
from test_screening_profile_transactions import data, grant, output

pytestmark = pytest.mark.asyncio
PERMISSIONS = {"job_applications:create", "job_applications:view", "job_applications:list",
               "application_stage_events:create", "job_scorecards:create", "job_scorecards:view",
               "application_reviews:create", "application_reviews:list"}


class MemoryRedis:
    """Business cache test double; JWT session state remains in real Mongo."""
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def scan_iter(self, match, count=100):
        for key in list(self.values):
            if fnmatch(key, match):
                yield key

    async def eval(self, script, numkeys, key, processing, completed, ttl):
        if self.values.get(key) in (None, processing, completed):
            self.values[key] = completed
            return 1
        return 0


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "recruitment_scope_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(limiter, "enabled", False)
    cache = MemoryRedis()
    monkeypatch.setattr("app.core.cache.get_redis", lambda: cache)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    try:
        yield cache
    finally:
        assert name.startswith("recruitment_scope_test_")
        await client.drop_database(name)
        client.close()


async def headers(user, permissions=PERMISSIONS, key=None):
    await grant(user, permissions)
    pair = await issue_token_pair(user)
    return {"Authorization": "Bearer " + pair.access_token, "Idempotency-Key": key or uuid4().hex}


def http_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend")


async def scenario():
    item = await data()
    other = await CompanyBranch(company_id=item.company.id, branch_name="Restricted", bussiness_type="IT",
                                address="Synthetic", company_size=1, working_days=[], created_by=item.owner.id).insert()
    hr = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    membership = await UserCompany(user_id=hr.id, company_branch_id=item.branch.id, role="member",
                                    assigned_by=item.owner.id).insert()
    other_job = await JobRequirement(user_id=item.owner.id, company_branch_id=other.id, title="Restricted",
                                     experience_level="junior").insert()
    other_application = await JobApplication(company_id=item.company.id, company_branch_id=other.id,
        candidate_id=item.candidate.id, resume_file_id=item.resume.id, job_requirement_id=other_job.id,
        applied_by=item.owner.id).insert()
    other_scorecard = await JobScorecard(company_id=item.company.id, job_requirement_id=other_job.id,
        created_by=item.owner.id, criteria=[{"code": "python", "name": "Python", "weight": 1}], pass_threshold=70).insert()
    return SimpleNamespace(**vars(item), other_branch=other, other_job=other_job,
                           other_application=other_application, other_scorecard=other_scorecard,
                           hr=hr, membership=membership)


async def snapshot():
    result = {}
    for model in (JobApplication, JobRequirement, JobScorecard, ApplicationReview, ApplicationStageEvent):
        result[model.__name__] = await model.get_motor_collection().find({}).sort("_id").to_list(length=None)
    return result


def review_body(item, application=None, round=1):
    return {"company_id": str(item.company.id), "application_id": str((application or item.application).id),
            "review_round": round, "score": 80, "recommendation": "yes", "summary": "Synthetic review"}


@pytest.mark.parametrize("access", ["allowed", "job_other_branch", "cv_other_branch", "owner", "superuser"])
async def test_application_create_requires_access_to_job_and_cv_branches(access):
    item = await scenario()
    job_branch = item.other_branch if access == "job_other_branch" else item.branch
    job = await JobRequirement(user_id=item.owner.id, company_branch_id=job_branch.id, title="New application",
                               experience_level="junior").insert()
    if access == "cv_other_branch":
        await item.resume.set({"company_branch_id": item.other_branch.id})
    caller = item.hr
    if access == "owner":
        caller = item.owner
    elif access == "superuser":
        caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused",
                             is_superuser=True, is_verified=True).insert()
    authorization = await headers(caller)
    before = await snapshot()
    body = {"company_id": str(item.company.id), "candidate_id": str(item.candidate.id),
            "resume_file_id": str(item.resume.id), "job_requirement_id": str(job.id)}
    async with http_client() as client:
        response = await client.post("/api/v1/recruitment/applications", headers=authorization, json=body)
    denied = access in {"job_other_branch", "cv_other_branch"}
    assert response.status_code == (403 if denied else 201), response.text
    if denied:
        assert await snapshot() == before
    else:
        assert response.json()["data"]["company_branch_id"] == str(job_branch.id)
        assert await JobApplication.find_all().count() == len(before["JobApplication"]) + 1


@pytest.mark.parametrize("operation", ["application_get", "stage", "scorecard_get", "scorecard_create", "review_create", "review_list"])
async def test_hr_permission_does_not_open_resources_in_another_branch(operation):
    item = await scenario()
    authorization = await headers(item.hr)
    company = {"company_id": str(item.company.id)}
    before = await snapshot()
    async with http_client() as client:
        if operation == "application_get":
            response = await client.get(f"/api/v1/recruitment/applications/{item.other_application.id}", params=company, headers=authorization)
        elif operation == "stage":
            response = await client.post(f"/api/v1/recruitment/applications/{item.other_application.id}/stage", params=company,
                                         headers=authorization, json={"to_stage": "screening"})
        elif operation == "scorecard_get":
            response = await client.get("/api/v1/recruitment/scorecards/active", headers=authorization,
                                        params={**company, "job_requirement_id": str(item.other_job.id)})
        elif operation == "scorecard_create":
            response = await client.post("/api/v1/recruitment/scorecards", headers=authorization, json={
                **company, "job_requirement_id": str(item.other_job.id),
                "criteria": [{"code": "python", "name": "Python", "weight": 1}]})
        elif operation == "review_create":
            response = await client.post("/api/v1/recruitment/reviews", headers=authorization,
                                         json=review_body(item, item.other_application))
        else:
            response = await client.get(f"/api/v1/recruitment/applications/{item.other_application.id}/reviews",
                                        headers=authorization, params=company)
    assert response.status_code == 403, response.text
    assert await snapshot() == before


@pytest.mark.parametrize("caller_type", ["owner", "superuser"])
async def test_owner_and_persisted_superuser_can_read_and_write_recruitment_in_each_branch(caller_type):
    item = await scenario()
    caller = item.owner if caller_type == "owner" else await User(email=uuid4().hex + "@example.com",
        hashed_password="unused", is_verified=True, is_superuser=True).insert()
    authorization = await headers(caller)
    company = {"company_id": str(item.company.id)}
    async with http_client() as client:
        detail = await client.get(f"/api/v1/recruitment/applications/{item.other_application.id}", params=company, headers=authorization)
        listing = await client.get("/api/v1/recruitment/applications", params=company, headers=authorization)
        scorecard = await client.get("/api/v1/recruitment/scorecards/active", headers=authorization,
                                     params={**company, "job_requirement_id": str(item.other_job.id)})
        changed_scorecard = await client.post("/api/v1/recruitment/scorecards", headers=authorization,
            json={**company, "job_requirement_id": str(item.other_job.id), "pass_threshold": 72,
                  "criteria": [{"code": "python", "name": "Python", "weight": 1}]})
        review = await client.post("/api/v1/recruitment/reviews", headers=authorization,
                                    json=review_body(item, item.other_application))
        stage = await client.post(f"/api/v1/recruitment/applications/{item.other_application.id}/stage", params=company,
                                   headers=authorization, json={"to_stage": "screening"})
    assert detail.status_code == listing.status_code == scorecard.status_code == stage.status_code == 200
    assert review.status_code == changed_scorecard.status_code == 201 and listing.json()["data"]["total"] == 2
    assert changed_scorecard.json()["data"]["version"] == 2


@pytest.mark.parametrize("operation", ["get", "stage", "list", "review"])
async def test_application_access_requires_cv_scope_after_resume_moves_to_another_branch(operation):
    item = await scenario()
    await item.resume.set({"company_branch_id": item.other_branch.id})
    authorization = await headers(item.hr)
    company = {"company_id": str(item.company.id)}
    before = await snapshot()
    async with http_client() as client:
        if operation == "get":
            response = await client.get(f"/api/v1/recruitment/applications/{item.application.id}", params=company, headers=authorization)
        elif operation == "stage":
            response = await client.post(f"/api/v1/recruitment/applications/{item.application.id}/stage", params=company,
                                         headers=authorization, json={"to_stage": "screening"})
        elif operation == "review":
            response = await client.post("/api/v1/recruitment/reviews", headers=authorization, json=review_body(item))
        else:
            response = await client.get("/api/v1/recruitment/applications", params=company, headers=authorization)
    if operation == "list":
        assert response.status_code == 200 and response.json()["data"]["total"] == 0
        assert response.json()["data"]["items"] == []
    else:
        assert response.status_code == 403, response.text
    assert await snapshot() == before


async def test_warm_application_and_scorecard_caches_cannot_widen_branch_scope(mock_db):
    item = await scenario()
    owner_headers, hr_headers = await headers(item.owner), await headers(item.hr)
    company = {"company_id": str(item.company.id)}
    # Simulate a legacy populated list cache. HR reads must apply current
    # application and CV scopes in Mongo even when this key contains other CVs.
    stale_key = cache.cache_key("application-list", item.company.id, 1, 20, "all", "all", str(item.branch.id))
    await cache.set_json(stale_key, {"items": [item.other_application.model_dump(mode="json")], "total": 99})
    async with http_client() as client:
        for _ in range(2):
            owner_list = await client.get("/api/v1/recruitment/applications", params=company, headers=owner_headers)
            owner_scorecard = await client.get("/api/v1/recruitment/scorecards/active", headers=owner_headers,
                params={**company, "job_requirement_id": str(item.other_job.id)})
            assert owner_list.status_code == owner_scorecard.status_code == 200
        assert any("application-list" in key for key in mock_db.values)
        assert any("scorecard" in key for key in mock_db.values)
        for _ in range(2):
            listing = await client.get("/api/v1/recruitment/applications", params=company, headers=hr_headers)
            assert listing.status_code == 200 and listing.json()["data"]["total"] == 1
            assert [row["id"] for row in listing.json()["data"]["items"]] == [str(item.application.id)]
            forbidden = await client.get("/api/v1/recruitment/scorecards/active", headers=hr_headers,
                params={**company, "job_requirement_id": str(item.other_job.id)})
            assert forbidden.status_code == 403
        filtered = await client.get("/api/v1/recruitment/applications", headers=hr_headers,
                                     params={**company, "job_requirement_id": str(item.other_job.id)})
        assert filtered.status_code == 200 and filtered.json()["data"]["total"] == 0
        await item.membership.set({"is_active": False})
        before = await snapshot()
        listing = await client.get("/api/v1/recruitment/applications", params=company, headers=hr_headers)
        assert listing.status_code == 403 and await snapshot() == before


async def test_warm_application_cache_rechecks_cv_scope_when_source_branch_changes(mock_db):
    item = await scenario()
    authorization = await headers(item.hr)
    company = {"company_id": str(item.company.id)}
    stale_key = cache.cache_key("application-list", item.company.id, 1, 20, "all", "all", str(item.branch.id))
    await cache.set_json(stale_key, {"items": [item.application.model_dump(mode="json")], "total": 1})
    async with http_client() as client:
        first = await client.get("/api/v1/recruitment/applications", params=company, headers=authorization)
        assert first.status_code == 200 and first.json()["data"]["total"] == 1
        assert any("application-list" in key for key in mock_db.values)
        await item.resume.set({"company_branch_id": item.other_branch.id})
        before = await snapshot()
        second = await client.get("/api/v1/recruitment/applications", params=company, headers=authorization)
    assert second.status_code == 200 and second.json()["data"]["total"] == 0
    assert second.json()["data"]["items"] == [] and await snapshot() == before


@pytest.mark.parametrize("membership_state", ["expired", "revoked"])
async def test_stage_replay_rechecks_membership_before_returning_prior_result(membership_state):
    item = await scenario()
    authorization = await headers(item.hr)
    company = {"company_id": str(item.company.id)}
    url = f"/api/v1/recruitment/applications/{item.application.id}/stage"
    async with http_client() as client:
        created = await client.post(url, params=company, headers=authorization, json={"to_stage": "screening"})
        assert created.status_code == 200, created.text
        await item.membership.set({"end_date": now_utc() - timedelta(seconds=1)} if membership_state == "expired" else {"is_active": False})
        before = await snapshot()
        replay = await client.post(url, params=company, headers=authorization, json={"to_stage": "screening"})
    assert replay.status_code == 403, replay.text
    assert await snapshot() == before


async def test_reviews_return_duplicate_conflict_without_changes_and_paginate():
    item = await scenario()
    authorization = await headers(item.hr)
    async with http_client() as client:
        for round in range(1, 4):
            response = await client.post("/api/v1/recruitment/reviews", headers=authorization, json=review_body(item, round=round))
            assert response.status_code == 201, response.text
        before = await snapshot()
        duplicate = await client.post("/api/v1/recruitment/reviews", headers=authorization, json=review_body(item))
        assert duplicate.status_code == 409, duplicate.text
        assert await snapshot() == before
        url = f"/api/v1/recruitment/applications/{item.application.id}/reviews"
        first = await client.get(url, headers=authorization, params={"company_id": str(item.company.id), "page": 1, "size": 2})
        second = await client.get(url, headers=authorization, params={"company_id": str(item.company.id), "page": 2, "size": 2})
        invalid = await client.get(url, headers=authorization, params={"company_id": str(item.company.id), "size": 101})
    assert first.status_code == second.status_code == 200 and invalid.status_code == 422
    assert [row["review_round"] for row in first.json()["data"]] == [3, 2]
    assert [row["review_round"] for row in second.json()["data"]] == [1]
    assert all(row["reviewer_id"] == str(item.hr.id) for row in first.json()["data"])


async def test_candidate_own_application_creation_and_reads_remain_available_without_membership():
    item = await scenario()
    candidate_user = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    await item.candidate.set({"user_id": candidate_user.id})
    actor = await Actor(name=settings.CANDIDATE_ROLE_NAME, is_active=True).insert()
    await UserActor(user_id=candidate_user.id, actor_id=actor.id, created_by=item.owner.id).insert()
    authorization = await headers(candidate_user, {"job_applications:create", "job_applications:view", "job_applications:list"})
    job = await JobRequirement(user_id=item.owner.id, company_branch_id=item.branch.id, title="Published",
                               experience_level="junior", status=JobStatus.PUBLISHED).insert()
    body = {"company_id": str(item.company.id), "candidate_id": str(item.candidate.id),
            "resume_file_id": str(item.resume.id), "job_requirement_id": str(job.id)}
    async with http_client() as client:
        created = await client.post("/api/v1/recruitment/applications", headers=authorization, json=body)
        assert created.status_code == 201, created.text
        application_id = created.json()["data"]["id"]
        detail = await client.get(f"/api/v1/recruitment/applications/{application_id}", headers=authorization,
                                   params={"company_id": str(item.company.id)})
        listing = await client.get("/api/v1/recruitment/applications", headers=authorization,
                                    params={"company_id": str(item.company.id)})
        denied = await client.get(f"/api/v1/recruitment/applications/{item.other_application.id}", headers=authorization,
                                   params={"company_id": str(item.company.id)})
    assert detail.status_code == listing.status_code == 200 and denied.status_code == 403
    assert listing.json()["data"]["total"] == 1
    assert [row["id"] for row in listing.json()["data"]["items"]] == [application_id]


@pytest.mark.parametrize("job_state", ["missing", "branch_mismatch", "inactive", "legacy_application_branch"])
@pytest.mark.parametrize("caller_type", ["hr", "owner"])
async def test_application_list_checks_live_job_scope_before_count_and_pagination(job_state, caller_type):
    item = await scenario()
    # Keep one accessible application behind the newer target. Filtering after
    # pagination would incorrectly produce an empty first page or a wrong count.
    await item.other_job.set({"company_branch_id": item.branch.id})
    await item.other_application.set({"company_branch_id": item.branch.id,
                                      "created_at": now_utc() - timedelta(days=1)})
    if job_state == "missing":
        await item.job.delete()
    elif job_state == "branch_mismatch":
        await item.job.set({"company_branch_id": item.other_branch.id})
    elif job_state == "inactive":
        await item.job.set({"status": JobStatus.ARCHIVED.value, "is_active": False})
    else:
        await JobApplication.get_motor_collection().update_one(
            {"_id": item.application.id}, {"$unset": {"company_branch_id": ""}})
    caller = item.owner if caller_type == "owner" else item.hr
    authorization = await headers(caller)
    before = await snapshot()
    params = {"company_id": str(item.company.id), "size": 1}
    async with http_client() as client:
        first = await client.get("/api/v1/recruitment/applications", headers=authorization, params={**params, "page": 1})
        second = await client.get("/api/v1/recruitment/applications", headers=authorization, params={**params, "page": 2})
        detail = await client.get(f"/api/v1/recruitment/applications/{item.application.id}", headers=authorization,
                                  params={"company_id": str(item.company.id)})
    assert first.status_code == second.status_code == 200
    valid_target = job_state in {"inactive", "legacy_application_branch"}
    assert first.json()["data"]["total"] == second.json()["data"]["total"] == (2 if valid_target else 1)
    expected_first = item.application.id if valid_target else item.other_application.id
    assert [row["id"] for row in first.json()["data"]["items"]] == [str(expected_first)]
    assert [row["id"] for row in second.json()["data"]["items"]] == ([str(item.other_application.id)] if valid_target else [])
    assert detail.status_code == ({"missing": 404, "branch_mismatch": 403}.get(job_state, 200)), detail.text
    assert await snapshot() == before


@pytest.mark.parametrize("resume_state", ["active", "deleted", "missing"])
@pytest.mark.parametrize("caller_type", ["hr", "owner", "superuser"])
async def test_screening_history_keeps_branchless_cv_scope_for_all_read_routes(resume_state, caller_type):
    item = await scenario()
    await item.resume.set({"company_branch_id": None})
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    owner_headers = await headers(item.owner, {"screening_runs:view", "screening_results:override"})
    base_url = f"/api/v1/recruitment/screenings/{run.id}"
    async with http_client() as client:
        recorded = await client.post(base_url + "/overrides", headers=owner_headers, json={
            "company_id": str(item.company.id), "expected_version": 0, "overall_score": 95,
            "decision": "pass", "reason": "Synthetic confirmation", "comments": "Private HR review",
        })
    assert recorded.status_code == 201, recorded.text
    if resume_state == "deleted":
        await item.resume.set({"is_deleted": True})
    elif resume_state == "missing":
        await item.resume.delete()
    caller = item.hr
    if caller_type == "owner":
        caller = item.owner
    elif caller_type == "superuser":
        caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused",
                             is_superuser=True, is_verified=True).insert()
    authorization = await headers(caller, {"screening_runs:view"})
    models = (ResumeFile, ScreeningRun, ScreeningResult, ScreeningOverride, JobApplication, ApplicationStageEvent)
    before = {model.__name__: await model.get_motor_collection().find({}).sort("_id").to_list(length=None)
              for model in models}
    async with http_client() as client:
        responses = {}
        for endpoint in ("", "/result", "/assessment", "/overrides"):
            response = await client.get(base_url + endpoint, headers=authorization,
                                        params={"company_id": str(item.company.id)})
            assert response.status_code == (403 if caller_type == "hr" else 200), response.text
            responses[endpoint] = response.json()
    if caller_type != "hr":
        assert responses["/result"]["data"]["id"] == str(result.id)
        assert responses["/result"]["data"]["criteria_evaluations"]["python"]["evidence"][0]["text"] == "Python"
        assert responses["/result"]["data"]["source_is_current"] == (resume_state == "active")
        assert responses["/assessment"]["data"]["effective"]["source"] == "hr_override"
        assert responses["/assessment"]["data"]["effective"]["comments"] == "Private HR review"
        assert responses["/overrides"]["data"]["total"] == 1
    for model in models:
        assert await model.get_motor_collection().find({}).sort("_id").to_list(length=None) == before[model.__name__]
