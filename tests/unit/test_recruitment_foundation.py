import pytest
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.core.cache import idempotency_key
from app.core.security import consume_refresh_token
from app.models.candidate import Candidate
from app.models.job_application import ApplicationStage, JobApplication
from app.models.job_scorecard import JobScorecard, ScorecardCriterion
from app.schemas.job_requirement import JobRequirementCreate
from app.schemas.candidate import CandidateCreate
from app.utils.otp import generate_otp, hash_otp, verify_otp_hash


@pytest.mark.asyncio
async def test_candidate_normalizes_identity_and_unique_per_company(mock_db):
    company_id = ObjectId()
    creator_id = ObjectId()
    first = Candidate(
        company_id=company_id,
        full_name="Jane Doe",
        email=" JANE@EXAMPLE.COM ",
        phone_number="+84 123 456",
        created_by=creator_id,
    )
    await first.insert()

    assert first.normalized_email == "jane@example.com"
    assert first.normalized_phone == "+84123456"

    duplicate = Candidate(
        company_id=company_id,
        full_name="Jane Duplicate",
        email="jane@example.com",
        created_by=creator_id,
    )
    with pytest.raises(DuplicateKeyError):
        await duplicate.insert()


@pytest.mark.asyncio
async def test_application_starts_at_applied(mock_db):
    oid = ObjectId()
    application = JobApplication(
        company_id=oid,
        candidate_id=ObjectId(),
        resume_file_id=ObjectId(),
        job_requirement_id=ObjectId(),
        applied_by=ObjectId(),
    )
    assert application.current_stage == ApplicationStage.APPLIED
    assert application.revision == 0


@pytest.mark.asyncio
async def test_scorecard_weights_must_sum_to_one(mock_db):
    with pytest.raises(ValidationError):
        JobScorecard(
            company_id=ObjectId(),
            job_requirement_id=ObjectId(),
            criteria=[
                ScorecardCriterion(code="skills", name="Skills", weight=0.5),
                ScorecardCriterion(code="experience", name="Experience", weight=0.4),
            ],
            created_by=ObjectId(),
        )


def test_salary_requires_currency():
    with pytest.raises(ValidationError):
        JobRequirementCreate(
            company_branch_id=str(ObjectId()),
            title="Backend Engineer",
            programming_languages=["Python"],
            skills_required=["FastAPI"],
            experience_level="mid",
            salary_min=1000,
            salary_max=2000,
        )


def test_idempotency_key_does_not_embed_user_input():
    key = idempotency_key("screening", "tenant", "unsafe:key/value")
    assert "unsafe" not in key
    assert len(key.rsplit(":", 1)[-1]) == 64


def test_otp_is_cryptographically_generated_and_hashed():
    otp = generate_otp()
    digest = hash_otp("User@Example.com", "registration", otp)

    assert len(otp) == 6 and otp.isdigit()
    assert otp not in digest
    assert verify_otp_hash("user@example.com", "registration", otp, digest)
    assert not verify_otp_hash("user@example.com", "registration", "000000", digest)


def test_candidate_metadata_is_bounded():
    with pytest.raises(ValidationError):
        CandidateCreate(
            company_id=str(ObjectId()),
            full_name="Oversized metadata",
            metadata={"payload": "x" * 20_000},
        )


@pytest.mark.asyncio
async def test_refresh_token_can_only_be_consumed_once(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def set(self, key, value, *, ex, nx):
            if nx and key in self.values:
                return None
            self.values[key] = (value, ex)
            return True

    fake = FakeRedis()
    monkeypatch.setattr("app.core.security.get_redis", lambda: fake)

    assert await consume_refresh_token("refresh-token", 60)
    assert not await consume_refresh_token("refresh-token", 60)
