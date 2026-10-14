"""Append-only HR profile revisions on an explicit, isolated Mongo replica set."""
import asyncio
from datetime import timedelta
import os
from unittest.mock import AsyncMock
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import SecretStr
import pytest

from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology, _run_pre_init_index_migrations
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, issue_token_pair
from app.main import app
from app.models.application_stage_event import ApplicationStageEvent
from app.models.company_branch import CompanyBranch
from app.models.job_application import JobApplication
from app.models.resume_extraction import ResumeExtractionRun, ResumeProfile
from app.models.resume_file import ResumeFile
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.models.user import User
from app.models.user_company import UserCompany
from app.schemas.resume_profile_review import ProfileRevisionCreate
from app.schemas.extraction import ExtractionServiceOutput
from app.schemas.worker import ScreeningOutput
from app.services.processing_service import ProcessingService
from app.services.recruitment_service import ScreeningService
from app.services.resume_profile_review_service import ResumeProfileReviewService
from app.services.extraction_service import ExtractionService
from app.utils.time import now_utc
from test_screening_profile_transactions import data, grant, output, SOURCE

pytestmark = pytest.mark.asyncio
EDIT_PERMISSIONS = {"resume_profiles:view", "resume_profiles:edit"}


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "hr_profile_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SCREENING_API_URL", "http://screening-service:8003")
    monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr("synthetic-token-" + "x" * 32))
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("hr_profile_test_")
        await client.drop_database(name)
        client.close()


def fact(value="Synthetic HR-only skill"):
    return {"section": "skills", "label": "Verified skill", "value": value,
            "reason": "Confirmed in interview"}


def command(parent, **values):
    return ProfileRevisionCreate.model_validate({
        "base_profile_id": str(parent.id), "expected_version": parent.version,
        "reason": "HR confirmed skill in interview", "hr_annotations": [fact()], **values,
    })


def reviewer(user):
    return CurrentUser(user, permission_names=EDIT_PERMISSIONS)


async def http_headers(user, permissions=EDIT_PERMISSIONS, key=None):
    await grant(user, permissions)
    pair = await issue_token_pair(user)
    return {"Authorization": "Bearer " + pair.access_token, "Idempotency-Key": key or uuid4().hex}


def http_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend")


async def snapshot(item):
    return {
        "resume": await ResumeFile.get_motor_collection().find_one({"_id": item.resume.id}),
        "profiles": await ResumeProfile.get_motor_collection().find({}).sort("_id").to_list(length=None),
        "application": await JobApplication.get_motor_collection().find_one({"_id": item.application.id}),
    }


@pytest.mark.parametrize("access", ["allowed", "no_edit", "no_view", "other_branch", "expired", "future", "revoked", "inactive_user", "inactive_branch"])
async def test_http_profile_edit_requires_both_permissions_and_live_branch_access(access):
    item = await data()
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    branch = item.branch
    if access == "other_branch":
        branch = await CompanyBranch(company_id=item.company.id, branch_name="Other", bussiness_type="IT",
                                     address="Synthetic", company_size=1, working_days=[], created_by=item.owner.id).insert()
    values = {"expired": {"end_date": now_utc() - timedelta(seconds=1)},
              "future": {"start_date": now_utc() + timedelta(days=1)}, "revoked": {"is_active": False}}
    await UserCompany(user_id=caller.id, company_branch_id=branch.id, role="member",
                      assigned_by=item.owner.id, **values.get(access, {})).insert()
    permissions = set(EDIT_PERMISSIONS)
    if access == "no_edit":
        permissions.remove("resume_profiles:edit")
    elif access == "no_view":
        permissions.remove("resume_profiles:view")
    headers = await http_headers(caller, permissions)
    if access == "inactive_user":
        await caller.set({"is_active": False})
    if access == "inactive_branch":
        await branch.set({"is_active": False})
    before = await snapshot(item)
    async with http_client() as client:
        response = await client.post(f"/api/v1/resumes/{item.resume.id}/profile-revisions",
                                     json=command(item.profile).model_dump(mode="json"), headers=headers)
    assert response.status_code == (201 if access == "allowed" else 401 if access == "inactive_user" else 403), response.text
    if access == "allowed":
        value = response.json()["data"]
        assert value["origin"] == "hr" and value["edited_by"] == str(caller.id)
        assert value["parent_profile_id"] == str(item.profile.id) and value["version"] == 2
        assert value["hr_annotations"][0]["confirmed_by"] == str(caller.id)
        assert value["hr_annotations"][0]["id"] and value["hr_annotations"][0]["confirmed_at"].endswith("Z")
        assert value["is_current"]
        assert (await ResumeProfile.get(item.profile.id)).profile == item.profile.profile
    else:
        assert await snapshot(item) == before


@pytest.mark.parametrize("bad_input", ["fake_quote", "fake_hash", "hr_id", "hr_reviewer", "hr_timestamp"])
async def test_http_rejects_fabricated_cv_evidence_and_client_hr_provenance_without_writes(bad_input):
    item = await data()
    headers = await http_headers(item.owner)
    body = command(item.profile).model_dump(mode="json")
    if bad_input in {"fake_quote", "fake_hash"}:
        body["profile"] = item.profile.profile.model_dump(mode="json")
        if bad_input == "fake_hash":
            body["profile"]["source_text_sha256"] = "0" * 64
        else:
            body["profile"]["personal_info"]["full_name"]["evidence"][0].update(
                quote="Invented identity", start=0, end=len("Invented identity")
            )
    else:
        key = {"hr_id": "id", "hr_reviewer": "confirmed_by", "hr_timestamp": "confirmed_at"}[bad_input]
        body["hr_annotations"][0][key] = str(ObjectId()) if key != "confirmed_at" else "2026-10-14T00:00:00Z"
    before = await snapshot(item)
    async with http_client() as client:
        response = await client.post(f"/api/v1/resumes/{item.resume.id}/profile-revisions", json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert await snapshot(item) == before


async def test_revisions_carry_or_replace_facts_preserve_ai_original_and_replay_historical_command():
    item = await data()
    caller = reviewer(item.owner)
    first_command, first_key = command(item.profile), uuid4().hex
    first, current = await ResumeProfileReviewService.revise(str(item.resume.id), first_command, first_key, caller)
    assert current and first.version == 2 and first.origin == "hr"
    assert first.extraction_run_id == item.profile.extraction_run_id
    assert first.hr_annotations[0].confirmed_by == item.owner.id
    assert first.hr_annotations[0].value not in SOURCE
    assert (await ResumeFile.get(item.resume.id)).screening_fence_version == 1
    replay, current = await ResumeProfileReviewService.revise(str(item.resume.id), first_command, first_key, caller)
    assert replay.id == first.id and current and await ResumeProfile.find_all().count() == 2
    second, current = await ResumeProfileReviewService.revise(str(item.resume.id), command(
        first, hr_annotations=None, profile=first.profile.model_dump(mode="json")), uuid4().hex, caller)
    assert current and second.version == 3 and second.parent_profile_id == first.id
    assert second.hr_annotations == first.hr_annotations
    replay, current = await ResumeProfileReviewService.revise(str(item.resume.id), first_command, first_key, caller)
    assert replay.id == first.id and not current
    third, _ = await ResumeProfileReviewService.revise(str(item.resume.id), command(second, hr_annotations=[]), uuid4().hex, caller)
    assert third.version == 4 and third.hr_annotations == []
    original = await ResumeProfile.get(item.profile.id)
    assert original.origin == "ai" and original.version == 1 and original.profile == item.profile.profile
    assert original.hr_annotations == [] and original.edited_by is None
    assert (await ResumeFile.get(item.resume.id)).latest_extraction_profile_id == third.id
    assert await ResumeProfile.find_all().count() == 4


@pytest.mark.parametrize("mismatch", ["body", "reviewer", "stale_base", "stale_version"])
async def test_revision_rejects_idempotency_mismatch_or_stale_base_without_writes(mismatch):
    item = await data()
    caller = reviewer(item.owner)
    original, key = command(item.profile), uuid4().hex
    await ResumeProfileReviewService.revise(str(item.resume.id), original, key, caller)
    update, new_key = original, key
    if mismatch == "body":
        update = original.model_copy(update={"reason": "Different command"})
    elif mismatch == "reviewer":
        other = await User(email=uuid4().hex + "@example.com", hashed_password="unused").insert()
        await UserCompany(user_id=other.id, company_branch_id=item.branch.id, assigned_by=item.owner.id).insert()
        caller = reviewer(other)
    elif mismatch == "stale_base":
        new_key = uuid4().hex
    else:
        current = await ResumeProfile.find_one({"resume_file_id": item.resume.id, "version": 2})
        update, new_key = command(current, expected_version=1), uuid4().hex
    before = await snapshot(item)
    with pytest.raises(CustomError) as caught:
        await ResumeProfileReviewService.revise(str(item.resume.id), update, new_key, caller)
    assert caught.value.status_code == 409
    assert await snapshot(item) == before


@pytest.mark.parametrize("kind", ["parse", "extraction"])
async def test_profile_edit_waits_for_active_parsing_or_extraction(kind):
    item = await data()
    common = {"company_id": item.company.id, "resume_file_id": item.resume.id,
              "triggered_by": item.owner.id, "idempotency_key": uuid4().hex, "input_hash": "a" * 64}
    if kind == "parse":
        await ResumeParseRun(**common, parser_version="synthetic").insert()
    else:
        await ResumeExtractionRun(**common, source_text=SOURCE, source_text_sha256=item.profile.profile.source_text_sha256,
                                  source_parse_revision=1, model_name="synthetic").insert()
    before = await snapshot(item)
    with pytest.raises(CustomError) as caught:
        await ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner))
    assert caught.value.status_code == 409 and await snapshot(item) == before


async def test_two_concurrent_profile_writers_have_one_committed_revision(monkeypatch):
    item = await data()
    collection = ResumeFile.get_motor_collection()
    original_update, arrived, release = collection.update_one, 0, asyncio.Event()

    async def barrier(*args, **kwargs):
        nonlocal arrived
        if "latest_extraction_profile_id" in args[1].get("$set", {}):
            arrived += 1
            if arrived == 2:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(collection, "update_one", barrier)
    results = await asyncio.wait_for(asyncio.gather(
        ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner)),
        ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner)),
        return_exceptions=True,
    ), timeout=20)
    winners = [result[0] for result in results if isinstance(result, tuple)]
    losers = [result for result in results if isinstance(result, CustomError)]
    assert arrived == 2 and len(winners) == len(losers) == 1 and losers[0].status_code == 409
    assert await ResumeProfile.find_all().count() == 2
    resume = await ResumeFile.get(item.resume.id)
    assert resume.latest_extraction_profile_id == winners[0].id and resume.extraction_revision == 2
    assert resume.screening_fence_version == 1


async def test_profile_insert_failure_rolls_back_pointer_revision_and_fence(monkeypatch):
    item = await data()
    before = await snapshot(item)
    monkeypatch.setattr(ResumeProfile, "insert", AsyncMock(side_effect=RuntimeError("synthetic insert failure")))
    with pytest.raises(RuntimeError, match="synthetic insert failure"):
        await ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner))
    assert await snapshot(item) == before


async def test_hr_profile_change_invalidates_active_screening_and_new_run_freezes_confirmation():
    item = await data()
    old = await ScreeningService.start(item.request, uuid4().hex, item.current)
    claimed = await ProcessingService.claim_screening(str(old.id), "old-worker")
    revised, _ = await ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner))
    # Active screening does not block HR editing. Its old source must fail closed.
    application_before = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    events_before = await ApplicationStageEvent.find_all().count()
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(old.id), "old-worker", output(), generation=claimed.attempt)
    assert caught.value.status_code == 422
    assert await ScreeningResult.find_all().count() == 0
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == application_before
    assert await ApplicationStageEvent.find_all().count() == events_before
    assert await ProcessingService.fail_screening(str(old.id), "old-worker", "source_changed", "synthetic", retryable=False,
                                                   generation=claimed.attempt)
    new = await ScreeningService.start(item.request, uuid4().hex, item.current)
    assert new.source_snapshot.profile_origin == "hr" and new.source_snapshot.resume_profile_id == revised.id
    assert new.source_snapshot.profile_edited_by == item.owner.id and new.source_snapshot.profile_edit_reason == revised.edit_reason
    assert new.source_snapshot.hr_annotations == revised.hr_annotations
    new_claim = await ProcessingService.claim_screening(str(new.id), "new-worker")
    value = revised.hr_annotations[0]
    body = {"criteria_evaluations": {"python": {"status": "evaluated", "score": 90, "reason": "Confirmed by HR",
            "evidence": [{"origin": "hr", "hr_fact_id": str(value.id), "text": value.value}]}}}
    wrong = ScreeningOutput.model_validate({"criteria_evaluations": {"python": {
        **body["criteria_evaluations"]["python"], "evidence": [{"origin": "hr", "hr_fact_id": str(ObjectId()), "text": value.value}]}}})
    with pytest.raises(CustomError) as caught:
        await ProcessingService.complete_screening(str(new.id), "new-worker", wrong, generation=new_claim.attempt)
    assert caught.value.status_code == 422 and await ScreeningResult.find_all().count() == 0
    result = await ProcessingService.complete_screening(str(new.id), "new-worker", ScreeningOutput.model_validate(body), generation=new_claim.attempt)
    assert result.overall_score == 90 and result.source_snapshot == new.source_snapshot
    assert result.criteria_evaluations["python"].evidence[0].hr_fact_id == value.id
    assert (await ScreeningRun.get(new.id)).status == "completed"


async def test_http_profile_history_is_paginated_and_permission_scoped():
    item = await data()
    first, _ = await ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner))
    second, _ = await ResumeProfileReviewService.revise(str(item.resume.id), command(first), uuid4().hex, reviewer(item.owner))
    headers = await http_headers(item.owner, {"resume_profiles:view"})
    url = f"/api/v1/resumes/{item.resume.id}/profiles"
    async with http_client() as client:
        page = await client.get(url, headers=headers, params={"page": 1, "size": 2})
        next_page = await client.get(url, headers=headers, params={"page": 2, "size": 2})
        invalid = await client.get(url, headers=headers, params={"size": 101})
        assert page.status_code == next_page.status_code == 200 and invalid.status_code == 422
        assert page.json()["data"]["total"] == 3
        assert [row["id"] for row in page.json()["data"]["items"]] == [str(second.id), str(first.id)]
        assert [row["is_current"] for row in page.json()["data"]["items"]] == [True, False]
        assert next_page.json()["data"]["items"][0]["origin"] == "ai"
        outsider = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
        outsider_headers = await http_headers(outsider, {"resume_profiles:view"})
        assert (await client.get(url, headers=outsider_headers)).status_code == 403


async def test_legacy_profile_index_migration_backfills_ai_origin_and_allows_hr_revisions(mock_db):
    item = await data()
    collection = ResumeProfile.get_motor_collection()
    await collection.update_one({"_id": item.profile.id}, {"$unset": {"origin": ""}})
    await collection.create_index("extraction_run_id", unique=True, name="extraction_run_id_1")
    await _run_pre_init_index_migrations(mock_db)
    await _run_pre_init_index_migrations(mock_db)
    assert "extraction_run_id_1" not in await collection.index_information()
    assert (await collection.find_one({"_id": item.profile.id}))["origin"] == "ai"
    revised, _ = await ResumeProfileReviewService.revise(str(item.resume.id), command(item.profile), uuid4().hex, reviewer(item.owner))
    assert revised.extraction_run_id == item.profile.extraction_run_id and await ResumeProfile.find_all().count() == 2


async def test_legacy_resume_without_parse_revision_can_start_and_complete_extraction(monkeypatch):
    item = await data()
    monkeypatch.setattr(settings, "EXTRACTION_API_URL", "http://extractor:8002")
    monkeypatch.setattr(settings, "EXTRACTION_MODEL_NAME", "synthetic-extraction-model")
    monkeypatch.setattr(settings, "EXTRACTION_SERVICE_TOKEN", SecretStr("synthetic-extraction-token-" + "x" * 32))
    collection = ResumeFile.get_motor_collection()
    await collection.update_one({"_id": item.resume.id}, {"$unset": {"parse_revision": ""}})
    caller = CurrentUser(item.owner, permission_names={"resume_files:extract", "resume_profiles:view"})
    key = uuid4().hex
    run = await ExtractionService.start(str(item.resume.id), str(item.company.id), key, caller)
    assert run.source_parse_revision == 0
    assert (await ExtractionService.start(str(item.resume.id), str(item.company.id), key, caller)).id == run.id
    assert await ResumeExtractionRun.find_all().count() == 1
    claimed = await ExtractionService.claim(str(run.id), "legacy-worker")
    controlled = ExtractionServiceOutput(profile=item.profile.profile, model_name=run.model_name,
        prompt_version=run.prompt_version, extractor_version=item.profile.extractor_version)
    assert await ExtractionService.complete(str(run.id), "legacy-worker", controlled, generation=claimed.attempt)
    current, is_current = await ExtractionService.get_profile(str(item.resume.id), caller)
    assert is_current and current.source_parse_revision == 0 and current.version == 2
    assert current.extraction_run_id == run.id and current.origin == "ai"
    stored_resume = await collection.find_one({"_id": item.resume.id})
    assert "parse_revision" not in stored_resume
    assert stored_resume["latest_extraction_profile_id"] == current.id
    assert stored_resume["screening_fence_version"] == 1
    assert (await ResumeExtractionRun.get(run.id)).status == "completed"
