"""Immutable HR overrides on a separate explicit Mongo replica-set test DB."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, issue_token_pair
from app.main import app
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.job_application import JobApplication
from app.models.resume_file import ResumeFile
from app.models.screening_override import ScreeningOverride
from app.models.screening_result import ScreeningResult
from app.models.screening_run import ScreeningRun
from app.models.user import User
from app.models.user_company import UserCompany
from app.schemas.screening_override import ScreeningOverrideCreate
from app.services.processing_service import ProcessingService
from app.services.recruitment_service import ScreeningService
from app.services.screening_override_service import ScreeningOverrideService, override_response
from test_screening_profile_transactions import data, output, grant, create_profile

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "screening_override_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("screening_override_test_")
        await client.drop_database(name)
        client.close()


def command(company_id, *, expected_version=0, score=95, decision="pass", reason="HR đã xác minh kỹ năng với ứng viên."):
    return ScreeningOverrideCreate(company_id=str(company_id), expected_version=expected_version,
                                   overall_score=score, decision=decision, reason=reason)


async def completed():
    item = await data()
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    item.current = CurrentUser(item.owner, permission_names={"screening_runs:create", "resume_profiles:view",
                                                           "screening_runs:view", "screening_results:override"})
    return SimpleNamespace(**vars(item), run=run, result=result)


def immutable_result(document):
    return {key: value for key, value in document.items() if key not in {"override_revision", "latest_override_id"}}


@pytest.mark.parametrize("access", ["allowed", "no_override", "no_view", "other_company", "other_application_branch", "other_resume_branch", "inactive_membership", "inactive_user"])
async def test_http_override_requires_live_permissions_and_resource_branches(access):
    item = await completed()
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    membership_branch = item.branch
    if access == "other_company":
        company = await Company(user_id=caller.id, name="Other", company_short_name="O", company_code=uuid4().hex,
                                email=caller.email, website="https://example.com").insert()
        membership_branch = await CompanyBranch(company_id=company.id, branch_name="Other", bussiness_type="IT",
                                                address="Other", company_size=1, working_days=[], created_by=caller.id).insert()
    elif access in {"other_application_branch", "other_resume_branch"}:
        branch = await CompanyBranch(company_id=item.company.id, branch_name="Restricted", bussiness_type="IT",
                                      address="Other", company_size=1, working_days=[], created_by=item.owner.id).insert()
        target = item.application if access == "other_application_branch" else item.resume
        await target.set({"company_branch_id": branch.id})
    await UserCompany(user_id=caller.id, company_branch_id=membership_branch.id, role="recruiter",
                      is_active=access != "inactive_membership", assigned_by=item.owner.id).insert()
    permissions = {"screening_runs:view", "screening_results:override"}
    if access == "no_override":
        permissions.remove("screening_results:override")
    if access == "no_view":
        permissions.remove("screening_runs:view")
    await grant(caller, permissions)
    pair = await issue_token_pair(caller)
    if access == "inactive_user":
        await caller.set({"is_active": False})
    results = ScreeningResult.get_motor_collection()
    resumes = ResumeFile.get_motor_collection()
    runs = ScreeningRun.get_motor_collection()
    before_result = await results.find_one({"_id": item.result.id})
    before_resume = await resumes.find_one({"_id": item.resume.id})
    before_run = await runs.find_one({"_id": item.run.id})
    headers = {"Authorization": "Bearer " + pair.access_token, "Idempotency-Key": uuid4().hex}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.post(f"/api/v1/recruitment/screenings/{item.run.id}/overrides",
                                     json=command(item.company.id).model_dump(mode="json"), headers=headers)
        expected = 201 if access == "allowed" else (401 if access == "inactive_user" else 403)
        assert response.status_code == expected, response.text
        if access == "allowed":
            value = response.json()["data"]
            assert value["reviewer_id"] == str(caller.id) and value["version"] == 1
            assert value["ai_base"]["overall_score"] == 80 and value["overall_score"] == 95
            assert "source_text" not in value["ai_base"]
            assessment = await client.get(f"/api/v1/recruitment/screenings/{item.run.id}/assessment",
                                           params={"company_id": str(item.company.id)}, headers=headers)
            assert assessment.status_code == 200, assessment.text
            assessed = assessment.json()["data"]
            assert assessed["ai"]["overall_score"] == 80
            assert assessed["effective"]["overall_score"] == 95 and assessed["effective"]["source"] == "hr_override"
            assert assessed["source_is_current"] and assessed["is_latest_result"]
    after_result = await results.find_one({"_id": item.result.id})
    assert immutable_result(after_result) == immutable_result(before_result)
    assert await runs.find_one({"_id": item.run.id}) == before_run
    assert await ScreeningOverride.find_all().count() == (1 if access == "allowed" else 0)
    if access != "allowed":
        assert after_result == before_result
        assert await resumes.find_one({"_id": item.resume.id}) == before_resume


async def test_same_key_replay_keeps_old_command_without_replacing_a_later_override():
    item = await completed()
    key = uuid4().hex
    original = command(item.company.id)
    first = await ScreeningOverrideService.create(str(item.run.id), original, key, item.current)
    second = await ScreeningOverrideService.create(str(item.run.id), command(item.company.id, expected_version=1, score=None,
                                                                            decision="hold", reason="Chờ HR xác minh bổ sung."),
                                                   uuid4().hex, item.current)
    before_result = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    repeated = await ScreeningOverrideService.create(str(item.run.id), original, key, item.current)
    assert repeated.id == first.id and repeated.version == 1
    assert override_response(repeated).model_dump(mode="json") == override_response(first).model_dump(mode="json")
    assert second.version == 2
    assert await ScreeningOverride.find_all().count() == 2
    assert await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id}) == before_result
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application
    assessment = await ScreeningOverrideService.assessment(str(item.run.id), str(item.company.id), item.current)
    assert assessment.latest_override.id == str(second.id) and assessment.effective.overall_score is None
    assert assessment.ai.overall_score == 80 and assessment.review_required


@pytest.mark.parametrize("field,value", [("overall_score", 90), ("decision", "fail"), ("reason", "Lý do khác."),
                                          ("expected_version", 1), ("comments", "Nhận xét khác.")])
async def test_replayed_key_with_changed_payload_is_conflict_and_does_not_write(field, value):
    item = await completed()
    key = uuid4().hex
    original = command(item.company.id)
    await ScreeningOverrideService.create(str(item.run.id), original, key, item.current)
    before = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    changed = original.model_copy(update={field: value})
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), changed, key, item.current)
    assert caught.value.status_code == 409
    assert await ScreeningOverride.find_all().count() == 1
    assert await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id}) == before


async def test_override_key_cannot_be_reused_by_another_reviewer():
    item = await completed()
    key = uuid4().hex
    request = command(item.company.id)
    await ScreeningOverrideService.create(str(item.run.id), request, key, item.current)
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    await UserCompany(user_id=caller.id, company_branch_id=item.branch.id, role="recruiter", assigned_by=item.owner.id).insert()
    current = CurrentUser(caller, permission_names={"screening_runs:view", "screening_results:override"})
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), request, key, current)
    assert caught.value.status_code == 409 and await ScreeningOverride.find_all().count() == 1


async def test_stale_expected_version_is_rejected_without_pointer_or_fence_changes():
    item = await completed()
    await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), uuid4().hex, item.current)
    results = ScreeningResult.get_motor_collection()
    resumes = ResumeFile.get_motor_collection()
    applications = JobApplication.get_motor_collection()
    before_result = await results.find_one({"_id": item.result.id})
    before_resume = await resumes.find_one({"_id": item.resume.id})
    before_application = await applications.find_one({"_id": item.application.id})
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), uuid4().hex, item.current)
    assert caught.value.status_code == 409
    assert await results.find_one({"_id": item.result.id}) == before_result
    assert await resumes.find_one({"_id": item.resume.id}) == before_resume
    assert await applications.find_one({"_id": item.application.id}) == before_application
    assert await ScreeningOverride.find_all().count() == 1


async def test_concurrent_overrides_create_one_version_and_preserve_ai_result():
    item = await completed()
    before = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    outcomes = await asyncio.gather(
        ScreeningOverrideService.create(str(item.run.id), command(item.company.id, score=90), uuid4().hex, item.current),
        ScreeningOverrideService.create(str(item.run.id), command(item.company.id, score=60, decision="fail"), uuid4().hex, item.current),
        return_exceptions=True,
    )
    assert sum(isinstance(value, ScreeningOverride) for value in outcomes) == 1
    assert sum(isinstance(value, CustomError) and value.status_code == 409 for value in outcomes) == 1
    stored = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    assert stored["override_revision"] == 1 and immutable_result(stored) == immutable_result(before)
    assert await ScreeningOverride.find_all().count() == 1


async def test_override_insert_failure_rolls_back_result_application_and_source_fences(monkeypatch):
    item = await completed()
    results = ScreeningResult.get_motor_collection()
    resumes = ResumeFile.get_motor_collection()
    applications = JobApplication.get_motor_collection()
    before_result = await results.find_one({"_id": item.result.id})
    before_resume = await resumes.find_one({"_id": item.resume.id})
    before_application = await applications.find_one({"_id": item.application.id})
    monkeypatch.setattr(ScreeningOverride, "insert", AsyncMock(side_effect=RuntimeError("injected override insert failure")))
    with pytest.raises(RuntimeError, match="injected"):
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), uuid4().hex, item.current)
    assert await results.find_one({"_id": item.result.id}) == before_result
    assert await resumes.find_one({"_id": item.resume.id}) == before_resume
    assert await applications.find_one({"_id": item.application.id}) == before_application
    assert await ScreeningOverride.find_all().count() == 0


@pytest.mark.parametrize("change", ["profile", "parse_revision"])
async def test_changed_source_refuses_new_override_but_keeps_authorized_replay_and_history(change):
    item = await completed()
    key = uuid4().hex
    request = command(item.company.id)
    first = await ScreeningOverrideService.create(str(item.run.id), request, key, item.current)
    if change == "profile":
        newer = await create_profile(item.company, item.resume, version=2)
        await item.resume.set({"latest_extraction_profile_id": newer.id, "extraction_revision": 2})
    else:
        await item.resume.set({"parse_revision": 2})
    before = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id, expected_version=1), uuid4().hex, item.current)
    assert caught.value.status_code == 409
    assert await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id}) == before
    repeated = await ScreeningOverrideService.create(str(item.run.id), request, key, item.current)
    assert repeated.id == first.id
    assessment = await ScreeningOverrideService.assessment(str(item.run.id), str(item.company.id), item.current)
    assert assessment.source_is_current is False and assessment.review_required
    assert assessment.effective.source == "hr_override" and assessment.effective.override_id == str(first.id)


async def test_a_new_screening_result_never_inherits_override_from_old_run():
    item = await completed()
    key = uuid4().hex
    original = await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), key, item.current)
    run = await ScreeningService.start(item.request, uuid4().hex, item.current)
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    result = await ProcessingService.complete_screening(str(run.id), "worker", output(), generation=claimed.attempt)
    new_assessment = await ScreeningOverrideService.assessment(str(run.id), str(item.company.id), item.current)
    old_assessment = await ScreeningOverrideService.assessment(str(item.run.id), str(item.company.id), item.current)
    assert new_assessment.ai.id == str(result.id) and new_assessment.effective.source == "ai"
    assert new_assessment.latest_override is None and new_assessment.override_revision == 0
    assert old_assessment.effective.override_id == str(original.id) and not old_assessment.is_latest_result
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id, expected_version=1), uuid4().hex, item.current)
    assert caught.value.status_code == 409
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(run.id), command(item.company.id), key, item.current)
    assert caught.value.status_code == 409
    assert await ScreeningOverride.find_all().count() == 1


async def test_history_is_paginated_and_old_versions_remain_unchanged():
    item = await completed()
    for version in range(3):
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id, expected_version=version,
                                                                       score=90 - version), uuid4().hex, item.current)
    first = await ScreeningOverrideService.history(str(item.run.id), str(item.company.id), 1, 2, item.current)
    second = await ScreeningOverrideService.history(str(item.run.id), str(item.company.id), 2, 2, item.current)
    assert first.total == second.total == 3
    assert [item.version for item in first.items] == [3, 2] and [item.version for item in second.items] == [1]
    assert second.items[0].overall_score == 90 and second.items[0].base_version == 0


async def test_replay_rechecks_membership_before_returning_old_override():
    item = await completed()
    caller = await User(email=uuid4().hex + "@example.com", hashed_password="unused", is_verified=True).insert()
    membership = await UserCompany(user_id=caller.id, company_branch_id=item.branch.id, role="recruiter", assigned_by=item.owner.id).insert()
    current = CurrentUser(caller, permission_names={"screening_runs:view", "screening_results:override"})
    key = uuid4().hex
    request = command(item.company.id)
    await ScreeningOverrideService.create(str(item.run.id), request, key, current)
    await membership.set({"is_active": False})
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), request, key, current)
    assert caught.value.status_code == 403 and await ScreeningOverride.find_all().count() == 1


async def test_legacy_result_without_pointer_fields_can_record_first_override():
    item = await completed()
    await ScreeningResult.get_motor_collection().update_one(
        {"_id": item.result.id}, {"$unset": {"override_revision": "", "latest_override_id": ""}}
    )
    override = await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), uuid4().hex, item.current)
    result = await ScreeningResult.get(item.result.id)
    assert override.version == 1 and result.override_revision == 1 and result.latest_override_id == override.id


async def test_source_change_during_override_write_aborts_every_override_write(monkeypatch):
    item = await completed()
    collection = ResumeFile.get_motor_collection()
    original_update = collection.update_one
    changed = False

    async def change_before_fence(*args, **kwargs):
        nonlocal changed
        update = args[1] if len(args) > 1 else kwargs["update"]
        if not changed and update.get("$inc", {}).get("screening_fence_version") == 1:
            changed = True
            await original_update({"_id": item.resume.id}, {"$inc": {"parse_revision": 1}}, session=None)
        return await original_update(*args, **kwargs)

    before_result = await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id})
    before_application = await JobApplication.get_motor_collection().find_one({"_id": item.application.id})
    monkeypatch.setattr(collection, "update_one", change_before_fence)
    with pytest.raises(CustomError) as caught:
        await ScreeningOverrideService.create(str(item.run.id), command(item.company.id), uuid4().hex, item.current)
    assert changed and caught.value.status_code == 409
    assert (await ResumeFile.get(item.resume.id)).parse_revision == 2
    assert await ScreeningResult.get_motor_collection().find_one({"_id": item.result.id}) == before_result
    assert await JobApplication.get_motor_collection().find_one({"_id": item.application.id}) == before_application
    assert await ScreeningOverride.find_all().count() == 0
