"""Tenant HTTP contracts and permission changes on an isolated real replica set."""
import asyncio
from datetime import timedelta
import json
import os
from types import SimpleNamespace
from uuid import uuid4

from beanie import init_beanie
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
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.permission import Permission
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.user_company import UserCompany
from app.repositories.company_repository import CompanyRepository
from app.repositories.company_branch_repository import CompanyBranchRepository
from app.utils.time import now_utc

pytestmark = pytest.mark.asyncio


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


@pytest.fixture(autouse=True)
async def mock_db(monkeypatch):
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI must point to an isolated real replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "mvp_tenant_test_" + uuid4().hex
    await require_transaction_topology(client)
    await init_beanie(database=client[name], document_models=DOCUMENT_MODELS)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.api.user_company.limiter.enabled", False)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    cache = MemoryRedis()
    for module in ("app.repositories.company_repository", "app.repositories.company_branch_repository"):
        monkeypatch.setattr(module + ".get_redis", lambda: cache)
        monkeypatch.setattr(module + ".is_redis_available", lambda: True)
    try:
        yield cache
    finally:
        assert name.startswith("mvp_tenant_test_")
        await client.drop_database(name)
        client.close()


async def user(**values):
    key = uuid4().hex
    return await User(email=key + "@example.com", username=key, hashed_password="unused",
                      is_verified=True, **values).insert()


async def data():
    owner, member = await user(), await user()
    key = uuid4().hex
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branches = []
    for label in ("A", "B"):
        branches.append(await CompanyBranch(company_id=company.id, branch_name=label, bussiness_type="IT",
                                            address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert())
    assignment = await UserCompany(user_id=member.id, company_branch_id=branches[0].id,
                                   role="member", assigned_by=owner.id).insert()
    return SimpleNamespace(owner=owner, member=member, company=company, branch=branches[0],
                           other_branch=branches[1], assignment=assignment)


async def token(user, permissions=(), actor_name=None):
    if permissions or actor_name:
        actor = await Actor(name=actor_name or uuid4().hex, is_active=True).insert()
        await UserActor(user_id=user.id, actor_id=actor.id, created_by=user.id).insert()
        for name in permissions:
            permission = await Permission.find_one({"name": name})
            if not permission:
                permission = await Permission(name=name, is_active=True).insert()
            await ActorPermission(actor_id=actor.id, permission_id=permission.id).insert()
    pair = await issue_token_pair(user)
    return {"Authorization": "Bearer " + pair.access_token}


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend")


async def test_company_create_returns_ids_and_partial_update_never_clears_required_fields():
    owner = await user()
    headers = await token(owner, ["companies:create", "companies:edit"])
    key = uuid4().hex
    async with client() as http:
        created = await http.post("/api/v1/companies/", headers=headers, json={
            "name": "Initial", "company_short_name": "Initial", "company_code": key,
            "email": owner.email, "website": "https://example.com",
        })
        assert created.status_code == 201, created.text
        response = created.json()["data"]
        company_id = response["id"]
        assert response["user_id"] == str(owner.id) and response["is_active"] is True
        assert response["created_at"] and response["updated_at"]
        changed = await http.put(f"/api/v1/companies/{company_id}", headers=headers, json={"name": "Updated"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["name"] == "Updated"
        assert changed.json()["data"]["company_code"] == key
        collection = Company.get_motor_collection()
        before = await collection.find_one({"company_code": key})
        for body in ({"name": None}, {"user_id": str(owner.id)}, {"is_active": False}):
            denied = await http.put(f"/api/v1/companies/{company_id}", headers=headers, json=body)
            assert denied.status_code == 422
            assert await collection.find_one({"company_code": key}) == before


@pytest.mark.parametrize("state", ["allowed", "expired", "future", "revoked", "branch_inactive", "company_inactive"])
async def test_http_company_reads_use_current_membership_and_parent_state(mock_db, state):
    item = await data()
    headers = await token(item.member)
    # Old authorization/business caches cannot preserve access after revocation.
    mock_db.values[CompanyRepository._get_user_companies_cache_key(str(item.member.id))] = json.dumps(
        [item.company.model_dump(mode="json", by_alias=True)], default=str
    )
    updates = {"expired": {"end_date": now_utc() - timedelta(seconds=1)},
               "future": {"start_date": now_utc() + timedelta(days=1)}, "revoked": {"is_active": False}}
    if state in updates:
        await item.assignment.set(updates[state])
    elif state == "branch_inactive":
        await item.branch.set({"is_active": False})
    elif state == "company_inactive":
        await item.company.set({"is_active": False})
    before = await UserCompany.get_motor_collection().find_one({"_id": item.assignment.id})
    async with client() as http:
        listing = await http.get("/api/v1/companies/", headers=headers)
        detail = await http.get(f"/api/v1/companies/{item.company.id}", headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["total"] == (1 if state == "allowed" else 0)
    assert detail.status_code == (200 if state == "allowed" else 404 if state == "company_inactive" else 403)
    assert await UserCompany.get_motor_collection().find_one({"_id": item.assignment.id}) == before


async def test_global_administrator_without_membership_has_no_tenant_read_bypass():
    item = await data()
    outsider = await user()
    headers = await token(outsider, actor_name=settings.ADMIN_ROLE_NAME)
    async with client() as http:
        listed = await http.get("/api/v1/companies/", headers=headers)
        detail = await http.get(f"/api/v1/companies/{item.company.id}", headers=headers)
        branches = await http.get(f"/api/v1/company-branches/{item.company.id}/branches", headers=headers)
    assert listed.status_code == 200 and listed.json()["data"]["total"] == 0
    assert detail.status_code == branches.status_code == 403


async def test_company_edit_permission_cannot_modify_another_tenant():
    item = await data()
    headers = await token(item.member, ["companies:edit"])
    collection = Company.get_motor_collection()
    before = await collection.find_one({"_id": item.company.id})
    async with client() as http:
        response = await http.put(f"/api/v1/companies/{item.company.id}", headers=headers,
                                  json={"name": "Denied edit"})
    assert response.status_code == 403, response.text
    assert await collection.find_one({"_id": item.company.id}) == before


async def test_branch_create_requires_matching_company_and_current_tenant_role():
    item = await data()
    owner_headers = await token(item.owner, ["companies:create"])
    member_headers = await token(item.member, ["companies:create"])
    body = {"company_id": str(item.company.id), "bussiness_type": "IT", "branch_name": "New",
            "address": "Synthetic", "company_size": 1, "working_days": []}
    url = f"/api/v1/company-branches/{item.company.id}/branches"
    collection = CompanyBranch.get_motor_collection()
    async with client() as http:
        before = await collection.find({}).sort("_id").to_list(length=None)
        denied = await http.post(url, headers=member_headers, json=body)
        assert denied.status_code == 403, denied.text
        assert await collection.find({}).sort("_id").to_list(length=None) == before
        mismatch = await http.post(url, headers=owner_headers, json={**body, "company_id": str(item.other_branch.id)})
        assert mismatch.status_code == 422, mismatch.text
        assert await collection.find({}).sort("_id").to_list(length=None) == before
        created = await http.post(url, headers=owner_headers, json=body)
        assert created.status_code == 201, created.text
        assert created.json()["data"]["company_id"] == str(item.company.id)
        assert await collection.count_documents({}) == len(before) + 1


async def test_owner_and_member_branch_reads_remain_scoped_with_warm_cache(mock_db):
    item = await data()
    owner_headers, member_headers = await token(item.owner), await token(item.member)
    list_url = f"/api/v1/company-branches/{item.company.id}/branches"
    detail_url = list_url + f"/{item.branch.id}"
    async with client() as http:
        cold = await http.get(list_url, headers=owner_headers)
        warm = await http.get(list_url, headers=owner_headers)
        assert cold.status_code == warm.status_code == 200
        assert {value["id"] for value in warm.json()["data"]} == {str(item.branch.id), str(item.other_branch.id)}
        assert CompanyBranchRepository._get_company_branches_key(str(item.company.id)) in mock_db.values
        own = await http.get(list_url, headers=member_headers)
        assert own.status_code == 200 and [value["id"] for value in own.json()["data"]] == [str(item.branch.id)]
        assert (await http.get(list_url + f"/{item.other_branch.id}", headers=member_headers)).status_code == 403
        assert (await http.get(detail_url, headers=member_headers)).status_code == 200
        assert (await http.get(detail_url, headers=member_headers)).status_code == 200
        await item.assignment.set({"is_active": False})
        assert (await http.get(list_url, headers=member_headers)).status_code == 403
        assert (await http.get(detail_url, headers=member_headers)).status_code == 403


async def test_duplicate_assignment_and_inactive_role_return_business_errors_without_writes():
    item = await data()
    headers = await token(item.owner)
    body = {"user_id": str(item.member.id), "company_branch_id": str(item.branch.id)}
    collection = UserCompany.get_motor_collection()
    before = await collection.find_one({"_id": item.assignment.id})
    async with client() as http:
        duplicate = await http.post("/api/v1/user-company-branch/assign", headers=headers, json=body)
        assert duplicate.status_code == 409, duplicate.text
        assert await collection.find_one({"_id": item.assignment.id}) == before
        removed = await http.post("/api/v1/user-company-branch/unassign", headers=headers, json=body)
        assert removed.status_code == 200, removed.text
        inactive = await collection.find_one({"_id": item.assignment.id})
        changed = await http.put(f"/api/v1/user-company-branch/assignments/{item.assignment.id}/role",
                                 headers=headers, params={"role": "manager"})
        assert changed.status_code == 400, changed.text
        assert await collection.find_one({"_id": item.assignment.id}) == inactive


async def test_reassignment_restarts_expired_inactive_membership_and_restores_read_access():
    item = await data()
    await item.assignment.set({"is_active": False, "start_date": now_utc() - timedelta(days=2),
                               "end_date": now_utc() - timedelta(days=1)})
    owner_headers, member_headers = await token(item.owner), await token(item.member)
    async with client() as http:
        assert (await http.get(f"/api/v1/companies/{item.company.id}", headers=member_headers)).status_code == 403
        assigned = await http.post("/api/v1/user-company-branch/assign", headers=owner_headers,
                                    json={"user_id": str(item.member.id), "company_branch_id": str(item.branch.id)})
        assert assigned.status_code == 201, assigned.text
        assert assigned.json()["data"]["assignment_id"] == str(item.assignment.id)
        assert (await http.get(f"/api/v1/companies/{item.company.id}", headers=member_headers)).status_code == 200
    refreshed = await UserCompany.get(item.assignment.id)
    assert refreshed.is_active and refreshed.end_date is None
    assert await UserCompany.find({"user_id": item.member.id, "company_branch_id": item.branch.id}).count() == 1


@pytest.mark.parametrize("problem", ["self", "owner", "direct_permissions", "other_branch"])
async def test_membership_denied_requests_do_not_modify_assignment_database(problem):
    item = await data()
    await item.assignment.set({"role": "manager"})
    headers = await token(item.member)
    target = await user()
    body = {"user_id": str(target.id), "company_branch_id": str(item.branch.id)}
    if problem == "self":
        body["user_id"] = str(item.member.id)
    elif problem == "owner":
        body["user_id"] = str(item.owner.id)
    elif problem == "direct_permissions":
        body["permissions"] = ["users:delete"]
    else:
        body["company_branch_id"] = str(item.other_branch.id)
    collection = UserCompany.get_motor_collection()
    before = await collection.find({}).sort("_id").to_list(length=None)
    async with client() as http:
        response = await http.post("/api/v1/user-company-branch/assign", headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert await collection.find({}).sort("_id").to_list(length=None) == before


async def test_user_actor_mapping_id_and_actor_id_support_removal_through_existing_api():
    administrator, target = await user(is_superuser=True), await user()
    actor = await Actor(name=uuid4().hex, description="Synthetic", is_active=True).insert()
    headers = await token(administrator)
    async with client() as http:
        assigned = await http.post("/api/v1/user-actor/user-actors", headers=headers,
                                   params={"user_id": str(target.id), "actor_id": str(actor.id)})
        assert assigned.status_code == 201, assigned.text
        mapping = assigned.json()["data"]
        assert mapping["id"] == mapping["user_actor_id"]
        assert mapping["actor"]["id"] == str(actor.id)
        read = await http.get(f"/api/v1/user-actor/user-actors/{target.id}", headers=headers)
        assert read.status_code == 200 and read.json()["data"]["user_actor_id"] == mapping["id"]
        removed = await http.delete(f"/api/v1/user-actor/user-actors/{mapping['id']}", headers=headers)
        assert removed.status_code == 200, removed.text
    assert await UserActor.find({"user_id": target.id}).count() == 0
    assert await User.get(target.id) is not None


async def test_membership_api_audit_events_persist_in_mongo_with_caller_and_route(monkeypatch):
    from app.middleware import audit_log as audit

    item = await data()
    target = await user()
    authorization = await token(item.owner)
    saved = asyncio.Queue()
    original_save = audit._save_audit_log_async

    async def observed_save(entry):
        await original_save(entry)
        await saved.put(entry.id)

    monkeypatch.setattr(audit, "_save_audit_log_async", observed_save)
    body = {"user_id": str(target.id), "company_branch_id": str(item.branch.id)}
    async with client() as http:
        assigned = await http.post("/api/v1/user-company-branch/assign", headers=authorization, json=body)
        assert assigned.status_code == 201, assigned.text
        assignment_id = assigned.json()["data"]["assignment_id"]
        calls = [("user_company.assigned", "POST", "/api/v1/user-company-branch/assign")]
        first_id = await asyncio.wait_for(saved.get(), timeout=5)
        ids = [first_id]
        role_url = f"/api/v1/user-company-branch/assignments/{assignment_id}/role"
        changed = await http.put(role_url, headers=authorization, params={"role": "manager"})
        assert changed.status_code == 200, changed.text
        calls.append(("user_company.role_updated", "PUT", role_url))
        ids.append(await asyncio.wait_for(saved.get(), timeout=5))
        removed = await http.post("/api/v1/user-company-branch/unassign", headers=authorization, json=body)
        assert removed.status_code == 200, removed.text
        calls.append(("user_company.unassigned", "POST", "/api/v1/user-company-branch/unassign"))
        ids.append(await asyncio.wait_for(saved.get(), timeout=5))
        delete_url = f"/api/v1/user-company-branch/{assignment_id}"
        deleted = await http.delete(delete_url, headers=authorization)
        assert deleted.status_code == 200, deleted.text
        calls.append(("user_company.deleted", "DELETE", delete_url))
        ids.append(await asyncio.wait_for(saved.get(), timeout=5))
    for audit_id, (event_type, method, endpoint) in zip(ids, calls):
        entry = await AuditLog.get(audit_id)
        assert entry is not None and entry.event_type == event_type and entry.action == event_type
        assert entry.user_id == item.owner.id and entry.success
        assert entry.method == method and entry.endpoint == endpoint
    assert await AuditLog.find({"resource_type": "user_company", "success": True}).count() == 4
