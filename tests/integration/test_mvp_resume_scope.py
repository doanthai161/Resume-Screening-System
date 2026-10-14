"""JWT/tenant/file read and parse command checks against a real isolated replica set."""
from datetime import timedelta
from hashlib import sha256
import os
from types import SimpleNamespace
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.core.rate_limiter import limiter
from app.core.security import issue_token_pair
from app.main import app
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.models.ai_model import AIModel
from app.models.candidate import Candidate
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.permission import Permission
from app.models.resume_file import ParsedResumeData, ResumeFile
from app.models.screening_run import ResumeParseRun
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.utils.time import now_utc

pytestmark = pytest.mark.asyncio
CONTENT = b"%PDF-1.4\nSynthetic real-DB CV\n"


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch, tmp_path):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "mvp_resume_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", tmp_path)
    monkeypatch.setattr(limiter, "enabled", False)
    for module in ("app.core.cache", "app.core.security", "app.core.job_queue"):
        monkeypatch.setattr(module + ".get_redis", lambda: None)
    try:
        yield client[name]
    finally:
        assert name.startswith("mvp_resume_test_")
        await client.drop_database(name)
        client.close()


async def user():
    key = uuid4().hex
    return await User(email=key + "@example.com", username=key, phone_number=str(int(key, 16))[-15:],
                       hashed_password="unused", is_verified=True, is_active=True).insert()


async def data():
    owner, caller = await user(), await user()
    key = uuid4().hex
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branches = [await CompanyBranch(company_id=company.id, branch_name=uuid4().hex, bussiness_type="IT",
                                   address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert()
                for _ in range(2)]
    candidate = await Candidate(company_id=company.id, full_name="Synthetic", created_by=owner.id).insert()
    folder = settings.resume_upload_path / str(company.id)
    folder.mkdir(parents=True)
    path = folder / "sample.pdf"
    path.write_bytes(CONTENT)
    resume = await ResumeFile(company_id=company.id, company_branch_id=branches[0].id, candidate_id=candidate.id,
                              filename=path.name, original_filename="CV tiếng Việt.pdf", file_path=str(path),
                              file_size=len(CONTENT), mime_type="application/pdf", uploader_id=owner.id,
                              checksum=sha256(CONTENT).hexdigest(), parsed_data=ParsedResumeData(raw_text="private CV"),
                              object_key=f"{company.id}/{path.name}").insert()
    return SimpleNamespace(owner=owner, caller=caller, company=company, branches=branches,
                           candidate=candidate, resume=resume)


async def grant(person, names, *, candidate=False):
    actor = await Actor(name=settings.CANDIDATE_ROLE_NAME if candidate else uuid4().hex, is_active=True).insert()
    await UserActor(user_id=person.id, actor_id=actor.id, created_by=person.id).insert()
    for name in names:
        permission = await Permission.find_one({"name": name})
        if not permission:
            permission = await Permission(name=name, is_active=True).insert()
        await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()
    tokens = await issue_token_pair(person)
    return {"Authorization": "Bearer " + tokens.access_token}


async def membership(person, branch, **kwargs):
    return await UserCompany(user_id=person.id, company_branch_id=branch.id, assigned_by=branch.created_by,
                             role="member", **kwargs).insert()


@pytest.mark.parametrize("access", ["owner", "member", "other_branch", "expired", "no_permission"])
async def test_real_jwt_metadata_download_branch_scope_and_no_denied_writes(access):
    ctx = await data()
    person = ctx.owner if access == "owner" else ctx.caller
    headers = await grant(person, [] if access == "no_permission" else ["resume_files:view"])
    if access not in {"owner", "no_permission"}:
        branch = ctx.branches[1] if access == "other_branch" else ctx.branches[0]
        await membership(person, branch, **({"end_date": now_utc() - timedelta(seconds=1)} if access == "expired" else {}))
    collection = ResumeFile.get_motor_collection()
    before = await collection.find_one({"_id": ctx.resume.id})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        metadata = await client.get(f"/api/v1/resumes/{ctx.resume.id}", headers=headers)
        download = await client.get(f"/api/v1/resumes/{ctx.resume.id}/download", headers=headers)
    expected = 200 if access in {"owner", "member"} else 403
    assert metadata.status_code == download.status_code == expected
    if expected == 200:
        assert download.content == CONTENT
        assert "private" not in metadata.text
        assert "file_path" not in metadata.text and "raw_text" not in metadata.text
    assert await collection.find_one({"_id": ctx.resume.id}) == before
    assert await ResumeParseRun.find_all().count() == 0


async def test_real_list_and_model_catalog_projection_limits():
    ctx = await data()
    headers = await grant(ctx.caller, ["resume_files:view", "screening_runs:create"])
    await membership(ctx.caller, ctx.branches[0])
    clone = ctx.resume.model_dump(exclude={"id"})
    clone.update(company_branch_id=ctx.branches[1].id, checksum="b" * 64, object_key=uuid4().hex)
    await ResumeFile(**clone).insert()
    for kind, active in [("scoring", True), ("skill_matcher", True), ("resume_parser", True), ("scoring", False)]:
        await AIModel(name=uuid4().hex, provider="synthetic", model_id=uuid4().hex, model_type=kind,
                      is_active=active, config={"api_key": "synthetic-private-secret"}).insert()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        listing = await client.get("/api/v1/resumes", params={"company_id": str(ctx.company.id)}, headers=headers)
        models = await client.get("/api/v1/ai-models", headers=headers)
        for path, params in [("/api/v1/resumes", {"company_id": str(ctx.company.id), "size": 101}), ("/api/v1/ai-models", {"size": 101})]:
            assert (await client.get(path, params=params, headers=headers)).status_code == 422
    assert listing.status_code == models.status_code == 200
    assert listing.json()["data"]["total"] == 1 and models.json()["data"]["total"] == 2
    assert "private" not in listing.text + models.text
    assert "config" not in models.text


async def test_real_candidate_own_branchless_read_does_not_grant_parse():
    ctx = await data()
    await ctx.candidate.set({"user_id": ctx.caller.id})
    await ctx.resume.set({"company_branch_id": None})
    headers = await grant(ctx.caller, ["resume_files:view"], candidate=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        listing = await client.get("/api/v1/resumes", params={"company_id": str(ctx.company.id)}, headers=headers)
        download = await client.get(f"/api/v1/resumes/{ctx.resume.id}/download", headers=headers)
        denied = await client.post(f"/api/v1/resumes/{ctx.resume.id}/parse-runs", json={"company_id": str(ctx.company.id)},
                                  headers={**headers, "Idempotency-Key": uuid4().hex})
    assert listing.status_code == download.status_code == 200
    assert denied.status_code == 403 and await ResumeParseRun.find_all().count() == 0


async def test_parse_binds_same_checksum_to_distinct_resumes_and_denies_other_branch():
    ctx = await data()
    other_candidate = await Candidate(company_id=ctx.company.id, full_name="Second", created_by=ctx.owner.id).insert()
    clone = ctx.resume.model_dump(exclude={"id"})
    clone.update(candidate_id=other_candidate.id, company_branch_id=ctx.branches[1].id, object_key=uuid4().hex)
    other = await ResumeFile(**clone).insert()
    owner_headers = await grant(ctx.owner, ["resume_files:parse", "resume_files:view"])
    member_headers = await grant(ctx.caller, ["resume_files:parse", "resume_files:view"])
    await membership(ctx.caller, ctx.branches[0])
    payload = {"company_id": str(ctx.company.id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        first = await client.post(f"/api/v1/resumes/{ctx.resume.id}/parse-runs", json=payload,
                                  headers={**owner_headers, "Idempotency-Key": uuid4().hex})
        denied = await client.post(f"/api/v1/resumes/{other.id}/parse-runs", json=payload,
                                   headers={**member_headers, "Idempotency-Key": uuid4().hex})
        second = await client.post(f"/api/v1/resumes/{other.id}/parse-runs", json=payload,
                                   headers={**owner_headers, "Idempotency-Key": uuid4().hex})
    assert first.status_code == second.status_code == 202, first.text + second.text
    assert denied.status_code == 403
    assert first.json()["data"]["resume_file_id"] == str(ctx.resume.id)
    assert second.json()["data"]["resume_file_id"] == str(other.id)
    runs = await ResumeParseRun.find_all().to_list()
    assert len(runs) == 2 and len({item.input_hash for item in runs}) == 2


async def test_parse_original_key_replays_legacy_hash_after_model_config_change_and_new_key_conflicts():
    ctx = await data()
    headers = await grant(ctx.owner, ["resume_files:parse", "resume_files:view"])
    model = await AIModel(name=uuid4().hex, provider="synthetic", model_id=uuid4().hex,
                          model_type="resume_parser", config={"tier": "basic"}).insert()
    payload = {"company_id": str(ctx.company.id), "parser_model_id": str(model.id)}
    key = uuid4().hex
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        first = await client.post(f"/api/v1/resumes/{ctx.resume.id}/parse-runs", json=payload,
                                  headers={**headers, "Idempotency-Key": key})
        assert first.status_code == 202, first.text
        run_id = ObjectId(first.json()["data"]["id"])
        legacy_hash = sha256(f"{ctx.resume.checksum}:1.0.0:{model.id}".encode()).hexdigest()
        await ResumeParseRun.find_one({"_id": run_id}).update({"$set": {"input_hash": legacy_hash}})
        await model.set({"is_active": False, "config": {"tier": "advanced"}})
        repeated = await client.post(f"/api/v1/resumes/{ctx.resume.id}/parse-runs", json=payload,
                                     headers={**headers, "Idempotency-Key": key})
        changed = await client.post(f"/api/v1/resumes/{ctx.resume.id}/parse-runs", json={"company_id": str(ctx.company.id)},
                                    headers={**headers, "Idempotency-Key": uuid4().hex})
        read = await client.get(f"/api/v1/resumes/{ctx.resume.id}/parse-runs/{run_id}", headers=headers)
    assert repeated.status_code == 202 and repeated.json()["data"]["id"] == str(run_id)
    assert read.status_code == 200
    assert changed.status_code == 409
    assert await ResumeParseRun.find_all().count() == 1
