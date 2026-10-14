import asyncio
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import uuid4

from bson import ObjectId
import httpx
import pytest

from app.api import upload as upload_api
from app.api.upload import _ResumeDownloadResponse
from app.core.config import settings
from app.core.errors import CustomError
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
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.services.resume_service import ResumeService
from app.utils.time import now_utc

CONTENT = b"%PDF-1.4\nSynthetic CV\n"
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", tmp_path)
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)


async def create_user():
    return await User(email=uuid4().hex + "@example.com", username=uuid4().hex, phone_number=str(uuid4().int)[:14],
                      hashed_password="unused", is_verified=True, is_active=True).insert()


async def data():
    owner, member = await create_user(), await create_user()
    key = uuid4().hex
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branches = [await CompanyBranch(company_id=company.id, branch_name=uuid4().hex, bussiness_type="IT",
                                   address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert()
                for _ in range(2)]
    candidate = await Candidate(company_id=company.id, full_name="Synthetic", created_by=owner.id).insert()
    root = settings.resume_upload_path / str(company.id)
    root.mkdir(parents=True)
    path = root / "synthetic.pdf"
    path.write_bytes(CONTENT)
    resume = await ResumeFile(company_id=company.id, candidate_id=candidate.id, company_branch_id=branches[0].id,
                              filename=path.name, original_filename="CV tiếng Việt.pdf", file_path=str(path),
                              file_size=len(CONTENT), mime_type="application/pdf", uploader_id=owner.id,
                              checksum=sha256(CONTENT).hexdigest(), parsed_data=ParsedResumeData(raw_text="private CV text"),
                              metadata={"private": "internal storage secret"}).insert()
    return SimpleNamespace(owner=owner, member=member, company=company, branches=branches, candidate=candidate,
                           resume=resume, path=path)


async def grant(user, permissions, *, candidate=False):
    name = settings.CANDIDATE_ROLE_NAME if candidate else uuid4().hex
    actor = await Actor(name=name, is_active=True).insert()
    await UserActor(user_id=user.id, actor_id=actor.id, created_by=user.id).insert()
    for name in permissions:
        permission = await Permission.find_one({"name": name})
        if not permission:
            permission = await Permission(name=name, is_active=True).insert()
        await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()
    pair = await issue_token_pair(user)
    return {"Authorization": "Bearer " + pair.access_token}


async def member(user, branch, **kwargs):
    return await UserCompany(user_id=user.id, company_branch_id=branch.id, assigned_by=branch.created_by,
                             role="member", **kwargs).insert()


@pytest.mark.parametrize("access", ["owner", "member", "other_branch", "expired", "future", "inactive_branch", "no_permission"])
async def test_resume_metadata_scope_uses_real_jwt_and_database_policy(access):
    context = await data()
    user = context.owner if access == "owner" else context.member
    headers = await grant(user, [] if access == "no_permission" else ["resume_files:view"])
    if access not in {"owner", "no_permission"}:
        branch = context.branches[1] if access == "other_branch" else context.branches[0]
        extra = {}
        if access == "expired":
            extra["end_date"] = now_utc() - timedelta(seconds=1)
        if access == "future":
            extra["start_date"] = now_utc() + timedelta(days=1)
        await member(user, branch, **extra)
        if access == "inactive_branch":
            await branch.set({"is_active": False})
    collection = ResumeFile.get_motor_collection()
    before = await collection.find_one({"_id": context.resume.id})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.get(f"/api/v1/resumes/{context.resume.id}", headers=headers)
    assert response.status_code == (200 if access in {"owner", "member"} else 403), response.text
    if response.status_code == 200:
        result = response.json()["data"]
        assert result["company_branch_id"] == str(context.branches[0].id)
        assert result["candidate_id"] == str(context.candidate.id)
        assert set(result).isdisjoint({"file_path", "filename", "parsed_data", "raw_text", "metadata", "object_key"})
        assert "private" not in response.text
    assert await collection.find_one({"_id": context.resume.id}) == before


async def test_list_filters_branchless_other_branch_deleted_and_invalid_pagination():
    context = await data()
    headers = await grant(context.member, ["resume_files:view"])
    await member(context.member, context.branches[0])
    for branch, deleted, checksum in [(context.branches[1].id, False, "b"), (None, False, "c"), (context.branches[0].id, True, "d")]:
        clone = context.resume.model_dump(exclude={"id"})
        clone.update(company_branch_id=branch, checksum=checksum * 64, is_deleted=deleted, object_key=uuid4().hex)
        await ResumeFile(**clone).insert()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.get("/api/v1/resumes", params={"company_id": str(context.company.id), "candidate_id": str(context.candidate.id)}, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["total"] == 1 and [item["id"] for item in result["items"]] == [str(context.resume.id)]
        for extra in [{"size": 101}, {"page": 0}, {"candidate_id": "not-an-id"}, {"company_id": "not-an-id"}]:
            invalid = await client.get("/api/v1/resumes", params={"company_id": str(context.company.id), **extra}, headers=headers)
            assert invalid.status_code == 422


async def test_owned_candidate_can_read_and_download_branchless_cv_without_membership():
    context = await data()
    await context.candidate.set({"user_id": context.member.id})
    await context.resume.set({"company_branch_id": None})
    headers = await grant(context.member, ["resume_files:view"], candidate=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        metadata = await client.get(f"/api/v1/resumes/{context.resume.id}", headers=headers)
        download = await client.get(f"/api/v1/resumes/{context.resume.id}/download", headers=headers)
        listing = await client.get("/api/v1/resumes", params={"company_id": str(context.company.id)}, headers=headers)
    assert metadata.status_code == download.status_code == listing.status_code == 200
    assert download.content == CONTENT and listing.json()["data"]["total"] == 1
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=utf-8''CV%20ti" in download.headers["Content-Disposition"]
    assert download.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("kind", ["other_company", "traversal", "wrong_company_folder", "size", "checksum", "missing", "remote"])
async def test_download_rejects_untrusted_path_or_changed_content(kind):
    context = await data()
    headers = await grant(context.owner, ["resume_files:view"])
    changes = {}
    if kind == "other_company":
        changes["file_path"] = str(settings.resume_upload_path.parent / "outside.pdf")
    elif kind == "traversal":
        changes["file_path"] = str(settings.resume_upload_path / ".." / context.path.name)
    elif kind == "wrong_company_folder":
        other = settings.resume_upload_path / str(ObjectId())
        other.mkdir()
        (other / context.path.name).write_bytes(CONTENT)
        changes["file_path"] = str(other / context.path.name)
    elif kind == "size":
        changes["file_size"] = len(CONTENT) + 1
    elif kind == "checksum":
        changes["checksum"] = "b" * 64
    elif kind == "missing":
        changes["file_path"] = str(context.path.parent / "missing.pdf")
    else:
        changes["storage_provider"] = "s3"
    await context.resume.set(changes)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.get(f"/api/v1/resumes/{context.resume.id}/download", headers=headers)
    assert response.status_code == 404
    assert str(context.path) not in response.text


async def test_download_closes_handle_when_cancelled_before_stream_starts():
    handle = BytesIO(CONTENT)
    item = SimpleNamespace(original_filename="bad\r\nheader.pdf", file_size=len(CONTENT), mime_type="application/pdf")
    response = _ResumeDownloadResponse(handle, item)

    async def send(message):
        raise asyncio.CancelledError()

    async def receive():
        await asyncio.Event().wait()

    with pytest.raises(asyncio.CancelledError):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert handle.closed
    assert "\r" not in response.headers["Content-Disposition"] and "\n" not in response.headers["Content-Disposition"]


async def test_native_open_cancellation_closes_late_handle_and_holds_capacity(monkeypatch):
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(upload_api, "_download_slots", slots)

    class TrackedHandle(BytesIO):
        def close(self):
            super().close()
            closed.set()

    handle = TrackedHandle(CONTENT)

    def native_open(item):
        started.set()
        assert release.wait(timeout=5)
        return handle

    monkeypatch.setattr(ResumeService, "_open_download", native_open)
    task = asyncio.create_task(upload_api._open_download_managed(object()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not handle.closed
        with pytest.raises(CustomError) as caught:
            await upload_api._open_download_managed(object())
        assert caught.value.status_code == 429
        release.set()
        assert await asyncio.to_thread(closed.wait, 2)
        # Native completion, not request cancellation, returns capacity.
        assert await asyncio.to_thread(slots.acquire, True, 2)
        slots.release()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_symlink_download_is_denied_without_opening_target(monkeypatch):
    context = await data()
    # Works on Windows without requiring privilege to create a native symlink.
    import stat
    real_lstat = Path.lstat

    def lstat(path):
        if path == context.path:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(CustomError) as caught:
        ResumeService._open_download(context.resume)
    assert caught.value.status_code == 404


async def test_ai_model_selector_only_returns_active_screening_metadata():
    user = await create_user()
    headers = await grant(user, ["screening_runs:create"])
    for kind, active in [("scoring", True), ("skill_matcher", True), ("resume_parser", True), ("scoring", False)]:
        await AIModel(name=uuid4().hex, provider="synthetic", model_id=uuid4().hex, model_type=kind,
                      is_active=active, config={"api_key": "synthetic-private-secret", "endpoint": "private-uri"}).insert()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as client:
        response = await client.get("/api/v1/ai-models", headers=headers)
        filtered = await client.get("/api/v1/ai-models", params={"model_type": "scoring"}, headers=headers)
        invalid = await client.get("/api/v1/ai-models", params={"size": 101}, headers=headers)
    assert response.status_code == filtered.status_code == 200, response.text
    assert response.json()["data"]["total"] == 2 and filtered.json()["data"]["total"] == 1
    assert all(set(item) == {"id", "name", "provider", "model_id", "version", "description"} for item in response.json()["data"]["items"])
    assert "private" not in response.text and invalid.status_code == 422
