"""Run against an isolated real replica set: TEST_MONGODB_URI must be explicit."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from beanie import init_beanie
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.core import job_queue
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.errors import CustomError
from app.core.security import CurrentUser
from app.core.transactions import transactional, current_session
from app.models.user import User
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.job_requirement import JobRequirement
from app.models.job_scorecard import JobScorecard
from app.models.job_application import JobApplication, ApplicationStage
from app.models.application_stage_event import ApplicationStageEvent
from app.models.resume_file import ResumeFile, ParsedResumeData
from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.models.screening_result import ScreeningResult
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.schemas.recruitment import ScorecardCreate, StageTransitionRequest
from app.services.processing_service import ProcessingService
from app.services.recruitment_service import ScorecardService, ApplicationService
from app.utils.time import now_utc

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("operation", ["soft", "hard", "update"])
async def test_concurrent_promotion_cannot_be_overwritten_by_user_mutation(
    monkeypatch, operation
):
    from app.repositories.user_repository import UserRepository
    from app.schemas.user import UserUpdate

    target = await User(
        email="race@example.com",
        hashed_password="unused",
        is_active=operation != "hard",
    ).insert()
    original_get = User.get

    async def stale_read(*args, **kwargs):
        snapshot = await original_get(*args, **kwargs)
        await User.get_motor_collection().update_one(
            {"_id": target.id}, {"$set": {"is_superuser": True}}
        )
        return snapshot

    monkeypatch.setattr(User, "get", stale_read)
    with pytest.raises(CustomError) as exc:
        if operation == "soft":
            await UserRepository.delete_user(str(target.id))
        elif operation == "hard":
            await UserRepository.hard_delete_user(str(target.id))
        else:
            await UserRepository.update_user(
                str(target.id), UserUpdate(is_active=False)
            )
    assert exc.value.status_code == 409
    stored = await original_get(target.id)
    assert stored.is_superuser and stored.is_active == target.is_active


@pytest.mark.parametrize("operation", ["deactivate", "update"])
async def test_concurrent_promotion_aborts_entire_bulk(monkeypatch, operation):
    from app.repositories.user_repository import UserRepository

    first = await User(
        email="first@example.com", hashed_password="unused", is_active=True
    ).insert()
    second = await User(
        email="second@example.com", hashed_password="unused", is_active=True
    ).insert()
    collection = User.get_motor_collection()
    original_update = collection.update_many

    async def promote_before_write(*args, **kwargs):
        await collection.update_one(
            {"_id": second.id}, {"$set": {"is_superuser": True}}
        )
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(collection, "update_many", promote_before_write)
    with pytest.raises(CustomError) as exc:
        if operation == "deactivate":
            await UserRepository.bulk_deactivate_users(
                [str(first.id), str(second.id)], str(ObjectId())
            )
        else:
            await UserRepository.bulk_update_users(
                [str(first.id), str(second.id)], {"is_active": False}
            )
    assert exc.value.status_code == 409
    assert (await User.get(first.id)).is_active
    assert (await User.get(second.id)).is_active


async def test_bulk_deactivate_is_atomic_and_invalidates_sessions():
    from app.repositories.user_repository import UserRepository

    owner, _, _, _ = await job_data()
    ordinary = await User(
        email="ordinary@example.com", hashed_password="unused", is_active=True
    ).insert()
    with pytest.raises(CustomError) as exc:
        await UserRepository.bulk_deactivate_users(
            [str(ordinary.id), str(owner.id)], str(ObjectId())
        )
    assert exc.value.status_code == 403
    assert (await User.get(ordinary.id)).is_active
    assert (
        await UserRepository.bulk_deactivate_users([str(ordinary.id)], str(owner.id))
        == 1
    )
    stored = await User.get(ordinary.id)
    assert not stored.is_active
    assert stored.auth_version == 1


async def test_concurrent_bulk_removal_cannot_disable_superusers():
    from app.repositories.user_repository import UserRepository

    owner, _, _, _ = await job_data()
    second = await User(
        email="second@example.com",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
    ).insert()
    results = await asyncio.gather(
        *(
            UserRepository.bulk_deactivate_users([str(target.id)], str(ObjectId()))
            for target in [owner, second]
        ),
        return_exceptions=True
    )
    assert all(
        isinstance(result, CustomError) and result.status_code == 403
        for result in results
    )
    assert await User.find({"is_superuser": True, "is_active": True}).count() == 2


async def test_bulk_update_protects_supers_and_commits_allowed_promotion():
    from app.repositories.user_repository import UserRepository

    owner, _, _, _ = await job_data()
    member = await User(
        email="member@example.com", hashed_password="unused", is_active=True
    ).insert()
    with pytest.raises(CustomError):
        await UserRepository.bulk_update_users(
            [str(member.id), str(owner.id)], {"is_active": False}, allow_superuser=True
        )
    assert (await User.get(member.id)).is_active
    assert await UserRepository.bulk_update_users(
        [str(member.id)], {"is_superuser": True}, allow_superuser=True
    ) == (1, 1)
    with pytest.raises(CustomError):
        await UserRepository.bulk_update_users(
            [str(member.id)], {"full_name": "unauthorized"}
        )
    assert (await User.get(member.id)).full_name != "unauthorized"


async def test_branch_admin_can_assign_despite_member_role_in_other_branch():
    from app.models.user_company import UserCompany
    from app.schemas.user_company import AssignUserToCompanyBranch
    from app.services.user_company_service import UserCompanyService
    from app.repositories.company_repository import CompanyRepository

    owner, company, first_branch, _ = await job_data()
    second_branch = await CompanyBranch(
        company_id=company.id,
        bussiness_type="IT",
        branch_name="B",
        address="Test",
        company_size=1,
        working_days=[],
        created_by=owner.id,
    ).insert()
    manager = await User(
        email="manager@example.com", hashed_password="unused", is_active=True
    ).insert()
    member = await User(
        email="member@example.com", hashed_password="unused", is_active=True
    ).insert()
    for branch, role in [(first_branch, "member"), (second_branch, "admin")]:
        await UserCompany(
            user_id=manager.id,
            company_branch_id=branch.id,
            role=role,
            assigned_by=owner.id,
        ).insert()
    assert (
        await CompanyRepository.get_user_company_role(str(manager.id), str(company.id))
        == "admin"
    )
    data = AssignUserToCompanyBranch(
        user_id=str(member.id), company_branch_id=str(second_branch.id)
    )
    result = await UserCompanyService.assign_user(data, str(manager.id))
    await UserCompanyService.unassign_user(data, str(manager.id))
    assert not (await UserCompany.get(ObjectId(result["assignment_id"]))).is_active
    with pytest.raises(CustomError):
        await UserCompanyService.assign_user(
            AssignUserToCompanyBranch(
                user_id=str(member.id), company_branch_id=str(first_branch.id)
            ),
            str(manager.id),
        )


async def test_membership_pagination_ignores_stale_cache_and_reflects_mutation(
    monkeypatch,
):
    from app.models.user_company import UserCompany
    from app.repositories.user_company_repository import UserCompanyRepository

    owner, _, branch, _ = await job_data()
    links = [
        await UserCompany(
            user_id=ObjectId(), company_branch_id=branch.id, assigned_by=owner.id
        ).insert()
        for _ in range(3)
    ]
    cached = AsyncMock(return_value={"assignments": [], "total": 999})
    monkeypatch.setattr(UserCompanyRepository, "_get_from_cache", cached)
    first, count = await UserCompanyRepository.list_branch_assignments(
        str(branch.id), skip=0, limit=1
    )
    second, _ = await UserCompanyRepository.list_branch_assignments(
        str(branch.id), skip=1, limit=1
    )
    assert count == 3 and first[0].id != second[0].id
    await first[0].set({"is_active": False})
    updated, count = await UserCompanyRepository.list_branch_assignments(
        str(branch.id), skip=0, limit=1
    )
    assert count == 2 and updated[0].id == second[0].id
    cached.assert_not_awaited()


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip(
            "TEST_MONGODB_URI replica set required; mongomock cannot test transactions"
        )
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "p1_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.job_queue.get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("p1_test_")
        await client.drop_database(name)
        client.close()


async def parse_run():
    tenant_id, user_id = ObjectId(), ObjectId()
    resume = await ResumeFile(
        company_id=tenant_id,
        filename="test.pdf",
        original_filename="test.pdf",
        file_path="unused",
        file_size=1,
        mime_type="application/pdf",
        uploader_id=user_id,
        checksum="a" * 64,
    ).insert()
    run = await ResumeParseRun(
        company_id=tenant_id,
        resume_file_id=resume.id,
        triggered_by=user_id,
        idempotency_key=uuid4().hex,
        input_hash=uuid4().hex,
        parser_version="1",
    ).insert()
    return resume, run


async def job_data():
    owner = await User(
        email="owner@example.com",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
    ).insert()
    company = await Company(
        user_id=owner.id,
        name="Test",
        company_short_name="T",
        company_code="T",
        email=owner.email,
        website="https://example.com",
    ).insert()
    branch = await CompanyBranch(
        company_id=company.id,
        bussiness_type="IT",
        branch_name="HQ",
        address="Test",
        company_size=1,
        working_days=[],
        created_by=owner.id,
    ).insert()
    job = await JobRequirement(
        user_id=owner.id,
        company_branch_id=branch.id,
        title="Test",
        experience_level="junior",
    ).insert()
    return owner, company, branch, job


async def test_scorecard_replacement_and_idempotent_retry_are_atomic():
    owner, company, _, job = await job_data()
    data = ScorecardCreate(
        company_id=str(company.id),
        job_requirement_id=str(job.id),
        criteria=[{"code": "skills", "name": "Skills", "weight": 1}],
    )
    current = CurrentUser(user=owner)
    first = await ScorecardService.create_version(data, "scorecard-first", current)
    second = await ScorecardService.create_version(data, "scorecard-second", current)
    retried = await ScorecardService.create_version(data, "scorecard-second", current)
    assert second.id == retried.id
    assert (await JobRequirement.get(job.id)).active_scorecard_id == second.id
    assert not (await JobScorecard.get(first.id)).is_active
    assert await JobScorecard.find({"is_active": True}).count() == 1


async def test_scorecard_pointer_failure_rolls_back_replacement(monkeypatch):
    owner, company, _, job = await job_data()
    data = ScorecardCreate(
        company_id=str(company.id),
        job_requirement_id=str(job.id),
        criteria=[{"code": "skills", "name": "Skills", "weight": 1}],
    )
    current = CurrentUser(user=owner)
    first = await ScorecardService.create_version(data, "scorecard-first", current)
    monkeypatch.setattr(
        JobRequirement,
        "save",
        AsyncMock(side_effect=RuntimeError("injected pointer failure")),
    )
    with pytest.raises(RuntimeError, match="injected"):
        await ScorecardService.create_version(data, "scorecard-second", current)
    assert await JobScorecard.find_all().count() == 1
    assert (await JobScorecard.get(first.id)).is_active
    assert (await JobRequirement.get(job.id)).active_scorecard_id == first.id


async def test_membership_mutations_use_transaction_and_role_policy():
    from app.schemas.user_company import AssignUserToCompanyBranch
    from app.services.user_company_service import UserCompanyService
    from app.models.user_company import UserCompany

    owner, _, branch, _ = await job_data()
    target = await User(
        email="member@example.com",
        username="member",
        phone_number="1234567890",
        hashed_password="unused",
        is_active=True,
    ).insert()
    result = await UserCompanyService.assign_user(
        AssignUserToCompanyBranch(
            user_id=str(target.id), company_branch_id=str(branch.id)
        ),
        str(owner.id),
    )
    await UserCompanyService.update_assignment_role(
        result["assignment_id"], "manager", CurrentUser(user=owner)
    )
    link = await UserCompany.get(ObjectId(result["assignment_id"]))
    assert link.role == "manager"
    with pytest.raises(CustomError):
        await UserCompanyService.update_assignment_role(
            str(link.id), "admin", CurrentUser(user=target)
        )
    assert (await UserCompany.get(link.id)).role == "manager"
    await UserCompanyService.unassign_user(
        AssignUserToCompanyBranch(
            user_id=str(target.id), company_branch_id=str(branch.id)
        ),
        str(owner.id),
    )
    assert not (await UserCompany.get(link.id)).is_active


async def test_stage_event_failure_rolls_back_application(monkeypatch):
    owner, company, branch, job = await job_data()
    application = await JobApplication(
        company_id=company.id,
        company_branch_id=branch.id,
        candidate_id=ObjectId(),
        resume_file_id=ObjectId(),
        job_requirement_id=job.id,
        applied_by=owner.id,
        idempotency_key="application-test",
    ).insert()
    monkeypatch.setattr(
        ApplicationStageEvent,
        "insert",
        AsyncMock(side_effect=RuntimeError("injected event failure")),
    )
    with pytest.raises(RuntimeError, match="injected"):
        await ApplicationService.transition_stage(
            str(application.id),
            str(company.id),
            StageTransitionRequest(to_stage=ApplicationStage.SCREENING),
            "transition-test",
            CurrentUser(user=owner),
        )
    stored = await JobApplication.get(application.id)
    assert stored.current_stage == ApplicationStage.APPLIED
    assert stored.revision == application.revision
    assert await ApplicationStageEvent.find_all().count() == 0


async def test_expired_and_previous_generation_workers_cannot_write():
    resume, run = await parse_run()
    first = await ProcessingService.claim_parse(str(run.id), "same-worker")
    await ResumeParseRun.find_one({"_id": run.id}).update(
        {"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}}
    )
    output = ParseOutput(parsed_data=ParsedResumeData(summary="stale result"))
    assert not await ProcessingService.renew_parse_lease(
        str(run.id), "same-worker", generation=first.attempt
    )
    assert not await ProcessingService.complete_parse(
        str(run.id), "same-worker", output, generation=first.attempt
    )
    assert not await ProcessingService.fail_parse(
        str(run.id), "same-worker", "x", "x", generation=first.attempt
    )
    await ProcessingService.recover_expired_leases()
    second = await ProcessingService.claim_parse(str(run.id), "same-worker")
    assert second.attempt == first.attempt + 1
    assert not await ProcessingService.complete_parse(
        str(run.id), "same-worker", output, generation=first.attempt
    )
    assert await ProcessingService.complete_parse(
        str(run.id), "same-worker", output, generation=second.attempt
    )
    assert (await ResumeFile.get(resume.id)).parsed_data.summary == "stale result"
    assert (await ResumeParseRun.get(run.id)).status == "completed"


async def test_concurrent_claim_has_one_winner():
    _, run = await parse_run()
    results = await asyncio.gather(
        *(
            ProcessingService.claim_parse(str(run.id), worker)
            for worker in ["first", "second"]
        ),
        return_exceptions=True
    )
    assert sum(isinstance(result, ResumeParseRun) for result in results) == 1
    for result in results:
        assert (
            result is None
            or isinstance(result, ResumeParseRun)
            or (isinstance(result, CustomError) and result.status_code == 409)
        )
    assert (await ResumeParseRun.get(run.id)).attempt == 1


async def test_screening_lease_fencing_and_atomic_completion():
    owner, company, branch, job = await job_data()
    application = await JobApplication(
        company_id=company.id,
        company_branch_id=branch.id,
        candidate_id=ObjectId(),
        resume_file_id=ObjectId(),
        job_requirement_id=job.id,
        applied_by=owner.id,
        idempotency_key="application-screen",
    ).insert()
    run = await ScreeningRun(
        company_id=company.id,
        application_id=application.id,
        resume_file_id=application.resume_file_id,
        job_requirement_id=job.id,
        scorecard_id=ObjectId(),
        ai_model_id=ObjectId(),
        triggered_by=owner.id,
        idempotency_key="screening-test",
        input_hash="a" * 64,
    ).insert()
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    output = ScreeningOutput(overall_score=80, match_percentage=80)
    assert not await ProcessingService.complete_screening(
        str(run.id), "worker", output, generation=claimed.attempt + 1
    )
    await ScreeningRun.find_one({"_id": run.id}).update(
        {"$set": {"lease_expires_at": now_utc() - timedelta(seconds=1)}}
    )
    assert not await ProcessingService.renew_screening_lease(
        str(run.id), "worker", generation=claimed.attempt
    )
    assert not await ProcessingService.fail_screening(
        str(run.id), "worker", "x", "x", generation=claimed.attempt
    )
    assert not await ProcessingService.complete_screening(
        str(run.id), "worker", output, generation=claimed.attempt
    )
    assert await ScreeningResult.find_all().count() == 0
    await ProcessingService.recover_expired_leases()
    claimed = await ProcessingService.claim_screening(str(run.id), "worker")
    result = await ProcessingService.complete_screening(
        str(run.id), "worker", output, generation=claimed.attempt
    )
    assert (await ScreeningRun.get(run.id)).result_id == result.id
    assert (
        await JobApplication.get(application.id)
    ).latest_screening_result_id == result.id
    assert (
        await JobApplication.get(application.id)
    ).current_stage == ApplicationStage.SCREENED
    assert await ApplicationStageEvent.find_all().count() == 1


async def test_mid_completion_failure_rolls_back_parsed_data(monkeypatch):
    resume, run = await parse_run()
    claimed = await ProcessingService.claim_parse(str(run.id), "worker")
    original = ResumeParseRun.find_one

    def fail_terminal(*args, **kwargs):
        query = original(*args, **kwargs)
        query.update = AsyncMock(side_effect=RuntimeError("injected terminal failure"))
        return query

    monkeypatch.setattr(ResumeParseRun, "find_one", fail_terminal)
    with pytest.raises(RuntimeError, match="injected"):
        await ProcessingService.complete_parse(
            str(run.id),
            "worker",
            ParseOutput(parsed_data=ParsedResumeData(summary="uncommitted")),
            generation=claimed.attempt,
        )
    assert (await ResumeFile.get(resume.id)).parsed_data is None
    assert (await ResumeParseRun.get(run.id)).status == "running"


async def test_queue_publishes_only_after_commit_and_rollback_discards_callback(
    monkeypatch,
):
    redis = AsyncMock()
    redis.xadd.return_value = "1-0"
    monkeypatch.setattr(job_queue, "get_redis", lambda: redis)
    _, run = await parse_run()

    @transactional
    async def command(fail):
        await ResumeParseRun.find_one(
            {"_id": run.id}, session=current_session()
        ).update({"$set": {"parser_version": "2"}}, session=current_session())
        await job_queue.enqueue("resume-parse", str(run.id), str(run.company_id))
        redis.xadd.assert_not_awaited()
        if fail:
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        await command(True)
    redis.xadd.assert_not_awaited()
    assert (await ResumeParseRun.get(run.id)).parser_version == "1"
    await command(False)
    redis.xadd.assert_awaited_once()
    assert (await ResumeParseRun.get(run.id)).queue_message_id == "1-0"
